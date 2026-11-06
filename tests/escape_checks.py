# -*- coding: utf-8 -*-
"""Esc、密码解锁与键盘钩子的隔离回归检查。"""
import ctypes
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path
import tempfile
import queue
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='escapecheck_')
loader = importlib.machinery.SourceFileLoader('escape_app', str(REPO/'banner.pyw'))
spec = importlib.util.spec_from_loader('escape_app', loader)
app = importlib.util.module_from_spec(spec)
loader.exec_module(app)
import password_unlock


class EscapeChecks(unittest.TestCase):
    def tearDown(self):
        app.banner_windows.clear()
        app.keyboard_blocked = False
        app._password_session = None
        app._unlock_release_vk = None

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

    def test_password_timeout_from_first_character(self):
        session = app.UnlockSession("123", 2)
        self.assertFalse(session.feed("1", 10))
        self.assertFalse(session.feed("2", 11))
        session.expire(12)
        self.assertFalse(session.feed("3", 12))
        self.assertTrue(session.feed("123", 12.5))
        self.assertFalse(session.feed("123", 13))

    def test_password_backspace_and_escape(self):
        session = app.UnlockSession("123", 5)
        self.assertFalse(session.feed("12", 0))
        self.assertFalse(session.feed("\x1b", 1))
        self.assertFalse(session.feed("3", 2))
        self.assertFalse(session.feed("1x", 3))
        self.assertFalse(session.feed("\b", 4))
        self.assertTrue(session.feed("23", 5))

    def test_password_options_encrypt_and_keep_existing_password(self):
        with patch.object(app, 'CFG', dict(app.DEFAULT_CONFIG)), tempfile.TemporaryDirectory() as directory:
            with patch.object(app, 'CONFIG_PATH', os.path.join(directory, 'banner.json')):
                with self.assertRaises(ValueError):
                    app.set_unlock_options(True, '', 5)
                with self.assertRaises(ValueError):
                    app.set_unlock_options(True, 'example-PIN', 0)
                app.set_unlock_options(True, 'example-PIN', 7)
                blob = app.CFG['unlock_password_blob']
                self.assertEqual(app.read_password(blob), 'example-PIN')
                self.assertNotIn('example-PIN', Path(app.CONFIG_PATH).read_text(encoding='utf-8'))
                app.set_unlock_options(True, '', 8)
                self.assertEqual(app.CFG['unlock_password_blob'], blob)
                self.assertEqual(app.CFG['unlock_password_timeout'], 8)

    def test_password_blocks_application_exit_and_stale_match(self):
        app.banner_windows.append(object())
        app._password_session = app.UnlockSession("123", 5)
        stale_session = app.UnlockSession("123", 5)
        with patch.object(app, 'hide_banner') as hide, patch.object(app, 'shutdown') as shutdown:
            self.assertEqual(app._on_banner_escape(), 'break')
            self.assertFalse(app.toggle_banner())
            app._handle_command(('esc',))
            app._handle_command(('exit',))
            app._handle_command(('force_unlock',))
            app._handle_command(('password_unlock', stale_session))
            hide.assert_not_called()
            shutdown.assert_not_called()
            app._handle_command(('password_unlock', app._password_session))
            hide.assert_called_once()

    def test_hook_matches_key_events_and_consumes_final_key(self):
        app.keyboard_blocked = True
        app.banner_windows.append(object())
        app._password_session = app.UnlockSession("12", 5)

        def translate(vk, scan, state, characters, size, flags, layout):
            characters[0] = chr(vk)
            return 1

        def event(vk, down):
            item = app.KBDLLHOOKSTRUCT()
            item.vkCode = vk
            return app._keyboard_proc(0, app.WM_KEYDOWN if down else app.WM_KEYUP,
                                      ctypes.addressof(item))

        with patch.object(app, 'commands', queue.Queue()), patch.object(
                password_unlock._user, 'ToUnicodeEx', side_effect=translate), patch.object(
                app, '_DEBUG_KEYS', True), patch.object(app, 'log') as log:
            for vk in (0xA2, 0xA4, 0xA0, app.VK_ESCAPE):
                self.assertEqual(event(vk, True), 1)
            for vk in (app.VK_ESCAPE, 0xA0, 0xA4, 0xA2):
                self.assertEqual(event(vk, False), 1)
            self.assertTrue(all(command[0] == 'activity' for command in list(app.commands.queue)))
            for vk, down in ((49, True), (49, True), (49, False), (50, True)):
                self.assertEqual(event(vk, down), 1)
            self.assertEqual(list(app.commands.queue)[-1], ('password_unlock', app._password_session))
            log.assert_not_called()
            app.keyboard_blocked = False
            self.assertEqual(event(50, True), 1)
            self.assertEqual(event(50, False), 1)
            self.assertIsNone(app._unlock_release_vk)


if __name__ == '__main__':
    unittest.main(verbosity=2)
