"""裸第二屏 boot_logo 读写测试。"""

from __future__ import annotations

import struct

import pytest
from PIL import Image

from logobuilder.core import (
    CorruptContainer,
    ExportError,
    LogoSize,
    UnknownImageSize,
    image_to_pixels,
    infer_boot_logo_size,
    read_boot_logo,
    write_boot_logo,
)


def make_image(width: int, height: int, color=(20, 40, 60, 255)) -> Image.Image:
    return Image.new("RGBA", (width, height), color)


def test_write_read_roundtrip_known_size(tmp_path):
    size = LogoSize(320, 480, 2)  # 307,200 字节，内置尺寸表精确命中
    image = make_image(size.width, size.height, (255, 0, 0, 255))
    path = tmp_path / "boot_logo"
    used = write_boot_logo(image, path, size)
    assert used == size
    assert path.stat().st_size == size.key
    read_image, read_size = read_boot_logo(path, size)
    assert read_size == size
    assert read_image.size == (320, 480)
    assert read_image.mode == "RGBA"
    # 读回的像素与"写出的像素再解码"逐字节一致
    assert image_to_pixels(read_image, size) == path.read_bytes()


def test_write_without_size_uses_image_dimensions(tmp_path):
    image = make_image(320, 480)
    path = tmp_path / "boot_logo"
    used = write_boot_logo(image, path)
    assert used == LogoSize(320, 480, 2)
    assert path.stat().st_size == 320 * 480 * 2
    # 读回时按内置表可推断回同一尺寸
    read_image, read_size = read_boot_logo(path)
    assert read_size == LogoSize(320, 480, 2)
    assert read_image.size == (320, 480)


def test_write_resizes_to_requested_size(tmp_path):
    image = make_image(100, 50, (0, 255, 0, 255))
    size = LogoSize(320, 480, 2)
    used = write_boot_logo(image, tmp_path / "boot_logo", size)
    assert used == size
    assert (tmp_path / "boot_logo").stat().st_size == size.key


def test_infer_boot_logo_size_from_db():
    assert infer_boot_logo_size(320 * 480 * 2) == LogoSize(320, 480, 2)
    assert infer_boot_logo_size(240 * 320 * 2) == LogoSize(240, 320, 2)


def test_infer_boot_logo_size_unknown_length():
    size = infer_boot_logo_size(1080 * 2400 * 2)
    assert size.color_depth == 2
    assert size.key == 1080 * 2400 * 2
    assert size.width < size.height  # 竖屏启发式


def test_infer_boot_logo_size_errors():
    with pytest.raises(CorruptContainer):
        infer_boot_logo_size(0)
    with pytest.raises(CorruptContainer):
        infer_boot_logo_size(3)  # 奇数
    with pytest.raises(ValueError):
        infer_boot_logo_size("abc")  # type: ignore[arg-type]


def test_read_errors(tmp_path):
    with pytest.raises(ExportError):
        read_boot_logo(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    with pytest.raises(CorruptContainer):
        read_boot_logo(empty)
    odd = tmp_path / "odd"
    odd.write_bytes(b"\x00" * 5)
    with pytest.raises(CorruptContainer):
        read_boot_logo(odd)
    good = tmp_path / "good"
    good.write_bytes(b"\x00" * (320 * 480 * 2))
    with pytest.raises(ValueError):
        read_boot_logo(good, LogoSize(320, 480, 4))
    with pytest.raises(CorruptContainer):
        read_boot_logo(good, LogoSize(240, 320, 2))


def test_write_errors(tmp_path):
    with pytest.raises(TypeError):
        write_boot_logo("not-an-image", tmp_path / "boot_logo")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        write_boot_logo(make_image(320, 480), tmp_path / "boot_logo", LogoSize(320, 480, 4))


def test_write_creates_parent_dirs(tmp_path):
    output = tmp_path / "a" / "b" / "boot_logo"
    used = write_boot_logo(make_image(320, 480), output)
    assert output.is_file()
    assert used == LogoSize(320, 480, 2)


def test_rgb565_bytes_are_little_endian(tmp_path):
    size = LogoSize(320, 480, 2)
    image = Image.new("RGBA", (320, 480), (255, 0, 0, 255))  # 纯红 -> 0xF800 -> bytes 00 F8
    path = tmp_path / "boot_logo"
    write_boot_logo(image, path, size)
    data = path.read_bytes()
    assert data[:2] == struct.pack("<H", 0xF800)
