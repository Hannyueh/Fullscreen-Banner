"""横幅密码的本地存储、键盘字符翻译与限时匹配。"""

import base64
import ctypes
from ctypes import wintypes as wt
import hmac
import threading
import time

DEFAULT_TIMEOUT = 3
MAX_TIMEOUT = 120
MAX_PASSWORD = 64


def valid_password(text):
    return 1 <= len(text) <= MAX_PASSWORD and all(" " <= char <= "~" for char in text)


def parse_timeout(value):
    try:
        seconds = int(str(value).strip())
        if 1 <= seconds <= MAX_TIMEOUT:
            return seconds
    except ValueError:
        pass
    raise ValueError("清空时间请输入 1–120 秒的整数。")


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("data", ctypes.c_void_p)]


_crypt = ctypes.WinDLL("crypt32", use_last_error=True)
_free = ctypes.WinDLL("kernel32").LocalFree
_free.argtypes = [ctypes.c_void_p]
_free.restype = ctypes.c_void_p
for _function in (_crypt.CryptProtectData, _crypt.CryptUnprotectData):
    _function.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(_DataBlob)]
    _function.restype = wt.BOOL


def _protect(data, operation):
    source = ctypes.create_string_buffer(data)
    incoming = _DataBlob(len(data), ctypes.addressof(source))
    result = _DataBlob()
    if not operation(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        _free(result.data)


def protect_password(text):
    return base64.b64encode(_protect(text.encode("ascii"), _crypt.CryptProtectData)).decode("ascii")


def read_password(blob):
    try:
        text = _protect(base64.b64decode(blob, validate=True), _crypt.CryptUnprotectData).decode("ascii")
        if valid_password(text):
            return text
    except (OSError, ValueError, TypeError):
        pass
    raise ValueError("无法读取已保存的密码，请重新设置。")


_user = ctypes.WinDLL("user32")
_user.GetForegroundWindow.restype = wt.HWND
_user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_user.GetWindowThreadProcessId.restype = wt.DWORD
_user.GetKeyboardLayout.argtypes = [wt.DWORD]
_user.GetKeyboardLayout.restype = wt.HANDLE
_user.GetKeyState.argtypes = [ctypes.c_int]
_user.GetKeyState.restype = ctypes.c_short
_user.ToUnicodeEx.argtypes = [wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_ubyte),
                             wt.LPWSTR, ctypes.c_int, wt.UINT, wt.HANDLE]
_user.ToUnicodeEx.restype = ctypes.c_int
_MODIFIERS = {0x10: 0x10, 0x11: 0x11, 0x12: 0x12, 0xA0: 0x10, 0xA1: 0x10,
              0xA2: 0x11, 0xA3: 0x11, 0xA4: 0x12, 0xA5: 0x12}


class UnlockSession:
    def __init__(self, password, seconds):
        self._password = password
        self._seconds = seconds
        self._lock = threading.Lock()
        self._buffer = ""
        self._deadline = None
        self._matched = False
        self._pressed = set()
        self._caps = bool(_user.GetKeyState(0x14) & 1)
        foreground = _user.GetForegroundWindow()
        thread_id = _user.GetWindowThreadProcessId(foreground, None) if foreground else 0
        self._layout = _user.GetKeyboardLayout(thread_id)
        self._key_state = (ctypes.c_ubyte * 256)()
        self._characters = ctypes.create_unicode_buffer(8)

    def _expire(self, now):
        if self._deadline is not None and now >= self._deadline:
            self._buffer = ""
            self._deadline = None

    def expire(self, now):
        with self._lock:
            self._expire(now)

    def feed(self, text, now):
        """返回一次匹配结果，候选字符串最多保留密码长度。"""
        with self._lock:
            self._expire(now)
            if self._matched:
                return False
            if text == "\x1b":
                self._buffer = ""
                self._deadline = None
                return False
            if text == "\b":
                self._buffer = self._buffer[:-1]
                if not self._buffer:
                    self._deadline = None
                return False
            if not valid_password(text):
                return False
            if self._deadline is None:
                self._deadline = now + self._seconds
            for char in text:
                self._buffer = (self._buffer + char)[-len(self._password):]
                if hmac.compare_digest(self._buffer, self._password):
                    self._buffer = ""
                    self._deadline = None
                    self._matched = True
                    return True
            return False

    def key(self, vk, scan, down):
        if not down:
            self._pressed.discard(vk)
            return False
        if vk >= 256 or vk in self._pressed:
            return False
        self._pressed.add(vk)
        if vk == 0x14:
            self._caps = not self._caps
            return False
        if vk in _MODIFIERS or vk in (0x5B, 0x5C):
            return False
        mods = {_MODIFIERS[k] for k in self._pressed if k in _MODIFIERS}
        if self._pressed.intersection((0x5B, 0x5C)) or (
                mods.intersection((0x11, 0x12)) and 0xA5 not in self._pressed):
            return False
        if vk in (0x08, 0x1B):
            return self.feed("\b" if vk == 0x08 else "\x1b", time.monotonic())
        ctypes.memset(self._key_state, 0, 256)
        for key in self._pressed | mods:
            self._key_state[key] = 0x80
        self._key_state[0x14] |= int(self._caps)
        # Windows 10 1607+ 的 bit 2 禁止修改键盘布局内部状态。
        count = _user.ToUnicodeEx(vk, scan, self._key_state, self._characters,
                                  len(self._characters), 4, self._layout)
        if count > 0:
            return self.feed(self._characters[:count], time.monotonic())
        return False
