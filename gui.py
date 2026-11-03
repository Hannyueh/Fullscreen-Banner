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
import sys
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageEnhance, ImageTk

# ---- 外观常量 ----
FONT_FAMILY = "Microsoft YaHei UI"      # theme.json 里的默认字体也是它
THEME_FILE = "theme.json"
# ---- 双主题色板（light, dark）：CustomTkinter 会随 appearance mode 自动切换 ----
PAPER = ("#EFECE5", "#0E1216")          # 窗口底色
PANEL = ("#F6F3EC", "#171C21")          # 面板
CARD = ("#FBF9F3", "#1A2127")           # 卡片 / 输入框
BG3 = ("#ECE8DF", "#1C2329")            # 次级面
INK = ("#0B0D0F", "#E9E8E1")            # 主文字
INK2 = ("#2E3438", "#C6CFD2")           # 正文
MUTED = ("#6E736E", "#8B989E")          # 次要文字
LINE = ("#C7C2B7", "#3C464C")           # 细边框
LINE2 = ("#A9A296", "#55636B")          # 强边框
ACCENT = ("#F27A22", "#FF8732")         # 主操作 / 警示
ACCENT_HOVER = ("#D9631A", "#E67122")
ACCENT_TEXT = ("#0B0D0F", "#111519")
ON_ACCENT = ACCENT_TEXT
CYAN = ("#2B9FBD", "#5BC2D8")           # 数据 / 正常
DANGER = ("#BE3630", "#F46458")         # 危险 / 退出

ACCENT_TEXT = ACCENT_TEXT
COLOR_TEXT = INK
COLOR_TEXT_DIM = MUTED
COLOR_TEXT_LABEL = INK2
COLOR_CARD = PANEL
COLOR_CARD_ALT = CARD
COLOR_LINE = LINE
COLOR_GHOST = BG3
COLOR_GHOST_HOVER = LINE2
COLOR_OK = CYAN
COLOR_OFF = LINE
COLOR_WARN = ACCENT
COLOR_DANGER = DANGER
COLOR_CANVAS = "#080B0E"                # 预览观测窗固定近黑
THEME_INDEX = {"light": 0, "dark": 1, "system": 2}
THEME_LABELS = {"浅色": "light", "深色": "dark", "跟随系统": "system"}

PREVIEW_W = 404        # 预览画布逻辑尺寸；实际按 DPI 缩放
PREVIEW_H = 252
PROJECT_URL = "https://github.com/Hannyueh/Fullscreen-Banner"
FIT_DESCRIPTIONS = {
    "cover": "铺满裁切：等比放大盖住整屏后居中裁切，无黑边",
    "contain": "完整显示：等比缩小到完整可见，可能留黑边",
    "fill": "拉伸铺满：直接拉伸到屏幕尺寸，会变形",
}

_theme_inited = False
_center = None          # 当前唯一控制中心窗口


# ===================== 主题 / 字体 =====================
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
    """在创建任何 CTk 窗口之前设定深色外观与中文字体主题。"""
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
            info += " · 未填充：透明（棋盘格示意，实际透出桌面）"
    else:
        rw = min(1440, max(960, pw * 3))
        rh = max(240, int(round(rw * mh / float(mw))))
        theme = _preview_theme(app)
        im = _preview_default_image(app, rw, rh)
        im = _fit_preview(im, pw, ph, "fill")
        info = "默认出图 · %s（%d×%d，按屏幕分辨率自动生成）" % (
            "深色" if theme == "dark" else "浅色", mw, mh)

    # 预览不叠加 OLED 亮度系数（避免浅色预览发灰；实际显示亮度见 LUM）
    canvas = Image.new("RGB", (bw, bh), (0, 0, 0))
    canvas.paste(im, ((bw - pw) // 2, (bh - ph) // 2))
    return canvas, info


# ===================== 控制中心窗口 =====================
class ControlCenter(ctk.CTkToplevel):
    """控制中心：状态 + 设置 + 实时预览。"""

    SWITCH_LABELS = {
        "oled_protect": "OLED 防烧屏",
        "hide_ime_bar": "横幅期间隐藏输入法状态条",
        "auto_generate": "自动按分辨率出图",
        "ask_before_generate": "出图前询问",
    }

    def __init__(self, master, app):
        super().__init__(master, fg_color=PAPER)
        self.app = app
        self._closing = False
        self._preview_seq = 0
        self._preview_pending = None
        self._preview_thread = None
        self._preview_queue = queue.Queue()
        self._preview_photo = None
        self._save_after_id = None
        self._pending_updates = {}
        self._sizes = []
        self._scale = self._detect_scale()
        self._canvas_w = int(round(PREVIEW_W * self._scale))
        self._canvas_h = int(round(PREVIEW_H * self._scale))
        self._last_chips = {}

        self.title("全屏提示 · 控制中心")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._init_geometry()
        self._set_icon()
        self.after(280, self._set_icon)      # CustomTkinter 会在 200ms 后改回自己的图标
        self.after(150, self._initial_refresh)

    # ---------- 基础工具 ----------
    def _detect_scale(self):
        try:
            return float(ctk.ScalingTracker.get_window_scaling(self) or 1.0)
        except Exception:
            return 1.0

    def _set_icon(self):
        try:
            self.iconbitmap(self.app.tray_icon_path())
        except Exception:
            pass

    def _init_geometry(self):
        """按当前屏幕逻辑尺寸给一个合适的初始大小（CTk 会按 DPI 缩放到物理像素）。"""
        self.update_idletasks()
        scale = self._scale
        try:
            sw = self.winfo_screenwidth() / scale
            sh = self.winfo_screenheight() / scale
        except Exception:
            sw, sh = 1280.0, 720.0
        w = int(min(1060, max(900, sw * 0.74)))
        h = int(min(820, max(600, sh * 0.80)))
        x = int(max(0, (sw - w) / 2))
        y = int(max(0, (sh - h) / 2 - 20))
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.minsize(900, 600)

    def _label(self, parent, text, size=12, color=COLOR_TEXT_LABEL, bold=False,
               wraplength=0, anchor="w", justify="left"):
        return ctk.CTkLabel(parent, text=text, font=_font(size, "bold" if bold else "normal"),
                            text_color=color, anchor=anchor, justify=justify,
                            wraplength=wraplength)

    def _button(self, parent, text, command, primary=False, width=112, height=32):
        if primary:
            return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                                 font=_font(13, "bold"), fg_color=ACCENT,
                                 hover_color=ACCENT_HOVER, text_color=ACCENT_TEXT,
                                 corner_radius=0, border_width=1, border_color=ACCENT)
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                             font=_font(12), fg_color=COLOR_GHOST,
                             hover_color=COLOR_GHOST_HOVER, text_color=COLOR_TEXT,
                             corner_radius=0, border_width=1, border_color=COLOR_LINE)

    def _entry(self, parent, row, label, var):
        self._label(parent, label, size=12).grid(row=row, column=0, sticky="w",
                                                 padx=(4, 12), pady=5)
        entry = ctk.CTkEntry(parent, textvariable=var, font=_font(12), height=32,
                             fg_color=COLOR_CARD_ALT, border_color=COLOR_LINE,
                             text_color=COLOR_TEXT)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        return entry

    def _section_title(self, parent, row, text):
        self._label(parent, text, size=13, color=COLOR_TEXT, bold=True).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(10, 2))

    def _cfg_text(self, key):
        cfg = self.app.CFG
        if key in ("title0", "title1"):
            vals = list(cfg.get("banner_title") or ["", ""])
            vals += [""] * (2 - len(vals))
            return str(vals[0 if key == "title0" else 1])
        if key in ("sub0", "sub1"):
            vals = list(cfg.get("banner_sub") or ["", ""])
            vals += [""] * (2 - len(vals))
            return str(vals[0 if key == "sub0" else 1])
        return str(cfg.get(key, ""))

    def _cfg_num(self, key):
        try:
            return float(self.app.CFG.get(key, 0))
        except (TypeError, ValueError):
            return 0.0

    def _set_status(self, text):
        """底部操作提示。"""
        try:
            self.footer_status.configure(text=str(text))
        except Exception:
            pass

    # ---------- 界面搭建 ----------
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_body()
        self._build_footer()
        self._build_tab_banner()
        self._build_tab_oled()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 6))
        header.grid_columnconfigure(1, weight=1)

        self._label(header, "全屏提示 · 控制中心", size=22, color=COLOR_TEXT,
                    bold=True).grid(row=0, column=0, sticky="w")
        self._label(header, "v%s · 托盘常驻 · OLED 防烧屏 / CONTROL CENTER" % self.app.VERSION,
                    size=11, color=COLOR_TEXT_DIM).grid(row=1, column=0, sticky="w", pady=(2, 0))

        right = ctk.CTkFrame(header, fg_color="transparent")
        right.grid(row=0, column=2, rowspan=2, sticky="e")
        chips = ctk.CTkFrame(right, fg_color="transparent")
        chips.pack(anchor="e")
        self.chip_banner = self._chip(chips, "横幅：--")
        self.chip_keyboard = self._chip(chips, "键盘：--")
        self.chip_screens = self._chip(chips, "显示器：--")

    def _chip(self, parent, text):
        holder = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=0,
                              border_width=1, border_color=COLOR_LINE)
        holder.pack(side="left", padx=(0, 8))
        lbl = self._label(holder, text, size=11, color=COLOR_TEXT)
        lbl.pack(padx=10, pady=4)
        return holder, lbl

    def _set_chip(self, chip, text, color):
        holder, lbl = chip
        key = id(chip)
        if self._last_chips.get(key) == (text, color):
            return
        self._last_chips[key] = (text, color)
        try:
            lbl.configure(text=text)
            holder.configure(border_color=color)
        except Exception:
            pass

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=18, pady=(4, 6))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(
            body, fg_color=COLOR_CARD, corner_radius=0, border_width=1,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=COLOR_GHOST,
            segmented_button_unselected_hover_color=COLOR_GHOST_HOVER,
            text_color=COLOR_TEXT, command=self._on_tab_change)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        for name in ("横幅", "息屏"):
            self.tabs.add(name)

        self._build_preview_panel(body)

    def _build_preview_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLOR_CARD, corner_radius=0,
                             border_width=1, border_color=COLOR_LINE,
                             width=440, height=560)
        panel.grid(row=0, column=1, sticky="ns")
        panel.grid_propagate(False)
        self.preview_panel = panel

        self._label(panel, "实时预览  /  PREVIEW", size=14, color=COLOR_TEXT, bold=True).pack(
            anchor="w", padx=14, pady=(12, 0))

        self.monitor_menu = ctk.CTkOptionMenu(
            panel, values=["--"], font=_font(11), command=self._on_monitor_change,
            fg_color=COLOR_GHOST, button_color=COLOR_LINE,
            button_hover_color=COLOR_GHOST_HOVER, text_color=COLOR_TEXT,
            dropdown_fg_color=COLOR_CARD_ALT, dropdown_text_color=COLOR_TEXT,
            dropdown_hover_color=COLOR_GHOST_HOVER)
        self.monitor_menu.pack(fill="x", padx=14, pady=(8, 8))

        self.preview_canvas = tk.Canvas(panel, width=self._canvas_w, height=self._canvas_h,
                                        bg=COLOR_CANVAS, highlightthickness=0, bd=0)
        self.preview_canvas.pack(padx=int(14 * self._scale), pady=(0, 8))

        self.preview_info = self._label(panel, "正在生成预览…", size=11,
                                        color=COLOR_TEXT_DIM, wraplength=400)
        self.preview_info.pack(fill="x", padx=14)

        self.lbl_out = self._label(panel, "", size=9, color=COLOR_TEXT_DIM, wraplength=400)
        self.lbl_out.pack(fill="x", padx=14, pady=(4, 0))

        self._label(panel, "预览按所选显示器的分辨率比例渲染；不叠加 OLED 亮度系数，实际显示亮度见 LUM。",
                    size=10, color=COLOR_TEXT_DIM, wraplength=400).pack(fill="x", padx=14,
                                                                       pady=(4, 10))

    def _build_footer(self):
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 12))

        row = ctk.CTkFrame(footer, fg_color="transparent")
        row.pack(fill="x")
        self.btn_toggle = self._button(row, "显示横幅", self._on_toggle,
                                       primary=True, width=126, height=38)
        self.btn_toggle.pack(side="left")
        self._button(row, "打开数据目录", self._on_open_data_dir, width=116).pack(
            side="left", padx=(10, 0))
        self._button(row, "打开日志", self._on_open_log, width=96).pack(side="left", padx=(10, 0))
        self.footer_status = self._label(row, "就绪", size=11, color=COLOR_TEXT_DIM)
        self.footer_status.pack(side="left", padx=(14, 0))
        exit_btn = ctk.CTkButton(row, text="退出程序", command=self._on_exit, width=96,
                                 height=38, font=_font(12), fg_color="transparent",
                                 hover_color=COLOR_GHOST, text_color=COLOR_DANGER,
                                 corner_radius=0, border_width=1, border_color=COLOR_DANGER)
        exit_btn.pack(side="right")

        meta = ctk.CTkFrame(footer, fg_color="transparent")
        meta.pack(fill="x", pady=(8, 0))
        self._label(meta, "全屏提示 v%s · 设置即时保存" % self.app.VERSION,
                    size=9, color=COLOR_TEXT_DIM).pack(side="left")
        self._label(meta, "CONTROL CENTER / 控制中心", size=9,
                    color=COLOR_TEXT_DIM).pack(side="right")

    # ---------- 页签：横幅 ----------
    def _build_tab_banner(self):
        tab = self.tabs.tab("横幅")
        wrap = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(1, weight=1)

        # 01 横幅来源（最上方）
        self._section_title(wrap, 0, "01 横幅来源 / SOURCE")
        src_box = ctk.CTkFrame(wrap, fg_color="transparent")
        src_box.grid(row=1, column=0, columnspan=2, sticky="w", padx=4, pady=2)
        self.source_var = tk.IntVar(value=0)
        for idx, label in enumerate(("自动生成横幅", "使用自定义图片")):
            ctk.CTkRadioButton(src_box, text=label, variable=self.source_var, value=idx,
                               font=_font(12), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                               border_color=COLOR_LINE, text_color=COLOR_TEXT_LABEL,
                               command=self._on_source_change).pack(side="left", padx=(0, 18))
        self.lbl_image_source = self._label(wrap, "—", size=11, color=COLOR_TEXT_DIM,
                                            wraplength=520)
        self.lbl_image_source.grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 4))

        # ---------- 生成器模式 ----------
        self.gen_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.gen_frame.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.gen_frame.grid_columnconfigure(1, weight=1)
        g = self.gen_frame
        self._section_title(g, 0, "02 横幅文案 / BANNER COPY")
        self.text_vars = {}
        specs = [("eyebrow", "顶部提示行", "banner_eyebrow"),
                 ("title0", "主标题 第一行", "title0"),
                 ("title1", "主标题 第二行", "title1"),
                 ("banner_plate", "警示牌文字", "banner_plate"),
                 ("sub0", "说明文字 第一行", "sub0"),
                 ("sub1", "说明文字 第二行", "sub1")]
        row = 1
        for key, label, cfg_key in specs:
            var = tk.StringVar(value=self._cfg_text(cfg_key))
            self.text_vars[key] = var
            self._entry(g, row, label, var)
            row += 1
        btns = ctk.CTkFrame(g, fg_color="transparent")
        btns.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4), padx=4)
        self._button(btns, "保存文案并重新出图", self._on_apply_text,
                     primary=True, width=168).pack(side="left")
        self._button(btns, "恢复默认文案", self._on_reset_text, width=112).pack(side="left", padx=(10, 0))
        self._button(btns, "重新出图", self._on_regen, width=96).pack(side="left", padx=(10, 0))
        row += 1
        self._section_title(g, row, "03 出图行为 / BANNER ENGINE")
        row += 1
        sw_box = ctk.CTkFrame(g, fg_color="transparent")
        sw_box.grid(row=row, column=0, columnspan=2, sticky="ew", padx=4, pady=2)
        self.sw_auto = ctk.CTkSwitch(sw_box, text="自动按分辨率出图", font=_font(12),
                                     text_color=COLOR_TEXT_LABEL, progress_color=ACCENT,
                                     button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                     command=lambda: self._on_switch("auto_generate", self.sw_auto))
        self.sw_auto.pack(anchor="w", pady=4)
        if bool(self.app.CFG.get("auto_generate", True)):
            self.sw_auto.select()
        self.sw_ask = ctk.CTkSwitch(sw_box, text="出图前先询问（关闭则直接生成）", font=_font(12),
                                    text_color=COLOR_TEXT_LABEL, progress_color=ACCENT,
                                    button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                    command=lambda: self._on_switch("ask_before_generate", self.sw_ask))
        self.sw_ask.pack(anchor="w", pady=4)
        if bool(self.app.CFG.get("ask_before_generate", True)):
            self.sw_ask.select()
        row += 1
        self._section_title(g, row, "04 横幅配色 / BANNER THEME")
        row += 1
        theme_box = ctk.CTkFrame(g, fg_color="transparent")
        theme_box.grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(2, 2))
        self.banner_theme_seg = ctk.CTkSegmentedButton(
            theme_box, values=["浅色", "深色"], command=self._on_banner_theme_seg,
            font=_font(12), height=30, corner_radius=0,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=COLOR_GHOST, unselected_hover_color=COLOR_GHOST_HOVER,
            text_color=COLOR_TEXT)
        self.banner_theme_seg.pack(anchor="w")
        row += 1
        self._label(g, "修改文案后会自动按当前显示器分辨率重新出图；\n" +
                    "横幅配色切换后会按新配色重画默认横幅。",
                    size=11, color=COLOR_TEXT_DIM, wraplength=520).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 4))

        # ---------- 图片模式 ----------
        self.img_frame = ctk.CTkFrame(wrap, fg_color="transparent")
        self.img_frame.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.img_frame.grid_columnconfigure(0, weight=1)
        f = self.img_frame
        src_btns = ctk.CTkFrame(f, fg_color="transparent")
        src_btns.grid(row=0, column=0, sticky="w", padx=4, pady=(4, 2))
        self._button(src_btns, "选择图片…", self._on_choose_image, width=110).pack(side="left")
        self._button(src_btns, "恢复默认出图", self._on_clear_image, width=118).pack(
            side="left", padx=(10, 0))
        self._section_title(f, 1, "02 适配方式 / FIT MODE")
        fit_box = ctk.CTkFrame(f, fg_color="transparent")
        fit_box.grid(row=2, column=0, sticky="w", padx=4, pady=2)
        self.fit_var = tk.IntVar(value=list(self.app.FIT_CHOICES).index(self.app.BANNER_FIT))
        self.fit_radios = []
        for idx, fit in enumerate(self.app.FIT_CHOICES):
            rb = ctk.CTkRadioButton(
                fit_box, text=FIT_DESCRIPTIONS.get(fit, self.app.FIT_LABELS.get(fit, fit)),
                variable=self.fit_var, value=idx, font=_font(12),
                fg_color=ACCENT, hover_color=ACCENT_HOVER, border_color=COLOR_LINE,
                text_color=COLOR_TEXT_LABEL, command=self._on_fit_change)
            rb.pack(anchor="w", pady=2)
            self.fit_radios.append(rb)
        self._section_title(f, 3, "03 未填充部分 / CONTAIN BACKGROUND（完整显示时）")
        fill_box = ctk.CTkFrame(f, fg_color="transparent")
        fill_box.grid(row=4, column=0, sticky="w", padx=4, pady=2)
        self.contain_fill_var = tk.IntVar(
            value=1 if str(self.app.CFG.get("contain_fill") or "black") == "transparent" else 0)
        for idx, label in enumerate(("黑边", "透明")):
            ctk.CTkRadioButton(fill_box, text=label, variable=self.contain_fill_var, value=idx,
                               font=_font(12), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                               border_color=COLOR_LINE, text_color=COLOR_TEXT_LABEL,
                               command=self._on_contain_fill).pack(side="left", padx=(0, 18))
        self._label(f, "「完整显示」时，图片以外的区域可选黑边或透明；选透明会透出桌面（\n" +
                       "需要窗口透明支持，预览用棋盘格示意）。",
                    size=10, color=COLOR_TEXT_DIM, wraplength=540).grid(
            row=5, column=0, sticky="w", padx=4, pady=(4, 8))

    def _on_source_change(self):
        want_custom = self.source_var.get() == 1
        has_custom = bool(self.app._user_image_path())
        if want_custom and not has_custom:
            self._on_choose_image()
            if not self.app._user_image_path():
                self.source_var.set(0)
        elif not want_custom and has_custom:
            self._on_clear_image()
        self._update_source_frames()

    def _update_source_frames(self):
        try:
            custom = self.source_var.get() == 1
            if custom:
                self.gen_frame.grid_remove()
                self.img_frame.grid()
            else:
                self.img_frame.grid_remove()
                self.gen_frame.grid()
        except Exception:
            pass

    def _on_contain_fill(self):
        fill = "transparent" if self.contain_fill_var.get() == 1 else "black"
        try:
            self.app._apply_settings({"contain_fill": fill})
        except Exception:
            self.app.log("设置未填充区域失败：\n" + traceback.format_exc())
            return
        self._sync_image_tab()
        self._request_preview()
        self._set_status("完整显示未填充部分：%s" % ("透明" if fill == "transparent" else "黑边"))

    # ---------- 页签：防烧屏 ----------
    def _build_tab_oled(self):
        tab = self.tabs.tab("息屏")
        wrap = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        sw_box = ctk.CTkFrame(wrap, fg_color="transparent")
        sw_box.grid(row=0, column=0, sticky="ew", padx=4, pady=(6, 2))
        self.sw_oled = ctk.CTkSwitch(sw_box, text="启用 OLED 防烧屏（微移 + 亮度 + 暗屏守护）",
                                     font=_font(12), text_color=COLOR_TEXT_LABEL,
                                     progress_color=ACCENT, button_color=ACCENT,
                                     button_hover_color=ACCENT_HOVER,
                                     command=lambda: self._on_switch("oled_protect", self.sw_oled))
        self.sw_oled.pack(anchor="w", pady=2)
        if bool(self.app.CFG.get("oled_protect", True)):
            self.sw_oled.select()

        row = 1
        self.sl_idle = self._slider(wrap, row, "无人操作多少秒后进入暗屏守护",
                                    "idle_to_guard", 10, 600, 590, "%d 秒")
        row += 2
        self.sl_shift = self._slider(wrap, row, "横幅微移间隔", "shift_interval",
                                     5, 120, 115, "%d 秒")
        row += 2
        self.sl_radius = self._slider(wrap, row, "微移半径", "shift_radius",
                                      0, 20, 20, "%d px")
        row += 2
        self.sl_bright = self._slider(wrap, row, "横幅亮度（越低越护屏）", "brightness",
                                      0.5, 1.0, 50, "%.2f", cast=float)
        row += 2
        self.sl_guard_move = self._slider(wrap, row, "暗屏守护小字移动间隔",
                                          "guard_move_interval", 3, 60, 57, "%d 秒")
        row += 2


        self._section_title(wrap, row, "暗屏守护文字 / GUARD TEXT")
        row += 1
        text_box = ctk.CTkFrame(wrap, fg_color="transparent")
        text_box.grid(row=row, column=0, sticky="ew", padx=4, pady=2)
        text_box.grid_columnconfigure(1, weight=1)
        self.var_guard_text = tk.StringVar(value=str(self.app.CFG.get("guard_text", "")))
        self.var_guard_color = tk.StringVar(value=str(self.app.CFG.get("guard_color", "#1a1a1a")))
        self._entry(text_box, 0, "显示文字", self.var_guard_text)
        self._entry(text_box, 1, "文字颜色（#RRGGBB）", self.var_guard_color)
        apply_btn = self._button(text_box, "应用文字", self._on_apply_guard_text,
                                 primary=True, width=96)
        apply_btn.grid(row=2, column=1, sticky="e", pady=(2, 4))
        row += 1

        self._label(wrap, "提示：暗屏守护时整屏变为纯黑，只留一行暗色小字；"
                          "动鼠标/按键立即恢复横幅。更多字体参数可在 banner.json 中调整。",
                    size=11, color=COLOR_TEXT_DIM, wraplength=520).grid(
            row=row, column=0, sticky="w", padx=4, pady=(8, 6))
        row += 1
        self._section_title(wrap, row, "系统 / SYSTEM")
        row += 1
        self.sw_startup = ctk.CTkSwitch(wrap, text="开机启动（当前用户，无需管理员权限）",
                                        font=_font(12), text_color=COLOR_TEXT_LABEL,
                                        progress_color=ACCENT, button_color=ACCENT,
                                        button_hover_color=ACCENT_HOVER,
                                        command=self._on_startup_toggle)
        self.sw_startup.grid(row=row, column=0, sticky="w", padx=4, pady=(2, 6))
        try:
            if self.app.is_startup_enabled():
                self.sw_startup.select()
        except Exception:
            pass

    def _slider(self, parent, row, label, key, from_, to, steps, fmt, cast=int):
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=row, column=0, sticky="ew", padx=4, pady=(8, 0))
        head.grid_columnconfigure(0, weight=1)
        self._label(head, label, size=12).grid(row=0, column=0, sticky="w")
        value = self._cfg_num(key)
        val_lbl = self._label(head, fmt % value, size=12, color=ACCENT, bold=True, anchor="e")
        val_lbl.grid(row=0, column=1, sticky="e")

        slider = ctk.CTkSlider(parent, from_=from_, to=to, number_of_steps=steps,
                               height=18, fg_color=COLOR_LINE, progress_color=ACCENT,
                               button_color=ACCENT, button_hover_color=ACCENT_HOVER)
        slider.set(value)
        slider.grid(row=row + 1, column=0, sticky="ew", padx=4, pady=(2, 4))

        def _changed(v):
            val_lbl.configure(text=fmt % v)
            converted = round(float(v), 2) if cast is float else int(round(float(v)))
            self._schedule_apply({key: converted})

        slider.configure(command=_changed)
        return slider

    # ---------- 页签：图片 ----------
    def _build_tab_image(self):
        tab = self.tabs.tab("03 图片")
        wrap = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        self._section_title(wrap, 0, "01 当前横幅来源 / SOURCE")
        self.lbl_image_source = self._label(wrap, "—", size=12, color=COLOR_TEXT,
                                            wraplength=520)
        self.lbl_image_source.grid(row=1, column=0, sticky="w", padx=4, pady=(2, 6))

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.grid(row=2, column=0, sticky="w", pady=4, padx=4)
        self._button(btns, "选择自定义图片…", self._on_choose_image,
                     primary=True, width=150).pack(side="left")
        self._button(btns, "恢复默认出图", self._on_clear_image, width=120).pack(
            side="left", padx=(10, 0))

        self._section_title(wrap, 3, "02 适配方式 / FIT MODE（图与屏幕比例不一致时）")
        fit_box = ctk.CTkFrame(wrap, fg_color="transparent")
        fit_box.grid(row=4, column=0, sticky="ew", padx=4, pady=2)
        self.fit_var = tk.IntVar(value=list(self.app.FIT_CHOICES).index(self.app.BANNER_FIT))
        self.fit_radios = []
        for idx, fit in enumerate(self.app.FIT_CHOICES):
            rb = ctk.CTkRadioButton(
                fit_box, text=FIT_DESCRIPTIONS.get(fit, self.app.FIT_LABELS.get(fit, fit)),
                variable=self.fit_var, value=idx, font=_font(12),
                text_color=COLOR_TEXT_LABEL, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                border_color=COLOR_LINE, radiobutton_width=20, radiobutton_height=20,
                command=self._on_fit_change)
            rb.pack(anchor="w", pady=3)
            self.fit_radios.append(rb)

        hint = ("支持 PNG / JPG / WebP / BMP / GIF 首帧 / AVIF / TIFF 等（以 Pillow 能否打开为准）。"
                "所有显示器共用同一张图，但每块屏按各自分辨率适配；"
                "首次使用会生成缓存，之后秒开。也可以把图片命名为 banner.png 放进数据目录自动识别。")
        self._label(wrap, hint, size=11, color=COLOR_TEXT_DIM, wraplength=520).grid(
            row=5, column=0, sticky="w", padx=4, pady=(10, 6))

    # ---------- 页签：高级 ----------
    def _build_tab_advanced(self):
        tab = self.tabs.tab("04 高级")
        wrap = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        self.sw_startup = ctk.CTkSwitch(wrap, text="开机启动（当前用户，无需管理员权限）",
                                        font=_font(12), text_color=COLOR_TEXT_LABEL,
                                        progress_color=ACCENT, button_color=ACCENT,
                                        button_hover_color=ACCENT_HOVER,
                                        command=self._on_startup_toggle)
        self.sw_startup.grid(row=0, column=0, sticky="w", padx=4, pady=(6, 2))
        try:
            if self.app.is_startup_enabled():
                self.sw_startup.select()
        except Exception:
            self.app.log("读取开机启动状态失败:\n" + traceback.format_exc())

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.grid(row=1, column=0, sticky="w", padx=4, pady=(8, 2))
        self._button(btns, "强制解除键盘屏蔽", self._on_force_unlock, width=150).pack(side="left")
        self._button(btns, "打开日志文件", self._on_open_log, width=116).pack(side="left", padx=(10, 0))
        self._button(btns, "打开数据目录", self._on_open_data_dir, width=116).pack(
            side="left", padx=(10, 0))

        self._section_title(wrap, 2, "屏幕与图片信息 / DISPLAY & IMAGE")
        self.txt_screen = ctk.CTkTextbox(wrap, height=120, font=_font(11),
                                         fg_color=COLOR_CARD_ALT, border_color=COLOR_LINE,
                                         border_width=1, text_color=COLOR_TEXT,
                                         wrap="word")
        self.txt_screen.grid(row=3, column=0, sticky="ew", padx=4, pady=(2, 6))

        paths = ("数据目录：%s\n配置文件：%s\n日志文件：%s"
                 % (self.app.DATA_DIR, self.app.CONFIG_PATH, self.app.LOG_PATH))
        self._label(wrap, paths, size=10, color=COLOR_TEXT_DIM, wraplength=540).grid(
            row=4, column=0, sticky="w", padx=4, pady=(4, 6))


    # ---------- 页签：关于 ----------
    def _build_tab_about(self):
        tab = self.tabs.tab("05 关于")
        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        self._label(wrap, "全屏提示 · 控制中心", size=18, color=COLOR_TEXT, bold=True).grid(
            row=0, column=0, sticky="w", padx=8, pady=(10, 2))

        self.lbl_about = self._label(wrap, "", size=11, color=COLOR_TEXT_DIM, wraplength=540)
        self.lbl_about.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        desc = ("托盘常驻的小工具：按一下托盘图标，所有显示器铺满「请勿触动」横幅并屏蔽键盘，"
                "长时间无人操作自动进入纯黑暗屏守护，避免 OLED 烧屏；"
                "支持自定义横幅图片与三种适配方式。")
        self._label(wrap, desc, size=12, color=COLOR_TEXT_LABEL, wraplength=540).grid(
            row=2, column=0, sticky="w", padx=8, pady=(0, 10))

        self._label(wrap, "本程序基于 MIT 许可发布；图标与默认横幅为本项目自制。",
                    size=11, color=COLOR_TEXT_DIM, wraplength=540).grid(
            row=3, column=0, sticky="w", padx=8, pady=(0, 10))

        btns = ctk.CTkFrame(wrap, fg_color="transparent")
        btns.grid(row=4, column=0, sticky="w", padx=8, pady=4)
        self._button(btns, "打开项目主页", self._on_open_project, width=124).pack(side="left")
        self._button(btns, "打开数据目录", self._on_open_data_dir, width=116).pack(
            side="left", padx=(10, 0))

        self._label(wrap, "快捷键：横幅显示时 Esc 关闭；Ctrl+Alt+Shift+Esc 强制解除键盘屏蔽。",
                    size=11, color=COLOR_TEXT_DIM, wraplength=540).grid(
            row=5, column=0, sticky="w", padx=8, pady=(12, 6))

    # ---------- 状态刷新 ----------
    def _initial_refresh(self):
        if self._closing:
            return
        try:
            self._refresh_status()
            self._sync_banner_theme()
            self._sync_image_tab()
            self._refresh_advanced()
            self._refresh_about()
            self._update_out_label()
            self._request_preview()
        except Exception:
            self.app.log("控制中心初始化刷新异常:\n" + traceback.format_exc())

    def _refresh_status(self):
        app = self.app
        try:
            visible = bool(app.banner_windows)
            keyboard = "已屏蔽" if app.keyboard_blocked else (
                "钩子不可用" if app.hook_failed else "未屏蔽")
            monitors = app.get_monitors()
        except Exception:
            return

        self._set_chip(self.chip_banner,
                       "横幅：" + ("显示中" if visible else "未显示"),
                       COLOR_OK if visible else COLOR_OFF)
        kb_color = COLOR_OK if app.keyboard_blocked else (
            COLOR_DANGER if app.hook_failed else COLOR_OFF)
        self._set_chip(self.chip_keyboard, "键盘：" + keyboard, kb_color)
        self._set_chip(self.chip_screens, "显示器：%d 屏" % len(monitors), COLOR_OFF)

        try:
            self.btn_toggle.configure(text="关闭横幅" if visible else "显示横幅")
        except Exception:
            pass

        sizes = []
        for (left, top, right, bottom) in monitors:
            size = (right - left, bottom - top)
            if size not in sizes:
                sizes.append(size)
        if sizes != self._sizes:
            self._sizes = sizes
            values = ["屏幕 %d · %d×%d" % (i + 1, w, h) for i, (w, h) in enumerate(sizes)]
            if not values:
                values = ["未检测到显示器"]
            try:
                self.monitor_menu.configure(values=values)
                self.monitor_menu.set(values[0])
            except Exception:
                pass
            self._request_preview()

    def _selected_monitor(self):
        if not self._sizes:
            return 0
        try:
            text = self.monitor_menu.get()
            for i in range(len(self._sizes)):
                if text.startswith("屏幕 %d " % (i + 1)):
                    return i
        except Exception:
            pass
        return 0

    def _on_monitor_change(self, _value=None):
        self._request_preview()

    def _sync_image_tab(self):
        if hasattr(self, "source_var"):
            try:
                self.source_var.set(1 if self.app._user_image_path() else 0)
                if hasattr(self, "contain_fill_var"):
                    self.contain_fill_var.set(
                        1 if str(self.app.CFG.get("contain_fill") or "black") == "transparent" else 0)
            except Exception:
                pass
        app = self.app
        try:
            src = app._user_image_path()
        except Exception:
            src = ""
        if src:
            text = "自定义图片：%s" % src
        else:
            text = ("默认出图：由程序按每块屏幕的分辨率自动生成（PC 风格横幅）")
            dropin = getattr(app, "_dropin_image_path", None)
            if dropin:
                try:
                    extra = dropin()
                except Exception:
                    extra = None
                if extra:
                    text += "\n检测到固定目录投图：%s" % extra
        try:
            self.lbl_image_source.configure(text=text)
        except Exception:
            pass
        self._update_out_label()
        self._update_source_frames()
        state = "normal" if src else "disabled"
        try:
            for rb in self.fit_radios:
                rb.configure(state=state)
            self.fit_var.set(list(app.FIT_CHOICES).index(app.BANNER_FIT))
        except Exception:
            pass
        self._refresh_advanced()

    def _refresh_advanced(self):
        try:
            info = self.app._screen_info_text()
        except Exception:
            info = "读取屏幕信息失败，详情见日志"
        try:
            self.txt_screen.configure(state="normal")
            self.txt_screen.delete("1.0", "end")
            self.txt_screen.insert("1.0", info)
            self.txt_screen.configure(state="disabled")
        except Exception:
            pass
        try:
            if self.app.is_startup_enabled():
                self.sw_startup.select()
            else:
                self.sw_startup.deselect()
        except Exception:
            pass

    def _refresh_about(self):
        try:
            import customtkinter as _ctk
            from PIL import __version__ as pil_version
            text = ("版本：v%s\nPython：%s\nPillow：%s\nCustomTkinter：%s\n运行模式：%s"
                    % (self.app.VERSION, sys.version.split()[0], pil_version,
                       getattr(_ctk, "__version__", "?"),
                       "打包 exe" if getattr(sys, "frozen", False) else "源码脚本"))
        except Exception:
            text = "版本：v%s" % self.app.VERSION
        try:
            self.lbl_about.configure(text=text)
        except Exception:
            pass

    # ---------- 预览 ----------
    def _request_preview(self):
        if self._closing:
            return
        if not self._sizes:
            try:
                self._sizes = self.app._monitor_sizes()
            except Exception:
                self._sizes = []
        if not self._sizes:
            return
        idx = min(self._selected_monitor(), len(self._sizes) - 1)
        mw, mh = self._sizes[idx]
        self._preview_seq += 1
        self._preview_pending = (self._preview_seq, (mw, mh,
                                                     self._canvas_w, self._canvas_h))
        try:
            if self._preview_thread is None or not self._preview_thread.is_alive():
                self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
                self._preview_thread.start()
        except Exception:
            pass

    def _preview_loop(self):
        while not self._closing and self._preview_pending is not None:
            seq, params = self._preview_pending
            self._preview_pending = None
            mw, mh, bw, bh = params
            try:
                image, info = build_preview_image(self.app, mw, mh, bw, bh)
            except Exception:
                self.app.log("生成预览图失败:\n" + traceback.format_exc())
                image, info = None, "预览生成失败，详情见日志"
            self._preview_queue.put((seq, image, info))

    def _drain_preview(self):
        while True:
            try:
                seq, image, info = self._preview_queue.get_nowait()
            except queue.Empty:
                break
            if seq != self._preview_seq or image is None or self._closing:
                continue
            try:
                photo = ImageTk.PhotoImage(image)
                self._preview_photo = photo
                self.preview_canvas.delete("all")
                self.preview_canvas.create_image(0, 0, anchor="nw", image=photo)
                self.preview_info.configure(text=info)
                self._update_out_label()
            except Exception:
                self.app.log("显示预览图失败:\n" + traceback.format_exc())

    def _tick(self):
        if self._closing or not self.winfo_exists():
            return
        if getattr(self.app, "_quitting", False):
            return
        try:
            self._drain_preview()
            if self.winfo_viewable():
                self._refresh_status()
        except Exception:
            self.app.log("控制中心刷新异常:\n" + traceback.format_exc())
        delay = 700 if self.winfo_viewable() else 1500
        try:
            self.after(delay, self._tick)
        except Exception:
            pass

    # ---------- 设置应用 ----------
    def _apply(self, updates, note=None):
        try:
            ok = self.app._apply_settings(updates)
        except Exception:
            self.app.log("应用设置异常：\n" + traceback.format_exc())
            self._set_status("应用设置失败，详情见日志")
            return False
        if not ok:
            self._set_status("没有可应用的设置")
            return False
        self._sync_image_tab()
        self._refresh_status()
        self._request_preview()
        if note:
            self._set_status(note)
        return True

    def _schedule_apply(self, updates):
        self._pending_updates.update(updates)
        if self._save_after_id is not None:
            try:
                self.after_cancel(self._save_after_id)
            except Exception:
                pass
        self._save_after_id = self.after(450, self._flush_apply)

    def _flush_apply(self):
        self._save_after_id = None
        updates = self._pending_updates
        self._pending_updates = {}
        if updates:
            self._apply(updates, note="设置已保存并生效")

    def _on_switch(self, key, widget):
        try:
            value = bool(widget.get())
        except Exception:
            value = False
        label = self.SWITCH_LABELS.get(key, key)
        self._apply({key: value},
                    note="%s：%s" % (label, "已开启" if value else "已关闭"))

    def _trigger_regenerate(self, note):
        app = self.app
        try:
            if app._user_image_path():
                self._set_status(note + "（当前使用自定义图片，默认出图被跳过）")
                return
            sizes = app._monitor_sizes()
            if not sizes:
                self._set_status("未检测到显示器")
                return
            app._start_generation(sizes)
            self._set_status(note)
        except Exception:
            app.log("触发重新出图失败：\n" + traceback.format_exc())
            self._set_status("重新出图失败，详情见日志")

    # ---------- 横幅页事件 ----------
    def _on_apply_text(self):
        updates = {
            "banner_eyebrow": self.text_vars["eyebrow"].get().strip(),
            "banner_title": [self.text_vars["title0"].get().strip(),
                             self.text_vars["title1"].get().strip()],
            "banner_plate": self.text_vars["banner_plate"].get().strip(),
            "banner_sub": [self.text_vars["sub0"].get().strip(),
                           self.text_vars["sub1"].get().strip()],
        }
        if not updates["banner_title"][0] and not updates["banner_title"][1]:
            self._set_status("主标题至少填一行")
            return
        if self._apply(updates, note="文案已保存"):
            self._trigger_regenerate("正在按当前分辨率重新出图…")

    def _on_reset_text(self):
        default = self.app.DEFAULT_CONFIG
        for key, cfg_key in (("eyebrow", "banner_eyebrow"),
                             ("title0", "banner_title"), ("title1", "banner_title"),
                             ("banner_plate", "banner_plate"),
                             ("sub0", "banner_sub"), ("sub1", "banner_sub")):
            if cfg_key == "banner_title":
                idx = 0 if key == "title0" else 1
                value = str((default.get("banner_title") or ["", ""])[idx])
            elif cfg_key == "banner_sub":
                idx = 0 if key == "sub0" else 1
                value = str((default.get("banner_sub") or ["", ""])[idx])
            else:
                value = str(default.get(cfg_key, ""))
            self.text_vars[key].set(value)
        self._on_apply_text()

    # ---------- 防烧屏页事件 ----------
    def _on_apply_guard_text(self):
        text = self.var_guard_text.get().strip()
        color = self.var_guard_color.get().strip()
        if color and not (len(color) == 7 and color.startswith("#")):
            self._set_status("颜色格式应为 #RRGGBB，例如 #1a1a1a")
            return
        updates = {"guard_text": text}
        if color:
            updates["guard_color"] = color
        self._apply(updates, note="暗屏守护文字已更新")

    # ---------- 图片页事件 ----------
    def _on_fit_change(self):
        idx = self.fit_var.get()
        fit = self.app.FIT_CHOICES[idx]
        if fit == self.app.BANNER_FIT:
            return
        try:
            self.app._set_fit(fit)
        except Exception:
            self.app.log("切换适配方式失败：\n" + traceback.format_exc())
            self._set_status("切换适配方式失败，详情见日志")
            return
        self._sync_image_tab()
        self._refresh_status()
        self._request_preview()
        self._set_status("适配方式：%s" % self.app.FIT_LABELS.get(fit, fit))

    def _on_choose_image(self):
        app = self.app
        was_visible = bool(app.banner_windows)
        if was_visible:
            app.hide_banner()
        path = ""
        try:
            path = filedialog.askopenfilename(
                parent=self,
                title="选择横幅图片（任意尺寸，按适配方式自动缩放）",
                initialdir=(os.path.dirname(app.BANNER_IMAGE)
                            if app.BANNER_IMAGE and os.path.isdir(os.path.dirname(app.BANNER_IMAGE))
                            else os.path.expanduser("~")),
                filetypes=[("图片文件", " ".join("*" + e for e in app.USER_IMAGE_EXTS)),
                           ("所有文件", "*.*")])
        except Exception:
            app.log("打开图片选择框失败：\n" + traceback.format_exc())
        if path:
            try:
                app._apply_custom_image(path)
                self._set_status("已应用自定义图片：%s" % os.path.basename(path))
            except Exception:
                app.log("应用自定义图片失败：\n" + traceback.format_exc())
                self._set_status("应用自定义图片失败，详情见日志")
        if was_visible:
            app.show_banner()
        self._sync_image_tab()
        self._refresh_status()
        self._request_preview()

    def _on_clear_image(self):
        try:
            self.app._clear_custom_image()
            self._set_status("已恢复默认出图")
        except Exception:
            self.app.log("恢复默认出图失败：\n" + traceback.format_exc())
            self._set_status("恢复默认出图失败，详情见日志")
        self._sync_image_tab()
        self._refresh_status()
        self._request_preview()

    # ---------- 高级页事件 ----------
    def _on_startup_toggle(self):
        enabled = bool(self.sw_startup.get())
        try:
            self.app.set_startup(enabled)
            self._set_status("开机启动已%s" % ("开启" if enabled else "关闭"))
            self.app.log("控制中心：开机启动已%s" % ("开启" if enabled else "关闭"))
        except Exception:
            self.app.log("切换开机启动失败：\n" + traceback.format_exc())
            self._set_status("切换开机启动失败，详情见日志")
            try:
                if self.app.is_startup_enabled():
                    self.sw_startup.select()
                else:
                    self.sw_startup.deselect()
            except Exception:
                pass

    def _on_force_unlock(self):
        try:
            self.app.commands.put(("force_unlock",))
            self._set_status("已请求强制解除键盘屏蔽")
        except Exception:
            self._set_status("发送命令失败，详情见日志")

    # ---------- 底部按钮 ----------
    def _on_toggle(self):
        try:
            self.app.toggle_banner()
        except Exception:
            self.app.log("切换横幅失败：\n" + traceback.format_exc())
            self._set_status("切换横幅失败，详情见日志")
            return
        self._set_status("已显示横幅" if self.app.banner_windows else "已关闭横幅")
        self._refresh_status()

    def _on_regen(self):
        if self.app._user_image_path():
            self._set_status("当前使用自定义图片；先在「图片」页恢复默认出图再重新生成")
            return
        self._trigger_regenerate("正在按当前分辨率重新出图（约 2–3 秒）…")

    def _on_exit(self):
        """退出程序（先确认）。"""
        try:
            ok = messagebox.askyesno(
                "退出程序",
                "确认退出「全屏提示」？\n退出后托盘图标消失，横幅也会关闭。",
                parent=self)
        except Exception:
            ok = True
        if not ok:
            return
        try:
            self.app.commands.put(("exit",))
            self._set_status("正在退出…")
        except Exception:
            self.app.log("发送退出命令失败：\n" + traceback.format_exc())
            self._set_status("退出失败，详情见日志")

    def _on_open_data_dir(self):
        self._open_path(self.app.DATA_DIR)

    def _on_open_log(self):
        path = self.app.LOG_PATH
        if os.path.isfile(path):
            self._open_path(path)
        else:
            self._open_path(self.app.DATA_DIR)

    def _on_open_project(self):
        try:
            webbrowser.open(PROJECT_URL)
            self._set_status("已在浏览器打开项目主页")
        except Exception:
            self._set_status("打开浏览器失败")

    def _open_path(self, path):
        try:
            os.startfile(path)     # noqa: 仅 Windows
            self._set_status("已打开：%s" % path)
        except Exception:
            self.app.log("打开路径失败 %s：\n%s" % (path, traceback.format_exc()))
            self._set_status("打开失败：%s" % path)

    def _on_banner_theme_seg(self, value):
        self._set_banner_theme("dark" if value == "深色" else "light")

    def _set_banner_theme(self, mode):
        """切换默认横幅配色（浅色/深色）：持久化并按新配色重画。"""
        mode = "dark" if str(mode).lower() == "dark" else "light"
        ok = False
        try:
            ok = self.app._apply_settings({"banner_theme": mode})
        except Exception:
            self.app.log("切换横幅配色失败：\n" + traceback.format_exc())
        self._sync_banner_theme()
        self._update_out_label()
        self._request_preview()
        self._set_status("横幅配色：%s%s" % ("深色" if mode == "dark" else "浅色",
                                          "（正在重画默认横幅…）" if ok else ""))
        if ok:
            self.app.log("横幅配色切换为 %s" % mode)

    def _sync_banner_theme(self):
        try:
            mode = self.app.effective_theme()
            if hasattr(self, "banner_theme_seg"):
                self.banner_theme_seg.set("深色" if mode == "dark" else "浅色")
        except Exception:
            pass

    def _update_out_label(self):
        try:
            app = self.app
            src = app._user_image_path()
            if src:
                src_txt = "自定义图片 · %s" % os.path.basename(src)
                fit = app.FIT_LABELS.get(app.BANNER_FIT, app.BANNER_FIT)
            else:
                src_txt = "默认出图 · 按分辨率生成"
                fit = "AUTO"
            theme = _preview_theme(app)
            self.lbl_out.configure(text="SOURCE %s   FIT %s   LUM %.2f   THEME %s" % (
                src_txt, fit, app.BANNER_BRIGHTNESS, "深色" if theme == "dark" else "浅色"))
        except Exception:
            pass

    def _on_tab_change(self, name=None):
        if not name:
            try:
                name = self.tabs.get()
            except Exception:
                return
        if "横幅" in name:
            self._sync_image_tab()

    # ---------- 生命周期 ----------
    def _on_close(self):
        """关闭窗口 = 隐藏到托盘，不退出程序。"""
        try:
            self.withdraw()
            self.app.log("控制中心已隐藏（程序继续在托盘运行）")
        except Exception:
            pass

    def destroy(self):
        self._closing = True
        try:
            super().destroy()
        except Exception:
            pass


# ===================== 对外接口 =====================
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
