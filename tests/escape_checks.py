# -*- coding: utf-8 -*-
"""Esc 退出回归检查：无钩子、正常屏蔽、空闲，以及 Tk 兜底绑定。"""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='escapecheck_')
loader = importlib.machinery.SourceFileLoader('escape_app', str(REPO/'banner.pyw'))
spec = importlib.util.spec_from_loader('escape_app', loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)


class EscapeChecks(unittest.TestCase):
    def tearDown(self):
        app.banner_windows.clear()
        app.keyboard_blocked = False

    def test_escape_command_without_keyboard_hook(self):
        app.banner_windows.append(object())
        app.keyboard_blocked = False
        with patch.object(app, 'hide_banner') as hide:
            app._handle_command(('esc',))
        hide.assert_called_once()

    def test_escape_command_while_keyboard_blocked(self):
        app.banner_windows.append(object())
        app.keyboard_blocked = True
        with patch.object(app, 'hide_banner') as hide:
            app._handle_command(('esc',))
        hide.assert_called_once()

    def test_local_escape_closes_banner(self):
        app.banner_windows.append(object())
        with patch.object(app, 'hide_banner') as hide:
            self.assertEqual(app._on_banner_escape(), 'break')
        hide.assert_called_once()

    def test_idle_escape_does_not_interfere_with_editor(self):
        with patch.object(app, 'hide_banner') as hide:
            self.assertIsNone(app._on_banner_escape())
            app._handle_command(('esc',))
        hide.assert_not_called()

    def test_local_binding_is_installed_once(self):
        class Root:
            def __init__(self):
                self.bindings = []
            def bind_all(self, *args, **kwargs):
                self.bindings.append((args, kwargs))
        root = Root()
        app._install_escape_fallback(root)
        app._install_escape_fallback(root)
        self.assertEqual(len(root.bindings), 1)
        self.assertEqual(root.bindings[0][0], ('<Escape>', app._on_banner_escape))
        self.assertEqual(root.bindings[0][1], {'add': '+'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
