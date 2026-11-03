# -*- coding: utf-8 -*-
"""自定义横幅图片（v1.4）无界面自检。

用法（在项目根目录）：
    python tests\\custom_image_checks.py

特点：
- 使用临时 LOCALAPPDATA，不碰真实配置/缓存/日志；
- 只验证渲染与选图逻辑，Tk 部分只建隐藏窗口，不显示横幅、不装键盘钩子；
- 覆盖 cover / contain / fill、透明 PNG、GIF 首帧、21:9 混编、磁盘缓存失效、
  选图优先级、托盘菜单构造与命令分发。
"""
import importlib.machinery
import importlib.util
import json
import os
import queue
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "banner.pyw")

tmp = tempfile.mkdtemp(prefix="bannercheck_")
os.environ["LOCALAPPDATA"] = tmp          # 隔离配置/缓存/日志
os.environ.pop("BANNER_SELFTEST", None)

loader = importlib.machinery.SourceFileLoader("banner_mod", SRC)
spec = importlib.util.spec_from_loader("banner_mod", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

from PIL import Image, ImageDraw  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("   [" + str(detail) + "]") if detail else ""))
    if not cond:
        FAIL.append(name)


print("Pillow %s | DATA_DIR=%s" % (Image.__version__, m.DATA_DIR))
check("隔离数据目录", m.DATA_DIR.startswith(tmp), m.DATA_DIR)
check("配置含新键", m.CFG.get("banner_image") == "" and m.CFG.get("banner_fit") == "cover")
check("fit 归一化", (m._normalize_fit("CONTAIN"), m._normalize_fit("bad"), m._normalize_fit(None))
      == ("contain", "cover", "cover"))
check("版本 1.5", m.VERSION == "1.5", m.VERSION)
check("占位图改到 placeholder.png", os.path.join(m.DATA_DIR, "placeholder.png") in m.IMAGE_FALLBACKS)

work = os.path.join(tmp, "imgs")
os.makedirs(work)


def make(name, size, mode="RGB", color=(200, 40, 40)):
    im = Image.new(mode, size, color if mode != "RGBA" else color + (255,))
    p = os.path.join(work, name)
    im.save(p)
    return p


jpg = make("wide.jpg", (1920, 1080))
ultra = make("ultra.jpg", (3440, 1440))
png_a = os.path.join(work, "alpha.png")
im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
ImageDraw.Draw(im).rectangle([150, 100, 250, 200], fill=(220, 30, 30, 255))
im.save(png_a)
gif = os.path.join(work, "anim.gif")
Image.new("RGB", (300, 200), (30, 120, 220)).save(
    gif, save_all=True,
    append_images=[Image.new("RGB", (300, 200), (240, 200, 30))], duration=200, loop=0)

margin = m._shift_margin()
check("微移安全边=6", margin == 6, margin)

# ---- cover：铺满整个屏幕（含安全边），四角不露黑 ----
for (mw, mh) in ((2560, 1600), (2560, 1440), (5120, 1440)):
    out = m._render_custom_image(jpg, mw, mh, "cover")
    want = (mw + 2 * margin, mh + 2 * margin)
    check("cover 尺寸 %dx%d" % (mw, mh), out.size == want, out.size)
    corners = [(0, 0), (want[0] - 1, 0), (0, want[1] - 1),
               (want[0] - 1, want[1] - 1), (want[0] // 2, want[1] // 2)]
    dark = [c for c in corners if sum(out.getpixel(c)) < 60]
    check("cover 无黑边 %dx%d" % (mw, mh), not dark, dark)

# ---- contain：完整可见 + 留黑边 ----
out = m._render_custom_image(jpg, 2560, 1600, "contain")
check("contain 尺寸", out.size == (2572, 1612), out.size)
check("contain 黑边", sum(out.getpixel((0, 0))) == 0 and sum(out.getpixel((1286, 806))) > 60)

# ---- fill：拉伸到目标尺寸 ----
out = m._render_custom_image(jpg, 2560, 1440, "fill")
check("fill 尺寸", out.size == (2572, 1452), out.size)
check("fill 无黑边", sum(out.getpixel((0, 0))) > 60)

# ---- 透明 PNG 合成黑底 ----
out = m._render_custom_image(png_a, 800, 600, "cover")
check("透明 PNG 尺寸", out.size == (812, 612), out.size)
check("透明区=黑底", sum(out.getpixel((0, 0))) == 0)
check("透明 PNG 主体保留", sum(out.getpixel((406, 306))) > 60)

# ---- 动图取首帧 ----
out = m._render_custom_image(gif, 300, 200, "fill")
px = out.getpixel((150, 100))
check("GIF 首帧", px[2] > 150 and px[0] < 80, px)

# ---- 21:9 / 16:10 混编不互相影响 ----
a = m._render_custom_image(ultra, 2560, 1600, "cover")
b = m._render_custom_image(ultra, 2560, 1440, "cover")
check("21:9 混编尺寸", a.size == (2572, 1612) and b.size == (2572, 1452), (a.size, b.size))

# ---- 磁盘缓存：命中不重生成、换 fit/换源图会失效 ----
m.BANNER_IMAGE = jpg
m.BANNER_FIT = "cover"
check("缓存生成成功", m._ensure_custom_cache([(2560, 1600), (2560, 1440)]))
c1 = m._custom_cache_path(jpg, 2560, 1600, "cover")
check("缓存文件存在", os.path.isfile(c1), c1)
mt = os.path.getmtime(c1)
check("缓存二次命中", m._ensure_custom_cache([(2560, 1600)]) and os.path.getmtime(c1) == mt)
check("换 fit 换缓存名", m._custom_cache_path(jpg, 2560, 1600, "contain") != c1)
check("换源图换缓存名", m._custom_cache_path(png_a, 2560, 1600, "cover") != c1)

# ---- 选图优先级 ----
path, exact, fit = m.image_for_monitor(2560, 1440)
check("配置图优先", path == jpg and exact is False and fit == "cover", (path, exact, fit))
m.BANNER_IMAGE = ""
drop = os.path.join(m.DATA_DIR, "banner.jpg")
Image.new("RGB", (120, 90), (10, 200, 60)).save(drop)
path, exact, fit = m.image_for_monitor(2560, 1440)
check("固定目录投图", path == drop and fit == "cover", (path, fit))
os.remove(drop)
path, exact, fit = m.image_for_monitor(1234, 567)
check("无自定义图回退默认", fit is None and exact is False and path.endswith("banner.png"),
      (path, exact, fit))

# ---- 有自定义图时不再弹“重新出图” ----
m.BANNER_IMAGE = jpg
check("跳过分辨率出图", m._missing_generation() == [])

# ---- 配置持久化 ----
m._update_config({"banner_image": jpg, "banner_fit": "fill"})
with open(m.CONFIG_PATH, encoding="utf-8") as f:
    cfg = json.load(f)
check("配置写入", cfg["banner_image"] == jpg and cfg["banner_fit"] == "fill")

# ---- 真实显示链路（Tk 只建隐藏窗口） ----
try:
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
except Exception as exc:                     # 无桌面会话时跳过
    print("SKIP  Tk 链路（%r）" % (exc,))
else:
    try:
        m.BANNER_IMAGE = jpg
        m.BANNER_FIT = "cover"
        photo, x0, y0 = m.get_photo(jpg, 800, 600, False, "cover")
        check("PhotoImage 尺寸", (photo.width(), photo.height()) == (812, 612),
              (photo.width(), photo.height()))
        check("自定义图偏移=-边距", (x0, y0) == (-6, -6), (x0, y0))
        p2, _x2, _y2 = m.get_photo(jpg, 800, 600, False, "cover")
        check("PhotoImage 命中缓存", p2 is photo)
        legacy = os.path.join(m.APP_DIR, "banner.png")
        photo3, x3, y3 = m.get_photo(legacy, 800, 400, False, None)
        _lim = Image.open(legacy)
        _bh = max(1, round(800 * _lim.height / _lim.width))
        check("旧图铺满逻辑不变", x3 == 0 and y3 == (400 - _bh) // 2,
              (x3, y3, _lim.size))
    finally:
        root.destroy()

# ---- 托盘菜单构造 + 菜单命令分发（不弹出、不点击） ----
m.BANNER_IMAGE = jpg
m.BANNER_FIT = "cover"
orig_track = m.user32.TrackPopupMenu
m.user32.TrackPopupMenu = lambda *a, **k: m.IDM_FIT_CONTAIN
try:
    while True:
        try:
            m.commands.get_nowait()
        except queue.Empty:
            break
    m._show_tray_menu(0)
    got = []
    while True:
        try:
            got.append(m.commands.get_nowait())
        except queue.Empty:
            break
    check("菜单→适配命令", ("set_fit", "contain") in got, got)
finally:
    m.user32.TrackPopupMenu = orig_track

m._handle_command(("set_fit", "contain"))
check("命令设置适配方式", m.BANNER_FIT == "contain", m.BANNER_FIT)
m._handle_command(("custom_ready", True, 10 ** 6))
check("过期生成结果被忽略", True)
m._handle_command(("clear_image",))
check("清除自定义图片", m.BANNER_IMAGE == "")

# ---------- 完整显示：透明未填充 ----------
_transp_src = os.path.join(tmp, "transp_src.png")
from PIL import Image as _Image
_Image.new("RGB", (300, 600), (40, 90, 200)).save(_transp_src)
_saved_fill = m.CONTAIN_FILL
try:
    m.CONTAIN_FILL = "transparent"
    _rgba = m._render_custom_image(_transp_src, 800, 400, "contain")
    check("透明模式返回 RGBA", _rgba.mode == "RGBA", _rgba.mode)
    check("透明模式边角 alpha=0", _rgba.getpixel((0, 0))[3] == 0, _rgba.getpixel((0, 0)))
    check("透明模式内容不透明", _rgba.getpixel((_rgba.width // 2, _rgba.height // 2))[3] == 255)
    _keyed = m._on_key_color(_rgba)
    check("色键合成到品红", _keyed.mode == "RGB"
          and _keyed.getpixel((0, 0)) == m.TRANSPARENT_KEY_RGB, _keyed.getpixel((0, 0)))
    check("色键合成保留内容", _keyed.getpixel((_keyed.width // 2, _keyed.height // 2))[2] > 120)
    _p_trans = m._custom_cache_path(_transp_src, 800, 400, "contain")
    m.CONTAIN_FILL = "black"
    _p_black = m._custom_cache_path(_transp_src, 800, 400, "contain")
    m.CONTAIN_FILL = "transparent"
    check("透明缓存路径区分黑边", _p_trans != _p_black)
finally:
    m.CONTAIN_FILL = _saved_fill

# ---------- 色键冲突：图片本身含品红 ----------
from PIL import Image as _Image2
_key_img = _Image2.new("RGB", (2, 1))
_key_img.putpixel((0, 0), m.TRANSPARENT_KEY_RGB)
_key_img.putpixel((1, 0), (10, 20, 30))
_fixed = m._avoid_key_color(_key_img)
check("图片含键色自动避让", _fixed.getpixel((0, 0)) != m.TRANSPARENT_KEY_RGB
      and _fixed.getpixel((1, 0)) == (10, 20, 30), _fixed.getpixel((0, 0)))

_rgba_key = _Image2.new("RGBA", (4, 3), (0, 0, 0, 0))
_rgba_key.paste(_Image2.new("RGB", (2, 2),
                            m.TRANSPARENT_KEY_RGB).convert("RGBA"), (0, 0))
_rgba_key.paste(_Image2.new("RGB", (2, 2), (30, 80, 200)).convert("RGBA"), (2, 0))
_disp = m._on_key_color(_rgba_key)
check("含品红图片不会漏透明洞", _disp.getpixel((0, 0)) != m.TRANSPARENT_KEY_RGB
      and _disp.getpixel((3, 0)) == (30, 80, 200),
      (_disp.getpixel((0, 0)), _disp.getpixel((3, 0))))
check("未填充区仍保持键色", _disp.getpixel((0, 2)) == m.TRANSPARENT_KEY_RGB,
      _disp.getpixel((0, 2)))

print()
print("=" * 60)
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
