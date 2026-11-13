"""像素格式转换测试（RGB565 / BGRA8888）。"""

from __future__ import annotations

import random
import struct

import pytest
from PIL import Image

from logobuilder.core import (
    LogoSize,
    decode_pixels,
    encode_pixels,
    image_to_pixels,
    pixels_to_image,
    resize_image,
    rgb565_to_rgb888,
    rgb888_to_rgb565,
)


def _random_image(width: int, height: int, seed: int = 1234, *, alpha: bool = True) -> Image.Image:
    rng = random.Random(seed)
    image = Image.new("RGBA", (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (
                rng.randrange(256),
                rng.randrange(256),
                rng.randrange(256),
                rng.choice([0, 1, 64, 128, 254, 255]) if alpha else 255,
            )
    return image


# ----------------------------------------------------------------------
# RGB565 公式
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "value,expected",
    [
        (0x0000, (0, 0, 0)),
        (0xFFFF, (248, 252, 248)),
        (0xF800, (248, 0, 0)),
        (0x07E0, (0, 252, 0)),
        (0x001F, (0, 0, 248)),
        (0x8000, (128, 0, 0)),
    ],
)
def test_rgb565_to_rgb888_linear_formula(value, expected):
    assert rgb565_to_rgb888(value) == expected


@pytest.mark.parametrize(
    "rgb,expected",
    [
        ((0, 0, 0), 0x0000),
        ((255, 255, 255), 0xFFFF),
        ((255, 0, 0), 0xF800),
        ((0, 255, 0), 0x07E0),
        ((0, 0, 255), 0x001F),
        ((8, 4, 8), 0x0821),
    ],
)
def test_rgb888_to_rgb565_shift_formula(rgb, expected):
    assert rgb888_to_rgb565(*rgb) == expected


def test_rgb565_roundtrip_stable():
    for value in range(0, 0x10000, 97):
        red, green, blue = rgb565_to_rgb888(value)
        assert rgb888_to_rgb565(red, green, blue) == value


# ----------------------------------------------------------------------
# decode_pixels
# ----------------------------------------------------------------------
def test_decode_pixels_bpp2():
    size = LogoSize(2, 2, 2)
    pixels = struct.pack("<4H", 0x0000, 0xFFFF, 0xF800, 0x001F)
    bgra = decode_pixels(pixels, size)
    assert len(bgra) == size.key * 2
    expected = [
        (0, 0, 0, 255),
        (248, 252, 248, 255),
        (0, 0, 248, 255),  # B=248
        (248, 0, 0, 255),  # R=248
    ]
    for index, color in enumerate(expected):
        assert tuple(bgra[index * 4 : index * 4 + 4]) == color


def test_decode_pixels_bpp4_passthrough():
    size = LogoSize(2, 1, 4)
    raw = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    assert decode_pixels(raw, size) == raw


def test_decode_pixels_length_mismatch():
    with pytest.raises(ValueError):
        decode_pixels(b"\x00" * 7, LogoSize(2, 2, 2))
    with pytest.raises(TypeError):
        decode_pixels(b"\x00" * 16, "540x960x4")


# ----------------------------------------------------------------------
# 图像 <-> 像素
# ----------------------------------------------------------------------
def test_bpp4_roundtrip_exact():
    image = _random_image(64, 48)
    size = LogoSize(64, 48, 4)
    pixels = image_to_pixels(image, size)
    assert len(pixels) == size.key
    decoded = pixels_to_image(pixels, size)
    assert decoded.mode == "RGBA"
    assert decoded.size == (64, 48)
    assert decoded.tobytes() == image.tobytes()


def test_bpp4_preserves_alpha():
    size = LogoSize(2, 1, 4)
    image = Image.new("RGBA", (2, 1))
    image.putpixel((0, 0), (10, 20, 30, 0))
    image.putpixel((1, 0), (40, 50, 60, 255))
    pixels = image_to_pixels(image, size)
    assert pixels[3] == 0
    assert pixels[7] == 255
    decoded = pixels_to_image(pixels, size)
    assert decoded.getpixel((0, 0)) == (10, 20, 30, 0)


def test_bpp2_quantization_stable():
    image = _random_image(32, 24)
    size = LogoSize(32, 24, 2)
    first = image_to_pixels(image, size)
    assert len(first) == size.key
    quantized = pixels_to_image(first, size)
    second = image_to_pixels(quantized, size)
    assert first == second


def test_bpp2_decoded_values_are_quantized():
    image = Image.new("RGBA", (4, 1))
    colors = [(255, 255, 255, 255), (0, 0, 0, 255), (255, 0, 0, 255), (0, 255, 0, 255)]
    for x, color in enumerate(colors):
        image.putpixel((x, 0), color)
    size = LogoSize(4, 1, 2)
    decoded = pixels_to_image(image_to_pixels(image, size), size)
    assert decoded.getpixel((0, 0))[:3] == (248, 252, 248)
    assert decoded.getpixel((1, 0))[:3] == (0, 0, 0)
    assert decoded.getpixel((2, 0))[:3] == (248, 0, 0)
    assert decoded.getpixel((3, 0))[:3] == (0, 252, 0)


def test_encode_pixels_alias():
    image = _random_image(8, 8)
    size = LogoSize(8, 8, 4)
    assert encode_pixels(image, size) == image_to_pixels(image, size)


def test_pixels_to_image_wrong_length():
    with pytest.raises(ValueError):
        pixels_to_image(b"\x00" * 10, LogoSize(4, 4, 4))


# ----------------------------------------------------------------------
# 缩放
# ----------------------------------------------------------------------
@pytest.mark.parametrize("fit", ["stretch", "contain", "cover", "keep-aspect", "pad", "crop"])
def test_resize_image_modes(fit):
    image = _random_image(100, 50)
    size = LogoSize(40, 40, 4)
    resized = resize_image(image, size, fit)
    assert resized.size == (40, 40)
    assert resized.mode == "RGBA"


def test_resize_contain_letterbox_transparent():
    image = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
    resized = resize_image(image, LogoSize(40, 40, 4), "contain")
    # 上下应有透明留白，中间是红色
    assert resized.getpixel((20, 0))[3] == 0
    assert resized.getpixel((20, 20))[:3] == (255, 0, 0)


def test_resize_cover_crops():
    image = Image.new("RGBA", (100, 50), (255, 0, 0, 255))
    resized = resize_image(image, LogoSize(40, 40, 4), "cover")
    assert all(resized.getpixel((x, y))[:3] == (255, 0, 0) for x in (0, 39) for y in (0, 39))


def test_resize_invalid_fit():
    with pytest.raises(ValueError):
        resize_image(Image.new("RGBA", (10, 10)), LogoSize(5, 5, 4), "stretch-to-fill")


def test_resize_non_image():
    with pytest.raises(TypeError):
        resize_image("not-an-image", LogoSize(5, 5, 4))  # type: ignore[arg-type]


def test_full_size_rgb565_encode_decode_speed_and_sanity():
    """540x960（真实 logo.bin 块尺寸）的 bpp2 编解码不失控且量化稳定。"""
    size = LogoSize(540, 960, 2)
    image = _random_image(540, 960, seed=7, alpha=False)
    first = image_to_pixels(image, size)
    assert len(first) == size.key
    decoded = pixels_to_image(first, size)
    second = image_to_pixels(decoded, size)
    assert first == second
