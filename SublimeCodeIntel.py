# -*- coding: utf-8 -*-
# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License
# Version 1.1 (the "License"); you may not use this file except in
# compliance with the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS"
# basis, WITHOUT WARRANTY OF ANY KIND, either express or implied. See the
# License for the specific language governing rights and limitations
# under the License.
#
# The Original Code is SublimeCodeIntel code by German M. Bravo (Kronuz).
#
# Contributor(s):
#   ActiveState Software Inc
#
# Portions created by ActiveState Software Inc are Copyright (C) 2000-2007
# ActiveState Software Inc. All Rights Reserved.
#
"""
CodeIntel is a plugin intended to display "code intelligence" information.
The plugin is based in code from the Open Komodo Editor and has a MPL license.
Port by German M. Bravo (Kronuz). 2011-2017

"""
from __future__ import absolute_import, unicode_literals, print_function

VERSION = "3.0.0-beta.25"


import os
import sys

__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)

python_sitelib_path = os.path.join(os.path.normpath(__path__), 'libs')
if python_sitelib_path not in sys.path:
    sys.path.insert(0, python_sitelib_path)

import re
import json
import logging
import textwrap
import threading
from collections import deque, defaultdict
from copy import deepcopy

import sublime
import sublime_plugin

from codeintel import CodeIntel, CodeIntelBuffer, logger as codeintel_logger, logger_level as codeintel_logger_level

PLUGIN_NAME = 'SublimeCodeIntel'

logger_name = 'CodeIntel'
logger_level = logging.WARNING  # WARNING

logger = logging.getLogger(logger_name)
logger.setLevel(logger_level)
if logger.root.handlers:
    logger.root.handlers[0].setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))


EXTRA_PATHS_MAP = {
    'ECMAScript': 'ecmascriptExtraPaths',
    'JavaScript': 'javascriptExtraPaths',
    'Node.js': 'nodejsExtraPaths',
    'Perl': 'perlExtraPaths',
    'PHP': 'phpExtraPaths',
    'Python3': 'python3ExtraPaths',
    'Python': 'pythonExtraPaths',
    'Ruby': 'rubyExtraPaths',
    'C++': 'cppExtraPaths',
}

EXCLUDE_PATHS_MAP = {
    'ECMAScript': 'ecmascriptExcludePaths',
    'JavaScript': 'javascriptExcludePaths',
    'Node.js': 'nodejsExcludePaths',
    'Perl': 'perlExcludePaths',
    'PHP': 'phpExcludePaths',
    'Python3': 'python3ExcludePaths',
    'Python': 'pythonExcludePaths',
    'Ruby': 'rubyExcludePaths',
    'C++': 'cppExcludePaths',
}


class Settings:
    """This class provides global access to and management of plugin settings."""

    def __init__(self):
        """Initialize a new instance."""
        self.settings = {}
        self.previous_settings = {}
        self.changeset = set()
        self.plugin_settings = None
        self.on_update_callback = None
        self.edits = defaultdict(list)

    def load(self, force=False):
        """Load the plugin settings."""
        if force or not self.settings:
            self.observe()
            self.on_update()

    def has_setting(self, setting):
        """Return whether the given setting exists."""
        return setting in self.settings

    def get(self, setting, default=None, lang=None):
        """Return a plugin setting, defaulting to default if not found."""
        language_settings = self.settings.get('language_settings', {}).get(lang)
        if language_settings and setting in language_settings:
            return language_settings[setting]
        return self.settings.get(setting, default)

    def set(self, setting, value, changed=False):
        """
        Set a plugin setting to the given value.

        Clients of this module should always call this method to set a value
        instead of doing settings['foo'] = 'bar'.

        If the caller knows for certain that the value has changed,
        they should pass changed=True.

        """
        self.copy()
        self.settings[setting] = value

        if changed:
            self.changeset.add(setting)

    def pop(self, setting, default=None):
        """
        Remove a given setting and return default if it is not in self.settings.

        Clients of this module should always call this method to pop a value
        instead of doing settings.pop('foo').

        """
        self.copy()
        return self.settings.pop(setting, default)

    def copy(self):
        """Save a copy of the plugin settings."""
        self.previous_settings = deepcopy(self.settings)

    def observe(self, observer=None):
        """Observer changes to the plugin settings."""
        self.plugin_settings = sublime.load_settings('{}.sublime-settings'.format(PLUGIN_NAME))
        self.plugin_settings.clear_on_change(PLUGIN_NAME)
        self.plugin_settings.add_on_change(PLUGIN_NAME, observer or self.on_update)

    def on_update_call(self, callback):
        """Set a callback to call when user settings are updated."""
        self.on_update_callback = callback

    def merge_user_settings(self, settings):
        """Return the default linter settings merged with the user's settings."""

        default = settings.get('default', {})
        user = settings.get('user', {})

        if user:
            for setting_name in ('codeintel_syntax_map', 'codeintel_language_settings'):
                default_setting = default.pop(setting_name, {})
                user_setting = user.get(setting_name, {})

                for name, data in user_setting.items():
                    if name in default_setting:
                        default_setting[name].update(data)
                    else:
                        default_setting[name] = data
                default[setting_name] = default_setting
                user.pop(setting_name, None)
            default.update(user)

        return default

    def get_prefs(self, lang=None):
        prefs = {
            'codeintel_max_recursive_dir_depth': self.settings.get('max_recursive_dir_depth'),
            'codeintel_scan_files_in_project': self.settings.get('scan_files_in_project'),
            'codeintel_selected_catalogs': self.settings.get('selected_catalogs'),
        }

        disabled_languages = self.settings.get('disabled_languages', [])

        scan_extra_paths = self.settings.get('scan_extra_paths', [])
        if scan_extra_paths:
            scan_extra_paths = set(os.path.normcase(os.path.normpath(e)).rstrip(os.sep) for e in scan_extra_paths)

        scan_exclude_paths = self.settings.get('scan_exclude_paths', [])
        if scan_exclude_paths:
            scan_exclude_paths = set(os.path.normcase(os.path.normpath(e)).rstrip(os.sep) for e in scan_exclude_paths)

        language_settings = self.settings.get('language_settings', {})
        for l, s in language_settings.items():
            if lang is not None and l != lang:
                continue

            if l in disabled_languages or s.get('@disable'):
                continue

            for k, v in s.items():
                if k not in self.settings:
                    prefs[k] = v

            extra_paths_name = EXTRA_PATHS_MAP.get(l)
            language_scan_extra_paths = set(s.get('scan_extra_paths', [])) | set(s.get(extra_paths_name, []))
            if language_scan_extra_paths:
                language_scan_extra_paths = [os.path.normcase(os.path.normpath(e)).rstrip(os.sep) for e in scan_extra_paths | language_scan_extra_paths]
            if extra_paths_name:
                prefs[extra_paths_name] = os.pathsep.join(language_scan_extra_paths)

            exclude_paths_name = EXCLUDE_PATHS_MAP.get(l)
            language_scan_exclude_paths = set(s.get('scan_exclude_paths', [])) | set(s.get(exclude_paths_name, []))
            if language_scan_exclude_paths:
                language_scan_exclude_paths = [os.path.normcase(os.path.normpath(e)).rstrip(os.sep) for e in scan_exclude_paths | language_scan_exclude_paths]
            if exclude_paths_name:
                prefs[exclude_paths_name] = os.pathsep.join(language_scan_exclude_paths)

        return prefs

    def on_update(self):
        """
        Update state when the user settings change.

        The settings before the change are compared with the new settings.
        Depending on what changes, views will either be redrawn or relinted.

        """

        settings = self.merge_user_settings(self.plugin_settings)
        self.settings.clear()
        self.settings.update(settings)

        need_deactivate = False
        for setting in ('@disable', 'command', 'oop_mode', 'log_levels'):
            if (
                setting in self.changeset or
                self.previous_settings and self.previous_settings.get(setting) != self.settings.get(setting)
            ):
                self.changeset.discard(setting)
                need_deactivate = True

        if (
            'debug' in self.changeset or
            self.previous_settings.get('debug', False) != self.settings.get('debug', False)
        ):
            self.changeset.discard('debug')

            if self.settings.get('debug'):
                logger.setLevel(logging.DEBUG)
                codeintel_logger.setLevel(logging.DEBUG)
            else:
                logger.setLevel(logger_level)
                codeintel_logger.setLevel(codeintel_logger_level)

        if need_deactivate:
            ci.deactivate()

        if not self.settings.get('@disable'):
            env = dict(os.environ)
            env.update(self.settings.get('env', {}))

            prefs = self.get_prefs()

            if ci.enabled:
                ci.mgr.set_global_environment(
                    env=env,
                    prefs=prefs,
                )
            else:
                command = self.settings.get('command')
                oop_mode = self.settings.get('oop_mode')
                log_levels = self.settings.get('log_levels')
                ci.activate(
                    reset_db_as_necessary=False,
                    codeintel_command=command,
                    oop_mode=oop_mode,
                    log_levels=log_levels,
                    env=env,
                    prefs=prefs,
                )

        self.changeset.clear()

        if self.previous_settings and self.on_update_callback:
            self.on_update_callback(self)

        self.copy()

    def save(self, view=None):
        """
        Regenerate and save the user settings.

        User settings are updated with the default settings and the defaults
        from every linter, and if the user settings are currently being edited,
        the view is updated.

        """

        self.load()

        # Fill in default linter settings
        settings = self.settings

        settings_filename = '{}.sublime-settings'.format(PLUGIN_NAME)
        user_settings_path = os.path.join(sublime.packages_path(), 'User', settings_filename)
        settings_views = []

        if view is None:
            # See if any open views are the user prefs
            for window in sublime.windows():
                for view in window.views():
                    if view.file_name() == user_settings_path:
                        settings_views.append(view)
        else:
            settings_views = [view]

        if settings_views:
            def replace(edit):
                if not view.is_dirty():
                    j = json.dumps({'user': settings}, indent=4, sort_keys=True)
                    j = j.replace(' \n', '\n')
                    view.replace(edit, sublime.Region(0, view.size()), j)

            for view in settings_views:
                self.edits[view.id()].append(replace)
                view.run_command('codeintel_edit')
                view.run_command('save')
        else:
            user_settings = sublime.load_settings(settings_filename)
            user_settings.set('user', settings)
            sublime.save_settings(settings_filename)

    def edit(self, vid, edit):
        """Perform an operation on a view with the given edit object."""
        callbacks = self.edits.pop(vid, [])

        for c in callbacks:
            c(edit)


class CodeintelEditCommand(sublime_plugin.TextCommand):
    """A plugin command used to generate an edit object for a view."""

    def run(self, edit):
        """Run the command."""
        settings.edit(self.view.id(), edit)


class CodeintelToggleSettingCommand(sublime_plugin.WindowCommand):
    """Command that toggles a setting."""

    def is_visible(self, **args):
        """Return True if the opposite of the setting is True."""
        if args.get('checked', False):
            return True

        if settings.has_setting(args['setting']):
            setting = settings.get(args['setting'], None)
            return setting is not None and setting is not args['value']
        else:
            return args['value'] is not None

    def is_checked(self, **args):
        """Return True if the setting should be checked."""
        if args.get('checked', False):
            setting = settings.get(args['setting'], False)
            return setting is True
        else:
            return False

    def run(self, **args):
        """Toggle the setting if value is boolean, or remove it if None."""

        if 'value' in args:
            if args['value'] is None:
                settings.pop(args['setting'])
            else:
                settings.set(args['setting'], args['value'], changed=True)
        else:
            setting = settings.get(args['setting'], False)
            settings.set(args['setting'], not setting, changed=True)

        settings.save()


class CodeintelHandler(object):
    HISTORY_SIZE = 64
    jump_history_by_window = {}  # map of window id -> deque([], HISTORY_SIZE)

    status_msg = {}
    status_lineno = {}
    status_lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        self.log = logging.getLogger(logger_name + '.' + self.__class__.__name__)
        super(CodeintelHandler, self).__init__(*args, **kwargs)
        ci.add_observer(self)

    @property
    def window(self):
        if hasattr(self, '_window'):
            return self._window
        window = sublime.active_window()
        if window:
            return window

    @window.setter
    def window(self, value):
        self._window = value

    @property
    def view(self):
        if hasattr(self, '_view'):
            return self._view
        window = self.window
        if window:
            view = window.active_view()
            if view:
                return view

    @view.setter
    def view(self, value):
        self._view = value

    def set_status(self, ltype, msg=None, timeout=None, delay=0, lid='SublimeCodeIntel', logger_obj=None):
        view = self.view
        if not view:
            return

        if timeout is None:
            timeout = {'error': 3000, 'warning': 5000, 'info': 10000,
                       'event': 10000}.get(ltype, 3000)

        if msg is None:
            msg, ltype = ltype, 'info'
        if isinstance(msg, tuple):
            try:
                msg = msg[0] % msg[1:]
            except:
                msg = repr(msg)
        msg = msg.strip()

        CodeintelHandler.status_lock.acquire()
        try:
            CodeintelHandler.status_msg.setdefault(lid, [None, None, 0])
            if msg == CodeintelHandler.status_msg[lid][1]:
                return
            CodeintelHandler.status_msg[lid][2] += 1
            order = CodeintelHandler.status_msg[lid][2]
        finally:
            CodeintelHandler.status_lock.release()

        def _set_status():
            is_warning = 'warning' in lid
            if not is_warning:
                view_sel = view.sel()
                lineno = view.rowcol(view_sel[0].end())[0] if view_sel else 0
            CodeintelHandler.status_lock.acquire()
            try:
                current_type, current_msg, current_order = CodeintelHandler.status_msg.get(lid, [None, None, 0])
                if msg != current_msg and order == current_order:
                    _logger_obj = getattr(logger, ltype, None) if logger_obj is None else logger_obj
                    if _logger_obj:
                        _logger_obj(msg)
                    if ltype != 'debug':
                        view.set_status(lid, "%s %s: %s" % (lid, ltype.capitalize(), msg.rstrip('.')))
                        CodeintelHandler.status_msg[lid] = [ltype, msg, order]
                    if not is_warning:
                        CodeintelHandler.status_lineno[lid] = lineno
            finally:
                CodeintelHandler.status_lock.release()

        def _erase_status():
            CodeintelHandler.status_lock.acquire()
            try:
                if msg == CodeintelHandler.status_msg.get(lid, [None, None, 0])[1]:
                    view.erase_status(lid)
                    CodeintelHandler.status_msg[lid][1] = None
                    if lid in CodeintelHandler.status_lineno:
                        del CodeintelHandler.status_lineno[lid]
            finally:
                CodeintelHandler.status_lock.release()

        if msg:
            sublime.set_timeout(_set_status, delay or 0)
            sublime.set_timeout(_erase_status, timeout)
        else:
            sublime.set_timeout(_erase_status, delay or 0)

    def pos2bytes(self, content, pos):
        return len(content[:pos].encode('utf-8'))

    def guess_language(self, view, path):
        language = os.path.splitext(os.path.basename(view.settings().get('syntax')))[0]
        lang = settings.get('syntax_map', {}).get(language, language)
        logger.info("Language guessed: %s (for %s)", lang, language)
        if lang in settings.get('disabled_languages'):
            return
        if settings.get('@disable', False, lang=lang):
            return
        return lang

    def buf_from_view(self, view):
        if not view:
            return

        view_sel = view.sel()
        if not view_sel:
            return

        file_name = view.file_name()
        path = file_name if file_name else "<Unsaved>"

        lang = self.guess_language(view, path)
        if not lang or lang not in ci.languages:
            logger.debug("buf_from_view: %r, %r? no: language unavailable in: [%s]", path, lang, ", ".join(ci.languages))
            return

        if not settings.get('live', False, lang=lang):
            logger.debug("buf_from_view: %r, %r? no: live disabled", path, lang)
            return

        logger.debug("buf_from_view: %r, %r? yes", path, lang)

        vid = view.id()
        try:
            buf = ci.buffers[vid]
        except KeyError:
            logger.debug("creating new %s document %s", lang, path)
            buf = CodeIntelBuffer(ci, vid=vid)
            ci.buffers[vid] = buf

        sel = view_sel[0]
        original_pos = sel.end()
        lpos = view.line(sel).begin()

        text_in_current_line = view.substr(sublime.Region(lpos, original_pos + 1))
        text = view.substr(sublime.Region(0, view.size()))

        # Get encoded content and current position
        pos = self.pos2bytes(text, original_pos)

        buf.lang = lang
        buf.path = path
        buf.text = text
        buf.pos = pos
        buf.text_in_current_line = text_in_current_line
        buf.original_pos = original_pos

        prefs = settings.get_prefs(lang)

        if settings.get('scan_files_in_project', lang=lang):
            window = sublime.active_window()
            extra_paths_name = EXTRA_PATHS_MAP.get(lang)
            extra_paths = prefs.get(extra_paths_name, '').split(os.pathsep)
            exclude_paths_name = EXCLUDE_PATHS_MAP.get(lang)
            exclude_paths = prefs.get(exclude_paths_name, '').split(os.pathsep)
            for f in window.folders():
                f = os.path.normcase(os.path.normpath(f)).rstrip(os.sep)
                if f not in exclude_paths and f not in extra_paths:
                    extra_paths.append(f)
            if extra_paths:
                prefs[extra_paths_name] = os.pathsep.join(extra_paths)

        buf.prefs = prefs

        return buf

    def format_completions_by_language(self, cplns, lang, text_in_current_line, type):
        function = None if 'import ' in text_in_current_line else 'function'

        def get_desc(c):
            return c[2] if len(c) > 2 else c[1]

        def get_name(c):
            name = c[1]
            name = name.replace("$", "\\$")
            if c[0] == function:
                name += "($0)"
            return name

        def get_type(c):
            return c[0].title()

        if lang == 'PHP' and type != 'object-members':
            def get_name(c):
                name = c[1]
                if c[0] == 'variable':
                    name = "$" + name
                name = name.replace("$", "\\$")
                if c[0] == function:
                    name += "($0)"
                return name

        if lang == 'ECMAScript':
            def get_name(c):
                name = c[1]
                name = name.replace("$", "\\$")
                if c[0] == 'attribute':
                    name += "=$0 "
                elif c[0] == function:
                    name += "($0)"
                return name

        def sorter(c):
            return {
                'import': '_',
                'attribute': '__',
                'variable': '__',
                'function': '___',
            }.get(c[0].lower(), c[0]), c[1]

        return [('%s\t〔%s〕' % (get_desc(c), get_type(c)), get_name(c)) for c in sorted(cplns, key=sorter)]

    # Handlers follow

    def on_document_scanned(self, buf):
        """Handler callback for scan_document"""

    def on_get_calltip_range(self, buf, start, end):
        pass

    def on_trg_from_pos(self, buf, context, trg):
        if context == 'trg_from_pos':
            buf.async_eval_at_trg(self, trg)
        elif context == 'defn_trg_from_pos':
            buf.async_eval_at_trg(self, trg)

    def set_status_message(self, buf, message, highlight=None):
        def _set_status_message():
            self.set_status(message)
        sublime.set_timeout(_set_status_message, 0)

    def set_call_tip_info(self, buf, calltip, explicit, trg):
        def _set_call_tip_info():
            view = self.view
            if not view:
                return
            vid = view.id()
            if vid != buf.vid:
                return

            # TODO: This snippets are created and work for Python language def functions.
            # i.e. in the form: name(arg1, arg2, arg3)
            # Other languages might need different treatment.

            # Figure out how many arguments are there already:
            text_in_current_line = buf.text_in_current_line[:-1]  # Remove next char after cursor
            arguments = text_in_current_line.rpartition('(')[2].replace(' ', '').strip() or 0
            if arguments:
                initial_separator = ''
                if arguments[-1] == ',':
                    arguments = arguments[:-1]
                else:
                    initial_separator += ','
                if not text_in_current_line.endswith(' '):
                    initial_separator += ' '
                arguments = arguments.count(',') + 1 if arguments else 0

            # Insert parameters as snippet:
            snippet = None
            tip_info = calltip.split('\n')
            tip0 = tip_info[0]
            m = re.search(r'^(.*\()([^\[\(\)]*)(.*)$', tip0)
            if m:
                params = [p.strip() for p in m.group(2).split(',')]
                if params:
                    n = 1
                    tip0 = []
                    snippet = []
                    for i, p in enumerate(params):
                        if p:
                            var, sep, default = p.partition('=')
                            var = var.strip()
                            tvar = var
                            if sep:
                                tvar = "%s<i>=%s</i>" % (tvar, default)
                            # if i == arguments:
                            #     tvar = "<b>%s</b>" % tvar
                            tip0.append(tvar)
                            if i >= arguments:
                                if ' ' in var:
                                    var = var.split(' ')[1]
                                if var[0] == '$':
                                    var = var[1:]
                                snippet.append('${%s:%s}' % (n, var))
                                n += 1
                    tip0 = "<h1>%s%s%s</h1>" % (m.group(1), ', '.join(tip0), m.group(3))
                    snippet = ', '.join(snippet)
                    if arguments and snippet:
                        snippet = initial_separator + snippet
            css = (
                "html {background-color: #232628; color: #999999;}" +
                "body {font-size: 10px; }" +
                "b {color: #6699cc; }" +
                "a {color: #99cc99; }" +
                "h1 {color: #cccccc; font-weight: normal; font-size: 11px; }"
            )

            # Wrap lines that are too long:
            wrapper = textwrap.TextWrapper(width=100, break_on_hyphens=False, break_long_words=False)
            measured_tips = [tip0]
            for t in tip_info[1:]:
                measured_tips.extend(wrapper.wrap(t))

            if hasattr(view, 'show_popup'):
                def insert_snippet(href):
                    view.run_command('insert_snippet', {'contents': snippet})
                    view.hide_popup()

                view.show_popup('<style>%s</style>%s<br><br><a href="insert">insert</a>' % (css, "<br>".join(measured_tips)), location=-1, max_width=700, on_navigate=insert_snippet)

            else:
                # Insert tooltip snippet
                padding = '   '
                snippets = [((padding if i > 0 else '') + l + (padding if i > 0 else ''), snippet or '${0}') for i, l in enumerate(measured_tips)]

                buf.cplns = snippets or None
                if buf.cplns:
                    view.run_command('auto_complete', {
                        'disable_auto_insert': True,
                        'api_completions_only': True,
                        'next_completion_if_showing': False,
                        'auto_complete_commit_on_tab': True,
                    })
        sublime.set_timeout(_set_call_tip_info, 0)

    def set_auto_complete_info(self, buf, cplns, trg):
        def _set_auto_complete_info():
            view = self.view
            if not view:
                return
            vid = view.id()
            if vid != buf.vid:
                return

            _cplns = self.format_completions_by_language(cplns, buf.lang, buf.text_in_current_line, trg.get('type'))

            buf.cplns = _cplns or None
            if buf.cplns:
                view.run_command('auto_complete', {
                    'disable_auto_insert': True,
                    'api_completions_only': True,
                    'next_completion_if_showing': False,
                    'auto_complete_commit_on_tab': True,
                })
        sublime.set_timeout(_set_auto_complete_info, 0)

    def set_definitions_info(self, buf, defns, trg):
        def _set_definitions_info():
            view = self.view

            view_sel = view.sel()
            if not view_sel:
                return

            file_name = view.file_name()
            path = file_name if file_name else "<Unsaved>"

            defn = defns[0]
            row, col = defn['line'], 1
            path = defn['path']
            if not path:
                msg = "Cannot jump to definition!"
                logger.debug(msg)
                return

            jump_location = "%s:%s:%s" % (path, row, col)
            msg = "Jumping to: %s" % jump_location
            logger.debug(msg)

            window = sublime.active_window()
            wid = window.id()
            if wid not in CodeintelHandler.jump_history_by_window:
                CodeintelHandler.jump_history_by_window[wid] = deque([], CodeintelHandler.HISTORY_SIZE)
            jump_history = CodeintelHandler.jump_history_by_window[wid]

            # Save current position so we can return to it
            row, col = view.rowcol(view_sel[0].begin())
            current_location = "%s:%d:%d" % (file_name, row + 1, col + 1)
            jump_history.append(current_location)

            window.open_file(jump_location, sublime.ENCODED_POSITION)
            window.open_file(jump_location, sublime.ENCODED_POSITION)
        sublime.set_timeout(_set_definitions_info, 0)

    def done(self):
        pass


class SublimeCodeIntel(CodeintelHandler, sublime_plugin.EventListener):
    def observer(self, topic, data):
        def _get_and_log_message(response):
            message = response.get('message')
            if message:
                stack = response.get('stack')
                if stack:
                    logger.error(message.rstrip() + "\n" + stack)
            return message

        def _observer():
            if topic == 'status_message':
                ltype = 'info'
            elif topic == 'error_message':
                ltype = 'error'
            elif 'codeintel_buffer_scanned':
                return
            else:
                return
            progress = data.get('progress') or data.get('completed')
            if progress is not None:
                total = data.get('total', 100)
                if not total:
                    progress = None
                elif total == 100:
                    progress = ("%0.1f" % progress).rstrip('.0') + "%"
                else:
                    progress = "%s/%s" % (progress, total)
            message = _get_and_log_message(data)
            if progress and message:
                message = "%s - %s" % (progress, message)
            elif progress:
                message = progress
            elif not message:
                return
            self.set_status(ltype, message, lid='SublimeCodeIntel Notification')
        sublime.set_timeout(_observer, 0)

    def on_pre_save(self, view):
        if view.is_dirty():
            buf = self.buf_from_view(view)
            if buf:
                buf.scan_document(self, True)

    def on_close(self, view):
        vid = view.id()
        ci.buffers.pop(vid, None)

    def on_modified(self, view):
        view_sel = view.sel()
        if not view_sel:
            return

        if settings.get('@disable', False) or not settings.get('live', False):
            return

        sel = view_sel[0]
        pos = sel.end()
        current_char = view.substr(sublime.Region(pos - 1, pos))

        if not current_char or current_char in ('\n', '\t'):
            return

        command_history = getattr(view, 'command_history', None)
        if command_history:
            redo_command = command_history(1)
            previous_command = view.command_history(0)
            before_previous_command = view.command_history(-1)
        else:
            redo_command = previous_command = before_previous_command = None

        # print('on_modified', "%r\n\tcommand_history: %r\n\tredo_command: %r\n\tprevious_command: %r\n\tbefore_previous_command: %r" % (current_char, bool(command_history), redo_command, previous_command, before_previous_command))
        if not command_history or redo_command[1] is None and (
            previous_command[0] == 'insert' and previous_command[1]['characters'][-1] not in ('\n', '\t') or
            previous_command[0] in ('insert_completion', 'paste', 'codeintel_complete_commit') or
            previous_command[0] == 'insert_snippet' and previous_command[1]['contents'] in (
                '(${0:$SELECTION})', '[${0:$SELECTION}]', '{${0:$SELECTION}}', '`${0:$SELECTION}`', '"${0:$SELECTION}"', "'${0:$SELECTION}'",
                '($0)', '[$0]', '{$0}', '`$0`', '"$0"', "'$0'",
            ) or
            before_previous_command[0] in ('insert', 'paste') and (
                previous_command[0] == 'commit_completion' or
                previous_command[0] == 'insert_completion' or
                previous_command[0] == 'insert_best_completion'
            )
        ):
            buf = self.buf_from_view(view)
            # print('on_modified.triggering', bool(buf))
            if buf:
                buf.trg_from_pos(self, True)

    def on_selection_modified(self, view):
        pass

    def on_query_completions(self, view, prefix, locations):
        buf = self.buf_from_view(view)
        if buf:
            cplns, buf.cplns = getattr(buf, 'cplns', None), None
            return cplns

    def on_query_context(self, view, key, operator, operand, match_all):
        if key.startswith("codeintel.setting."):
            setting_name = key[len("codeintel.setting."):]
            value = settings.get(setting_name)
            if operator == sublime.OP_NOT_EQUAL:
                return value != operand
            elif operator == sublime.OP_EQUAL:
                return value == operand


class CodeintelAutoComplete(CodeintelHandler, sublime_plugin.TextCommand):
    def run(self, edit, block=False):
        view = self.view

        buf = self.buf_from_view(view)

        if buf:
            buf.trg_from_pos(self, True)


class GotoPythonDefinition(CodeintelHandler, sublime_plugin.TextCommand):
    def run(self, edit, block=False):
        view = self.view

        buf = self.buf_from_view(view)

        if buf:
            buf.defn_trg_from_pos(self)


class BackToPythonDefinition(sublime_plugin.TextCommand):
    def run(self, edit, block=False):
        window = sublime.active_window()
        wid = window.id()
        if wid in CodeintelHandler.jump_history_by_window:
            jump_history = CodeintelHandler.jump_history_by_window[wid]

            if len(jump_history) > 0:
                previous_location = jump_history.pop()
                window = sublime.active_window()
                window.open_file(previous_location, sublime.ENCODED_POSITION)


class CodeintelCompleteCommitCommand(CodeintelHandler, sublime_plugin.TextCommand):
    def run(self, edit, character):
        view = self.view

        buf = self.buf_from_view(view)
        if buf:
            cpln_fillup_chars = buf.cpln_fillup_chars
            cpln_stop_chars = buf.cpln_stop_chars
        else:
            cpln_fillup_chars = ""
            cpln_stop_chars = "~`!@#$%^&*()-=+{}[]|\\;:'\",.<>?/ "

        # Fillup characters commit autocomplete
        if settings.get(buf.lang, 'complete_commit_fillup') and character in cpln_fillup_chars:
            view.window().run_command('commit_completion')
            if character not in ("(", "="):
                view.run_command('insert', {'characters': character})

        # Stop characters hide autocomplete window
        elif character in cpln_stop_chars:
            view.run_command('hide_auto_complete')
            view.run_command('insert', {'characters': character})

        else:
            view.run_command('insert', {'characters': character})


if 'plugin_is_loaded' not in globals():
    settings = Settings()
    ci = CodeIntel(lambda fn: sublime.set_timeout(fn, 0))

    # Set to true when the plugin is loaded at startup
    plugin_is_loaded = False


def plugin_loaded():
    global plugin_is_loaded, settings
    plugin_is_loaded = True

    settings.load()


# ST3 features a plugin_loaded hook which is called when ST's API is ready.
#
# We must therefore call our init callback manually on ST2. It must be the last
# thing in this plugin (thanks, beloved contributors!).
if int(sublime.version()) < 3000:
    plugin_loaded()
