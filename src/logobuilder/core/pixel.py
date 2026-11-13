"""像素格式转换：RGB565（bpp=2）/ BGRA8888（bpp=4）<-> Pillow 图像。

约定
----
* bpp=2：块像素是小端 ``u16`` 的 RGB565，行内无 stride 填充。
* bpp=4：块像素是小端 ``u32`` 的 .NET ``Format32bppArgb``，即文件字节序 **B,G,R,A**，
  行内无 stride 填充。
* :func:`decode_pixels` 统一输出紧凑 BGRA8888（``W*H*4``，可直接作为
  ``Image.frombytes("RGBA", (W, H), data, "raw", "BGRA")`` 的输入）。

RGB565 的定量公式沿用原版 ``Color16ToInt`` 的"线性放大"（不是标准 255/31 拉伸）：

    B = (v & 0x1F) * 8
    G = ((v >> 5) & 0x3F) * 4
    R = ((v >> 11) & 0x1F) * 8

编码为其逆运算的位运算直式：``R5 = R >> 3``、``G6 = G >> 2``、``B5 = B >> 3``，
因此 ``encode(decode(v)) == v`` 恒成立（量化稳定），这也是测试断言之一。
"""

from __future__ import annotations

import struct
from typing import Any

from PIL import Image

from .sizes import LogoSize

__all__ = [
    "decode_pixels",
    "pixels_to_image",
    "image_to_pixels",
    "encode_pixels",
    "resize_image",
    "rgb565_to_rgb888",
    "rgb888_to_rgb565",
    "SUPPORTED_FITS",
]

#: 支持的缩放方式。
SUPPORTED_FITS = ("stretch", "contain", "cover")
_FIT_ALIASES = {
    "stretch": "stretch",
    "fill": "stretch",
    "contain": "contain",
    "keep-aspect": "contain",
    "keep_aspect": "contain",
    "keepaspect": "contain",
    "pad": "contain",
    "fit": "contain",
    "cover": "cover",
    "crop": "cover",
    "fill-crop": "cover",
}

try:  # Pillow >= 9 推荐 Image.Resampling；旧版本回退到 Image.LANCZOS
    _LANCZOS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
except AttributeError:  # pragma: no cover - Pillow < 9
    _LANCZOS = Image.LANCZOS  # type: ignore[attr-defined]


def _normalize_fit(fit: str) -> str:
    try:
        normalized = _FIT_ALIASES[str(fit).strip().lower()]
    except KeyError as exc:
        raise ValueError(
            f"不支持的缩放方式 {fit!r}，可选：stretch（拉伸）/ contain（等比留白）/ cover（等比裁剪）"
        ) from exc
    return normalized


def _channel_tables() -> tuple[bytes, ...]:
    """预生成 RGB565 位域 -> 8bit 通道值的查找表。"""
    r5 = bytes((i * 8) for i in range(32))
    g6 = bytes((i * 4) for i in range(64))
    b5 = bytes((i * 8) for i in range(32))
    return r5, g6, b5


_R5, _G6, _B5 = _channel_tables()


# ----------------------------------------------------------------------
# RGB565 单像素换算
# ----------------------------------------------------------------------
def rgb565_to_rgb888(value: int) -> tuple[int, int, int]:
    """一个 RGB565 值 -> ``(r, g, b)``（0..255，线性放大公式）。"""
    value = int(value) & 0xFFFF
    return (
        ((value >> 11) & 0x1F) << 3,
        ((value >> 5) & 0x3F) << 2,
        (value & 0x1F) << 3,
    )


def rgb888_to_rgb565(r: int, g: int, b: int) -> int:
    """``(r, g, b)``（0..255）-> 一个 RGB565 值（位运算直式）。"""
    return ((int(r) >> 3) << 11) | ((int(g) >> 2) << 5) | (int(b) >> 3)


# ----------------------------------------------------------------------
# 块像素 -> BGRA / 图像
# ----------------------------------------------------------------------
def decode_pixels(pixels: bytes | bytearray | memoryview, size: LogoSize) -> bytes:
    """块像素数据 -> 紧凑 BGRA8888（``len = W*H*4``，行 = W*4，无 padding）。

    bpp=2 时执行 RGB565 展开（alpha 恒为 255）；bpp=4 时原样返回 BGRA 字节。
    """
    if not isinstance(size, LogoSize):
        raise TypeError(f"size 必须是 LogoSize，收到 {type(size).__name__}")
    data = bytes(pixels)
    expected = size.key
    if len(data) != expected:
        raise ValueError(
            f"像素数据长度 {len(data)} 与尺寸 {size}（需 {expected} 字节）不符"
        )
    if size.color_depth == 4:
        # 文件字节序就是 B,G,R,A，天然是 RGBA raw "BGRA"
        return data

    count = size.width * size.height
    values = struct.unpack(f"<{count}H", data)
    out = bytearray(count * 4)
    r5, g6, b5 = _R5, _G6, _B5
    pos = 0
    for value in values:
        out[pos] = b5[value & 0x1F]
        out[pos + 1] = g6[(value >> 5) & 0x3F]
        out[pos + 2] = r5[value >> 11]
        out[pos + 3] = 0xFF
        pos += 4
    return bytes(out)


def pixels_to_image(pixels: bytes | bytearray | memoryview, size: LogoSize) -> Image.Image:
    """块像素 -> Pillow ``RGBA`` 图像（``size.key`` 必须等于 ``len(pixels)``）。"""
    bgra = decode_pixels(pixels, size)
    return Image.frombytes("RGBA", (size.width, size.height), bgra, "raw", "BGRA")


# ----------------------------------------------------------------------
# 图像 -> 块像素
# ----------------------------------------------------------------------
def resize_image(image: Any, size: LogoSize, fit: str = "stretch") -> Image.Image:
    """把 Pillow 图像按 ``fit`` 缩放/裁剪/留白为精确 ``size.width x size.height`` 的 RGBA 图。

    * ``stretch``：直接拉伸；
    * ``contain``：等比缩放后居中留白（背景透明，bpp2 编码时视为黑色）；
    * ``cover``：等比缩放后居中裁剪。
    """
    if not isinstance(size, LogoSize):
        raise TypeError(f"size 必须是 LogoSize，收到 {type(size).__name__}")
    mode = _normalize_fit(fit)
    if not isinstance(image, Image.Image):
        raise TypeError(f"image 必须是 Pillow.Image，收到 {type(image).__name__}")
    rgba = image.convert("RGBA") if image.mode != "RGBA" else image
    target = (size.width, size.height)

    if mode == "stretch":
        return rgba.resize(target, _LANCZOS)

    source_width, source_height = rgba.size
    if source_width <= 0 or source_height <= 0:  # pragma: no cover - 防御
        raise ValueError("图像尺寸非法（宽或高为 0）")
    scale = (
        min(target[0] / source_width, target[1] / source_height)
        if mode == "contain"
        else max(target[0] / source_width, target[1] / source_height)
    )
    scaled_w = max(1, int(round(source_width * scale)))
    scaled_h = max(1, int(round(source_height * scale)))
    resized = rgba.resize((scaled_w, scaled_h), _LANCZOS)

    if mode == "contain":
        canvas = Image.new("RGBA", target, (0, 0, 0, 0))
        canvas.paste(resized, ((target[0] - scaled_w) // 2, (target[1] - scaled_h) // 2))
        return canvas

    # cover：居中裁剪
    left = max(0, (scaled_w - target[0]) // 2)
    top = max(0, (scaled_h - target[1]) // 2)
    return resized.crop((left, top, left + target[0], top + target[1]))


def image_to_pixels(
    image: Any,
    size: LogoSize,
    fit: str = "stretch",
) -> bytes:
    """Pillow 图 -> 块像素（``W*H*bpp`` 字节）。

    * bpp=4：小端 BGRA（保留 alpha，来自图像 alpha 通道）；
    * bpp=2：小端 RGB565（忽略 alpha，与 .NET 原版一致）。
    """
    if not isinstance(size, LogoSize):
        raise TypeError(f"size 必须是 LogoSize，收到 {type(size).__name__}")
    resized = resize_image(image, size, fit)
    if size.color_depth == 4:
        return resized.tobytes("raw", "BGRA")

    # RGB565：先取 RGB888，再逐像素位运算打包
    rgb = resized.convert("RGB")
    raw = rgb.tobytes()  # R,G,B,R,G,B,...
    count = size.width * size.height
    out = bytearray(count * 2)
    pos = 0
    for i in range(0, count * 3, 3):
        red = raw[i]
        green = raw[i + 1]
        blue = raw[i + 2]
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        out[pos] = value & 0xFF
        out[pos + 1] = (value >> 8) & 0xFF
        pos += 2
    return bytes(out)


def encode_pixels(image: Any, size: LogoSize, fit: str = "stretch") -> bytes:
    """``image_to_pixels`` 的别名（GUI 命名习惯）。"""
    return image_to_pixels(image, size, fit)


__all__ = [
    "decode_pixels",
    "pixels_to_image",
    "image_to_pixels",
    "encode_pixels",
    "resize_image",
    "rgb565_to_rgb888",
    "rgb888_to_rgb565",
    "SUPPORTED_FITS",
]
