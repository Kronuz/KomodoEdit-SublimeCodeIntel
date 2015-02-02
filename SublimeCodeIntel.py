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
# The Original Code is SublimeCodeIntel code.
#
# The Initial Developer of the Original Code is German M. Bravo (Kronuz).
# Portions created by German M. Bravo (Kronuz) are Copyright (C) 2011
# German M. Bravo (Kronuz). All Rights Reserved.
#
# Contributor(s):
#   German M. Bravo (Kronuz)
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

import logging

import sublime
import sublime_plugin

from codeintel import CodeIntel, CodeIntelBuffer

logger_name = 'sublimecodeintel'
logger = logging.getLogger(logger_name)

handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
logger.handlers = [handler]
logger.setLevel(logging.DEBUG)  # INFO


def pos2bytes(content, pos):
    return len(content[:pos].encode('utf-8'))


def format_completions_by_language(cplns, language, text_in_current_line, type):
    print('>>>cplns', repr(cplns))
    function = None if 'import ' in text_in_current_line else 'function'
    if language == 'PHP':
        if type != 'php-complete-object-members':
            return [('%s〔%s〕' % (('$' if t == 'variable' else '') + n, t), (('$' if t == 'variable' else '') + n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]
        else:
            return [('%s〔%s〕' % (n, t), (n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]
    else:
        return [('%s〔%s〕' % (n, t), (n).replace("$", "\\$") + ('($0)' if t == function else '')) for t, n in cplns]


class SublimeCodeIntelHandler(sublime_plugin.EventListener):
    def __init__(self):
        self.log = logging.getLogger(logger_name + '.' + self.__class__.__name__)

    def on_activated(self, view):
        print('on_activated')

    def guess_language(self, view):
        lang = os.path.splitext(os.path.basename(view.settings().get('syntax')))[0]
        return lang

    def buf_from_view(self, view):
        if not ci.mgr or not view:
            return None

        lang = self.guess_language(view)
        if lang not in ci.mgr.languages:
            return None

        file_name = view.file_name()
        path = file_name if file_name else "<Unsaved>"

        self.log.debug("buf_from_view: %r, %r, %r", view, path, lang)

        vid = view.id()
        try:
            buf = ci.buffers[vid]
            buf.vid = vid
        except KeyError:
            self.log.debug("creating new %s document %s", lang, path)
            buf = CodeIntelBuffer(ci, vid=vid)
            ci.buffers[vid] = buf

        view_sel = view.sel()
        if view_sel:
            sel = view_sel[0]
            pos = sel.end()
            lpos = view.line(sel).begin()

            text_in_current_line = view.substr(sublime.Region(lpos, pos + 1))
            text = view.substr(sublime.Region(0, view.size()))

            # Get encoded content and current position
            pos = pos2bytes(text, pos)

            buf.lang = lang
            buf.path = path
            buf.text = text
            buf.text_in_current_line = text_in_current_line
            buf.pos = pos

        return buf

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

        # print('on_modified', view.command_history(1), view.command_history(0), view.command_history(-1))
        if (not hasattr(view, 'command_history') or view.command_history(1)[1] is None and (
                view.command_history(0)[0] == 'insert' and (
                    view.command_history(0)[1]['characters'][-1] != '\n'
                ) or
                view.command_history(-1)[0] in ('insert', 'paste') and (
                    view.command_history(0)[0] == 'commit_completion' or
                    view.command_history(0)[0] == 'insert_snippet' and view.command_history(0)[1]['contents'] == '($0)'
                )
        )):
            if view.command_history(0)[0] == 'commit_completion':
                pass
            else:
                buf = self.buf_from_view(view)
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
        if buf and buf.cplns:
            completions = format_completions_by_language(buf.cplns, buf.lang, buf.text_in_current_line, buf.trg.get('type'))
            buf.cplns = None
            return completions

    # Handlers follow
    def on_document_scanned(self, buf):
        """Handler callback for scan_document"""
        print('on_document_scanned')

    def on_get_calltip_range(self, buf, start, end):
        """Handler callback for scan_document"""
        raise NotImplemented

    def on_trg_from_pos(self, buf, context, trg):
        if context == 'trg_from_pos':
            buf.async_eval_at_trg(self, trg)

    def set_status_message(self, buf, message, highlight=None):
        print('set_status_message', message)
        self.log.info(message)

    def set_auto_complete_info(self, buf, cplns, trg):
        def _set_auto_complete_info():
            window = sublime.active_window()
            if not window:
                return
            view = window.active_view()
            if not view:
                return
            vid = view.id()
            if vid != buf.vid:
                return
            buf.trg = trg
            buf.cplns = cplns
            view.run_command('auto_complete', {
                'disable_auto_insert': True,
                'api_completions_only': True,
                'next_completion_if_showing': False,
                'auto_complete_commit_on_tab': True,
            })
        sublime.set_timeout(_set_auto_complete_info, 0)

    def set_call_tip_info(self, calltip, explicit):
        raise NotImplemented

    def set_definitions_info(self, defns):
        raise NotImplemented

ci = CodeIntel()
ci.activate()
