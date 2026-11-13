"""老 MTK "第二屏" 裸数据 ``boot_logo`` 的读写。

裸 ``boot_logo`` 没有任何容器头，就是一段 RGB565（bpp=2）像素数组::

    len(file) = width * height * 2

因此尺寸只能由长度反推：先查 sizes.txt，再用像素总数因式分解 + 常见分辨率启发式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from . import pixel
from .container import _atomic_write
from .errors import CorruptContainer, ExportError, UnknownImageSize
from .sizes import LogoSize, SizeDB

__all__ = [
    "read_boot_logo",
    "write_boot_logo",
    "infer_boot_logo_size",
]

#: 常见手机分辨率宽度（用于推断打分）。
_COMMON_WIDTHS = frozenset({240, 320, 360, 480, 540, 600, 720, 768, 800, 1080, 1200, 1280, 1440})
#: 常见手机分辨率高度。
_COMMON_HEIGHTS = frozenset({320, 480, 568, 640, 800, 854, 960, 1024, 1280, 1334, 1440, 1600, 1920, 2340, 2400, 2560})


def _iter_factor_pairs(pixels: int, *, max_side: int = 8192, min_side: int = 2) -> list[tuple[int, int]]:
    """枚举像素总数的 (宽, 高) 对（两侧都限制在合理范围）。"""
    pairs: list[tuple[int, int]] = []
    if pixels <= 0:
        return pairs
    import math

    for width in range(min_side, min(math.isqrt(pixels), max_side) + 1):
        if pixels % width:
            continue
        height = pixels // width
        if height > max_side:
            continue
        pairs.append((width, height))
        if width != height:
            pairs.append((height, width))
    return pairs


def _score_pair(width: int, height: int) -> float:
    """给"像不像真实手机分辨率"打分（越大越可信）。"""
    score = 0.0
    if width % 8 == 0:
        score += 1.5
    if height % 8 == 0:
        score += 1.0
    if width in _COMMON_WIDTHS:
        score += 2.0
    if height in _COMMON_HEIGHTS:
        score += 2.0
    if width % 16 == 0 and height % 16 == 0:
        score += 0.5
    if width < height:  # 竖屏
        score += 0.8
    aspect = width / height
    score += max(0.0, 1.0 - abs(aspect - 0.55) * 2.0)  # 手机竖屏常见宽高比 ~0.45..0.65
    return score


def infer_boot_logo_size(length: int) -> LogoSize:
    """由裸 boot_logo 文件长度推断 ``LogoSize``（仅 RGB565）。

    优先级：

    1. sizes.txt 精确命中且色深=2；
    2. 因式分解 + 常见分辨率启发式（唯一最高分）；
    3. 都失败抛 :class:`UnknownImageSize`。
    """
    try:
        length = int(length)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"length 必须是整数，收到 {length!r}") from exc
    if length <= 0:
        raise CorruptContainer("boot_logo 文件为空（0 字节）")
    if length % 2:
        raise CorruptContainer(
            f"boot_logo 数据长度 {length} 不是偶数，无法作为 RGB565（每像素 2 字节）解析",
            hint="请确认文件是裸 boot_logo，而不是带头的 logo.bin。",
        )

    database = SizeDB.default()
    exact = [size for size in database.exact(length) if size.color_depth == 2]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        exact.sort(key=lambda s: (-_score_pair(s.width, s.height), s.width, s.height))
        return exact[0]

    pixels = length // 2
    candidates = _iter_factor_pairs(pixels)
    if not candidates:
        raise UnknownImageSize(
            f"无法从长度 {length} 字节（{pixels} 像素）推断分辨率",
            hint="请在创建第二屏时手动指定分辨率（宽 x 高）。",
            context={"length": length, "pixel_count": pixels},
        )
    scored = sorted(
        ((_score_pair(width, height), width, height) for width, height in candidates),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    best_score, best_width, best_height = scored[0]
    # 分数没有明显优势时也返回最优候选（GUI 会先让用户确认）
    if best_score <= 0:
        raise UnknownImageSize(
            f"无法从长度 {length} 字节推断分辨率（候选过多）",
            hint="请在创建第二屏时手动指定分辨率（宽 x 高）。",
            context={
                "length": length,
                "pixel_count": pixels,
                "candidates": [f"{w}x{h}" for _score, w, h in scored[:8]],
            },
        )
    return LogoSize(best_width, best_height, 2)


def read_boot_logo(
    path: str | Path,
    size: LogoSize | None = None,
) -> tuple[Image.Image, LogoSize]:
    """读取裸 ``boot_logo`` 文件，返回 ``(RGBA 图像, 推断/指定的尺寸)``。"""
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise ExportError(
            f"无法读取 boot_logo 文件：{target}（{exc}）",
            hint="请检查文件是否存在、是否有读取权限。",
            context={"path": str(target)},
        ) from exc
    if not data:
        raise CorruptContainer("boot_logo 文件为空（0 字节）")
    if size is not None:
        if not isinstance(size, LogoSize):
            raise TypeError(f"size 必须是 LogoSize 或 None，收到 {type(size).__name__}")
        if size.color_depth != 2:
            raise ValueError("裸 boot_logo 固定为 RGB565（色深必须是 2）")
        if size.key != len(data):
            raise CorruptContainer(
                f"指定尺寸 {size} 需要 {size.key} 字节，但文件长度为 {len(data)} 字节",
                hint="请确认分辨率/色深是否正确，或让程序按长度自动推断。",
                context={"length": len(data), "expected": size.key},
            )
    else:
        size = infer_boot_logo_size(len(data))
    return pixel.pixels_to_image(data, size), size


def write_boot_logo(
    image: Any,
    path: str | Path,
    size: LogoSize | None = None,
) -> LogoSize:
    """把 Pillow 图像写成裸 ``boot_logo``（RGB565，无头），返回实际使用的尺寸。"""
    if not isinstance(image, Image.Image):
        raise TypeError(f"image 必须是 Pillow.Image，收到 {type(image).__name__}")
    if size is None:
        size = LogoSize(int(image.width), int(image.height), 2)
    elif not isinstance(size, LogoSize):
        raise TypeError(f"size 必须是 LogoSize 或 None，收到 {type(size).__name__}")
    if size.color_depth != 2:
        raise ValueError("裸 boot_logo 固定为 RGB565（色深必须是 2）")
    data = pixel.image_to_pixels(image, size, "stretch")
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - mkdir 失败
        raise ExportError(
            f"无法创建目录：{target.parent}（{exc}）",
            context={"path": str(target.parent)},
        ) from exc
    _atomic_write(target, data)
    return size
