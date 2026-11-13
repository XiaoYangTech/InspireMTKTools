"""自检 / 截图模式用的演示数据生成器。

仅用于 ``--self-test`` 与 ``--screenshot``（以及测试），不参与正常 UI 逻辑；
所有格式数据都由 core 的公共 API 构造，UI 不重复实现压缩/像素格式。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .imaging import _load_font  # 复用字体探测

# 选用的分辨率都来自内置 sizes.txt，保证解析时能自动识别
_DEMO_SIZES: tuple[tuple[int, int, int, str], ...] = (
    (540, 960, 4, "LOGO"),
    (540, 960, 2, "第二屏"),
    (480, 800, 2, "开机"),
    (304, 52, 2, "进度条"),
    (135, 24, 4, "小图"),
    (480, 854, 4, "备用"),
)


def _gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line(((0, y), (width, y)), fill=color)
    return image


def _draw_center_text(
    image: Image.Image,
    text: str,
    *,
    size_ratio: float = 0.16,
    color: str = "#ffffff",
    offset_ratio: float = 0.0,
) -> None:
    draw = ImageDraw.Draw(image)
    font_size = max(8, int(min(image.size) * size_ratio))
    font = _load_font(font_size)
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = font_size * len(text), font_size
    x = (image.width - tw) / 2
    y = (image.height - th) / 2 + image.height * offset_ratio
    draw.text((x, y), text, font=font, fill=color)


def build_demo_image(size: tuple[int, int], index: int) -> Image.Image:
    """生成一张好看的演示图（渐变色块 + 文字，模拟开机 Logo 风格）。"""
    palettes = (
        ((14, 22, 44), (37, 99, 235)),
        ((255, 255, 255), (236, 240, 248)),
        ((6, 95, 70), (16, 185, 129)),
        ((17, 24, 39), (75, 85, 99)),
        ((124, 45, 18), (249, 115, 22)),
        ((30, 27, 75), (139, 92, 246)),
    )
    top, bottom = palettes[index % len(palettes)]
    image = _gradient(size, top, bottom)
    if sum(top) > 600:  # 浅色背景用深色文字
        text_color = "#0f172a"
    else:
        text_color = "#f8fafc"
    labels = ("亿方工具箱", "联发科", "开机 Logo", "logo.bin", "第二屏", "LOGO")
    text = labels[index % len(labels)]
    _draw_center_text(image, text, size_ratio=0.13, color=text_color)
    # 底部小字，增加层次
    _draw_center_text(
        image,
        f"{size[0]} × {size[1]}",
        size_ratio=0.045,
        color=text_color,
        offset_ratio=0.22,
    )
    return image


def build_container_header() -> bytes:
    """构造 512 字节 LOGO 头（magic 等由 core 打包时写入）。"""
    header = bytearray(512)
    header[0:4] = (0x58881688).to_bytes(4, "little")
    header[8:12] = b"LOGO"
    for index in range(16, 512):
        header[index] = 0xFF if index % 3 else 0x00
    return bytes(header)


def build_demo_container(core: Any) -> Any:
    """用 core 公共 API 组装一个小型演示容器（6 图像块 + 1 二进制块）。"""
    blocks: list[Any] = []
    for index, (width, height, depth, _label) in enumerate(_DEMO_SIZES):
        size = core.LogoSize(width, height, depth)
        image = build_demo_image((width, height), index)
        pixels = core.image_to_pixels(image, size, fit="stretch")
        blocks.append(core.ImageBlock(index=index, size=size, pixels=pixels, raw_compressed=None))
    binary_index = len(blocks)
    binary_data = b"\x00BIN" + bytes(range(256)) * 2
    blocks.append(
        core.BinaryBlock(
            index=binary_index,
            data=binary_data,
            raw_compressed=None,
            decompressed_ok=False,
        )
    )
    try:
        return core.LogoContainer(header=build_container_header(), tag="LOGO", blocks=blocks)
    except TypeError:
        container = core.LogoContainer(build_container_header(), "LOGO", blocks)
        return container


def build_unknown_container(core: Any, *, width: int = 123, height: int = 45, depth: int = 4) -> Any:
    """构造一个「解压尺寸不在内置尺寸表」的容器，用于自检未知尺寸路径。"""
    size = core.LogoSize(width, height, depth)
    image = build_demo_image((width, height), 4)
    pixels = core.image_to_pixels(image, size, fit="stretch")
    block = core.ImageBlock(index=0, size=size, pixels=pixels, raw_compressed=None)
    header = build_container_header()
    return core.LogoContainer(header=header, tag="LOGO", blocks=[block])


def build_replace_image() -> Image.Image:
    """替换图片对话框演示用源图（横向、有明显颜色变化）。"""
    width, height = 960, 640
    image = _gradient((width, height), (12, 74, 110), (250, 204, 21))
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * 0.12, height * 0.18, width * 0.52, height * 0.82), fill=(37, 99, 235))
    draw.rounded_rectangle(
        (width * 0.55, height * 0.3, width * 0.92, height * 0.7),
        radius=24,
        fill=(248, 250, 252),
    )
    _draw_center_text(image, "YOUR LOGO", size_ratio=0.09, color="#0f172a", offset_ratio=0.0)
    return image


@dataclass
class DemoCandidate:
    """分辨率对话框演示用的候选对象（与 core.SizeCandidate 属性兼容）。"""

    size: Any
    score: float
    reason: str


def build_resolution_candidates(core: Any, db: Any | None = None) -> tuple[list[Any], int, bytes]:
    """构造分辨率对话框演示数据：返回 (候选列表, 解压字节数, 原始像素)。"""
    size = core.LogoSize(480, 854, 4)
    image = build_demo_image((480, 854), 1)
    pixels = core.image_to_pixels(image, size, fit="stretch")
    length = int(size.key)
    candidates: list[Any] = [
        DemoCandidate(size, 1.0, "内置尺寸表精确匹配"),
        DemoCandidate(core.LogoSize(854, 480, 4), 0.9, "相邻块一致（同机型常见分辨率）"),
        DemoCandidate(core.LogoSize(960, 427, 4), 0.62, "宽高比接近 16:9"),
        DemoCandidate(core.LogoSize(640, 640, 4), 0.41, "字节数相同的其它候选"),
    ]
    return candidates, length, pixels


# ── LK 修补演示/自检用的合成 ARM32 LK 镜像 ─────────────────
def build_synthetic_lk_arm32() -> bytes:
    """构造一个含已知 ARM32 特征码 + 警告文本 + 内核参数的合成 LK 分区镜像。

    仅用于自检/截图/测试；真实样本请用 analysis/lk/samples/lk_a.bin。
    """
    needles = (
        (bytes.fromhex("08b50e4b7b441b681b68022b"), "orange_state"),
        (bytes.fromhex("30b583b002ab0022"), "dm_verity"),
    )
    payload = bytearray(0x3000)
    # 开头放 ARM32 Thumb 函数序言，便于 core 识别架构
    payload[0:2] = bytes((0x08, 0xB5))  # ARM32 Thumb push {r3,lr} 序言
    offset = 0x100
    for needle, _category in needles:
        payload[offset : offset + len(needle)] = needle
        offset += 0x200
    strings = (
        b"Orange State",
        b"Red State",
        b"Your device has been unlocked",
        b"dm-verity",
        b"Your device has failed verification",
        b"5 seconds",
    )
    cursor = 0x1000
    for item in strings:
        payload[cursor : cursor + len(item)] = item
        cursor += 0x40
    guards = (
        b"androidboot.verifiedbootstate=orange",
        b"androidboot.veritymode=enforcing",
        b"androidboot.atm=disabled",
    )
    cursor = 0x2000
    for item in guards:
        payload[cursor : cursor + len(item)] = item
        cursor += 0x60
    header = bytearray(512)
    header[0:4] = (0x58881688).to_bytes(4, "little")
    header[4:8] = len(payload).to_bytes(4, "little")
    header[8:12] = bytes((0x6C, 0x6B, 0, 0))  # name = "lk"
    header[0x30:0x34] = (0x58891689).to_bytes(4, "little")
    return bytes(header) + bytes(payload)
