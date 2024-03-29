# -*- coding: utf-8 -*-

# Copyright 2009-2014 Jaap Karssenberg <jaap.karssenberg@gmail.com>
# Copyright 2014 Andri Kusumah


import time
import logging
import platform
from platform import os

import gtk

from zim.plugins import PluginClass, WindowExtension, extends
from zim.actions import action
from zim.fs import TmpFile
from zim.applications import Application, ApplicationError
from zim.gui.widgets import ui_environment, Dialog, ErrorDialog

logger = logging.getLogger('zim.plugin.screenshot')

PLATFORM = os.name
if ui_environment['platform'] == 'maemo':  # don't know what os.name return on maemo
	PLATFORM = 'maemo'

"""
TESTED:
	- import (imagemagick)
	- scrot
UNTESTED:
	- boxcutter (windows, http://keepnote.org/boxcutter/)
	- screenshot-tool (maemo)
"""
COMMAND = 'import'
SUPPORTED_COMMANDS_BY_PLATFORM = dict([
	('posix', ('import', 'scrot', 'screencapture')),
	('nt', ('boxcutter', 'i_view32.exe', 'i_view64.exe')),
	('maemo', ('screenshot-tool',)),
])
SUPPORTED_COMMANDS = SUPPORTED_COMMANDS_BY_PLATFORM[PLATFORM]
if len(SUPPORTED_COMMANDS):
	COMMAND = SUPPORTED_COMMANDS[0]  # set first available tool as default

#WSL='Microsoft' in platform.uname().release;
WSL1='Microsoft' in platform.uname()[3];
WSL2='microsoft' in platform.uname()[2];

if PLATFORM == 'posix' and ( WSL1 or WSL2 ):
	SUPPORTED_COMMANDS=SUPPORTED_COMMANDS+SUPPORTED_COMMANDS_BY_PLATFORM['nt']

class ScreenshotPicker(object):
	cmd_options = dict([
		('scrot', {
			'select': ('--select', '--border'),
			'full': ('--multidisp',),
			'delay': '-d',
		}),
		('import', {
			'select': ('-silent',),
			'full': ('-silent', '-window', 'root'),
			'delay': '-delay',
		}),
		('i_view32.exe', {
			'select': ('/capture=4',),
			'full': ('/capture=0'),
			'delay': None,
			'savePrefix': '/convert=',
		}),
		('i_view64.exe', {
			'select': ('/capture=4',),
			'full': ('/capture=0'),
			'delay': None,
			'savePrefix': '/convert=',
		}),
		('boxcutter', {
			'select': None,
			'full': ('--fullscreen',),
			'delay': None,
		}),
		('screencapture', {
			'select': ('-xs'),
			'full': None,
			'delay': None,
		}),
		('screenshot-tool', {
			'select': None,
			'full': (),
			'delay': '-d',
		})
	])
	cmd_default = COMMAND
	final_cmd_options = ()

	def __init__(self, cmd, select=False, delay=0):
		cmd = self.select_cmd(cmd)
		screenshot_mode = 'select' if select is True else 'full'
		self.final_cmd_options += self.cmd_options[cmd][screenshot_mode]

		if str(delay).isdigit() and int(delay) > 0 and self.cmd_options[cmd]['delay'] is not None:
			self.final_cmd_options += (self.cmd_options[cmd]['delay'], str(delay))

	@classmethod
	def select_cmd(cls, cmd=None):
		if cmd is None or cmd not in SUPPORTED_COMMANDS or cmd not in cls.cmd_options:
			cmd = cls.cmd_default
		return cmd

	@classmethod
	def get_cmd_options(cls, cmd=None, select=False, delay=0):
		cmd = cls.select_cmd(cmd)
		delay = delay if str(delay).isdigit() and int(delay) > 0 else 0
		me = cls(cmd, select, str(delay))
		return me.final_cmd_options

	@classmethod
	def has_delay_cmd(cls, cmd=None):
		cmd = cls.select_cmd(cmd)
		return True if cls.cmd_options[cmd]['delay'] is not None else False

	@classmethod
	def has_select_cmd(cls, cmd):
		cmd = cls.select_cmd(cmd)
		return True if cls.cmd_options[cmd]['select'] is not None else False


class InsertScreenshotPlugin(PluginClass):
	plugin_info = {
		'name': _('Insert Screenshot (FASTER)'),  # T: plugin name
		'description': _('''\
This plugin allows taking a screenshot and directly insert it
in a zim page without a confirmation dialog and at the impulse
of a hot key or toolbar icon with slightly better and more descriptive
filenames (which can make a difference when sharing the images).

This is derived from (and is intended to replace [in operation]) a
core plugin that ships with zim with the same name.
'''),  # T: plugin description
		'author': 'Jaap Karssenberg',
		'help': 'Plugins:Insert Screenshot',
	}
	plugin_preferences = (
		# key, type, label, default
		('autohide', 'bool', _('Hide zim when taking a screenshot (good for small/single-monitor setups).'), False),
		('screenshot_command', 'choice', _('Screenshot Command'), COMMAND, SUPPORTED_COMMANDS), # T: plugin preference
	)
	screenshot_cmd = COMMAND

	def __init__(self, config=None):
		PluginClass.__init__(self, config)
		self.on_preferences_changed(self.preferences)
		self.preferences.connect('changed', self.on_preferences_changed)

	def on_preferences_changed(self, preferences):
		self.screenshot_cmd = preferences['screenshot_command']

	@classmethod
	def check_dependencies(cls):
		cmds = []
		is_ok = False
		if len(SUPPORTED_COMMANDS):
			for cmd in SUPPORTED_COMMANDS:
				has_tool = Application(cmd).tryexec()
				if has_tool:
					is_ok = True
					cmds.append((cmd, True, False))
				else:
					cmds.append((cmd, False, False))
		return is_ok, cmds


@extends('MainWindow')
class MainWindowExtension(WindowExtension):
	uimanager_xml = '''
	<ui>
		<menubar name='menubar'>
			<menu action='insert_menu'>
				<placeholder name='plugin_items'>
					<menuitem action='insert_screenshot'/>
				</placeholder>
			</menu>
		</menubar>
		<toolbar name='toolbar'>
			<placeholder name='tools'>
				<toolitem action='insert_screenshot'/>
			</placeholder>
		</toolbar>
	</ui>
	'''
	screenshot_command = COMMAND
	plugin = None
	pngquant = Application('pngquant')
	onImageInserted = Application('zim-on-image-inserted')

	def __init__(self, plugin, window):
		WindowExtension.__init__(self, plugin, window)
		self.on_preferences_changed(plugin.preferences)
		self.connectto(plugin.preferences, 'changed', self.on_preferences_changed)
		self.plugin = plugin

	def on_preferences_changed(self, preferences):
		if preferences['screenshot_command']:
			self.screenshot_command = preferences['screenshot_command']

	@action(
		_('_Screenshot...'),
		stock=gtk.STOCK_LEAVE_FULLSCREEN,
		readonly=True,
		accelerator = '<Control><Shift>U'
	)  # T: menu item for insert screenshot plugin
	def insert_screenshot(self):
		notebook = self.window.ui.notebook  # XXX
		page = self.window.ui.page  # XXX

		tmpfile = TmpFile('insert-screenshot.png')
		selection_mode = True
		delay = 0

		#delay = self.time_spin.get_value_as_int()
		prefix = page.name.replace(':','-')

		options = ScreenshotPicker.get_cmd_options(self.screenshot_command, selection_mode, str(delay))
		helper = Application((self.screenshot_command,) + options)

		def callback(status, tmpfile):
			if self.plugin.preferences['autohide']:
				self.window.present()
			if status == helper.STATUS_OK:
				name = prefix+'-'+("%x" % time.time())+'.png'
				imgdir = notebook.get_attachments_dir(page)
				imgfile = imgdir.new_file(name)
				if self.pngquant.tryexec():
					imgfile.parent().touch();
					try:
						self.pngquant.run(('--skip-if-larger',tmpfile,'--output',imgfile,))
						if os.path.isfile(imgfile.encodedpath):
							logger.info("png8 conversion successful")
						else:
							tmpfile.rename(imgfile);
					except ApplicationError as e:
						# pngquant returns 98 if the resulting file would be larger, which throws an exception
						tmpfile.rename(imgfile);
				else:
					tmpfile.rename(imgfile)

				if hasattr(self.window.ui, 'mainwindow'):
					pageview = self.window.ui.mainwindow.pageview
				else:
					pageview = self.window.pageview

				pageview.insert_image(imgfile, interactive=False, force=True)

				if self.onImageInserted.tryexec():
					self.onImageInserted.run((imgfile, notebook.dir, page.name));
				else:
					logger.debug("dne: %s"%self.onImageInserted);
			else:
				ErrorDialog(self.window.ui,
							_('Some error occurred while running "%s"') % self.screenshot_command).run()
				# T: Error message in "insert screenshot" dialog, %s will be replaced by application name

		savePrefix = '';
		options=ScreenshotPicker(COMMAND).cmd_options[self.screenshot_command];
		if 'savePrefix' in options:
			savePrefix = options['savePrefix'];

		tmpfile.dir.touch()
		helper.spawn((savePrefix+tmpfile.basename,), callback, tmpfile, tmpfile.dir)

		if self.plugin.preferences['autohide']:
			self.window.iconify()

		return True
