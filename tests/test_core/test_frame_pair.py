# -*- coding: utf-8 -*-
"""左右双帧（wide block = 2 frames）支持的单元测试。"""
from __future__ import annotations

import zlib

import pytest
from PIL import Image, ImageDraw

from logobuilder.core import ImageBlock, LogoContainer, LogoSize
from logobuilder.core import pixel

from helpers import make_container_bytes


def _draw_pair(fw: int = 240, h: int = 160, *, shift: int = 0) -> bytes:
    """画一张 480x160 的图：左右各 240x160，两个几乎相同的白色方块（模拟两帧动画）。"""
    canvas = Image.new("RGBA", (fw * 2, h), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    for frame in range(2):
        x0 = frame * fw + 70 + (shift if frame else 0)
        draw.rectangle((x0, 50, x0 + 100, 110), fill=(255, 255, 255, 255))
    return pixel.image_to_pixels(canvas, LogoSize(fw * 2, h, 4))


def _noise(width: int, height: int) -> bytes:
    data = bytearray()
    value = 1
    for _ in range(width * height):
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        data.extend((value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, 0xFF))
    return bytes(data)


def test_pair_layout_detected_on_known_wide_block():
    blob = make_container_bytes([_draw_pair()])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    block = container.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.size == LogoSize(480, 160, 4)
    assert block.frame_count == 2
    assert block.frame_width == 240
    assert block.frame_size == LogoSize(240, 160, 4)
    frames = block.frames()
    assert [frame.size for frame in frames] == [(240, 160), (240, 160)]
    assert any("双帧" in warning for warning in container.warnings)


def test_wide_block_with_different_halves_stays_single():
    left = _noise(240, 160)
    right = _noise(240, 160)[::-1]
    pixels = bytearray()
    for y in range(160):
        pixels.extend(left[y * 240 * 4 : (y + 1) * 240 * 4])
        pixels.extend(right[y * 240 * 4 : (y + 1) * 240 * 4])
    # 该长度 307200 在 sizes.txt 中对应 480x160x4；左右内容完全不同，不应拆帧
    blob = make_container_bytes([bytes(pixels)])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    block = container.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.frame_count == 1


def test_non_wide_block_is_not_split():
    # 320x240x4 宽高比 < 2，即使左右两半相似也不拆
    canvas = Image.new("RGBA", (320, 240), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((60, 80, 260, 160), fill=(255, 255, 255, 255))
    pixels = pixel.image_to_pixels(canvas, LogoSize(320, 240, 4))
    blob = make_container_bytes([pixels])
    container = LogoContainer.parse(blob, block_sizes=[LogoSize(320, 240, 4)])
    assert container.blocks[0].frame_count == 1


def test_get_and_set_frame():
    blob = make_container_bytes([_draw_pair()])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    block = container.blocks[0]
    frame0_before = block.get_frame(0).tobytes()
    frame1_before = block.get_frame(1).tobytes()

    red = Image.new("RGBA", (240, 160), (255, 0, 0, 255))
    block.set_frame(1, red)

    assert block.modified is True
    assert block.get_frame(0).tobytes() == frame0_before  # 帧 0 未被改动
    assert block.get_frame(1).tobytes() != frame1_before
    assert block.get_frame(1).getpixel((10, 10)) == (255, 0, 0, 255)
    assert len(block.pixels) == block.size.key


def test_set_frame_index_validation():
    blob = make_container_bytes([_draw_pair()])
    block = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    ).blocks[0]
    with pytest.raises(IndexError):
        block.set_frame(2, Image.new("RGBA", (240, 160)))
    with pytest.raises(IndexError):
        block.get_frame(-3)


def test_unmodified_pair_block_repack_is_byte_identical():
    blob = make_container_bytes([_draw_pair()])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    assert container.to_bytes() == blob


def test_project_persists_frame_count(tmp_path):
    from logobuilder.core.project import LogoProject

    blob = make_container_bytes([_draw_pair()])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    project_dir = tmp_path / "proj"
    project = LogoProject.new(project_dir, container)

    assert project.blocks[0].frame_count == 2
    text = (project_dir / "project.json").read_text(encoding="utf-8")
    assert '"frames": 2' in text

    reopened = LogoProject.open(project_dir)
    assert reopened.blocks[0].frame_count == 2
    rebuilt = reopened.to_container()
    rebuilt_block = rebuilt.blocks[0]
    assert isinstance(rebuilt_block, ImageBlock)
    assert rebuilt_block.frame_count == 2
    assert rebuilt_block.frames()[0].size == (240, 160)

def test_sparse_but_different_content_not_split():
    """宽图左右都只有零散内容、但位置完全不同 -> 不能误判为双帧。"""
    canvas = Image.new("RGBA", (480, 160), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((20, 50, 60, 110), fill=(255, 255, 255, 255))
    draw.rectangle((180, 50, 220, 110), fill=(255, 255, 255, 255))
    pixels = pixel.image_to_pixels(canvas, LogoSize(480, 160, 4))
    blob = make_container_bytes([pixels])
    container = LogoContainer.parse(
        blob, block_sizes=[LogoSize(480, 160, 4)], detect_frames=True
    )
    block = container.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.frame_count == 1

def test_pair_detection_disabled_by_default():
    """默认不自动拆双帧（避免把普通宽图误判成两帧），需显式 detect_frames=True。"""
    blob = make_container_bytes([_draw_pair()])
    container = LogoContainer.parse(blob, block_sizes=[LogoSize(480, 160, 4)])
    assert container.blocks[0].frame_count == 1
