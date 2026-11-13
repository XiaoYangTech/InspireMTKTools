"""图像辅助函数测试（不依赖显示）。"""

from __future__ import annotations

import pytest

from PIL import Image

from logobuilder.ui.imaging import (
    aspect_ratio,
    clamp,
    fit_scale,
    flatten_on,
    format_dimensions,
    make_checkerboard,
    make_placeholder,
    make_thumbnail,
    nearest_supported_size,
    scaled_size,
)


def test_make_thumbnail_contain():
    image = Image.new("RGB", (960, 540), "#ff0000")
    thumb = make_thumbnail(image, 64, bg="#ffffff")
    assert thumb.size == (64, 64)
    assert thumb.mode == "RGB"
    # 上下留白（16:9 图片放进正方形）
    assert thumb.getpixel((32, 2)) == (255, 255, 255)


def test_make_thumbnail_cover():
    image = Image.new("RGB", (960, 540), "#00ff00")
    thumb = make_thumbnail(image, 64, bg="#000000", mode="cover")
    assert thumb.size == (64, 64)
    assert thumb.getpixel((32, 32)) == (0, 255, 0)


def test_make_thumbnail_handles_rgba():
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    thumb = make_thumbnail(image, 32, bg="#123456")
    assert thumb.size == (32, 32)
    assert thumb.getpixel((1, 1))[0] < 60  # 透明像素被铺到深色底上


def test_make_placeholder_and_checkerboard():
    placeholder = make_placeholder(48, "BIN")
    assert placeholder.size == (48, 48)
    checker = make_checkerboard(37, 21, "#ffffff", "#000000", cell=8)
    assert checker.size == (37, 21)
    # 相邻棋盘格颜色应不同
    assert checker.getpixel((1, 1)) != checker.getpixel((9, 1))


def test_fit_scale_and_scaled_size():
    scale = fit_scale((100, 50), (200, 200))
    assert scale == pytest.approx(2.0)
    assert scaled_size((100, 50), scale) == (200, 100)
    assert fit_scale((100, 50), (200, 200), allow_upscale=False) == 1.0
    assert scaled_size((10, 10), 0.33) >= (1, 1)


def test_format_dimensions_and_ratio_helpers():
    assert format_dimensions((540, 960)) == "540 × 960"
    assert clamp(5, 0, 1) == 1
    assert aspect_ratio((400, 200)) == pytest.approx(2.0)
    assert aspect_ratio((0, 0)) == 1.0
    # 720x1280 与 540x960 宽高比完全一致，应优先于 480x800
    assert nearest_supported_size([(480, 800), (720, 1280)], (540, 960)) == (720, 1280)
    assert nearest_supported_size([], (540, 960)) is None


def test_flatten_on_removes_alpha():
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 0))
    flat = flatten_on(image, "#ffffff")
    assert flat.mode == "RGB"
    assert flat.getpixel((0, 0)) == (255, 255, 255)
