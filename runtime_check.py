# -*- coding: utf-8 -*-
"""打包后的离线冒烟检查；不启动托盘、键盘钩子或开机启动修复。"""
import hashlib
import json
import os
import tempfile
import traceback


def run(app):
    import tkinter as tk
    import gui
    from PIL import Image
    from customtkinter.windows.widgets.core_rendering import DrawEngine

    report = {"ok": False, "checks": []}
    root = None
    try:
        if hashlib.sha1(b"abc").hexdigest() != "a9993e364706816aba3e25717850c26c9cd0d89d":
            raise RuntimeError("SHA-1 cache key mismatch")
        report["checks"].append("hashlib fallback")
        with tempfile.TemporaryDirectory(prefix="runtime_", dir=app.DATA_DIR) as folder:
            for ext in ("png", "jpg", "bmp", "gif", "webp", "tiff", "avif"):
                path = os.path.join(folder, "image." + ext)
                Image.new("RGB", (80, 60), (230, 90, 30)).save(path)
                for fit in app.FIT_CHOICES:
                    image = app._render_custom_image(path, 320, 180, fit)
                    if image.width < 320 or image.height < 180:
                        raise RuntimeError("Invalid rendered image: " + ext)
                report["checks"].append(ext)
        preview, _ = gui.build_preview_image(app, 1920, 1080, 320, 180)
        if preview.size != (320, 180):
            raise RuntimeError("Invalid preview size")
        report["checks"].append("banner preview")
        root = tk.Tk()
        root.withdraw()
        window = gui.open_control_center(root, app)
        if window is None:
            raise RuntimeError("Control center creation failed")
        window.withdraw()
        window.tabs.set("息屏")
        root.update_idletasks()
        if DrawEngine.preferred_drawing_method != "font_shapes":
            raise RuntimeError("Widget font resource was not loaded")
        report["checks"].append("control center and bundled fonts")
        window.tabs._wipe._init_apis()
        report["checks"].append("native diagonal transition")
        app._install_escape_fallback(root)
        if not root.bind_all("<Escape>"):
            raise RuntimeError("Escape fallback was not registered")
        report["checks"].append("Escape fallback binding")
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        gui.close_control_center()
        if root is not None:
            root.destroy()
    with open(os.path.join(app.DATA_DIR, "runtime-check.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return 0 if report["ok"] else 1
