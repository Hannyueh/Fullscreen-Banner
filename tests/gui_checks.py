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
check("版本 1.7", m.VERSION == "1.7", m.VERSION)
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

from unittest.mock import patch
with patch.object(m.threading, "Thread") as thread_factory:
    m._start_generation([(800, 600)])
    m._start_generation([(1024, 768)])
    check("生成中保留最新请求", m._generation_pending == [(1024, 768)])
    m._handle_command(("generated", True))
    check("旧任务结束后生成最新版本", thread_factory.call_count == 2
          and m._generating and m._generation_pending is None)
    m._handle_command(("generated", True))
    check("生成任务正确收尾", not m._generating)

root = tk.Tk()
root.withdraw()
win = None
try:
    win = gui.open_control_center(root, m)
    check("创建控制中心", win is not None and gui.is_open())
    win.withdraw()
    win.update_idletasks()
    check("窗口标题", win.title().startswith("全屏提示"), win.title())
    check("四个独立场景齐全", all(win.tabs.tab(n) is not None for n in ("开始", "横幅", "息屏", "更多")))
    check("品牌使用现有蜂巢图标", win.tabs.brand_label.cget("text") == ""
          and win.tabs.brand_image.cget("size") == (56, 56))
    def rounded_widgets(parent):
        found = []
        for child in parent.winfo_children():
            if getattr(child, "_corner_radius", 0) != 0:
                found.append(type(child).__name__)
            found.extend(rounded_widgets(child))
        return found
    check("所有界面控件为直角", not rounded_widgets(win), rounded_widgets(win))
    slider = win.sl_bright
    slider_canvas = slider._canvas
    slider_items = slider_canvas.find_all()
    thumb_left, thumb_top, thumb_right, thumb_bottom = slider_canvas.bbox("slider_parts")
    check("卡片版滑轨、进度条和滑块均为矩形",
          len(slider_items) == 3 and all(slider_canvas.type(item) == "rectangle" for item in slider_items)
          and thumb_right - thumb_left > thumb_bottom - thumb_top > 0)
    old_brightness = slider.get()
    slider.set(slider.cget("from_"))
    left_endpoint = slider_canvas.bbox("slider_parts")[0]
    slider.set(slider.cget("to"))
    check("滑块移动时复用矩形图元", slider_canvas.find_all() == slider_items
          and slider_canvas.bbox("slider_parts")[0] > left_endpoint)
    slider.set(old_brightness)
    check("侧栏为纯文字导航", [button.cget("text") for button in win.tabs._buttons.values()]
          == ["开始", "画面", "屏保", "设置"])
    check("切页保留已完成的布局", all(page.place_info() for page in win.tabs._pages.values()))
    win._motion_enabled = False
    win.tabs.set("息屏")
    win.tabs.set("横幅")
    check("减少动态效果时页面立即就位", win.tabs.get() == "横幅"
          and int(win.tabs.tab("横幅").place_info()["x"]) == 0)
    auto_mode, shift_mode = win.OLED_MODE_LABELS
    win.oled_mode_seg.set(shift_mode, from_button_callback=True)
    check("卡片版常亮模式保留亮度和位移控制", not m.load_config()["oled_auto_guard"]
          and win.sl_idle.cget("state") == "disabled" and win.sl_bright.cget("state") == "normal"
          and win.sl_shift.cget("state") == "normal" and win.sl_radius.cget("state") == "normal")
    win.oled_mode_seg.set(auto_mode, from_button_callback=True)
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
    check("界面刷新不报错", True)
    with patch.object(win.sl_idle, "configure", wraps=win.sl_idle.configure) as configure_slider:
        win._refresh_status()
        check("状态未变化时不重复重绘滑块", configure_slider.call_count == 0)
    win._paint_preview(Image.new("RGB", (320, 180), (20, 20, 20)))
    preview_item = win._preview_item
    win._paint_preview(Image.new("RGB", (320, 180), (80, 80, 80)))
    check("更新预览复用画布图元", win._preview_item == preview_item
          and len(win.preview_canvas.find_all()) == 1)

    # 未应用文案与失败反馈：错误不能显示为已保存，也不能污染磁盘或内存配置。
    win.text_vars["title0"].set("尚未保存的标题")
    check("编辑文案标记未应用", win._text_dirty and win.copy_state.cget("text") == "尚未应用")
    win.text_vars["title0"].set(m.CFG["banner_title"][0])
    check("还原文案清除未应用状态", not win._text_dirty)
    win.var_guard_color.set("#GGGGGG")
    old_color = m.CFG["guard_color"]
    win._on_apply_guard_text()
    check("非法颜色给出错误且不保存", win._feedback_tone == "error" and m.CFG["guard_color"] == old_color)
    win.var_guard_color.set(old_color)

    from unittest.mock import patch
    original_replace = os.replace
    def fail_config_replace(src, dst):
        if dst == m.CONFIG_PATH:
            raise PermissionError("模拟配置不可写")
        return original_replace(src, dst)
    before = open(m.CONFIG_PATH, encoding="utf-8").read()
    with patch.object(os, "replace", side_effect=fail_config_replace):
        saved_ok = win._apply({"brightness": 0.7})
    check("保存失败反馈准确", saved_ok is False and win._feedback_tone == "error")
    check("保存失败保留原配置", open(m.CONFIG_PATH, encoding="utf-8").read() == before
          and abs(m.BANNER_BRIGHTNESS - 0.85) < 1e-6)
    check("失败后没有残留临时配置", not os.path.exists(m.CONFIG_PATH + ".tmp"))

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
    win._schedule_apply({"brightness": 0.65})
    win._on_close()
    check("关闭前保存待处理的滑块修改", abs(m.BANNER_BRIGHTNESS - 0.65) < 1e-6
          and not win._pending_updates)
    check("关闭仅隐藏", gui.is_open() and not win.winfo_viewable(),
          (gui.is_open(), win.winfo_viewable()))

    # 再次打开应唤回同一个窗口
    again = gui.open_control_center(root, m)
    check("唤回同一窗口", again is win)
    again.withdraw()

    def save_with_confirmation(window, interact):
        def respond():
            dialog = next(child for child in window.winfo_children()
                          if isinstance(child, gui._PasswordConfirmation))
            try:
                interact(dialog)
            finally:
                if dialog.winfo_exists():
                    dialog.destroy()
        job = window.after(100, respond)
        try:
            window.btn_save_unlock.invoke()
        finally:
            window.after_cancel(job)

    # 布局切换共用配置，保留尚未应用的输入，并可在保存失败时重试。
    win.text_vars["title0"].set("切换时保留的草稿")
    win.var_guard_text.set("守护文字草稿")
    check("默认密码清空时间为 3 秒", win.unlock_timeout.get() == "3")
    win.unlock_enabled.set(True)
    win.unlock_password.set("Test-17!")
    win.unlock_timeout.set("3")
    def confirm_modern(dialog):
        check("确认弹窗遮罩密码并锁定父窗口", dialog.entry.cget("show") == "•"
              and dialog.grab_current() is dialog)
        dialog.entry.insert(0, "Different")
        dialog.confirm_button.invoke()
        check("确认不一致时弹窗保持打开且不启用密码", dialog.winfo_exists() and dialog.error.cget("text")
              and not m.load_config()["unlock_password_enabled"] and not m.CFG["unlock_password_blob"])
        dialog.entry.delete(0, "end")
        dialog.entry.insert(0, "Test-17!")
        dialog.confirm_button.invoke()

    with patch.object(gui.messagebox, "showinfo") as help_dialog:
        save_with_confirmation(win, lambda dialog: dialog.destroy())
        check("取消确认时保留密码草稿", not m.CFG["unlock_password_enabled"]
              and win.unlock_password.get() == "Test-17!" and not help_dialog.called)
        save_with_confirmation(win, confirm_modern)
        with patch.object(gui, "_PasswordConfirmation") as confirmation:
            win.btn_save_unlock.invoke()
            check("沿用密码时跳过确认", not confirmation.called)
        check("首次启用时只自动提示一次", help_dialog.call_count == 1)
        win.btn_unlock_help.invoke()
        check("卡片版问号可重看提示", help_dialog.call_count == 2)
    check("密码卡片保存成功并清除明文输入", m.CFG["unlock_password_enabled"]
          and m.CFG["unlock_password_timeout"] == 3 and win.unlock_password.get() == "")
    saved_blob = m.CFG["unlock_password_blob"]
    win.unlock_password.set("未保存的草稿")
    win.unlock_timeout.set("未完成")
    with patch.object(os, "replace", side_effect=fail_config_replace):
        win.unlock_switch._toggle()
    check("关闭密码失败时恢复开关", win.unlock_switch.get() and m.CFG["unlock_password_enabled"]
          and win._feedback_tone == "error")
    win.unlock_switch._toggle()
    check("卡片版关闭密码立即生效且不保存草稿", not m.CFG["unlock_password_enabled"]
          and not m.load_config()["unlock_password_enabled"] and m.CFG["unlock_password_blob"] == saved_blob
          and m.CFG["unlock_password_timeout"] == 3 and win.unlock_password.get() == "未保存的草稿"
          and win.unlock_timeout.get() == "未完成")
    win.unlock_password.set("")
    win.unlock_timeout.set("3")
    win.unlock_switch._toggle()
    check("开启密码仍由保存确认", not m.CFG["unlock_password_enabled"])
    win.btn_save_unlock.invoke()
    with patch.object(os, "replace", side_effect=fail_config_replace):
        gui._switch_control_layout(win, "classic")
    check("布局保存失败保留当前窗口", gui._center is win and win.winfo_exists()
          and m.CFG["control_layout"] == "modern" and win.btn_layout.cget("state") == "normal")

    win.btn_layout.invoke()
    root.after(210, root.quit)
    root.mainloop()
    classic = gui._center
    classic.withdraw()
    check("设置按钮打开原版界面", m.CFG["control_layout"] == "classic"
          and classic.tabs.get() == "横幅" and classic.preview_panel.winfo_exists()
          and not win.winfo_exists())
    check("简洁版保留两类草稿", classic.text_vars["title0"].get() == "切换时保留的草稿"
          and classic.var_guard_text.get() == "守护文字草稿"
          and m.CFG["banner_title"][0] == "测试标题")
    check("简洁版共用密码选项", classic.unlock_enabled.get() and classic.unlock_timeout.get() == "3")
    classic.oled_mode_seg.set(shift_mode, from_button_callback=True)
    check("简洁版切换常亮即时生效且可调亮度", not m.auto_guard_enabled()
          and classic.sl_bright.cget("state") == "normal"
          and classic.sl_guard_move.cget("state") == "disabled" and classic.sl_shift.cget("state") == "normal")
    classic.tabs.set("密码")
    check("简洁版密码设置位于独立页签", classic.tabs.get() == "密码"
          and classic.btn_save_unlock.master.master is classic.tabs.tab("密码"))
    classic.unlock_password.set("Replacement-17!")
    def confirm_classic(dialog):
        dialog.entry.insert(0, "Replacement-17!")
        dialog.confirm_button.invoke()
    save_with_confirmation(classic, confirm_classic)
    check("简洁版更换密码需要确认", m.read_password(m.CFG["unlock_password_blob"]) == "Replacement-17!")
    with patch.object(gui.messagebox, "showinfo") as help_dialog:
        classic.btn_unlock_help.invoke()
        check("简洁版问号可查看同一说明", help_dialog.call_count == 1)
    classic.unlock_switch.toggle()
    check("简洁版关闭密码立即生效", not m.CFG["unlock_password_enabled"]
          and not m.load_config()["unlock_password_enabled"])
    check("重读配置记住界面", m.load_config()["control_layout"] == "classic")
    classic.btn_layout.invoke()
    root.after(210, root.quit)
    root.mainloop()
    win = gui._center
    win.withdraw()
    check("可切回卡片版设置页", win.tabs.get() == "更多" and not classic.winfo_exists()
          and m.CFG["control_layout"] == "modern" and win._text_dirty)
    check("切换界面保留屏保模式", win.oled_mode_seg.get() == shift_mode)

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
