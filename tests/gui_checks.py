# -*- coding: utf-8 -*-
"""控制中心 GUI（v1.5）无界面自检。

用法（在项目根目录）：
    python tests\\gui_checks.py

特点：
- 使用临时 LOCALAPPDATA，不碰真实配置/缓存/日志；
- 只建隐藏窗口，不弹出界面、不装键盘钩子；重新出图用打桩替代，避免真的渲染；
- 覆盖：模块导入、主题文件、配置热应用与持久化、文案保存、
  预览尺寸与三种适配方式、窗口隐藏/唤回/销毁。
"""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "banner.pyw")
if ROOT not in sys.path:                  # 从 tests\ 目录运行时也能 import gui
    sys.path.insert(0, ROOT)

tmp = tempfile.mkdtemp(prefix="guicheck_")
os.environ["LOCALAPPDATA"] = tmp          # 隔离配置/缓存/日志
os.environ.pop("BANNER_SELFTEST", None)

loader = importlib.machinery.SourceFileLoader("banner_mod", SRC)
spec = importlib.util.spec_from_loader("banner_mod", loader)
m = importlib.util.module_from_spec(spec)
sys.modules["banner_mod"] = m             # 让 banner._self_module() 能取到引用
loader.exec_module(m)

from PIL import Image  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (("   [" + str(detail) + "]") if detail else ""))
    if not cond:
        FAIL.append(name)


print("Python %s | Pillow %s" % (sys.version.split()[0], Image.__version__))
check("版本 1.5", m.VERSION == "1.5", m.VERSION)
check("默认横幅配色 light", m.CFG.get("banner_theme") == "light", m.CFG.get("banner_theme"))
check("有效横幅配色", m.effective_theme() in ("light", "dark"), m.effective_theme())
check("包含 _apply_settings", callable(getattr(m, "_apply_settings", None)))
check("包含 _open_gui", callable(getattr(m, "_open_gui", None)))

try:
    import gui
except Exception as exc:                  # customtkinter 缺失时直接失败
    print("FAIL  导入 gui：%r" % (exc,))
    sys.exit(1)
check("gui 导入", True)
check("主题文件存在", os.path.isfile(os.path.join(ROOT, "theme.json")))
check("主题路径解析", gui._theme_path(m) == os.path.join(ROOT, "theme.json"),
      gui._theme_path(m))

# ---------------- 预览纯函数：三种适配方式 ----------------
im = Image.new("RGB", (800, 600), (200, 40, 40))
cov = gui._fit_preview(im, 400, 300, "cover")
check("preview cover 尺寸", cov.size == (400, 300), cov.size)
check("preview cover 无黑边", sum(cov.getpixel((0, 0))) > 60)
con = gui._fit_preview(im, 400, 400, "contain")
check("preview contain 尺寸", con.size == (400, 400), con.size)
check("preview contain 上下黑边",
      sum(con.getpixel((0, 0))) == 0 and sum(con.getpixel((200, 200))) > 60)
fil = gui._fit_preview(im, 200, 300, "fill")
check("preview fill 尺寸", fil.size == (200, 300), fil.size)

# ---------------- build_preview_image ----------------
m.OLED_PROTECT = True
m.BANNER_BRIGHTNESS = 0.85
m.BANNER_IMAGE = ""
box = (320, 180)
img, info = gui.build_preview_image(m, 2560, 1440, *box)
check("默认预览尺寸", img.size == box, img.size)
check("默认预览内容", img.getbbox() is not None and "默认出图" in info, info)

img_same, _info2 = gui.build_preview_image(m, 1920, 1080, *box)
check("默认预览缓存命中", img_same.size == box, img_same.size)

custom_path = os.path.join(tmp, "custom_preview.png")
Image.new("RGB", (1200, 500), (30, 180, 90)).save(custom_path)
m.BANNER_IMAGE = custom_path
m.BANNER_FIT = "cover"
img3, info3 = gui.build_preview_image(m, 2560, 1440, *box)
check("自定义预览尺寸", img3.size == box, img3.size)
check("自定义预览信息", "自定义图片" in info3 and "铺满裁切" in info3, info3)
m.BANNER_IMAGE = ""

# ---------------- 配置热应用 ----------------
check("热应用亮度", m._apply_settings({"brightness": 0.5})
      and abs(m.BANNER_BRIGHTNESS - 0.5) < 1e-6, m.BANNER_BRIGHTNESS)
check("热应用范围夹取", m._apply_settings({"brightness": 9})
      and abs(m.BANNER_BRIGHTNESS - 1.0) < 1e-6, m.BANNER_BRIGHTNESS)
check("热应用守护时长", m._apply_settings({"idle_to_guard": 120})
      and m.IDLE_TO_GUARD == 120.0, m.IDLE_TO_GUARD)
check("热应用微移半径", m._apply_settings({"shift_radius": 8})
      and m.SHIFT_RADIUS == 8)
check("热应用未知键忽略", m._apply_settings({"no_such_key": 1}) is False)
check("热应用文案", m._apply_settings({"banner_title": ["A", "B"]})
      and m.CFG["banner_title"] == ["A", "B"], m.CFG["banner_title"])
check("热应用适配归一", m._apply_settings({"banner_fit": "BAD"})
      and m.BANNER_FIT == "cover", m.BANNER_FIT)
with open(m.CONFIG_PATH, encoding="utf-8") as f:
    saved = json.load(f)
check("热应用持久化",
      saved.get("idle_to_guard") == 120.0 and saved.get("banner_title") == ["A", "B"],
      (saved.get("idle_to_guard"), saved.get("banner_title")))

# ---------- 横幅配色：持久化与预览差异（打桩重新出图） ----------
_orig_gen = m._start_generation
m._start_generation = lambda sizes=None: None
try:
    check("横幅配色持久化", m._apply_settings({"banner_theme": "dark"})
          and m.BANNER_THEME == "dark" and m.effective_theme() == "dark")
    img_dark, info_dark = gui.build_preview_image(m, 1920, 1080, 320, 180)
    m._apply_settings({"banner_theme": "light"})
    img_light, info_light = gui.build_preview_image(m, 1920, 1080, 320, 180)
    check("预览随横幅配色变化", img_light.tobytes() != img_dark.tobytes())
    check("预览信息含配色", "浅色" in info_light and "深色" in info_dark,
          (info_light, info_dark))
finally:
    m._start_generation = _orig_gen

# 复位到合理值，避免影响后续窗口断言
m._apply_settings({"brightness": 0.85, "banner_title": ["此计算机", "正在运行任务"],
                   "banner_fit": "cover", "auto_generate": True})

# ---------------- 控制中心窗口（隐藏） ----------------
import tkinter as tk  # noqa: E402

root = tk.Tk()
root.withdraw()
win = None
try:
    win = gui.open_control_center(root, m)
    check("创建控制中心", win is not None and gui.is_open())
    win.withdraw()
    win.update_idletasks()
    check("窗口标题", win.title().startswith("全屏提示"), win.title())
    check("页签齐全", all(win.tabs.tab(n) is not None for n in ("横幅", "息屏")))
    check("横幅配色控件存在", hasattr(win, "banner_theme_seg"))
    try:
        win._on_tab_change()          # CTk 5.2.2 的 command 回调不带参数
        win._on_tab_change("息屏")
        tab_ok = True
    except TypeError:
        tab_ok = False
    check("页签回调兼容无参调用", tab_ok)

    win._refresh_status()
    win._sync_image_tab()
    win._refresh_advanced()
    win._refresh_about()
    check("界面刷新不报错", True)

    # 文案保存：打桩重新出图，避免真的渲染
    calls = []
    orig_gen = m._start_generation
    m._start_generation = lambda sizes=None: calls.append(sizes)
    try:
        win.text_vars["title0"].set("测试标题")
        win.text_vars["title1"].set("第二行")
        win._on_apply_text()
        check("文案热应用", m.CFG["banner_title"] == ["测试标题", "第二行"],
              m.CFG["banner_title"])
        check("文案触发重新出图", bool(calls), calls)
    finally:
        m._start_generation = orig_gen

    # 适配方式切换
    win.fit_var.set(1)
    win._on_fit_change()
    check("适配切换到 contain", m.BANNER_FIT == "contain", m.BANNER_FIT)

    # 开关：自动出图
    win.sw_auto.deselect()
    win._on_switch("auto_generate", win.sw_auto)
    check("开关写入 CFG", m.CFG.get("auto_generate") is False,
          m.CFG.get("auto_generate"))

    # 预览请求（后台线程，不阻塞等待结果）
    win._selected_monitor()
    win._request_preview()
    check("预览请求已排队",
          win._preview_pending is not None or win._preview_thread is not None)

    # 横幅配色切换（窗口内，不重建窗口）
    _orig_gen = m._start_generation
    m._start_generation = lambda sizes=None: None
    try:
        win._set_banner_theme("dark")
        check("窗口切深色横幅", m.BANNER_THEME == "dark", m.BANNER_THEME)
        win._set_banner_theme("light")
        check("窗口切浅色横幅", m.BANNER_THEME == "light", m.BANNER_THEME)
    finally:
        m._start_generation = _orig_gen

    # 关闭按钮 = 隐藏到托盘，不销毁
    win._on_close()
    check("关闭仅隐藏", gui.is_open() and not win.winfo_viewable(),
          (gui.is_open(), win.winfo_viewable()))

    # 再次打开应唤回同一个窗口
    again = gui.open_control_center(root, m)
    check("唤回同一窗口", again is win)
    again.withdraw()

    # 退出时销毁
    check("销毁控制中心", gui.close_control_center() and not gui.is_open())
finally:
    try:
        gui.close_control_center()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

# ---------- 预览：透明未填充用棋盘格示意 ----------
try:
    _cb = gui._fit_preview(gui.Image.new("RGB", (100, 50), (10, 10, 10)),
                           120, 120, "contain", "transparent")
    check("预览透明棋盘格", _cb.size == (120, 120)
          and _cb.getpixel((0, 0)) != (0, 0, 0), _cb.getpixel((0, 0)))
    _bk = gui._fit_preview(gui.Image.new("RGB", (100, 50), (10, 10, 10)),
                           120, 120, "contain", "black")
    check("预览黑边仍为纯黑", _bk.getpixel((0, 0)) == (0, 0, 0), _bk.getpixel((0, 0)))
except Exception as exc:
    check("预览透明棋盘格", False, exc)

print()
print("=" * 60)
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
