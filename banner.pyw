# -*- coding: utf-8 -*-
"""
全屏提示横幅
============
- 托盘图标左键单击：显示 / 关闭横幅
- 托盘图标右键菜单：显示/关闭、退出
- 横幅显示时：屏蔽键盘（除 Esc 外），按 Esc 关闭（不屏蔽鼠标）
- 每个显示器各显示一条横幅：左右贴屏幕边缘，上下按图片比例缩放并垂直居中
- 横幅上下未填满处透明（窗口大小恰为图片大小），露出桌面

依赖：Pillow（tkinter、ctypes 为标准库）
运行：双击 banner.pyw（无控制台窗口）
"""

import ctypes
import ctypes.wintypes as wt
import os
import queue
import sys
import threading
import winreg

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageTk

# ===================== 可配置项 =====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_PATH = os.path.join(SCRIPT_DIR, "banner.png")   # 横幅图片，换图就替换这个文件
TRAY_ICO = os.path.join(SCRIPT_DIR, "tray.ico")       # 托盘图标（首次自动生成）
LOG_PATH = os.path.join(SCRIPT_DIR, "banner.log")     # 运行日志（无控制台，便于排查）


def log(msg):
    """无控制台运行：信息写入 banner.log，避免 pythonw 下 print 报错。"""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

# ===================== 全局状态 =====================
commands = queue.Queue()       # 线程间命令：("toggle",) / ("esc",) / ("exit",)
keyboard_blocked = False       # 当前是否屏蔽键盘
banner_windows = []            # 横幅窗口引用
banner_images = []             # PhotoImage 引用（防垃圾回收）

# ===================== Win32 常量 =====================
# 托盘
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_NULL = 0x0000
WM_TRAYICON = WM_USER + 20
NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
HWND_MESSAGE = -3
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
IDM_TOGGLE = 1001
IDM_EXIT = 1002
IDM_STARTUP = 1003
NIM_MODIFY = 0x00000001
NIM_SETVERSION = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NOTIFYICON_VERSION = 3   # 必须是 3：设成 4(NOTIFYICON_VERSION_4) 会改变回调消息格式，导致点击无效

# 键盘钩子
WH_KEYBOARD_LL = 13
VK_ESCAPE = 0x1B
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

# ===================== Win32 结构与函数签名 =====================
WPARAM_T = ctypes.c_size_t
LPARAM_T = ctypes.c_ssize_t
LRESULT_T = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", wt.UINT),
        ("wParam", WPARAM_T),
        ("lParam", LPARAM_T),
        ("time", wt.DWORD),
        ("pt", POINT),
    ]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wt.LONG), ("top", wt.LONG),
        ("right", wt.LONG), ("bottom", wt.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wt.DWORD),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD),
        ("scanCode", wt.DWORD),
        ("flags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT_T, wt.HWND, wt.UINT, WPARAM_T, LPARAM_T)
LowLevelKeyboardProc = ctypes.WINFUNCTYPE(LRESULT_T, ctypes.c_int, WPARAM_T, LPARAM_T)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---- 托盘 / 窗口相关 ----
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = ctypes.c_ushort
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = wt.HWND
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.DefWindowProcW.restype = LRESULT_T
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wt.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT_T
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, WPARAM_T, LPARAM_T]
user32.PostMessageW.restype = wt.BOOL

# ---- 托盘图标 ----
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadImageW.restype = wt.HANDLE
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wt.BOOL

# ---- 托盘右键菜单 ----
user32.CreatePopupMenu.restype = wt.HMENU
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, WPARAM_T, wt.LPCWSTR]
user32.AppendMenuW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = [wt.HMENU]
user32.DestroyMenu.restype = wt.BOOL
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL

# ---- 键盘钩子 ----
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc, wt.HMODULE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, WPARAM_T, LPARAM_T]
user32.CallNextHookEx.restype = LRESULT_T
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL

# ---- 模块句柄 / 显示器 ----
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wt.BOOL


# ===================== DPI 感知 =====================
def set_dpi_awareness():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass


# ===================== 显示器枚举 =====================
def get_monitors():
    result = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wt.BOOL, wt.HMONITOR, wt.HDC, ctypes.POINTER(RECT), wt.LPARAM)

    def _cb(hmon, hdc, rect, data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        result.append((r.left, r.top, r.right, r.bottom))
        return True

    proc = MonitorEnumProc(_cb)
    user32.EnumDisplayMonitors.argtypes = [
        wt.HDC, ctypes.POINTER(RECT), MonitorEnumProc, wt.LPARAM]
    user32.EnumDisplayMonitors.restype = wt.BOOL
    user32.EnumDisplayMonitors(None, None, proc, 0)
    return result


# ===================== 横幅显示 / 关闭 =====================
def show_banner():
    global keyboard_blocked
    hide_banner()
    try:
        img = Image.open(IMAGE_PATH)
        iw, ih = img.size
    except Exception as e:
        log("无法读取横幅图片 %s: %s" % (IMAGE_PATH, e))
        return
    for (left, top, right, bottom) in get_monitors():
        mw = right - left
        mh = bottom - top
        bw = max(1, mw)
        bh = max(1, round(mw * ih / iw))
        y = top + (mh - bh) // 2
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.geometry("%dx%d+%d+%d" % (bw, bh, left, y))
        photo = ImageTk.PhotoImage(img.resize((bw, bh), Image.LANCZOS))
        tk.Label(win, image=photo, bd=0, highlightthickness=0).pack(fill="both", expand=True)
        banner_windows.append(win)
        banner_images.append(photo)
    keyboard_blocked = True
    log("横幅已显示（%d 个显示器）" % len(banner_windows))


def hide_banner():
    global keyboard_blocked
    keyboard_blocked = False
    for w in banner_windows:
        try:
            w.destroy()
        except Exception:
            pass
    banner_windows.clear()
    banner_images.clear()
    log("横幅已关闭")


def toggle_banner():
    if banner_windows:
        hide_banner()
    else:
        show_banner()


# ===================== 键盘钩子 =====================
_hook_proc = None


def _keyboard_proc(nCode, wParam, lParam):
    if nCode >= 0 and keyboard_blocked:
        vk = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
        if vk == VK_ESCAPE and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            commands.put(("esc",))
        return 1  # 屏蔽该按键（Esc 已被我们消费，同样屏蔽）
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def _keyboard_thread():
    try:
        _keyboard_thread_body()
    except Exception:
        import traceback
        log("键盘线程异常:\n" + traceback.format_exc())


def _keyboard_thread_body():
    global _hook_proc
    _hook_proc = LowLevelKeyboardProc(_keyboard_proc)
    hhook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _hook_proc,
                                     kernel32.GetModuleHandleW(None), 0)
    if not hhook:
        log("键盘钩子安装失败")
        return
    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    user32.UnhookWindowsHookEx(hhook)


# ===================== 托盘图标 =====================
_tray_wndproc_obj = None
_tray_nid = None
_tray_hicon = None


def _tray_wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_TRAYICON:
        if lparam == WM_LBUTTONUP:
            commands.put(("toggle",))
        elif lparam == WM_RBUTTONUP:
            _show_tray_menu(hwnd)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _show_tray_menu(hwnd):
    hmenu = user32.CreatePopupMenu()
    user32.AppendMenuW(hmenu, MF_STRING, IDM_TOGGLE, "显示/关闭横幅")
    startup_flag = MF_STRING | (MF_CHECKED if is_startup_enabled() else 0)
    user32.AppendMenuW(hmenu, startup_flag, IDM_STARTUP, "开机启动")
    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
    user32.AppendMenuW(hmenu, MF_STRING, IDM_EXIT, "退出")
    user32.SetForegroundWindow(hwnd)
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_NONOTIFY,
                                pt.x, pt.y, 0, hwnd, None)
    user32.DestroyMenu(hmenu)
    user32.PostMessageW(hwnd, WM_NULL, 0, 0)
    if cmd == IDM_TOGGLE:
        commands.put(("toggle",))
    elif cmd == IDM_STARTUP:
        _toggle_startup()
    elif cmd == IDM_EXIT:
        commands.put(("exit",))


def _tray_thread():
    try:
        _tray_thread_body()
    except Exception:
        import traceback
        log("托盘线程异常:\n" + traceback.format_exc())


def _tray_thread_body():
    global _tray_wndproc_obj, _tray_nid, _tray_hicon
    hinst = kernel32.GetModuleHandleW(None)

    _tray_wndproc_obj = WNDPROC(_tray_wndproc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = _tray_wndproc_obj
    wc.hInstance = hinst
    wc.lpszClassName = "BannerTrayWndClass"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        log("托盘窗口类注册失败")
        return

    hwnd = user32.CreateWindowExW(0, "BannerTrayWndClass", "", 0, 0, 0, 0, 0,
                                  ctypes.c_void_p(HWND_MESSAGE), None, hinst, None)
    if not hwnd:
        log("托盘窗口创建失败")
        return

    _tray_hicon = user32.LoadImageW(None, TRAY_ICO, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)

    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_TRAYICON
    nid.hIcon = _tray_hicon
    nid.szTip = "全屏提示"
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
    _tray_nid = nid
    # 设置版本并弹出启动气泡，提示用户程序已运行
    nid.uTimeoutOrVersion = NOTIFYICON_VERSION
    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
    nid.uFlags = NIF_INFO
    nid.szInfoTitle = "全屏提示"
    nid.szInfo = "已启动：点击此图标显示/关闭横幅，Esc 关闭"
    nid.dwInfoFlags = NIIF_INFO
    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    msg = MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _remove_tray_icon():
    if _tray_nid is not None:
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(_tray_nid))


# ===================== 开机启动 =====================
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "全屏提示"


def _startup_command():
    exe = sys.executable
    # 确保用 pythonw.exe（无控制台窗口）
    if exe.lower().endswith("python.exe"):
        exe = exe[:-len("python.exe")] + "pythonw.exe"
    return '"%s" "%s"' % (exe, os.path.abspath(__file__))


def is_startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        return True
    except OSError:
        return False


def set_startup(enabled):
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, _startup_command())
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE)
        except OSError:
            pass


def _toggle_startup():
    enabled = not is_startup_enabled()
    set_startup(enabled)
    log("开机启动已%s" % ("开启" if enabled else "关闭"))


# ===================== 占位资源生成 =====================
def _find_cjk_font():
    for p in (r"C:\Windows\Fonts\msyh.ttc",
              r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf",
              r"C:\Windows\Fonts\simsun.ttc"):
        if os.path.exists(p):
            return p
    return None


def ensure_placeholder():
    if os.path.exists(IMAGE_PATH):
        return
    W, H = 1920, 480
    img = Image.new("RGB", (W, H), (185, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, W - 9, H - 9], outline=(255, 255, 255), width=10)
    text = "请勿触动电脑"
    try:
        font = ImageFont.truetype(_find_cjk_font(), 140) if _find_cjk_font() else None
    except Exception:
        font = None
    if font is None:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (W - tw) // 2 - bbox[0]
    y = (H - th) // 2 - bbox[1]
    d.text((x, y), text, font=font, fill=(255, 255, 255))
    img.save(IMAGE_PATH)
    log("已生成占位横幅: %s" % IMAGE_PATH)


def _draw_padlock(d, S):
    """在画布上画一个金色锁头（以 64 为基准等比缩放）。"""
    gold = (255, 193, 7, 255)
    dark = (90, 65, 0, 255)
    k = S / 64.0
    s = lambda v: v * k
    # 锁梁（U 形，先画，让锁体盖住两端）
    d.arc([s(16), s(8), s(48), s(40)], 180, 360, fill=gold, width=max(2, int(round(s(8)))))
    # 锁体
    d.rounded_rectangle([s(10), s(26), s(54), s(58)], radius=int(round(s(12))), fill=gold)
    # 钥匙孔
    d.ellipse([s(27), s(34), s(37), s(44)], fill=dark)
    d.rectangle([s(30), s(41), s(34), s(52)], fill=dark)


def ensure_tray_icon():
    if os.path.exists(TRAY_ICO):
        return
    S = 64
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    _draw_padlock(d, S)
    img.save(TRAY_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    log("已生成托盘图标: %s" % TRAY_ICO)


# ===================== 主循环 =====================
root = None


def _exit():
    hide_banner()
    _remove_tray_icon()
    try:
        root.destroy()
    except Exception:
        pass
    sys.exit(0)


def _poll():
    try:
        while True:
            cmd = commands.get_nowait()
            if cmd[0] == "toggle":
                toggle_banner()
            elif cmd[0] == "esc":
                if keyboard_blocked:
                    hide_banner()
            elif cmd[0] == "exit":
                _exit()
                return
    except queue.Empty:
        pass
    root.after(50, _poll)


def main():
    global root
    set_dpi_awareness()
    ensure_placeholder()
    ensure_tray_icon()
    threading.Thread(target=_keyboard_thread, daemon=True).start()
    threading.Thread(target=_tray_thread, daemon=True).start()
    root = tk.Tk()
    root.withdraw()
    log("程序已启动")
    root.after(50, _poll)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        log("未捕获异常:\n" + traceback.format_exc())
