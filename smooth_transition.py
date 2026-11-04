# -*- coding: utf-8 -*-
"""原生斜切遮罩。遮罩是独立的合成窗口，不与输入框共享重绘区域。"""
import ctypes
from ctypes import wintypes as wt
import time
import tkinter as tk


def ease_cover(t):
    t = max(0.0, min(1.0, t))
    return 4*t*t*t if t < 0.5 else 1-((-2*t+2)**3)/2


def ease_reveal(t):
    t = max(0.0, min(1.0, t))
    return 1-(1-t)**3


def mask_polygon(width, height, progress, reveal=False, direction=1):
    """覆盖与揭开使用相同斜率；两阶段交接处完全覆盖内容区。"""
    p = max(0.0, min(1.0, progress))
    slant = max(1, round(min(width*0.12, height*0.24)))
    if reveal:
        edge = -slant+(width+2*slant)*p
        points = [(edge+slant, 0), (width+slant, 0),
                  (width+slant, height), (edge, height)]
    else:
        edge = -slant+(width+slant)*p
        points = [(-slant, 0), (edge+slant, 0), (edge, height), (-slant, height)]
    if direction < 0:
        points = [(width-x, y) for x, y in points]
    return tuple((round(x), round(y)) for x, y in points)


class DiagonalWipe:
    COVER_SECONDS = 0.12
    REVEAL_SECONDS = 0.16

    def __init__(self, owner, viewport, color, commit, finished):
        self.owner, self.viewport = owner, viewport
        self.color, self.commit, self.finished = color, commit, finished
        self.window = None
        self.active = False
        self.phase = None
        self.job = None
        self.committed = False
        self.direction = 1
        self._apis_ready = False
        self._duration_scale = 1.0
        self.frame_times = []

    def _init_apis(self):
        if self._apis_ready:
            return
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        self.user.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        self.user.GetAncestor.restype = wt.HWND
        self.user.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
        self.user.SetWindowRgn.restype = ctypes.c_int
        self.user.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int, wt.UINT]
        self.user.SetWindowPos.restype = wt.BOOL
        self.user.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.HRGN, wt.UINT]
        self.user.RedrawWindow.restype = wt.BOOL
        self.user.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.gdi.CreatePolygonRgn.argtypes = [ctypes.POINTER(wt.POINT), ctypes.c_int, ctypes.c_int]
        self.gdi.CreatePolygonRgn.restype = wt.HRGN
        self.gdi.DeleteObject.argtypes = [wt.HGDIOBJ]
        self._apis_ready = True

    def _prepare_window(self):
        self._init_apis()
        if self.window is None:
            self.window = tk.Toplevel(self.owner, bg=self.color, takefocus=0)
            self.window.withdraw()
            self.window.overrideredirect(True)
            self.window.transient(self.owner)
            self.window.title('全屏提示 · 转场层')
            self.window.update_idletasks()
            self.hwnd = self.user.GetAncestor(self.window.winfo_id(), 2)
            style = self.user.GetWindowLongPtrW(self.hwnd, -20)
            self.user.SetWindowLongPtrW(self.hwnd, -20, style | 0x08000000 | 0x80)
        self.bounds = (self.viewport.winfo_rootx(), self.viewport.winfo_rooty(),
                       self.viewport.winfo_width(), self.viewport.winfo_height())
        x, y, self.width, self.height = self.bounds
        self.window.geometry('%dx%d+%d+%d' % (self.width, self.height, x, y))
        self._shape(0)
        self.window.deiconify()
        if not self.user.SetWindowPos(self.hwnd, None, x, y, self.width, self.height, 0x0250):
            raise OSError('Could not position transition surface')

    def _shape(self, progress, reveal=False):
        coordinates = mask_polygon(self.width, self.height, progress, reveal, self.direction)
        points = (wt.POINT*len(coordinates))(*(wt.POINT(x, y) for x, y in coordinates))
        region = self.gdi.CreatePolygonRgn(points, len(points), 1)
        if not region:
            raise OSError('Could not create transition region')
        if not self.user.SetWindowRgn(self.hwnd, region, True):
            self.gdi.DeleteObject(region)
            raise OSError('Could not set transition region')
        # SetWindowRgn transfers ownership of the region to Windows on success.

    def start(self, direction=1):
        self.cancel()
        self.direction = direction
        self.committed = False
        try:
            self._prepare_window()
            self.active = True
            self.phase = 'cover'
            self.started = time.perf_counter()
            self.frame_times = []
            self._frame()
        except Exception:
            self._hide()
            raise

    def _frame(self):
        self.job = None
        if not self.active:
            return
        if not self.owner.winfo_viewable() or self.bounds != (
                self.viewport.winfo_rootx(), self.viewport.winfo_rooty(),
                self.viewport.winfo_width(), self.viewport.winfo_height()):
            self.complete()
            return
        now = time.perf_counter()
        self.frame_times.append(now)
        try:
            duration = self.COVER_SECONDS if self.phase == 'cover' else self.REVEAL_SECONDS
            t = min(1.0, (now-self.started)/(duration*self._duration_scale))
            self._shape(ease_cover(t) if self.phase == 'cover' else ease_reveal(t),
                        reveal=self.phase == 'reveal')
            if t >= 1 and self.phase == 'cover':
                # The entire viewport is orange here; commit and synchronously
                # redraw every child before revealing any part of the next page.
                page_hwnd = self.commit()
                self.committed = True
                self.owner.update_idletasks()
                owner_hwnd = self.user.GetAncestor(self.owner.winfo_id(), 2)
                self.user.RedrawWindow(page_hwnd or owner_hwnd, None, None, 0x0185)
                try:
                    ctypes.windll.dwmapi.DwmFlush()
                except (AttributeError, OSError):
                    pass
                self.phase = 'reveal'
                self.started = time.perf_counter()
                self.job = self.owner._later(8, self._frame)
            elif t >= 1:
                self._hide()
                self.finished()
            else:
                self.job = self.owner._later(8, self._frame)
        except Exception:
            self.owner.app.log('转场失败，立即完成页面切换')
            self.complete()

    def _hide(self):
        self.owner._cancel(self.job)
        self.job = None
        if self.window is not None:
            self.window.withdraw()
        self.active = False
        self.phase = None

    def complete(self):
        self._hide()
        if not self.committed:
            self.commit()
            self.committed = True
        self.finished()

    def cancel(self):
        self._hide()

    def destroy(self):
        self.cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None
