"""从提供的挂锁图案提取透明轮廓，按横幅配色显示单色图标。"""

from PIL import Image, ImageChops

ARTWORK_NAME = "极简黑色挂锁图标.png"
MASK_NAME = "unlock-lock-mask.png"


def make_lock_mask(path):
    with Image.open(path) as source:
        artwork = source.convert("RGBA")
    # 保留黑色轮廓及边缘过渡，去掉原图略有纹理的白色背景。
    mask = artwork.convert("L").point(
        lambda value: max(0, min(255, round((192 - value) * 255 / 128))))
    mask = ImageChops.multiply(mask, artwork.getchannel("A"))
    bounds = mask.getbbox()
    if not bounds:
        raise ValueError("挂锁图案没有可见轮廓")
    mask = mask.crop(bounds)
    mask.thumbnail((96, 96), Image.LANCZOS)
    return mask


def render_lock(mask, size, dark):
    alpha = mask.copy()
    alpha.thumbnail((size, size), Image.LANCZOS)
    icon = Image.new("RGBA", alpha.size, "#FFFFFF" if dark else "#000000")
    icon.putalpha(alpha)
    return icon
