# -*- coding: utf-8 -*-
"""档案风格「请勿触动」横幅生成器（浅色 / 深色双主题）。

设计基准 1920×1080，按目标分辨率等比缩放并居中；任意比例（16:9 / 21:9 /
32:9 / 竖屏）都不会拉伸字形。所有元素用 Pillow 几何绘制，不依赖外部素材，
字体使用 Windows 自带的 微软雅黑 / Consolas / Bahnschrift。

用法（兼容旧 CLI）：
    python make_banner.py out.png 2560x1440 [--title 此计算机 正在运行任务]
        [--plate "请 勿 触 动"] [--sub 第一行 第二行] [--theme light|dark]
"""
import argparse
import math
import os

from PIL import Image, ImageDraw, ImageFont

DESIGN_W, DESIGN_H = 1920, 1080
PAD = 120                    # 内容左右内边距（设计坐标）
FRAME = 40                   # 纸张外框内边距
LEFT_W = 1190                # 左栏宽度
PANEL_W = 430                # 右栏宽度
PANEL_X = PAD + LEFT_W + 60  # 右栏起始 x

DEFAULT_TEXT = {
    "title": ["此计算机", "正在运行任务"],
    "plate": "请 勿 触 动",
    "sub": ["任务执行中，触碰键盘或鼠标可能导致数据丢失或系统异常",
            "感谢你的理解与配合 —— 让它安静地跑完吧。"],
    "eyebrow": "正在运行任务 / TASK RUNNING",
}

# ---------------- 双主题色板 ----------------
PALETTES = {
    "light": dict(
        paper=(239, 236, 229), panel=(247, 244, 238), card=(251, 249, 243),
        ink=(11, 13, 15), ink2=(46, 52, 56), muted=(110, 115, 110),
        line=(199, 194, 183), line2=(169, 162, 150),
        accent=(242, 122, 34), accent_soft=(248, 226, 206), on_accent=(11, 13, 15),
        cyan=(43, 159, 189), red=(190, 54, 48),
    ),
    "dark": dict(
        paper=(14, 18, 22), panel=(23, 28, 33), card=(26, 33, 40),
        ink=(233, 232, 225), ink2=(198, 207, 210), muted=(139, 152, 158),
        line=(60, 70, 76), line2=(82, 94, 101),
        accent=(255, 135, 50), accent_soft=(48, 33, 22), on_accent=(18, 22, 26),
        cyan=(91, 194, 216), red=(244, 100, 88),
    ),
}

_FONT_CACHE = {}
_FONT_CANDIDATES = {
    "cn_bold": ["msyhbd.ttc", "msyh.ttc", "simhei.ttf"],
    "cn": ["msyh.ttc", "simhei.ttf"],
    "mono": ["consola.ttf", "cour.ttf"],
    "mono_bold": ["consolab.ttf", "consola.ttf", "cour.ttf"],
    "display": ["bahnschrift.ttf", "msyhbd.ttc"],
}


def _font(kind, size):
    size = max(6, int(round(size)))
    key = (kind, size)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _FONT_CANDIDATES.get(kind, []):
        path = os.path.join(fdir, name)
        if not os.path.isfile(path):
            continue
        try:
            f = ImageFont.truetype(path, size)
            if kind == "display":
                try:
                    f.set_variation_by_name("SemiBold")
                except Exception:
                    pass
            _FONT_CACHE[key] = f
            return f
        except Exception:
            continue
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


def _mix(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _hex_points(cx, cy, r):
    return [(cx + r * math.cos(math.radians(-90 + 60 * i)),
             cy + r * math.sin(math.radians(-90 + 60 * i))) for i in range(6)]


def _chamfer_pts(x0, y0, x1, y1, c):
    """切角矩形（档案卡片 / 工程图框）。"""
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c),
            (x1 - c, y1), (x0 + c, y1), (x0, y1 - c), (x0, y0 + c)]


def _draw_hex(d, cx, cy, r, color, width=1, fill=None):
    pts = _hex_points(cx, cy, r)
    if fill:
        d.polygon(pts, fill=fill)
    if width:
        d.line(pts + [pts[0]], fill=color, width=max(1, int(round(width))))


def _hex_dashed(d, cx, cy, r, color, width, dash=8.0, gap=6.0):
    """虚线六边形（工程制图 / 全息轮廓用）。"""
    pts = _hex_points(cx, cy, r)
    for i in range(6):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % 6]
        length = math.hypot(x2 - x1, y2 - y1)
        if length <= 0:
            continue
        ux, uy = (x2 - x1) / length, (y2 - y1) / length
        pos = 0.0
        while pos < length:
            end = min(pos + dash, length)
            d.line([(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)],
                   fill=color, width=width)
            pos = end + gap


def _merge_text(text):
    txt = dict(DEFAULT_TEXT)
    if isinstance(text, dict):
        for key in txt:
            if key in text and text[key] not in (None, ""):
                txt[key] = text[key]
    titles = [str(x) for x in (txt.get("title") or [])][:2]
    sub = [str(x) for x in (txt.get("sub") or [])][:2]
    while len(titles) < 2:
        titles.append("")
    while len(sub) < 2:
        sub.append("")
    return {
        "eyebrow": str(txt.get("eyebrow") or DEFAULT_TEXT["eyebrow"]),
        "title": titles,
        "plate": str(txt.get("plate") or ""),
        "sub": sub,
    }


def _fit_font(draw, text, kind, start_size, max_width, min_size=12):
    size = start_size
    while size > min_size:
        f = _font(kind, size)
        try:
            w = draw.textlength(text, font=f)
        except Exception:
            w = 0
        if w <= max_width:
            return f, size
        size = int(size * 0.96)
    return _font(kind, min_size), min_size


def _text(d, xy, s, font, fill, anchor="la"):
    if s:
        d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _has_cjk(s):
    return any(ord(ch) > 127 for ch in str(s))


def _auto_font(s, kind, size):
    """含中文的字符串必须用中文字体（Consolas 没有中文字形）。"""
    return _font("cn" if _has_cjk(s) else kind, size)


def render(out_path, width, height, text=None, theme="light"):
    """生成一张 width×height 的档案风横幅并保存到 out_path；返回 out_path。"""
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("invalid size %r x %r" % (width, height))
    pal = PALETTES.get(theme, PALETTES["light"])
    txt = _merge_text(text)

    k = min(width / float(DESIGN_W), height / float(DESIGN_H))
    cw, ch = DESIGN_W * k, DESIGN_H * k
    ox, oy = (width - cw) / 2.0, (height - ch) / 2.0

    def X(v):
        return ox + v * k

    def Y(v):
        return oy + v * k

    def S(v):
        return max(1, int(round(v * k)))

    img = Image.new("RGB", (width, height), pal["paper"])
    d = ImageDraw.Draw(img)

    # ---------- 背景花纹（低对比，不干扰文字） ----------
    grid_c = _mix(pal["paper"], pal["ink"], 0.045)
    lat_c = _mix(pal["paper"], pal["ink"], 0.03)
    step = 120 * k
    xx = ox
    while xx <= ox + cw + 1:
        d.line([(xx, oy), (xx, oy + ch)], fill=grid_c, width=1)
        xx += step
    yy = oy
    while yy <= oy + ch + 1:
        d.line([(ox, yy), (ox + cw, yy)], fill=grid_c, width=1)
        yy += step
    off = -ch
    while off <= (cw + ch) * 1.2:
        d.line([(ox + off, oy + ch), (ox + off + ch, oy)], fill=lat_c, width=1)
        d.line([(ox + off, oy), (ox + off + ch, oy + ch)], fill=lat_c, width=1)
        off += 47 * k * 1.73
    _draw_hex(d, X(560), Y(430), 420 * k, _mix(pal["paper"], pal["ink"], 0.05), S(1.2))
    _draw_hex(d, X(560), Y(430), 350 * k, _mix(pal["paper"], pal["ink"], 0.04), S(1.0))
    _draw_hex(d, X(560), Y(430), 280 * k, _mix(pal["paper"], pal["accent"], 0.10), S(1.0))

    # ---------- 纸张外框 + 角标 ----------
    d.rectangle([X(FRAME), Y(FRAME), X(DESIGN_W - FRAME), Y(DESIGN_H - FRAME)],
                outline=pal["line2"], width=S(1))
    for (cx, cy, sx, sy) in ((FRAME, FRAME, 1, 1), (DESIGN_W - FRAME, FRAME, -1, 1),
                             (FRAME, DESIGN_H - FRAME, 1, -1),
                             (DESIGN_W - FRAME, DESIGN_H - FRAME, -1, -1)):
        d.line([(X(cx), Y(cy)), (X(cx + 22 * sx), Y(cy))], fill=pal["ink"], width=S(1))
        d.line([(X(cx), Y(cy)), (X(cx), Y(cy + 22 * sy))], fill=pal["ink"], width=S(1))

    # ---------- 页眉 ----------
    hx, hy = PAD + 22, 122
    _draw_hex(d, X(hx), Y(hy), 22 * k, pal["ink"], S(2.4))
    _draw_hex(d, X(hx), Y(hy), 14 * k, pal["accent"], S(3.2))
    _draw_hex(d, X(hx), Y(hy), 7 * k, pal["accent"], 0, fill=pal["accent"])
    _text(d, (X(PAD + 68), Y(hy)), "系统提示 / SYSTEM NOTICE",
          _font("cn", 14 * k), pal["muted"], "lm")
    _text(d, (X(DESIGN_W - PAD), Y(hy - 18)), "屏幕 / DISPLAY",
          _font("cn", 12 * k), pal["muted"], "rs")
    _text(d, (X(DESIGN_W - PAD), Y(hy + 16)), "%d × %d" % (width, height),
          _font("mono", 17 * k), pal["ink2"], "rs")

    # ---------- 左侧主视觉 ----------
    sq = 10 * k
    d.rectangle([X(PAD), Y(243), X(PAD) + sq, Y(243) + sq], fill=pal["accent"])
    _text(d, (X(PAD + 26), Y(243 + sq / 2)), txt["eyebrow"],
          _font("cn_bold", 16 * k), pal["accent"], "lm")

    _text(d, (X(PAD), Y(330)), txt["title"][0] or "此计算机",
          _font("cn_bold", 58 * k), pal["ink2"], "ls")
    big, bsize_px = _fit_font(d, txt["title"][1] or "正在运行任务",
                              "cn_bold", 150 * k, LEFT_W * k * 0.98)
    # 字号是像素值，必须换算回设计坐标，否则高分辨率下整版会被二次缩放
    bs = bsize_px / k
    _text(d, (X(PAD), Y(330 + bs + 40)), txt["title"][1] or "正在运行任务",
          big, pal["ink"], "ls")

    band_y = 330 + bs + 40 + 36
    band_h = 136 * k
    bx0, bx1 = X(PAD), X(PAD + LEFT_W)
    d.rectangle([bx0, Y(band_y), bx1, Y(band_y + 136)], fill=pal["accent"])
    end_w, stripe_w = 56 * k, 10 * k
    hatch = Image.new("RGBA", (max(1, int(end_w)), max(1, int(band_h))), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hatch)
    hh = int(band_h)
    i = -hh
    while i < int(end_w) + hh:
        hd.line([(i, hh), (i + hh, 0)], fill=pal["on_accent"] + (235,), width=max(1, int(stripe_w)))
        i += int(22 * k)
    img.paste(hatch, (int(bx0), int(Y(band_y))), hatch)
    img.paste(hatch, (int(bx1 - end_w), int(Y(band_y))), hatch)

    plate = txt["plate"] or "请 勿 触 动"
    pf, _ps = _fit_font(d, plate, "cn_bold", 92 * k, (LEFT_W - 240) * k)
    _text(d, (X(PAD + 84), Y(band_y + 136 / 2)), plate, pf, pal["on_accent"], "lm")

    desc_y = band_y + 136 + 46
    _text(d, (X(PAD), Y(desc_y)), txt["sub"][0], _font("cn", 28 * k), pal["ink2"], "la")
    _text(d, (X(PAD), Y(desc_y + 46)), txt["sub"][1], _font("cn", 28 * k), pal["ink2"], "la")

    meta_y = desc_y + 86
    metals = ["键盘已屏蔽 · KEYBOARD LOCKED", "鼠标可用 · MOUSE ENABLED"]
    mx = PAD
    for idx, s in enumerate(metals):
        _text(d, (X(mx), Y(meta_y + 26)), s, _font("cn", 14 * k), pal["muted"], "la")
        mx += (d.textlength(s, font=_font("cn", 14 * k)) / k) + 40
        if idx < len(metals) - 1:
            d.line([(X(mx - 20), Y(meta_y + 14)), (X(mx - 20), Y(meta_y + 34))],
                   fill=pal["line"], width=S(1))

    note_y = meta_y + 52
    d.rectangle([X(PAD), Y(note_y), X(PAD + LEFT_W), Y(note_y + 66)], fill=pal["accent_soft"])
    d.rectangle([X(PAD), Y(note_y), X(PAD + 3), Y(note_y + 66)], fill=pal["accent"])
    _text(d, (X(PAD + 20), Y(note_y + 16)), "本提示不会中断正在执行的任务，任务仍会在后台继续运行。",
          _font("cn", 19 * k), pal["ink2"], "la")
    _text(d, (X(PAD + 20), Y(note_y + 46)), "THE RUNNING TASK WILL NOT BE INTERRUPTED.",
          _font("mono", 12 * k), pal["muted"], "la")

    # ---------- 右侧装饰面板（档案封印：切角双框 / 大六边形徽记 / 十字准线 / 弧封条） ----------
    px0, px1 = X(PANEL_X), X(PANEL_X + PANEL_W)
    py0, py1 = Y(238), Y(848)
    outer = _chamfer_pts(px0, py0, px1, py1, 22 * k)
    d.polygon(outer, fill=pal["panel"])
    d.line(outer + [outer[0]], fill=pal["line2"], width=S(1))
    inset = 11 * k
    inner = _chamfer_pts(px0 + inset, py0 + inset, px1 - inset, py1 - inset, 16 * k)
    d.line(inner + [inner[0]], fill=pal["line"], width=max(1, S(1) // 2))

    cx_d = PANEL_X + PANEL_W / 2.0
    cy_d = 505.0
    cx, cy = X(cx_d), Y(cy_d)

    # 背景：角落六边形晶格（避开中央徽记，克制）
    lat_c = _mix(pal["panel"], pal["ink"], 0.05)
    R = 18.0
    hh = math.sqrt(3) * R
    yy = 276.0
    row = 0
    while yy < 816.0:
        xx = PANEL_X + 42.0 + (0.0 if row % 2 == 0 else 0.75 * R)
        while xx < PANEL_X + PANEL_W - 40.0:
            if math.hypot(xx - cx_d, yy - cy_d) > 196.0:
                _draw_hex(d, X(xx), Y(yy), R * 0.9 * k, lat_c, max(1, S(1) // 2))
            xx += 1.5 * R
        yy += hh
        row += 1

    # 十字准线（贯穿面板，作为构图骨架）
    ret_c = _mix(pal["panel"], pal["ink"], 0.10)
    vy = Y(cy_d)
    d.line([(X(PANEL_X + 26), vy), (X(PANEL_X + PANEL_W - 26), vy)], fill=ret_c, width=S(1))
    d.line([(X(cx_d), Y(238 + 26)), (X(cx_d), Y(848 - 26))], fill=ret_c, width=S(1))

    # 虚线六边形封条（全直线、无圆弧）
    _hex_dashed(d, cx, cy, 176 * k, pal["accent"], S(2),
                dash=24 * k, gap=17 * k)

    # 大六边形徽记
    _draw_hex(d, cx, cy, 150 * k, pal["line2"], S(1.5))
    _draw_hex(d, cx, cy, 112 * k, pal["line"], S(1))
    for i in range(6):
        ang = math.radians(-90 + 60 * i)
        x1 = cx_d + 112 * math.cos(ang)
        y1 = cy_d + 112 * math.sin(ang)
        x2 = cx_d + 126 * math.cos(ang)
        y2 = cy_d + 126 * math.sin(ang)
        d.line([(X(x1), Y(y1)), (X(x2), Y(y2))], fill=pal["line2"], width=S(1))
        d.ellipse([X(x2) - S(2), Y(y2) - S(2), X(x2) + S(2), Y(y2) + S(2)],
                  fill=pal["accent"])
    _draw_hex(d, cx, cy, 58 * k, pal["accent"], S(2), fill=pal["accent_soft"])
    _draw_hex(d, cx, cy, 32 * k, pal["accent"], S(1))
    d.ellipse([cx - S(4), cy - S(4), cx + S(4), cy + S(4)], fill=pal["accent"])

    # 底部刻度尺
    ruler_y = 724.0
    rx0, rx1 = PANEL_X + 44.0, PANEL_X + PANEL_W - 44.0
    d.line([(X(rx0), Y(ruler_y)), (X(rx1), Y(ruler_y))], fill=pal["line2"], width=S(1))
    for i in range(13):
        xx = rx0 + (rx1 - rx0) * i / 12.0
        major = (i % 4 == 0)
        ln = 12.0 if major else 7.0
        d.line([(X(xx), Y(ruler_y)), (X(xx), Y(ruler_y - ln))],
               fill=pal["accent"] if major else pal["line2"], width=S(1))

    # 底部细斜纹
    hy0, hy1 = 790.0, 804.0
    stx0, stx1 = cx_d - 120.0, cx_d + 120.0
    d.line([(X(stx0), Y(hy0)), (X(stx1), Y(hy0))], fill=pal["line"], width=S(1))
    d.line([(X(stx0), Y(hy1)), (X(stx1), Y(hy1))], fill=pal["line"], width=S(1))
    off = 0.0
    while off <= (stx1 - stx0):
        d.line([(X(stx0 + off), Y(hy1)), (X(stx0 + off - (hy1 - hy0)), Y(hy0))],
               fill=pal["accent"], width=max(1, S(4)))
        off += 16.0

    # ---------- 页脚（仅版本信息，无操作提示） ----------
    fy = 966
    d.line([(X(PAD), Y(fy)), (X(DESIGN_W - PAD), Y(fy))], fill=pal["line2"], width=S(1))
    _text(d, (X(PAD), Y(fy + 22)), "全屏提示 · v1.5", _font("cn", 14 * k), pal["muted"], "la")
    _text(d, (X(DESIGN_W - PAD), Y(fy + 20)), "FULL SCREEN NOTICE · WINDOWS 10 / 11",
          _font("mono", 11 * k), pal["muted"], "ra")

    img.save(out_path, optimize=True)
    return out_path


# ---------- 命令行 ----------
def _parse_size(value):
    try:
        w, h = value.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError("尺寸格式应为 宽x高，例如 2560x1440")


def main(argv=None):
    ap = argparse.ArgumentParser(description="生成档案风格「请勿触动」横幅")
    ap.add_argument("out", nargs="?", default="banner_new.png", help="输出文件")
    ap.add_argument("size", nargs="?", default="2560x1440", type=_parse_size,
                    help="宽x高，例如 2560x1440")
    ap.add_argument("--eyebrow", help="顶部提示行（默认：正在运行任务 / TASK RUNNING）")
    ap.add_argument("--title", nargs=2, metavar=("L1", "L2"), help="标题两行")
    ap.add_argument("--plate", help="警示牌文字")
    ap.add_argument("--sub", nargs=2, metavar=("L1", "L2"), help="说明两行")
    ap.add_argument("--theme", choices=("light", "dark"), default="light", help="配色")
    args = ap.parse_args(argv)
    text = {}
    if args.eyebrow:
        text["eyebrow"] = args.eyebrow
    if args.title:
        text["title"] = list(args.title)
    if args.plate:
        text["plate"] = args.plate
    if args.sub:
        text["sub"] = list(args.sub)
    render(args.out, args.size[0], args.size[1], text=text, theme=args.theme)
    print("已生成 %s（%d×%d，%s）" % (args.out, args.size[0], args.size[1], args.theme))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
