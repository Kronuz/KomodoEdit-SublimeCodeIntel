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
Port by German M. Bravo (Kronuz). 2011-2015

"""
from __future__ import absolute_import, unicode_literals, print_function

VERSION = "3.0.0"

codeintel_syntax_map = {
    "Python Django": "Python",
}


import os
import sys

__file__ = os.path.normpath(os.path.abspath(__file__))
__path__ = os.path.dirname(__file__)

libs_path = os.path.normpath(os.path.join(__path__, 'libs'))
if libs_path not in sys.path:
    sys.path.insert(0, libs_path)

common_path = os.path.normpath(os.path.join(__path__, 'libs', 'common'))
if common_path not in sys.path:
    sys.path.insert(0, common_path)

arch_path = os.path.normpath(os.path.join(__path__, 'arch'))
if arch_path not in sys.path:
    sys.path.insert(0, arch_path)

import re
import logging
import threading
import collections

import sublime
import sublime_plugin

from codeintel import CodeIntel, CodeIntelBuffer

logger_name = 'sublimecodeintel'
logger = logging.getLogger(logger_name)

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.DEBUG)  # INFO

HISTORY_SIZE = 64
jump_history_by_window = {}  # map of window id -> collections.deque([], HISTORY_SIZE)

status_msg = {}
status_lineno = {}
status_lock = threading.Lock()


class CodeIntelHandler(object):
    def __init__(self, *args, **kwargs):
        self.log = logging.getLogger(logger_name + '.' + self.__class__.__name__)
        super(CodeIntelHandler, self).__init__(*args, **kwargs)

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

    def set_status(self, ltype, msg=None, timeout=None, delay=0, lid='CodeIntel', logger_obj=None):
        view = self.view
        if not view:
            return

        if timeout is None:
            timeout = {'error': 3000, 'warning': 5000, 'info': 10000,
                       'event': 10000}.get(ltype, 3000)

        if msg is None:
            msg, ltype = ltype, 'info'
        msg = msg.strip()

        status_lock.acquire()
        try:
            status_msg.setdefault(lid, [None, None, 0])
            if msg == status_msg[lid][1]:
                return
            status_msg[lid][2] += 1
            order = status_msg[lid][2]
        finally:
            status_lock.release()

        def _set_status():
            is_warning = 'warning' in lid
            if not is_warning:
                view_sel = view.sel()
                lineno = view.rowcol(view_sel[0].end())[0] if view_sel else 0
            status_lock.acquire()
            try:
                current_type, current_msg, current_order = status_msg.get(lid, [None, None, 0])
                if msg != current_msg and order == current_order:
                    _logger_obj = getattr(logger, ltype, None) if logger_obj is None else logger_obj
                    if _logger_obj:
                        _logger_obj(msg)
                    if ltype != 'debug':
                        view.set_status(lid, "%s: %s" % (ltype.capitalize(), msg))
                        status_msg[lid] = [ltype, msg, order]
                    if not is_warning:
                        status_lineno[lid] = lineno
            finally:
                status_lock.release()

        def _erase_status():
            status_lock.acquire()
            try:
                if msg == status_msg.get(lid, [None, None, 0])[1]:
                    view.erase_status(lid)
                    status_msg[lid][1] = None
                    if lid in status_lineno:
                        del status_lineno[lid]
            finally:
                status_lock.release()

        if msg:
            sublime.set_timeout(_set_status, delay or 0)
            sublime.set_timeout(_erase_status, timeout)
        else:
            sublime.set_timeout(_erase_status, delay or 0)

    def set_tooltip(self, calltips, codeintel_tooltips='popup'):
        """
            codeintel_tooltips can be either:
                popup
                status
                panel
        """
        view = self.view
        if not view:
            return
        vid = view.id()
        buf = self.buf_from_view(view)
        if vid != buf.vid:
            return

        snippets = []
        padding = '   '
        for calltip in calltips:
            tip_info = calltip.split('\n')
            defn = tip_info[0]
            text = ' '.join(tip_info[1:])
            snippet = None

            # TODO: This snippets are based and work for Python language.
            # Other languages might need different treatment.
            # Insert parameters as snippet:
            m = re.search(r'([^\s]+)\(([^\[\(\)]*)', defn)
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
            if m:
                params = [p.strip() for p in m.group(2).split(',')]
                if params:
                    n = 1
                    snippet = []
                    for i, p in enumerate(params):
                        if p and i >= arguments:
                            var, _, _ = p.partition('=')
                            var = var.strip()
                            if ' ' in var:
                                var = var.split(' ')[1]
                            if var[0] == '$':
                                var = var[1:]
                            snippet.append('${%s:%s}' % (n, var))
                            n += 1
                    snippet = ', '.join(snippet)
                    if arguments and snippet:
                        snippet = initial_separator + snippet
                text += ' - ' + defn  # Add function to the end
            else:
                text = defn + ' ' + text  # No function match, just add the first line

            # Wrap lines that are too long:
            min_line_length = 80
            max_line_length = 100
            measured_tips = []
            for i, tip in enumerate(tip_info):
                if i == 0:
                    measured_tips.append(tip + ' ' * max(0, min_line_length - len(tip)))
                elif len(tip) > max_line_length:
                    chunks = len(tip)
                    for j in range(0, chunks, max_line_length):
                        measured_tips.append(tip[j:j + max_line_length] + padding)
                else:
                    measured_tips.append(tip + padding)

            # Insert tooltip snippet
            snippets.extend(((padding if i > 0 else '') + l, snippet or '${0}') for i, l in enumerate(measured_tips))

        if codeintel_tooltips == 'popup':
            buf.cplns = snippets
            view.run_command('auto_complete', {
                'disable_auto_insert': True,
                'api_completions_only': True,
                'next_completion_if_showing': False,
                'auto_complete_commit_on_tab': True,
            })

        elif codeintel_tooltips in ('status', 'panel'):
            if codeintel_tooltips == 'status':
                self.set_status('tip', text, timeout=15000)
            else:
                window = view.window()
                output_panel = window.get_output_panel('tooltips')
                output_panel.set_read_only(False)
                text = '\n'.join(list(zip(*snippets))[0])
                output_panel.run_command('tooltip_output', {'output': text})
                output_panel.set_read_only(True)
                window.run_command('show_panel', {'panel': 'output.tooltips'})
                sublime.set_timeout(lambda: window.run_command('hide_panel', {'panel': 'output.tooltips'}), 15000)

            if snippets:
                # Insert function call snippets:
                # func = m.group(1)
                # scope = view.scope_name(pos)
                # view.run_command('new_snippet', {'contents': snippets[0][0], 'tab_trigger': func, 'scope': scope})  # FIXME: Doesn't add the new snippet... is it possible to do so?
                def _insert_snippet():
                    # Check to see we are still at a position where the snippet is wanted:
                    view_sel = view.sel()
                    if not view_sel:
                        return
                    sel = view_sel[0]
                    pos = sel.end()
                    if not pos or pos != buf.original_pos:
                        return
                    view.run_command('insert_snippet', {'contents': snippets[0][0]})
                sublime.set_timeout(_insert_snippet, 500)  # Delay snippet insertion a bit... it's annoying some times

    def pos2bytes(self, content, pos):
        return len(content[:pos].encode('utf-8'))

    def guess_language(self, view, path):
        lang = os.path.splitext(os.path.basename(view.settings().get('syntax')))[0]
        lang = codeintel_syntax_map.get(lang, lang)
        return lang

    def buf_from_view(self, view):
        if not ci.mgr or not view:
            return None

        view_sel = view.sel()
        if not view_sel:
            return None

        file_name = view.file_name()
        path = file_name if file_name else "<Unsaved>"

        lang = self.guess_language(view, path)
        if not lang or lang not in ci.mgr.languages:
            return None

        logger.debug("buf_from_view: %r, %r, %r", view, path, lang)

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

        return buf

    def format_completions_by_language(self, cplns, language, text_in_current_line, type):
        function = None if 'import ' in text_in_current_line else 'function'
        if language == 'PHP':
            if type != 'php-complete-object-members':
                return [('%s〔%s〕' % (('$' if t == 'variable' else '') + n, t), (('$' if t == 'variable' else '') + n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]
            else:
                return [('%s〔%s〕' % (n, t), (n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]
        else:
            return [('%s〔%s〕' % (n, t), (n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]

    # Handlers follow

    def on_document_scanned(self, buf):
        """Handler callback for scan_document"""
        print('on_document_scanned')

    def on_get_calltip_range(self, buf, start, end):
        print('on_get_calltip_range', start, end)

    def on_trg_from_pos(self, buf, context, trg):
        if context == 'trg_from_pos':
            buf.async_eval_at_trg(self, trg)
        elif context == 'defn_trg_from_pos':
            buf.async_eval_at_trg(self, trg)

    def set_status_message(self, buf, message, highlight=None):
        print('set_status_message', message)
        self.set_status(message)

    def set_call_tip_info(self, buf, calltip, explicit, trg):
        print('set_call_tip_info', calltip, explicit)
        self.set_tooltip([calltip])

    def set_auto_complete_info(self, buf, cplns, trg):
        def _set_auto_complete_info():
            view = self.view
            if not view:
                return
            vid = view.id()
            if vid != buf.vid:
                return
            buf.cplns = self.format_completions_by_language(cplns, buf.lang, buf.text_in_current_line, trg.get('type'))
            view.run_command('auto_complete', {
                'disable_auto_insert': True,
                'api_completions_only': True,
                'next_completion_if_showing': False,
                'auto_complete_commit_on_tab': True,
            })
        sublime.set_timeout(_set_auto_complete_info, 0)

    def set_definitions_info(self, buf, defns, trg):
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
        if wid not in jump_history_by_window:
            jump_history_by_window[wid] = collections.deque([], HISTORY_SIZE)
        jump_history = jump_history_by_window[wid]

        # Save current position so we can return to it
        row, col = view.rowcol(view_sel[0].begin())
        current_location = "%s:%d:%d" % (file_name, row + 1, col + 1)
        jump_history.append(current_location)

        window.open_file(jump_location, sublime.ENCODED_POSITION)
        window.open_file(jump_location, sublime.ENCODED_POSITION)


class SublimeCodeIntel(CodeIntelHandler, sublime_plugin.EventListener):
    def on_activated(self, view):
        print('on_activated')

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

        sel = view_sel[0]
        pos = sel.end()
        current_char = view.substr(sublime.Region(pos - 1, pos))

        if not current_char or current_char == '\n':
            return

        # print('on_modified', "'%s'" % current_char, view.command_history(1), view.command_history(0), view.command_history(-1))
        if (not hasattr(view, 'command_history') or view.command_history(1)[1] is None and (
                view.command_history(0)[0] == 'insert_completion' or
                view.command_history(0)[0] in 'insert' and (
                    view.command_history(0)[1]['characters'][-1] != '\n'
                ) or
                view.command_history(-1)[0] in ('insert', 'paste') and (
                    view.command_history(0)[0] == 'commit_completion' or
                    view.command_history(0)[0] == 'insert_snippet' and view.command_history(0)[1]['contents'] == '($0)'
                )
        )):
            buf = self.buf_from_view(view)
            if view.command_history(0)[0] == 'commit_completion':
                pass
            else:
                if buf:
                    is_stop_char = current_char in buf.cpln_stop_chars

                    # Stop characters hide autocomplete window
                    if is_stop_char:
                        view.run_command('hide_auto_complete')

                    buf.scan_document(self, True)
                    buf.trg_from_pos(self, True)

    def on_selection_modified(self, view):
        pass

    def on_query_completions(self, view, prefix, locations):
        buf = self.buf_from_view(view)
        if not buf:
            return
        cplns, buf.cplns = getattr(buf, 'cplns', None), None
        return cplns


class GotoPythonDefinition(CodeIntelHandler, sublime_plugin.TextCommand):
    def run(self, edit, block=False):
        view = self.view

        buf = self.buf_from_view(view)
        if buf:
            buf.scan_document(self, True)
            buf.defn_trg_from_pos(self)


class BackToPythonDefinition(sublime_plugin.TextCommand):
    def run(self, edit, block=False):

        window = sublime.active_window()
        wid = window.id()
        if wid in jump_history_by_window:
            jump_history = jump_history_by_window[wid]

            if len(jump_history) > 0:
                previous_location = jump_history.pop()
                window = sublime.active_window()
                window.open_file(previous_location, sublime.ENCODED_POSITION)


ci = CodeIntel()
ci.activate()
