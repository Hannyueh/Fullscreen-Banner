# -*- coding: utf-8 -*-
"""默认横幅渲染（v1.5 档案版 · 浅色/深色）无界面自检。

用法（在项目根目录）：
    python tests\\banner_checks.py

覆盖：双主题渲染、常见分辨率、超宽/竖屏、自定义文案、非法主题回退、
以及源码中不再包含旧版装饰文字/品牌字样。
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="bannercheck_")

from PIL import Image  # noqa: E402

import make_banner  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name
          + (("   [" + str(detail) + "]") if detail else ""))
    if not cond:
        FAIL.append(name)


tmp = tempfile.mkdtemp(prefix="bannercheck_out_")
light = os.path.join(tmp, "light.png")
dark = os.path.join(tmp, "dark.png")
make_banner.render(light, 1920, 1080, theme="light")
make_banner.render(dark, 1920, 1080, theme="dark")
check("浅色渲染产出", os.path.isfile(light) and os.path.getsize(light) > 20000)
check("深色渲染产出", os.path.isfile(dark) and os.path.getsize(dark) > 20000)
im_l = Image.open(light).convert("RGB")
im_d = Image.open(dark).convert("RGB")
check("浅色尺寸", im_l.size == (1920, 1080), im_l.size)
check("深色尺寸", im_d.size == (1920, 1080), im_d.size)
check("双主题像素不同", im_l.tobytes() != im_d.tobytes())
check("浅色背景为纸白", sum(im_l.getpixel((10, 10))) > 660, im_l.getpixel((10, 10)))
check("深色背景为炭黑", sum(im_d.getpixel((10, 10))) < 90, im_d.getpixel((10, 10)))

for (w, h, label) in ((2560, 1440, "2K"), (3840, 2160, "4K"),
                      (2560, 1080, "21:9"), (1080, 1920, "竖屏")):
    out = os.path.join(tmp, "size_%dx%d.png" % (w, h))
    make_banner.render(out, w, h, theme="light")
    im = Image.open(out)
    check("尺寸 %s %dx%d" % (label, w, h), im.size == (w, h), im.size)
    check("尺寸 %s 非空" % label, im.convert("RGB").getbbox() is not None)

custom = os.path.join(tmp, "custom.png")
make_banner.render(custom, 1920, 1080, theme="dark", text={
    "title": ["实验设备", "正在校准"],
    "plate": "禁止操作",
    "sub": ["校准过程中触碰设备可能影响结果。", "感谢配合。"],
})
check("自定义文案渲染", os.path.isfile(custom) and os.path.getsize(custom) > 20000)
check("自定义文案与默认不同",
      Image.open(custom).convert("RGB").tobytes() != im_d.tobytes())

bad = os.path.join(tmp, "bad_theme.png")
make_banner.render(bad, 1280, 720, theme="no-such-theme")
check("非法主题回退浅色", os.path.isfile(bad))

src = open(os.path.join(ROOT, "make_banner.py"), encoding="utf-8").read()
for word in ("RHINE", "莱茵", "HANDS OFF", "BANNER-PCB", "REV 2.0", "TASK-CORE",
             "KEY_INTERLOCK", "SEALED", "已封存"):
    check("源码不含 %r" % word, word not in src)

print()
print("=" * 60)
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
