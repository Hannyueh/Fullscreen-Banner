# -*- coding: utf-8 -*-
"""控制中心 GUI（CustomTkinter）。

由 banner.pyw 托盘菜单「打开控制中心…」在主线程打开：

    import gui
    gui.open_control_center(root, banner模块)

设计约束：
- 只允许主线程操作 Tk 控件；耗时的预览渲染放在后台线程，结果经
  queue 交回主线程处理（见 ControlCenter._preview_loop / _drain_preview）。
- 所有设置改动都通过 banner 模块的 _apply_settings() 热应用并持久化，
  GUI 自己读写 banner.CFG 只用于展示。
- 关闭窗口只是 withdraw 隐藏到托盘，程序继续运行；退出程序走底部按钮。

对外接口：
- open_control_center(master, app) -> ControlCenter | None
- close_control_center() -> bool
- is_open() -> bool
- build_preview_image(app, mw, mh, box_w, box_h) -> (PIL.Image, str)
"""

import os
import queue
import threading
import time
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageTk
from smooth_transition import DiagonalWipe

# ---- 外观常量 ----
FONT_FAMILY = "Microsoft YaHei UI"      # theme.json 里的默认字体也是它
THEME_FILE = "theme.json"
# 暖白卡片与橙色操作层；不用图片背景或额外字体资源。
PAPER = "#F1F0EC"
PANEL = "#F8F7F4"
CARD = "#FFFFFF"
BG3 = "#EEECE7"
INK = "#25231F"
INK2 = "#514C44"
MUTED = "#777067"
LINE = "#E5E0D8"
ACCENT = "#FF6B1A"
ACCENT_INK = "#A83800"
ACCENT_HOVER = "#E65A0A"
ON_ACCENT = "#25231F"
SOFT_ORANGE = "#FFF1E5"
ORANGE_LINE = "#E9B88D"
SUCCESS = "#26704D"
SUCCESS_BG = "#E8F4EC"
DANGER = "#B5362C"
COLOR_CANVAS = "#20201F"
PREVIEW_W = 400
PREVIEW_H = 250
FIT_DESCRIPTIONS = {
    "cover": "铺满裁切",
    "contain": "完整显示",
    "fill": "拉伸铺满",
}

_theme_inited = False
_center = None          # 当前唯一控制中心窗口


# ===================== 主题 / 字体 =====================
def _square_window_corners(window):
    """Windows 11 外框也采用直角；旧版 Windows 不支持此属性时忽略。"""
    try:
        import ctypes
        from ctypes import wintypes
        ancestor = ctypes.windll.user32.GetAncestor
        ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        ancestor.restype = wintypes.HWND
        hwnd = ancestor(window.winfo_id(), 2)
        preference = ctypes.c_int(1)  # DWMWCP_DONOTROUND
        setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
        setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        setter.restype = ctypes.c_long
        setter(hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
    except (AttributeError, OSError, tk.TclError):
        pass


def _font(size, weight="normal"):
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def _theme_path(app):
    for base in (getattr(app, "RES_DIR", None), getattr(app, "APP_DIR", None)):
        if not base:
            continue
        path = os.path.join(base, THEME_FILE)
        if os.path.isfile(path):
            return path
    return None


def _ensure_ctk_theme(app):
    """在创建窗口之前设定浅色外观与中文字体主题。"""
    global _theme_inited
    if _theme_inited:
        return
    try:
        ctk.set_appearance_mode("light")      # 控制中心固定浅色，只有横幅有深浅色
    except Exception:
        pass
    path = _theme_path(app)
    try:
        if path:
            ctk.set_default_color_theme(path)
        else:
            ctk.set_default_color_theme("dark-blue")
    except Exception:
        try:
            ctk.set_default_color_theme("dark-blue")
        except Exception:
            pass
    _theme_inited = True


# ===================== 预览渲染（纯函数，可单测） =====================
def _checkerboard(w, h, cell=12):
    """透明区域的棋盘格示意。"""
    from PIL import ImageDraw
    w, h = max(1, int(w)), max(1, int(h))
    out = Image.new("RGB", (w, h), (236, 236, 236))
    d = ImageDraw.Draw(out)
    for y in range(0, h, cell):
        for x in range(0, w, cell):
            if ((x // cell) + (y // cell)) % 2 == 0:
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(205, 205, 205))
    return out


def _fit_preview(im, w, h, fit, contain_fill="black"):
    """把 RGB 图按 cover / contain / fill 适配到 w×h，返回 RGB Image。"""
    w, h = max(1, int(w)), max(1, int(h))
    iw, ih = im.size
    if iw <= 0 or ih <= 0:
        raise ValueError("图片尺寸无效：%r" % (im.size,))
    if fit == "fill":
        return im.resize((w, h), Image.LANCZOS)
    if fit == "contain":
        k = min(w / float(iw), h / float(ih))
        nw, nh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
        out = _checkerboard(w, h) if contain_fill == "transparent" else Image.new("RGB", (w, h), (0, 0, 0))
        out.paste(im.resize((nw, nh), Image.LANCZOS), ((w - nw) // 2, (h - nh) // 2))
        return out
    # cover：等比放大盖住目标区域后居中裁切
    k = max(w / float(iw), h / float(ih))
    nw, nh = max(1, int(round(iw * k))), max(1, int(round(ih * k)))
    big = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return big.crop((left, top, left + w, top + h))


def _text_digest(app):
    """默认横幅文案的短摘要，用于预览缓存失效。"""
    import hashlib
    import json
    try:
        raw = json.dumps(app._banner_text_cfg(), ensure_ascii=False, sort_keys=True)
    except Exception:
        raw = "default"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def _preview_theme(app):
    try:
        return app.effective_theme()
    except Exception:
        return "light"


def _preview_default_image(app, rw, rh):
    """渲染（并缓存）默认横幅的缩略原图（区分主题）。"""
    import make_banner
    theme = _preview_theme(app)
    out = os.path.join(app.CACHE_DIR,
                       "preview_%dx%d_%s_%s.png" % (rw, rh, _text_digest(app), theme))
    if os.path.isfile(out):
        try:
            return Image.open(out).convert("RGB")
        except Exception:
            app.log("预览缓存读取失败，重新渲染：%s" % out)
    tmp = out[:-4] + ".part.png"
    make_banner.render(tmp, rw, rh, text=app._banner_text_cfg(), theme=theme)
    os.replace(tmp, out)
    # 顺手清掉旧文案的预览缓存（最多保留最近几张）
    try:
        leftovers = [p for p in os.listdir(app.CACHE_DIR)
                     if p.startswith("preview_") and p.endswith(".png") and os.path.basename(out) != p]
        leftovers.sort()
        for name in leftovers[:-2]:
            try:
                os.remove(os.path.join(app.CACHE_DIR, name))
            except OSError:
                pass
    except OSError:
        pass
    return Image.open(out).convert("RGB")


def build_preview_image(app, mw, mh, box_w=PREVIEW_W, box_h=PREVIEW_H):
    """生成控制中心预览图。

    返回 (image, info)：
    - image：box 内按屏幕比例 letterbox 后的黑底 RGB 图（尺寸 = box）
    - info：当前横幅来源说明
    """
    mw, mh = max(1, int(mw)), max(1, int(mh))
    bw, bh = max(1, int(box_w)), max(1, int(box_h))
    scale = min(bw / float(mw), bh / float(mh))
    pw = max(1, int(round(mw * scale)))
    ph = max(1, int(round(mh * scale)))

    src = app._user_image_path()
    if src:
        fit = app.BANNER_FIT
        contain_fill = str(app.CFG.get("contain_fill") or "black").lower()
        im = app._open_rgb(src)
        im = _fit_preview(im, pw, ph, fit, contain_fill)
        info = "自定义图片 · %s · %s" % (os.path.basename(src),
                                         app.FIT_LABELS.get(fit, fit))
        if fit == "contain" and contain_fill == "transparent":
            info += " · 透明"
    else:
        rw = min(1440, max(960, pw * 3))
        rh = max(240, int(round(rw * mh / float(mw))))
        theme = _preview_theme(app)
        im = _preview_default_image(app, rw, rh)
        im = _fit_preview(im, pw, ph, "fill")
        info = "默认出图 · %s · %d×%d" % (
            "深色" if theme == "dark" else "浅色", mw, mh)

    # 预览不叠加 OLED 亮度系数（避免浅色预览发灰；实际显示亮度见 LUM）
    canvas = Image.new("RGB", (bw, bh), (0, 0, 0))
    canvas.paste(im, ((bw - pw) // 2, (bh - ph) // 2))
    return canvas, info


def _system_motion_enabled():
    """遵循 Windows 的客户端区域动画（关闭动画时直接切换状态）。"""
    try:
        import ctypes
        enabled = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return True


# ===================== 控制中心窗口 =====================
class MotionButton(ctk.CTkButton):
    """在原生按钮行为上增加短促的按压回弹，不引入动画库。"""

    def __init__(self, master, owner, command=None, **kwargs):
        self._owner = owner
        self._pulse_job = None
        self._action = command
        super().__init__(master, command=self._respond, **kwargs)

    def _respond(self):
        if self._action:
            self._action()
        if not self.winfo_exists() or not self._owner._motion_enabled or not getattr(self, "_press_motion", True):
            return
        self._owner._cancel(self._pulse_job)
        base = self.cget("fg_color")
        if not isinstance(base, str) or not base.startswith("#"):
            return
        flash = "#56514A" if base == INK else "#FFD0AD"
        start = time.monotonic()
        def settle():
            if not self.winfo_exists():
                return
            p = min(1.0, (time.monotonic()-start)/0.22)
            t = 1-(1-p)**3
            rgb = tuple(round(int(flash[i:i+2], 16)*(1-t)+int(base[i:i+2], 16)*t) for i in (1, 3, 5))
            self.configure(fg_color="#%02x%02x%02x" % rgb)
            if p < 1:
                self._pulse_job = self._owner._later(16, settle)
        settle()


class AnimatedToggle(ctk.CTkFrame):
    """用原生矢量画布绘制开关，状态和拇指位置同时变化。"""

    def __init__(self, master, owner, text="", command=None, font=None,
                 text_color=INK, **kwargs):
        for key in ("progress_color", "button_color", "button_hover_color", "fg_color"):
            kwargs.pop(key, None)
        super().__init__(master, fg_color="transparent", **kwargs)
        self.owner, self.command = owner, command
        self.value, self.position, self._job = False, 0.0, None
        self._disabled = False
        parent = master
        background = PAPER
        while parent is not None:
            try:
                color = parent.cget("fg_color")
                if color != "transparent":
                    background = color[0] if isinstance(color, (tuple, list)) else color
                    break
            except (ValueError, tk.TclError):
                pass
            parent = getattr(parent, 'master', None)
        scale = owner._scale
        self._factor = scale
        self.track = tk.Canvas(self, width=round(52*scale), height=round(32*scale),
                               highlightthickness=0, bd=0, bg=background, takefocus=1, cursor="hand2")
        self.track.pack(side="left", padx=(0, 12))
        self.label = ctk.CTkLabel(self, text=text, font=font or _font(13), text_color=text_color)
        self.label.pack(side="left")
        self.track.bind("<Button-1>", self._toggle)
        self.label.bind("<Button-1>", self._toggle)
        self.track.bind("<space>", self._toggle)
        self.track.bind("<Return>", self._toggle)
        self.track.bind("<FocusIn>", lambda e: self._paint_track())
        self.track.bind("<FocusOut>", lambda e: self._paint_track())
        self._paint_track()

    def _paint_track(self):
        s = self._factor
        self.track.delete("all")
        color = ACCENT if self.value else "#C6C0B6"
        if self._disabled:
            color = LINE
        self.track.create_rectangle(2*s, 2*s, 50*s, 30*s, fill=color, outline="")
        x = (16+20*self.position)*s
        self.track.create_rectangle(x-10*s, 6*s, x+10*s, 26*s, fill=INK if self.value else CARD, outline="")
        if self.track.focus_get() is self.track:
            self.track.create_rectangle(s, s, 51*s, 31*s, outline=ACCENT_INK, width=1)

    def _set(self, value):
        self.value = bool(value)
        self.owner._cancel(self._job)
        initial, target = self.position, float(self.value)
        started = time.monotonic()
        def frame():
            if not self.winfo_exists():
                return
            t = min(1.0, (time.monotonic()-started)/0.18) if self.owner._motion_enabled and self.winfo_viewable() else 1
            eased = 1-(1-t)**3
            self.position = initial+(target-initial)*eased
            self._paint_track()
            if t < 1:
                self._job = self.owner._later(16, frame)
        frame()

    def _toggle(self, event=None):
        if not self._disabled:
            self.track.focus_set()
            self._set(not self.value)
            if self.command:
                self.command()
        return "break"

    def get(self):
        return int(self.value)

    def select(self):
        self._set(True)

    def deselect(self):
        self._set(False)

    def configure(self, **kwargs):
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        if "state" in kwargs:
            self._disabled = kwargs.pop("state") == "disabled"
            self._paint_track()
        if kwargs:
            super().configure(**kwargs)


class MotionDeck(ctk.CTkFrame):
    """独立场景导航：开始屏、画面、保护、设置；不固定显示预览侧栏。"""

    LABELS = {"开始": "开始", "横幅": "画面", "息屏": "保护", "更多": "设置"}

    def __init__(self, master, owner):
        super().__init__(master, fg_color=PAPER, corner_radius=0)
        self.owner, self._pages, self._buttons = owner, {}, {}
        self._current, self._job = "", None
        self._requested = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        rail = ctk.CTkFrame(self, width=96, fg_color=INK, corner_radius=0)
        self.rail = rail
        rail.grid(row=0, column=0, sticky="ns")
        rail.grid_propagate(False)
        rail.grid_rowconfigure(2, weight=1)
        with Image.open(owner.app.tray_icon_path()) as icon:
            self.brand_image = ctk.CTkImage(light_image=icon.copy(), dark_image=icon.copy(), size=(56, 56))
        self.brand_label = ctk.CTkLabel(rail, text="", image=self.brand_image, width=56, height=56)
        self.brand_label.grid(row=0, column=0, padx=20, pady=(24, 32))
        self.nav = ctk.CTkFrame(rail, fg_color="transparent")
        self.nav.grid(row=1, column=0, sticky="n")
        self._indicator = tk.Canvas(rail, bg=ACCENT, bd=0, highlightthickness=0)
        self._indicator_y = None
        exit_button = owner._button(rail, "退出", owner._on_exit, width=56)
        exit_button.configure(fg_color=INK, hover_color="#34332F", text_color="#D7D2C8")
        exit_button.grid(row=3, column=0, pady=24)
        surface = ctk.CTkFrame(self, fg_color="transparent")
        surface.grid(row=0, column=1, sticky="nsew", padx=24)
        surface.grid_columnconfigure(0, weight=1)
        surface.grid_rowconfigure(1, weight=1)
        self.header = ctk.CTkFrame(surface, fg_color="transparent")
        self.header.grid(row=0, column=0, sticky="ew", pady=(24, 24))
        self.viewport = ctk.CTkFrame(surface, fg_color="transparent", corner_radius=0)
        self.viewport.grid(row=1, column=0, sticky="nsew")
        self._wipe = DiagonalWipe(owner, self.viewport, ACCENT,
                                  self._commit_requested, self._wipe_finished)
        self.footer = ctk.CTkFrame(surface, fg_color="transparent")
        self.footer.grid(row=2, column=0, sticky="ew", pady=16)

    def add(self, name):
        page = ctk.CTkFrame(self.viewport, fg_color="transparent", corner_radius=0)
        self._pages[name] = page
        button = self.owner._button(self.nav, self.LABELS[name], lambda: self.set(name), width=64)
        button._press_motion = False
        button.configure(height=56, font=_font(16, "bold"), corner_radius=0)
        button.pack(pady=(0, 12))
        button._canvas.bind("<Down>", lambda e: self._step(1))
        button._canvas.bind("<Up>", lambda e: self._step(-1))
        self._buttons[name] = button
        if not self._current:
            self._current = name
            page.place(x=0, y=0, relwidth=1, relheight=1)
        self._sync_nav()
        return page

    def tab(self, name):
        return self._pages[name]

    def prime(self):
        """首次显示时缓存各页布局，避免切页时重新映射控件。"""
        self.update_idletasks()
        for page in self._pages.values():
            page.place(x=0, y=0, relwidth=1, relheight=1)
        self._pages[self._current].lift()
        self.update_idletasks()
        self._position_indicator(animate=False)

    def _position_indicator(self, animate=True):
        """只移动侧栏指示条，不跨越文字与输入框的独立绘制区域。"""
        self.owner._cancel(self._job)
        scale = self.owner._scale
        button = self._buttons[self._requested or self._current]
        height = round(24*scale)
        target = button.winfo_rooty()-self.rail.winfo_rooty()+(button.winfo_height()-height)/2
        initial = self._indicator_y if self._indicator_y is not None else target
        animate = animate and self.owner._motion_enabled and self.winfo_viewable()
        start = time.monotonic()
        def frame():
            t = min(1.0, (time.monotonic()-start)/0.16) if animate else 1.0
            self._indicator_y = initial+(target-initial)*(1-(1-t)**3)
            self._indicator.place(x=round(9*scale), y=round(self._indicator_y),
                                  width=max(2, round(3*scale)), height=height)
            if t < 1:
                self._job = self.owner._later(16, frame)
        frame()

    def focus_next(self, backwards=False):
        """页面保持布局缓存，Tab 跳过被遮住的页面。"""
        widget = self.owner.focus_get() or self.owner
        origin = widget
        command = "tk_focusPrev" if backwards else "tk_focusNext"
        for _ in range(256):
            path = self.tk.call(command, widget._w)
            try:
                widget = self.nametowidget(path)
            except KeyError:
                return "break"
            ancestor = widget
            while ancestor is not None and ancestor not in self._pages.values():
                ancestor = getattr(ancestor, "master", None)
            if ancestor is None or ancestor is self._pages[self._current]:
                widget.focus_set()
                return "break"
            if widget is origin:
                break
        return "break"

    def get(self):
        return self._current

    def _step(self, direction):
        names = list(self._pages)
        name = names[(names.index(self._current)+direction) % len(names)]
        self.set(name)
        self._buttons[name]._canvas.focus_set()

    def _sync_nav(self):
        for name, button in self._buttons.items():
            self.owner._cancel(button._pulse_job)
            selected = name == (self._requested or self._current)
            button.configure(fg_color=ACCENT if selected else INK,
                hover_color=ACCENT_HOVER if selected else "#34332F",
                text_color=INK if selected else "#C1BBB1")

    def set(self, name):
        if name not in self._pages:
            raise ValueError("Unknown scene: " + name)
        if name == self._requested or (self._requested is None and name == self._current):
            return
        self._requested = name
        self._sync_nav()
        self._position_indicator()
        if not self.owner._motion_enabled or not self.winfo_viewable():
            self.cancel_transition(commit=True)
        elif not self._wipe.active:
            self._begin_requested()

    def _begin_requested(self):
        if self._requested is None:
            return
        names = list(self._pages)
        direction = 1 if names.index(self._requested) >= names.index(self._current) else -1
        try:
            self._wipe.start(direction)
        except Exception:
            self.owner.app.log("原生转场不可用，直接切换页面")
            self._commit_requested()

    def _commit_requested(self):
        name = self._requested
        if name is None:
            return
        self._requested = None
        self._current = name
        self._pages[name].lift()
        self._sync_nav()
        self.owner._on_tab_change(name)
        return self._pages[name].winfo_id()

    def _wipe_finished(self):
        if not self.owner._closing and self._requested is not None:
            self._begin_requested()

    def cancel_transition(self, commit=False):
        self._wipe.cancel()
        if commit:
            self._commit_requested()
        else:
            self._requested = None
        self._sync_nav()


class ControlCenter(ctk.CTkToplevel):
    """卡片式控制中心；所有 Tk 更新与动画都在主线程执行。"""

    SWITCH_LABELS = {"oled_protect": "屏幕保护", "auto_generate": "自动生成横幅",
                     "ask_before_generate": "生成前询问"}

    def __init__(self, master, app):
        super().__init__(master, fg_color=PAPER)
        self.withdraw()
        self.app = app
        self._closing = False
        self._jobs = set()
        self._save_after_id = None
        self._pending_updates = {}
        self._preview_seq = 0
        self._preview_pending = None
        self._preview_thread = None
        self._preview_requests = queue.Queue(maxsize=1)
        self._preview_queue = queue.Queue()
        self._preview_photo = None
        self._preview_image = None
        self._fade_job = None
        self._toast_job = None
        self._toast_motion = None
        self._sizes = []
        self._last_chips = {}
        self._last_status_refresh = 0.0
        self._text_dirty = False
        self._generation_requested = False
        self._motion_enabled = _system_motion_enabled()
        self._scale = float(ctk.ScalingTracker.get_window_scaling(self) or 1.0)
        self._canvas_w = round(PREVIEW_W * self._scale)
        self._canvas_h = round(PREVIEW_H * self._scale)
        self.title("全屏提示 · 控制中心")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._init_geometry()
        self._set_icon()
        self.deiconify()
        self.tabs.prime()
        self._later(280, self._set_icon)
        self._later(100, self._initial_refresh)
        self.bind("<Control-s>", lambda event: self._on_apply_text()
                  if self.tabs.get() == "横幅" and not self.app._user_image_path() else None)
        self.bind("<Tab>", lambda event: self.tabs.focus_next())
        self.bind("<Shift-Tab>", lambda event: self.tabs.focus_next(backwards=True))

    def _later(self, ms, callback):
        if self._closing:
            return None
        job = None
        def run():
            self._jobs.discard(job)
            if not self._closing:
                callback()
        job = self.after(ms, run)
        self._jobs.add(job)
        return job

    def _cancel(self, job):
        if job:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            self._jobs.discard(job)

    def _set_icon(self):
        _square_window_corners(self)
        try:
            self.iconbitmap(self.app.tray_icon_path())
        except Exception:
            pass

    def _init_geometry(self):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth() / self._scale, self.winfo_screenheight() / self._scale
        w, h = int(min(1240, sw - 64)), int(min(820, sh - 80))
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw-w)//2), max(0, (sh-h)//2-16)))
        self.minsize(min(1080, w), min(700, h))

    def _label(self, parent, text, size=13, color=INK, bold=False, **kwargs):
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("height", 20 if size <= 13 else 28)
        return ctk.CTkLabel(parent, text=text, font=_font(size, "bold" if bold else "normal"),
                            text_color=color, **kwargs)

    def _button(self, parent, text, command, primary=False, width=112, **kwargs):
        button = MotionButton(parent, owner=self, text=text, command=command, width=width, height=36,
                              corner_radius=0, border_width=0,
                              fg_color=INK if primary else BG3,
                              hover_color="#34332F" if primary else LINE,
                              text_color=CARD if primary else INK,
                              font=_font(13, "bold" if primary else "normal"), **kwargs)
        button._canvas.configure(takefocus=1)
        button._canvas.bind("<Return>", lambda e: button.invoke())
        button._canvas.bind("<space>", lambda e: button.invoke())
        button._canvas.bind("<FocusIn>", lambda e: button.configure(border_width=2, border_color=ACCENT_INK))
        button._canvas.bind("<FocusOut>", lambda e: button.configure(border_width=0))
        return button

    def _card(self, parent, title, subtitle=None, tint=False):
        card = ctk.CTkFrame(parent, fg_color=SOFT_ORANGE if tint else CARD,
                            corner_radius=0, border_width=0)
        card.pack(fill="x", pady=(0, 16))
        self._label(card, title, size=16, bold=True).pack(anchor="w", padx=24, pady=(20, 0))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=24, pady=(16, 24))
        return body

    def _cfg_text(self, key):
        for prefix, cfg_key in (("title", "banner_title"), ("sub", "banner_sub")):
            if key in (prefix + "0", prefix + "1"):
                return str((self.app.CFG.get(cfg_key) or ["", ""])[int(key[-1])])
        return str(self.app.CFG.get(key, ""))

    def _field(self, parent, label, var, row, column=0):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=column, sticky="ew", padx=(0, 12) if column == 0 else 0,
                 pady=(0, 12))
        self._label(box, label, size=12, color=INK2).pack(anchor="w", pady=(0, 4))
        entry = ctk.CTkEntry(box, textvariable=var, height=36, corner_radius=0,
                             fg_color=PANEL, border_color=LINE, border_width=1,
                             font=_font(13), text_color=INK)
        entry.pack(fill="x")
        entry.bind("<FocusIn>", lambda e: entry.configure(border_color=ACCENT))
        entry.bind("<FocusOut>", lambda e: entry.configure(border_color=LINE))
        return entry

    def _build_ui(self):
        self.tabs = MotionDeck(self, self)
        self.tabs.pack(fill="both", expand=True)
        header = self.tabs.header
        header.grid_columnconfigure(0, weight=1)
        self.scene_title = self._label(header, "全屏提示", size=28, bold=True, height=40)
        self.scene_title.grid(row=0, column=0, sticky="w")
        chips = ctk.CTkFrame(header, fg_color="transparent")
        chips.grid(row=0, column=1, sticky="e")
        self.chip_banner = self._chip(chips, "待命")
        self.chip_keyboard = self._chip(chips, "键盘可用")
        self.chip_screens = self._chip(chips, "检测屏幕…")
        for name in ("开始", "横幅", "息屏", "更多"):
            self.tabs.add(name)
        self._build_preview_panel(self.tabs.tab("开始"))
        self._build_tab_banner()
        self._build_tab_oled()
        self._build_more()
        self._build_footer()
        self._build_toast()

    def _chip(self, parent, text):
        label = self._label(parent, text, size=12, color=MUTED,
                            fg_color=BG3, corner_radius=0, height=28)
        label.pack(side="left", padx=(8, 0), ipadx=8)
        return label

    def _set_chip(self, chip, text, color, background=BG3):
        key = (text, color, background)
        if self._last_chips.get(id(chip)) != key:
            chip.configure(text=text, text_color=color, fg_color=background)
            self._last_chips[id(chip)] = key

    @staticmethod
    def _configure_changed(widget, **values):
        changed = {key: value for key, value in values.items() if widget.cget(key) != value}
        if changed:
            widget.configure(**changed)

    def _scroll(self, tab):
        frame = ctk.CTkScrollableFrame(self.tabs.tab(tab), fg_color="transparent",
                                      corner_radius=0, scrollbar_button_color=LINE,
                                      scrollbar_button_hover_color=MUTED)
        frame.pack(fill="both", expand=True)
        return frame

    def _build_tab_banner(self):
        wrap = self._scroll("横幅")
        source = self._card(wrap, "来源", "选择自动生成，或使用一张自己的图片。")
        source.grid_columnconfigure((0, 1), weight=1)
        self.source_var = tk.IntVar(value=1 if self.app._user_image_path() else 0)
        self.source_buttons = []
        for index, (title, caption) in enumerate((("自动生成", ""), ("自定义图片", ""))):
            button = self._button(source, title,
                                   lambda i=index: self._select_source(i), width=0)
            button.configure(height=64, anchor="w", font=_font(16, "bold"), corner_radius=0)
            button.grid(row=0, column=index, sticky="ew", padx=(0, 12) if index == 0 else 0)
            self.source_buttons.append(button)
        self.lbl_image_source = self._label(source, "", size=12, color=MUTED,
                                             wraplength=420, justify="left")


        self.gen_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.gen_frame.pack(fill="x")
        self.gen_frame.grid_columnconfigure(0, weight=3, uniform="editor")
        self.gen_frame.grid_columnconfigure(1, weight=2, uniform="editor")
        content = ctk.CTkFrame(self.gen_frame, fg_color="transparent")
        content.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        options = ctk.CTkFrame(self.gen_frame, fg_color="transparent")
        options.grid(row=0, column=1, sticky="nsew")
        text = self._card(content, "文案", "编辑后点「应用文案」，或按 Ctrl + S 保存。")
        text.grid_columnconfigure((0, 1), weight=1, uniform="fields")
        self.text_vars = {}
        self.text_entries = {}
        for index, (key, label, cfg) in enumerate((
                ("title0", "主标题 · 第一行", "title0"), ("title1", "主标题 · 第二行", "title1"),
                ("eyebrow", "顶部提示", "banner_eyebrow"), ("banner_plate", "警示牌", "banner_plate"),
                ("sub0", "说明 · 第一行", "sub0"), ("sub1", "说明 · 第二行", "sub1"))):
            var = tk.StringVar(value=self._cfg_text(cfg))
            self.text_vars[key] = var
            self.text_entries[key] = self._field(text, label, var, index // 2, index % 2)
            var.trace_add("write", self._mark_text_dirty)
        actions = ctk.CTkFrame(text, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.btn_apply_text = self._button(actions, "应用文案", self._on_apply_text, width=104)
        self.btn_apply_text.pack(side="left")
        self._button(actions, "恢复默认", self._on_reset_text, width=96).pack(side="left", padx=8)
        self.copy_state = self._label(actions, "已保存", size=12, color=MUTED)
        self.copy_state.pack(side="right")
        appearance = self._card(options, "配色", "只改变横幅颜色，设置界面保持明亮。")
        self.banner_theme_seg = ctk.CTkSegmentedButton(
            appearance, values=["浅色", "深色"], command=self._on_banner_theme_seg,
            height=36, corner_radius=0, font=_font(13), selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, text_color=INK,
            unselected_color=BG3, unselected_hover_color=LINE)
        self.banner_theme_seg.pack(anchor="w")
        generation = self._card(options, "生成设置")
        self.sw_auto = self._switch(generation, "自动按屏幕分辨率生成", "auto_generate")
        self.sw_ask = self._switch(generation, "生成前先询问", "ask_before_generate")

        self.img_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        image = self._card(self.img_frame, "图片", "先选图片，再调整它在屏幕上的显示方式。")
        self._button(image, "选择图片…", self._on_choose_image).pack(anchor="w")
        self.image_name = self._label(image, "尚未选择图片", size=12, color=MUTED, wraplength=420)
        self.image_name.pack(anchor="w", pady=(12, 0))
        fit = self._card(self.img_frame, "适配")
        self.fit_var = tk.IntVar(value=list(self.app.FIT_CHOICES).index(self.app.BANNER_FIT))
        self.fit_radios = []
        for index, mode in enumerate(self.app.FIT_CHOICES):
            radio = ctk.CTkRadioButton(fit, text=FIT_DESCRIPTIONS[mode], font=_font(13),
                                       text_color=INK, variable=self.fit_var, value=index,
                                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                       command=self._on_fit_change)
            radio.pack(anchor="w", pady=8)
            self.fit_radios.append(radio)
        self.fill_frame = ctk.CTkFrame(fit, fg_color=PANEL, corner_radius=0)
        self.fill_frame.pack(fill="x", pady=(12, 0))
        self._label(self.fill_frame, "图片以外的区域", size=12, color=MUTED).pack(anchor="w", padx=12, pady=(12, 4))
        self.contain_fill_var = tk.IntVar(value=1 if self.app.CFG.get("contain_fill") == "transparent" else 0)
        row = ctk.CTkFrame(self.fill_frame, fg_color="transparent")
        row.pack(anchor="w", padx=12, pady=(0, 12))
        for index, name in enumerate(("黑色", "透明 · 透出桌面")):
            ctk.CTkRadioButton(row, text=name, font=_font(12), variable=self.contain_fill_var,
                               value=index, fg_color=ACCENT, command=self._on_contain_fill).pack(side="left", padx=(0, 16))

    def _switch(self, parent, text, key):
        switch = AnimatedToggle(parent, owner=self, text=text, font=_font(13), text_color=INK,
                               progress_color=ACCENT, button_color=CARD,
                               button_hover_color=SOFT_ORANGE, fg_color=LINE)
        switch.configure(command=lambda: self._on_switch(key, switch))
        switch.pack(anchor="w", pady=8)
        if self.app.CFG.get(key, True):
            switch.select()
        return switch

    def _build_tab_oled(self):
        wrap = self._scroll("息屏")
        protection = self._card(wrap, "屏幕保护", "横幅显示时启用微移、降亮度和暗屏守护。", tint=True)
        self.sw_oled = self._switch(protection, "启用 OLED 屏幕保护", "oled_protect")
        columns = ctk.CTkFrame(wrap, fg_color="transparent")
        columns.pack(fill="x")
        columns.grid_columnconfigure((0, 1), weight=1, uniform="protection")
        timing = ctk.CTkFrame(columns, fg_color="transparent")
        timing.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        appearance = ctk.CTkFrame(columns, fg_color="transparent")
        appearance.grid(row=0, column=1, sticky="nsew")
        controls = self._card(timing, "节奏", "滑块停止操作后自动保存，无需额外确认。")
        self.sl_idle = self._slider(controls, "空闲后进入暗屏", "idle_to_guard", 10, 600, 590, "%d 秒")
        self.sl_bright = self._slider(controls, "横幅亮度", "brightness", 0.2, 1.0, 80, "%.0f%%", percent=True)
        self.sl_shift = self._slider(controls, "横幅微移间隔", "shift_interval", 5, 120, 115, "%d 秒")
        self.sl_radius = self._slider(controls, "微移范围", "shift_radius", 0, 20, 20, "%d 像素")
        self.sl_guard_move = self._slider(controls, "守护文字移动间隔", "guard_move_interval", 3, 60, 57, "%d 秒")
        self.protection_hint = self._label(controls, "", size=12, color=MUTED, wraplength=420)
        self.protection_hint.pack(anchor="w", pady=(8, 0))
        guard = self._card(appearance, "守护文字", "暗屏后显示少量文字；动鼠标或按键会恢复横幅。")
        guard.grid_columnconfigure((0, 1), weight=1)
        self.var_guard_text = tk.StringVar(value=self.app.CFG.get("guard_text", ""))
        self.var_guard_color = tk.StringVar(value=self.app.CFG.get("guard_color", "#4f5c56"))
        self._field(guard, "显示文字", self.var_guard_text, 0)
        self.guard_color_entry = self._field(guard, "颜色 · #RRGGBB", self.var_guard_color, 0, 1)
        self._button(guard, "应用守护文字", self._on_apply_guard_text, width=128).grid(row=1, column=0, sticky="w")

    def _slider(self, parent, label, key, start, end, steps, fmt, percent=False):
        group = ctk.CTkFrame(parent, fg_color="transparent")
        group.pack(fill="x", pady=(0, 16))
        head = ctk.CTkFrame(group, fg_color="transparent")
        head.pack(fill="x")
        self._label(head, label).pack(side="left")
        value = float(self.app.CFG.get(key, self.app.DEFAULT_CONFIG[key]))
        amount = self._label(head, fmt % (value * 100 if percent else value), size=13, color=ACCENT_INK, bold=True)
        amount.pack(side="right")
        slider = ctk.CTkSlider(group, from_=start, to=end, number_of_steps=steps,
                               progress_color=ACCENT, button_color=INK, button_hover_color="#4A4138",
                               button_corner_radius=0, button_length=16, border_width=4,
                               fg_color=BG3, height=24)
        slider.set(value)
        slider.pack(fill="x", pady=(8, 0))
        def change(v):
            amount.configure(text=fmt % (v * 100 if percent else v))
            self._schedule_apply({key: round(float(v), 2) if percent else int(round(v))})
        slider.configure(command=change)
        return slider

    def _build_preview_panel(self, page):
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.hero = ctk.CTkFrame(page, fg_color=ACCENT, corner_radius=0)
        self.hero.grid(row=0, column=0, sticky="nsew", pady=(0, 16))
        self.hero.grid_columnconfigure(0, weight=1)
        self.hero.grid_rowconfigure(0, weight=1)
        left = ctk.CTkFrame(self.hero, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(32, 16), pady=32)
        left.grid_rowconfigure(1, weight=1)
        self._label(left, "●  全屏横幅", size=13, bold=True).grid(row=0, column=0, sticky="w")
        self.hero_title = self._label(left, "让任务\n继续。", size=56, bold=True, height=160, justify="left")
        self.hero_title.grid(row=1, column=0, sticky="w", pady=24)
        self.btn_toggle = self._button(left, "显示横幅  ↗", self._on_toggle, primary=True, width=248)
        self.btn_toggle.configure(height=64, corner_radius=0, font=_font(22, "bold"))
        self.btn_toggle.grid(row=2, column=0, sticky="w")
        self._label(left, "Esc 返回", size=12).grid(row=3, column=0, sticky="w", pady=(12, 0))
        right = ctk.CTkFrame(self.hero, fg_color="transparent")
        right.grid(row=0, column=1, sticky="ns", padx=(0, 32), pady=32)
        right.grid_rowconfigure(1, weight=1)
        self.monitor_menu = ctk.CTkOptionMenu(right, values=["检测显示器…"], width=PREVIEW_W,
            height=32, corner_radius=0, font=_font(12), fg_color=ACCENT,
            button_color=ACCENT, button_hover_color="#E85C0A", text_color=INK,
            dropdown_font=_font(12), command=self._on_monitor_change)
        self.monitor_menu.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        stage = ctk.CTkFrame(right, fg_color="transparent")
        stage.grid(row=1, column=0, sticky="nsew")
        well = ctk.CTkFrame(stage, fg_color=INK, corner_radius=0)
        well.pack(expand=True)
        self.preview_canvas = tk.Canvas(well, width=self._canvas_w, height=self._canvas_h,
            bg=INK, bd=0, highlightthickness=0)
        self.preview_canvas.pack(padx=8, pady=8)
        self.preview_canvas.create_text(self._canvas_w//2, self._canvas_h//2,
            text="正在加载", fill="#B5B0AA", font=(FONT_FAMILY, 12))
        self.preview_progress = ctk.CTkProgressBar(right, height=3, corner_radius=0,
            border_width=0, border_color=ACCENT, mode="determinate", progress_color=INK, fg_color="#E85C0A")
        self.preview_progress.grid(row=2, column=0, sticky="ew", pady=(16, 12))
        self.preview_progress.set(0)
        self.preview_info = self._label(right, "正在加载", size=12, color=INK,
            wraplength=PREVIEW_W, justify="left")
        self.preview_info.grid(row=3, column=0, sticky="w")
        self.lbl_out = self._label(right, "", size=12, color=INK)
        self.lbl_out.grid(row=4, column=0, sticky="w", pady=(4, 0))
        quick = ctk.CTkFrame(page, fg_color="transparent")
        quick.grid(row=1, column=0, sticky="ew")
        quick.grid_columnconfigure((0, 1), weight=1, uniform="quick")
        self.home_source = self._button(quick, "画面  ↗", lambda: self.tabs.set("横幅"), width=0)
        self.home_source.configure(height=112, fg_color=CARD, hover_color=LINE,
            font=_font(18, "bold"), anchor="w", corner_radius=0)
        self.home_source.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.home_protection = self._button(quick, "屏幕保护  ↗", lambda: self.tabs.set("息屏"), width=0)
        self.home_protection.configure(height=112, fg_color=INK, hover_color="#34332F",
            text_color=CARD, font=_font(18, "bold"), anchor="w", corner_radius=0)
        self.home_protection.grid(row=0, column=1, sticky="ew", padx=(8, 0))

    def _build_more(self):
        wrap = self._scroll("更多")
        system = self._card(wrap, "启动")
        self.sw_startup = AnimatedToggle(system, owner=self, text="登录 Windows 后自动启动", font=_font(16),
            text_color=INK, progress_color=ACCENT, button_color=CARD, command=self._on_startup_toggle)
        self.sw_startup.pack(anchor="w")
        if self.app.is_startup_enabled():
            self.sw_startup.select()
        tools = self._card(wrap, "工具")
        self._button(tools, "数据目录  ↗", self._on_open_data_dir, width=160).pack(side="left", padx=(0, 16))
        self._button(tools, "运行日志  ↗", self._on_open_log, width=160).pack(side="left", padx=(0, 16))
        self._button(tools, "快捷键", self._on_help, width=128).pack(side="left")
        about = self._card(wrap, "全屏提示")
        self._label(about, "v%s" % self.app.VERSION, size=32, bold=True).pack(anchor="w")
        self._label(about, "本地运行 · 多屏横幅 · OLED 保护", color=MUTED).pack(anchor="w", pady=(12, 0))

    def _build_footer(self):
        self.status_label = self._label(self.tabs.footer, "●  就绪", size=12, color=MUTED)
        self.status_label.pack(anchor="w")

    def _build_toast(self):
        self.toast = ctk.CTkFrame(self, fg_color=INK, corner_radius=0, border_width=0)
        self.toast_icon = self._label(self.toast, "✓", size=22, bold=True, color=ACCENT)
        self.toast_icon.pack(side="left", padx=(24, 16), pady=16)
        self.toast_text = self._label(self.toast, "", size=16, color=CARD, wraplength=400, justify="left")
        self.toast_text.pack(side="left", pady=16)
        close = self._button(self.toast, "×", self._dismiss_toast, width=32)
        close.configure(fg_color=INK, hover_color="#34332F", text_color=CARD)
        close.pack(side="right", padx=12, pady=12)

    def _set_status(self, text, tone="success", toast=True):
        if self._closing:
            return
        color = {"success": SUCCESS, "error": DANGER, "info": MUTED, "warning": ACCENT_INK}[tone]
        self.status_label.configure(text=text, text_color=color)
        self._feedback_tone = tone
        if not toast:
            return
        self._cancel(self._toast_job)
        self._cancel(self._toast_motion)
        self.toast_icon.configure(text={"success": "✓", "error": "!", "info": "i", "warning": "!"}[tone], text_color="#FF9485" if tone == "error" else ACCENT)
        self.toast_text.configure(text=text)
        self.toast.configure(border_color=color)
        self.toast.lift()
        def slide(step=0):
            ease = 1 - (1 - step / 16) ** 3
            self.toast.place(relx=1, rely=1, x=-24, y=-72 + round(56 * (1-ease)), anchor="se")
            if step < 16 and self._motion_enabled and self.winfo_viewable():
                self._toast_motion = self._later(16, lambda: slide(step + 1))
        slide(0 if self._motion_enabled else 16)
        if tone != "error":
            self._toast_job = self._later(4200, self._dismiss_toast)

    def _dismiss_toast(self):
        self._cancel(self._toast_motion)
        self._cancel(self._toast_job)
        self.toast.place_forget()

    def _mark_text_dirty(self, *_):
        self._text_dirty = any(var.get() != self._cfg_text(
            "banner_eyebrow" if key == "eyebrow" else key) for key, var in self.text_vars.items())
        if hasattr(self, "copy_state"):
            self.copy_state.configure(text="尚未应用" if self._text_dirty else "已保存",
                                      text_color=ACCENT_INK if self._text_dirty else MUTED)
            self.btn_apply_text.configure(fg_color=SOFT_ORANGE if self._text_dirty else CARD,
                                           border_color=ORANGE_LINE if self._text_dirty else LINE)

    def _initial_refresh(self):
        self.tabs._position_indicator(animate=False)
        self._refresh_status()
        self._sync_image_tab()
        self._sync_banner_theme()
        self._request_preview()

    def _refresh_status(self):
        app = self.app
        visible = bool(app.banner_windows)
        self._set_chip(self.chip_banner, "● 显示中" if visible else "待命",
                       SUCCESS if visible else MUTED, SUCCESS_BG if visible else BG3)
        self._set_chip(self.chip_keyboard, "键盘已屏蔽" if app.keyboard_blocked else (
            "键盘屏蔽不可用" if app.hook_failed else "键盘可用"),
            DANGER if app.hook_failed else MUTED)
        monitors = app.get_monitors()
        self._set_chip(self.chip_screens, "%d 块屏幕" % len(monitors), MUTED)
        self._configure_changed(self.btn_toggle, text="关闭横幅  ↙" if visible else "显示横幅  ↗",
                                   state="normal" if monitors else "disabled")
        sizes = [(r-l, b-t) for l, t, r, b in monitors]
        if sizes != self._sizes:
            old_index = self._selected_monitor()
            self._sizes = sizes
            values = ["屏幕 %d · %d×%d" % (i+1, w, h) for i, (w, h) in enumerate(sizes)] or ["未检测到显示器"]
            self.monitor_menu.configure(values=values)
            self.monitor_menu.set(values[min(old_index, len(values)-1)])
            self._request_preview()
        self._configure_changed(self.hero_title, text="运行中，\n勿打扰。" if visible else "让任务\n继续。")
        self._update_out_label()
        enabled = bool(app.CFG.get("oled_protect", True))
        for slider in (self.sl_idle, self.sl_bright, self.sl_shift, self.sl_radius, self.sl_guard_move):
            self._configure_changed(slider, state="normal" if enabled else "disabled")
        self._configure_changed(self.home_protection, text="    屏幕保护  ↗\n    " + ("已开启" if enabled else "已关闭"))
        self._configure_changed(self.protection_hint, text="自动保存" if enabled else "已关闭 · 横幅静止常亮")

    def _selected_monitor(self):
        text = self.monitor_menu.get()
        return next((i for i in range(len(self._sizes)) if text.startswith("屏幕 %d " % (i+1))), 0)

    def _on_monitor_change(self, _value=None):
        self._request_preview()

    def _select_source(self, index):
        self.source_var.set(index)
        self._on_source_change()

    def _on_source_change(self):
        custom = bool(self.app._user_image_path())
        if self.source_var.get() and not custom:
            self._on_choose_image()
        elif not self.source_var.get() and custom:
            self._on_clear_image()
        self._sync_image_tab()

    def _update_source_frames(self):
        custom = bool(self.source_var.get())
        active, inactive = ((self.img_frame, self.gen_frame) if custom else
                            (self.gen_frame, self.img_frame))
        if inactive.winfo_manager():
            inactive.pack_forget()
        if not active.winfo_manager():
            active.pack(fill="x")
        for index, button in enumerate(self.source_buttons):
            self._cancel(button._pulse_job)
            selected = index == int(custom)
            title = ("自动生成", "自定义图片")[index]
            button.configure(fg_color=ACCENT if selected else PANEL,
                              border_color=ACCENT if selected else LINE,
                              border_width=0,
                              text_color=INK,
                              text=("✓  " if selected else "    ") + title)

    def _sync_image_tab(self):
        src = self.app._user_image_path()
        self.source_var.set(int(bool(src)))
        signature = (src, self.app.BANNER_FIT, self.app.CFG.get("contain_fill"), self.app.effective_theme())
        if signature == getattr(self, "_image_signature", None):
            return
        self._image_signature = signature
        self.lbl_image_source.configure(text="已选择自己的图片，可随时更换。" if src else "按每块屏幕的尺寸生成，默认即可直接使用。")
        self.image_name.configure(text=os.path.basename(src) if src else "尚未选择图片")
        self.home_source.configure(text="    画面  ↗\n    " + ("自定义图片" if src else "自动生成 · " + ("深色" if self.app.effective_theme() == "dark" else "浅色")))
        self.fit_var.set(list(self.app.FIT_CHOICES).index(self.app.BANNER_FIT))
        self.contain_fill_var.set(int(self.app.CFG.get("contain_fill") == "transparent"))
        if self.app.BANNER_FIT == "contain":
            self.fill_frame.pack(fill="x", pady=(12, 0))
        else:
            self.fill_frame.pack_forget()
        for radio in self.fit_radios:
            radio.configure(state="normal" if src else "disabled")
        self._update_source_frames()
        self._update_out_label()

    def _update_out_label(self):
        app = self.app
        label = "亮度 %d%%" % round((app.BANNER_BRIGHTNESS if app.OLED_PROTECT else 1.0)*100)
        self._configure_changed(self.lbl_out, text=label + "  ·  " + ("保护开启" if app.OLED_PROTECT else "保护关闭"))

    def _preview_snapshot(self):
        from types import SimpleNamespace
        app = self.app
        source, theme, text = app._user_image_path(), app.effective_theme(), app._banner_text_cfg()
        return SimpleNamespace(CACHE_DIR=app.CACHE_DIR, CFG=dict(app.CFG), BANNER_FIT=app.BANNER_FIT,
                               FIT_LABELS=app.FIT_LABELS, _user_image_path=lambda: source,
                               effective_theme=lambda: theme, _banner_text_cfg=lambda: text,
                               _open_rgb=app._open_rgb, log=app.log)

    def _request_preview(self):
        if self._closing:
            return
        if not self._sizes:
            self.preview_info.configure(text="尚未检测到显示器，连接屏幕后会自动预览。")
            self.preview_progress.stop()
            self.preview_progress.set(0)
            return
        mw, mh = self._sizes[min(self._selected_monitor(), len(self._sizes)-1)]
        self._preview_seq += 1
        self._preview_busy = True
        request = (self._preview_seq, self._preview_snapshot(), (mw, mh, self._canvas_w, self._canvas_h))
        self._preview_pending = request
        try:
            self._preview_requests.get_nowait()
        except queue.Empty:
            pass
        self._preview_requests.put_nowait(request)
        self.preview_info.configure(text="正在更新预览…")
        self.preview_progress.configure(mode="indeterminate")
        self.preview_progress.start()
        if self._preview_thread is None or not self._preview_thread.is_alive():
            self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
            self._preview_thread.start()

    def _preview_loop(self):
        while not self._closing:
            try:
                seq, snapshot, params = self._preview_requests.get(timeout=0.2)
            except queue.Empty:
                continue
            self._preview_pending = None
            try:
                image, info = build_preview_image(snapshot, *params)
            except Exception:
                self.app.log("生成预览失败：\n" + traceback.format_exc())
                image, info = None, "预览暂不可用，请重新选择图片或查看日志。"
            if not self._closing:
                self._preview_queue.put((seq, image, info))

    def _paint_preview(self, image):
        self._preview_photo = ImageTk.PhotoImage(image, master=self)
        item = getattr(self, "_preview_item", None)
        if item is None or self.preview_canvas.type(item) != "image":
            self.preview_canvas.delete("all")
            self._preview_item = self.preview_canvas.create_image(0, 0, anchor="nw", image=self._preview_photo)
        else:
            self.preview_canvas.itemconfigure(item, image=self._preview_photo)
            self.preview_canvas.coords(item, 0, 0)

    def _show_preview(self, image, seq):
        self._cancel(self._fade_job)
        self._preview_image = image
        self._paint_preview(image)
        if not self._motion_enabled or self.tabs.get() != "开始" or not self.preview_canvas.winfo_viewable():
            return
        start = time.monotonic()
        def frame():
            if seq != self._preview_seq:
                return
            t = min(1.0, (time.monotonic()-start)/0.16)
            self.preview_canvas.coords(self._preview_item, 0, round(12*self._scale*(1-t)**3))
            if t < 1:
                self._fade_job = self._later(16, frame)
        frame()

    def _drain_preview(self):
        while True:
            try:
                seq, image, info = self._preview_queue.get_nowait()
            except queue.Empty:
                return
            if seq != self._preview_seq:
                continue
            self._preview_busy = False
            self.preview_progress.stop()
            self.preview_progress.configure(mode="determinate")
            self.preview_progress.set(0)
            self.preview_info.configure(text=info)
            if image is None:
                self.preview_canvas.delete("all")
                self.preview_canvas.create_text(self._canvas_w//2, self._canvas_h//2,
                    text="预览暂不可用\n请更换图片后重试", fill="#E9E4DC", justify="center", font=(FONT_FAMILY, 12))
                self._set_status("预览失败，请更换图片或打开日志查看原因。", "error")
            else:
                self._show_preview(image, seq)

    def _tick(self):
        if self._closing or getattr(self.app, "_quitting", False):
            return
        try:
            self._drain_preview()
            if self.winfo_viewable() and time.monotonic() - self._last_status_refresh > 0.7:
                self._refresh_status()
                self._last_status_refresh = time.monotonic()
        except Exception:
            self.app.log("控制中心刷新异常：\n" + traceback.format_exc())
        self._later((50 if getattr(self, "_preview_busy", False) else 150)
                    if self.winfo_viewable() else 600, self._tick)

    def _apply(self, updates, note="设置已保存并生效"):
        try:
            if not self.app._apply_settings(updates):
                raise ValueError("设置未能保存")
        except Exception:
            self.app.log("应用设置失败：\n" + traceback.format_exc())
            self._set_status("保存失败，修改尚未生效；请查看日志后重试。", "error")
            return False
        self._sync_image_tab()
        self._refresh_status()
        self._request_preview()
        self._set_status(note)
        return True

    def _schedule_apply(self, updates):
        self._pending_updates.update(updates)
        self._cancel(self._save_after_id)
        self._set_status("调整中 · 停止操作后自动保存", "info", toast=False)
        self._save_after_id = self._later(350, self._flush_apply)

    def _flush_apply(self):
        self._cancel(self._save_after_id)
        self._save_after_id = None
        if not self._pending_updates:
            return True
        updates = dict(self._pending_updates)
        if self._apply(updates):
            self._pending_updates.clear()
            return True
        return False

    def _on_switch(self, key, widget):
        value = bool(widget.get())
        if not self._apply({key: value}, self.SWITCH_LABELS[key] + ("已开启" if value else "已关闭")):
            widget.select() if self.app.CFG.get(key) else widget.deselect()

    def _trigger_regenerate(self, note):
        if self.app._user_image_path():
            self._set_status("当前使用自己的图片，无需生成默认横幅。", "info")
            return
        sizes = self.app._monitor_sizes()
        if not sizes:
            self._set_status("未检测到显示器，连接屏幕后重试。", "error")
            return
        self._generation_requested = True
        self.app._start_generation(sizes)
        self._set_status(note, "info")

    def _on_apply_text(self):
        updates = {"banner_eyebrow": self.text_vars["eyebrow"].get().strip(),
                   "banner_title": [self.text_vars[k].get().strip() for k in ("title0", "title1")],
                   "banner_plate": self.text_vars["banner_plate"].get().strip(),
                   "banner_sub": [self.text_vars[k].get().strip() for k in ("sub0", "sub1")]}
        if not any(updates["banner_title"]):
            self.text_entries["title0"].configure(border_color=DANGER)
            self.text_entries["title0"].focus_set()
            self._set_status("主标题至少填写一行，再应用文案。", "error")
            return
        if self._apply(updates, "文案已保存"):
            for key, var in self.text_vars.items():
                var.set(self._cfg_text("banner_eyebrow" if key == "eyebrow" else key))
            self._mark_text_dirty()
            self._trigger_regenerate("文案已保存，正在生成横幅…")

    def _on_reset_text(self):
        default = self.app.DEFAULT_CONFIG
        values = {"eyebrow": default["banner_eyebrow"], "banner_plate": default["banner_plate"],
                  "title0": default["banner_title"][0], "title1": default["banner_title"][1],
                  "sub0": default["banner_sub"][0], "sub1": default["banner_sub"][1]}
        for key, value in values.items():
            self.text_vars[key].set(value)
        self._on_apply_text()

    def _on_fit_change(self):
        fit = self.app.FIT_CHOICES[self.fit_var.get()]
        self._apply({"banner_fit": fit}, "图片已切换为" + self.app.FIT_LABELS[fit])

    def _on_contain_fill(self):
        fill = "transparent" if self.contain_fill_var.get() else "black"
        self._apply({"contain_fill": fill}, "图片外的区域已设为" + ("透明，实际会透出桌面" if fill == "transparent" else "黑色"))

    def _on_choose_image(self):
        app = self.app
        visible = bool(app.banner_windows)
        if visible:
            app.hide_banner()
        try:
            path = filedialog.askopenfilename(parent=self, title="选择一张横幅图片",
                filetypes=[("图片", " ".join("*"+ext for ext in app.USER_IMAGE_EXTS)), ("所有文件", "*.*")])
            if path:
                # Validate before saving so corrupt or unsupported files never replace a working choice.
                with Image.open(path) as image:
                    image.load()
                self._apply({"banner_image": os.path.abspath(path)}, "图片已应用：" + os.path.basename(path))
            else:
                self._set_status("已取消选图，保留原来的横幅。", "info")
        except Exception:
            app.log("选择图片失败：\n" + traceback.format_exc())
            self._set_status("这张图片无法打开，请换一张 PNG、JPG 或其他支持的图片。", "error")
        finally:
            self._sync_image_tab()
            if visible:
                app.show_banner()

    def _on_clear_image(self):
        if self.app._dropin_image_path():
            self._set_status("数据目录中有自动投图，请先移走 banner 或 banner_custom 图片，再切换为自动生成。", "warning")
            return
        self._apply({"banner_image": ""}, "已恢复自动生成横幅")

    def _on_banner_theme_seg(self, value):
        self._set_banner_theme("dark" if value == "深色" else "light")

    def _set_banner_theme(self, mode):
        self._generation_requested = not bool(self.app._user_image_path())
        if not self._apply({"banner_theme": mode}, "正在更新" + ("深色" if mode == "dark" else "浅色") + "横幅…"):
            self._generation_requested = False
        self._sync_banner_theme()

    def _on_generation_result(self, ok):
        if self._generation_requested:
            self._generation_requested = False
            self._set_status("横幅已更新" if ok else "生成失败，保留原横幅；请查看日志。",
                             "success" if ok else "error")

    def _sync_banner_theme(self):
        self.banner_theme_seg.set("深色" if self.app.effective_theme() == "dark" else "浅色")

    def _on_apply_guard_text(self):
        import re
        color = self.var_guard_color.get().strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            self.guard_color_entry.configure(border_color=DANGER)
            self.guard_color_entry.focus_set()
            self._set_status("颜色请填写 # 加 6 位十六进制数字，例如 #4f5c56。", "error")
            return
        self._apply({"guard_text": self.var_guard_text.get().strip(), "guard_color": color}, "暗屏守护文字已更新")

    def _on_startup_toggle(self):
        enabled = bool(self.sw_startup.get())
        try:
            self.app.set_startup(enabled)
            if self.app.is_startup_enabled() != enabled:
                raise OSError("启动项写入未生效")
            self._set_status("开机启动已" + ("开启" if enabled else "关闭"))
        except Exception:
            self.sw_startup.select() if self.app.is_startup_enabled() else self.sw_startup.deselect()
            self._set_status("开机启动设置失败，请打开日志查看原因。", "error")

    def _on_toggle(self):
        if not self._flush_apply():
            return
        if self._text_dirty and not self.app.banner_windows:
            self._set_status("还有未应用的文案；先点「应用文案」再显示横幅。", "warning")
            return
        try:
            self.app.toggle_banner()
            self._refresh_status()
            self._set_status("横幅已显示 · 按 Esc 返回" if self.app.banner_windows else "横幅已关闭")
        except Exception:
            self.app.log("切换横幅失败：\n" + traceback.format_exc())
            self._set_status("横幅未能显示，请打开日志查看原因。", "error")

    def _on_help(self):
        messagebox.showinfo("随时返回，放心使用", "Esc：关闭横幅，恢复键盘。\n\n"
            "Ctrl + Alt + Shift + Esc：强制解除键盘屏蔽。\n\n"
            "托盘右键 → 强制解除键盘屏蔽，也可以恢复操作。\n\n"
            "关闭这个设置窗口会隐藏到托盘；彻底退出请点「退出程序」。", parent=self)

    def _on_exit(self):
        if not self._flush_apply():
            return
        note = "退出后横幅会关闭，托盘图标会消失。"
        if self._text_dirty:
            note += "\n尚未应用的文案将被丢弃。"
        if messagebox.askyesno("退出全屏提示？", note, parent=self):
            self.app.commands.put(("exit",))

    def _on_open_data_dir(self):
        self._open_path(self.app.DATA_DIR)

    def _on_open_log(self):
        if os.path.isfile(self.app.LOG_PATH):
            self._open_path(self.app.LOG_PATH)
        else:
            self._set_status("还没有日志，可在数据目录查看配置。", "info")

    def _open_path(self, path):
        try:
            os.startfile(path)
            self._set_status("已打开" + ("数据目录" if path == self.app.DATA_DIR else "日志"))
        except OSError:
            self._set_status("打开失败，请检查文件是否还在。", "error")

    def _on_tab_change(self, name=None):
        self._flush_apply()
        self._sync_image_tab()
        self.scene_title.configure(text={"开始": "全屏提示", "横幅": "画面", "息屏": "屏幕保护", "更多": "设置"}.get(name or self.tabs.get(), "全屏提示"))
        self._dismiss_toast()

    def _on_close(self):
        if self._flush_apply():
            self.tabs.cancel_transition(commit=True)
            self._dismiss_toast()
            self.withdraw()

    def destroy(self):
        if self._closing:
            return
        self._flush_apply()
        self.tabs._wipe.destroy()
        self._closing = True
        self.preview_progress.stop()
        for job in list(self._jobs):
            self._cancel(job)
        super().destroy()


# ===================== 对外接口 =====================
def notify_generation_result(ok):
    """主程序完成出图后，在主线程更新准确的完成反馈。"""
    if _center is not None and not _center._closing:
        _center._on_generation_result(ok)


def open_control_center(master, app):
    """打开（或唤回）控制中心。必须在主线程调用。"""
    global _center
    _ensure_ctk_theme(app)
    try:
        ctk.set_appearance_mode("light")
    except Exception:
        pass
    win = _center
    try:
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            try:
                win.focus_force()
            except Exception:
                pass
            win._sync_image_tab()
            win._refresh_status()
            win._request_preview()
            return win
    except Exception:
        _center = None

    try:
        _center = ControlCenter(master, app)
        # 打开后立即开始轮询（首次刷新会顺带生成预览）
        try:
            _center.after(60, _center._tick)
        except Exception:
            pass
        return _center
    except Exception:
        app.log("打开控制中心失败：\n" + traceback.format_exc())
        try:
            app.show_balloon("全屏提示", "打开控制中心失败，详情见日志")
        except Exception:
            pass
        _center = None
        return None


def close_control_center():
    """销毁控制中心（程序退出时调用）。"""
    global _center
    win = _center
    _center = None
    if win is None:
        return False
    try:
        win.destroy()
        return True
    except Exception:
        return False


def is_open():
    win = _center
    try:
        return bool(win is not None and win.winfo_exists())
    except Exception:
        return False
