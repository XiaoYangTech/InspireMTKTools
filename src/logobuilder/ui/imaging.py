"""PIL ↔ Tk 图像辅助：缩略图、占位图、棋盘格背景、等比缩放计算。

所有函数都是纯计算（除 :func:`to_photo` 需要 Tk root），便于单元测试。
"""

from __future__ import annotations

import math
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageTk

# ── 字体（用于在占位图/缩略图上写字）─────────────────────────
_FONT_PATHS = (
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\segoeui.ttf",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


@lru_cache(maxsize=8)
def _load_font(size: int) -> Any:
    for path in _FONT_PATHS:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, max(6, int(size)))
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=max(6, int(size)))
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


# ── 基础转换 ───────────────────────────────────────────────
def to_rgba(image: Image.Image) -> Image.Image:
    if image.mode == "RGBA":
        return image
    return image.convert("RGBA")


def flatten_on(image: Image.Image, color: str) -> Image.Image:
    """把带透明通道的图像铺到底色上（生成缩略图用）。"""
    rgba = to_rgba(image)
    background = Image.new("RGBA", rgba.size, color)
    background.alpha_composite(rgba)
    return background.convert("RGB")


def to_photo(master: Any, image: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(image, master=master)


def make_thumbnail(
    image: Image.Image,
    box: int,
    *,
    bg: str = "#ffffff",
    pad: int = 2,
    mode: str = "contain",
) -> Image.Image:
    """生成固定 ``box × box`` 的缩略图（居中、留白、铺底色）。

    ``mode=contain`` 等比缩放到框内；``mode=cover`` 等比裁剪铺满。
    """
    box = max(4, int(box))
    pad = max(0, int(pad))
    inner = max(1, box - pad * 2)
    rgba = to_rgba(image)
    if mode == "cover":
        scale = max(inner / max(1, rgba.width), inner / max(1, rgba.height))
    else:
        scale = min(inner / max(1, rgba.width), inner / max(1, rgba.height))
    scale = max(scale, 0.0001)
    new_size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
    resized = rgba.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (box, box), bg)
    offset = ((box - new_size[0]) // 2, (box - new_size[1]) // 2)
    if mode == "cover":
        # 居中裁剪
        left = max(0, (new_size[0] - inner) // 2)
        top = max(0, (new_size[1] - inner) // 2)
        resized = resized.crop((left, top, left + inner, top + inner))
        offset = ((box - resized.width) // 2, (box - resized.height) // 2)
    canvas.alpha_composite(resized, offset)
    return canvas.convert("RGB")


def make_placeholder(
    box: int,
    text: str,
    *,
    bg: str = "#eef1f5",
    fg: str = "#6b7280",
    accent: str = "#2563eb",
) -> Image.Image:
    """生成占位缩略图：圆角底 + 简短文字（BIN / ? / PNG）。"""
    box = max(8, int(box))
    image = Image.new("RGBA", (box, box), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = max(3, box // 6)
    draw.rounded_rectangle(
        (0, 0, box - 1, box - 1), radius=radius, fill=bg, outline=accent, width=max(1, box // 32)
    )
    font_size = max(7, int(box * 0.30))
    font = _load_font(font_size)
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw = th = font_size
    draw.text(
        ((box - tw) / 2, (box - th) / 2 - box * 0.02),
        text,
        font=font,
        fill=fg,
    )
    return image.convert("RGB")


def make_checkerboard(
    width: int,
    height: int,
    color_a: str,
    color_b: str,
    cell: int = 12,
) -> Image.Image:
    """生成棋盘格背景（预览透明区域用），尺寸至少 1×1。"""
    width = max(1, int(width))
    height = max(1, int(height))
    cell = max(2, int(cell))
    image = Image.new("RGB", (width, height), color_a)
    draw = ImageDraw.Draw(image)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if ((x // cell) + (y // cell)) % 2:
                draw.rectangle((x, y, min(x + cell - 1, width - 1), min(y + cell - 1, height - 1)), fill=color_b)
    return image


def fit_scale(
    image_size: tuple[int, int],
    box_size: tuple[int, int],
    *,
    allow_upscale: bool = True,
) -> float:
    """计算自适应缩放的倍率（``contain`` 语义）。"""
    iw, ih = max(1, image_size[0]), max(1, image_size[1])
    bw, bh = max(1, box_size[0]), max(1, box_size[1])
    scale = min(bw / iw, bh / ih)
    if not allow_upscale:
        scale = min(scale, 1.0)
    return max(scale, 0.0001)


def format_dimensions(size: tuple[int, int]) -> str:
    return f"{size[0]} × {size[1]}"


def scaled_size(image_size: tuple[int, int], scale: float) -> tuple[int, int]:
    return (max(1, round(image_size[0] * scale)), max(1, round(image_size[1] * scale)))


def platform_reveal_command(path: Path) -> list[str] | None:
    """返回「在文件管理器中打开」命令；未知平台返回 ``None``。"""
    directory = path if path.is_dir() else path.parent
    if sys.platform == "win32":
        return ["explorer", str(directory)]
    if sys.platform == "darwin":
        return ["open", str(directory)]
    if sys.platform.startswith("linux"):
        return ["xdg-open", str(directory)]
    return None


# ── 数学辅助（测试用）──────────────────────────────────────
def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def aspect_ratio(size: tuple[int, int]) -> float:
    width, height = size
    if not width or not height:
        return 1.0
    return width / height


def nearest_supported_size(
    candidates: list[tuple[int, int]], target: tuple[int, int]
) -> tuple[int, int] | None:
    """从候选里找与目标宽高比最接近的一项（自定义输入时的提示用）。"""
    if not candidates:
        return None
    target_ratio = aspect_ratio(target)

    def score(item: tuple[int, int]) -> float:
        ratio = aspect_ratio(item)
        return abs(math.log(max(ratio, 1e-6) / max(target_ratio, 1e-6)))

    return min(candidates, key=score)
