"""测试辅助：手工构造 MTK LOGO 容器字节（独立于被测实现，避免循环验证）。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

HEADER_SIZE = 512
MAGIC = 0x58881688

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版"
LOGO_BIN = SAMPLE_DIR / "logo.bin"
LK_BIN = SAMPLE_DIR / "lk.bin"

#: 缺少真实样本时自动 skip。
requires_samples = pytest.mark.skipif(
    not (LOGO_BIN.is_file() and LK_BIN.is_file()),
    reason="缺少真实样本 LogoBuilder_jb51/LogoBuilder_v1.6中文版/{logo,lk}.bin",
)


def make_header(
    tag: str = "LOGO",
    data_len: int = 0,
    *,
    magic: int = MAGIC,
    reserved: bytes | None = None,
) -> bytearray:
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<II", header, 0, magic, data_len)
    encoded = tag.encode("ascii")[:8]
    header[8 : 8 + len(encoded)] = encoded
    if reserved is not None:
        header[16 : 16 + len(reserved)] = reserved[: HEADER_SIZE - 16]
    header[0x28:] = b"\xff" * (HEADER_SIZE - 0x28)
    return header


def make_container_bytes(
    blocks: list[bytes],
    *,
    tag: str = "LOGO",
    header: bytes | None = None,
    compress: bool = True,
) -> bytes:
    """按格式手工拼一个容器（blocks 为原始数据，默认逐块 zlib 压缩）。"""
    payload_blocks = [zlib.compress(block, 9) if compress else block for block in blocks]
    count = len(payload_blocks)
    table_end = 8 + 4 * count
    data_len = table_end + sum(len(block) for block in payload_blocks)
    offsets: list[int] = []
    running = table_end
    for block in payload_blocks:
        offsets.append(running)
        running += len(block)
    header_bytes = header if header is not None else bytes(make_header(tag, data_len))
    header_buffer = bytearray(header_bytes)
    struct.pack_into("<II", header_buffer, 0, MAGIC, data_len)
    parts = [bytes(header_buffer[:HEADER_SIZE]), struct.pack("<II", count, data_len)]
    if count:
        parts.append(struct.pack(f"<{count}I", *offsets))
    parts.extend(payload_blocks)
    return b"".join(parts)


def patch_u32(data: bytes, offset: int, value: int) -> bytes:
    buffer = bytearray(data)
    struct.pack_into("<I", buffer, offset, value)
    return bytes(buffer)


def block_offsets(data: bytes) -> tuple[list[int], int, int]:
    """返回 (offsets, dataLen, count)，读取自一个容器字节。"""
    count, data_len = struct.unpack_from("<II", data, HEADER_SIZE)
    offsets = list(struct.unpack_from(f"<{count}I", data, HEADER_SIZE + 8))
    return offsets, data_len, count
