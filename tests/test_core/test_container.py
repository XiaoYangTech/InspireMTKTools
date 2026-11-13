"""容器解析 / 组包 / 健壮性测试。

重点验收：

* 真实 logo.bin：51 块、全部可解压、tag/dataLen 正确；
* 未修改 ``to_bytes()`` 与原始文件 **逐字节一致**（SHA-256 相等）；
* 真实 lk.bin：raw 模式无损还原；
* 截断/损坏输入抛 typed 异常，不泄漏 struct.error / IndexError / zlib.error；
* QA BUG-1：dataLen echo 不一致 / count==0 且 header dataLen≠8 宽容读 + warning。
"""

from __future__ import annotations

import hashlib
import struct
import time
import zlib

import pytest

from logobuilder.core import (
    HEADER_SIZE,
    MAGIC,
    BinaryBlock,
    CorruptContainer,
    ExportError,
    ImageBlock,
    LogoBuilderError,
    LogoContainer,
    LogoSize,
    NotLogoFile,
    SizeDB,
    is_mtk_logo,
    sniff_format,
)
from logobuilder.core.container import MAX_BLOCKS

from helpers import (
    LK_BIN,
    LOGO_BIN,
    block_offsets,
    make_container_bytes,
    make_header,
    patch_u32,
    requires_samples,
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------
# 真实 logo.bin
# ----------------------------------------------------------------------
@requires_samples
def test_real_logo_parse(real_logo_container, real_logo_bytes):
    container = real_logo_container
    assert container.mode == "block-table"
    assert container.tag == "LOGO"
    assert container.count == 51
    assert len(container.blocks) == 51
    assert container.source_path == LOGO_BIN
    assert container.data_len == 2_005_959
    assert struct.unpack_from("<I", container.header, 4)[0] == 2_005_959
    assert container.header[:4] == struct.pack("<I", MAGIC)
    assert container.trailing == b""
    assert container.pending_image_blocks == []
    assert container.warnings == []

    # 每个块都是图像块（内置 sizes.txt 全部唯一命中）
    assert all(isinstance(block, ImageBlock) for block in container.blocks)
    first = container.blocks[0]
    assert first.size == LogoSize(540, 960, 4)
    assert first.size.key == 2_073_600
    assert len(first.pixels) == 2_073_600
    assert first.raw_compressed is not None
    # raw 解压后与 pixels 一致（zlib 用 level 9 但解码结果必须一致）
    assert zlib.decompress(first.raw_compressed) == first.pixels
    # 特殊块：720x1280x4 -> 3,686,400
    assert container.blocks[49].size == LogoSize(720, 1280, 4)
    assert container.blocks[50].size == LogoSize(720, 1280, 4)
    # 块索引连续
    assert [block.index for block in container.blocks] == list(range(51))


@requires_samples
def test_real_logo_byte_identical_repack(real_logo_container, real_logo_bytes):
    repacked = real_logo_container.to_bytes()
    assert len(repacked) == len(real_logo_bytes)
    assert sha256(repacked) == sha256(real_logo_bytes)
    # 二次解析后再回包仍一致
    reparsed = LogoContainer.parse(repacked)
    assert sha256(reparsed.to_bytes()) == sha256(real_logo_bytes)
    # 便捷方法
    assert real_logo_container.sha256() == sha256(real_logo_bytes)


@requires_samples
def test_real_logo_all_blocks_decompress(real_logo_container):
    for block in real_logo_container.blocks:
        assert isinstance(block, ImageBlock)
        assert block.raw_compressed is not None
        assert len(zlib.decompress(block.raw_compressed)) == block.size.key


@requires_samples
def test_real_logo_save_and_reload(tmp_path, real_logo_container, real_logo_bytes):
    target = tmp_path / "out.bin"
    saved = real_logo_container.save(target)
    assert saved == target
    assert sha256(target.read_bytes()) == sha256(real_logo_bytes)
    loaded = LogoContainer.load(target)
    assert loaded.count == 51
    assert sha256(loaded.to_bytes()) == sha256(real_logo_bytes)


@requires_samples
def test_real_logo_parse_without_db(real_logo_bytes):
    """不传 db 时也用内置尺寸表推断（默认行为）。"""
    container = LogoContainer.parse(real_logo_bytes)
    assert container.count == 51
    assert all(isinstance(block, ImageBlock) for block in container.blocks)


@requires_samples
def test_real_logo_explicit_empty_db_gives_pending(real_logo_bytes):
    container = LogoContainer.parse(real_logo_bytes, db=SizeDB())
    assert container.count == 51
    assert all(isinstance(block, BinaryBlock) for block in container.blocks)
    assert container.pending_image_blocks == list(range(51))
    assert all(block.decompressed_ok for block in container.blocks)
    tracking_db = SizeDB.default()
    for index, block in enumerate(list(container.blocks)):
        exact = tracking_db.exact(len(block.data))
        assert len(exact) == 1
        container.assign_size(index, exact[0])
    assert all(isinstance(block, ImageBlock) for block in container.blocks)
    assert container.pending_image_blocks == []
    # 原始压缩字节被复用 -> 仍然逐字节一致
    assert sha256(container.to_bytes()) == sha256(real_logo_bytes)


@requires_samples
def test_real_logo_block_sizes_override(real_logo_bytes):
    tracking_db = SizeDB.default()
    container = LogoContainer.parse(real_logo_bytes, db=SizeDB())
    sizes = []
    for block in container.blocks:
        exact = tracking_db.exact(len(block.data))
        assert len(exact) == 1
        sizes.append(exact[0])
    container = LogoContainer.parse(real_logo_bytes, db=SizeDB(), block_sizes=sizes)
    assert all(isinstance(block, ImageBlock) for block in container.blocks)
    bad = list(sizes)
    bad[4] = LogoSize(48, 54, 4)  # 第 4 块实际是 38x54x4
    with pytest.raises(CorruptContainer):
        LogoContainer.parse(real_logo_bytes, db=SizeDB(), block_sizes=bad)


@requires_samples
def test_real_logo_first_image(real_logo_container):
    first = real_logo_container.blocks[0]
    assert isinstance(first, ImageBlock)
    image = first.to_image()
    assert image.size == (540, 960)
    assert image.mode == "RGBA"


# ----------------------------------------------------------------------
# 真实 lk.bin（raw 模式）
# ----------------------------------------------------------------------
@requires_samples
def test_real_lk_raw_mode(real_lk_container, real_lk_bytes):
    container = real_lk_container
    assert container.mode == "raw"
    assert container.tag == "LK"
    assert container.count == 1
    assert container.raw_payload == real_lk_bytes[HEADER_SIZE:]
    assert len(container.raw_payload) == 294_132
    assert container.header[:4] == struct.pack("<I", MAGIC)
    assert container.warnings  # 说明为何按 raw 处理
    block = container.blocks[0]
    assert isinstance(block, BinaryBlock)
    assert block.decompressed_ok is False
    assert block.data == container.raw_payload
    assert block.raw_compressed is None
    assert container.trailing == b""


@requires_samples
def test_real_lk_byte_identical_repack(real_lk_container, real_lk_bytes):
    assert sha256(real_lk_container.to_bytes()) == sha256(real_lk_bytes)


@requires_samples
def test_real_lk_repack_after_tag_change(real_lk_container, real_lk_bytes):
    container = real_lk_container
    container.set_tag("LOGO")
    assert container.tag == "LOGO"
    repacked = container.to_bytes()
    assert len(repacked) == len(real_lk_bytes)
    assert repacked[8:16] == b"LOGO" + b"\x00" * 4
    assert repacked[HEADER_SIZE:] == real_lk_bytes[HEADER_SIZE:]


# ----------------------------------------------------------------------
# sniff / is_mtk_logo
# ----------------------------------------------------------------------
@requires_samples
def test_sniff_format(real_logo_bytes, real_lk_bytes):
    assert sniff_format(real_logo_bytes) == "mtk-logo-block"
    assert sniff_format(real_lk_bytes) == "mtk-logo-raw"
    assert sniff_format(b"") == "unknown"
    assert sniff_format(b"hello world") == "unknown"
    assert sniff_format(b"\x88\x16\x88\x58" + b"\x00" * 10) == "mtk-logo-raw"
    assert is_mtk_logo(real_logo_bytes)
    assert is_mtk_logo(real_lk_bytes)
    assert not is_mtk_logo(b"")
    assert not is_mtk_logo(b"xxxx")


def test_sniff_empty_container():
    container = LogoContainer.create([])
    data = container.to_bytes()
    assert len(data) == HEADER_SIZE + 8
    assert sniff_format(data) == "mtk-logo-block"


# ----------------------------------------------------------------------
# 合成容器：create / parse / 修改
# ----------------------------------------------------------------------
def test_synthetic_create_and_roundtrip():
    from PIL import Image

    from logobuilder.core import image_to_pixels

    sizes = [LogoSize(16, 8, 4), LogoSize(16, 8, 2)]
    blocks = []
    for index, size in enumerate(sizes):
        image = Image.new("RGBA", (size.width, size.height), (index * 40, 10, 200, 255))
        blocks.append(ImageBlock(index, size, image_to_pixels(image, size)))
    blocks.append(BinaryBlock(2, b"hello binary"))
    container = LogoContainer.create(blocks, tag="LOGO")
    assert container.count == 3
    data = container.to_bytes()
    assert struct.unpack_from("<I", data, 4)[0] == len(data) - HEADER_SIZE
    parsed = LogoContainer.parse(data, db=SizeDB(), block_sizes=[sizes[0], sizes[1], None])
    assert parsed.count == 3
    assert parsed.tag == "LOGO"
    assert isinstance(parsed.blocks[0], ImageBlock)
    assert isinstance(parsed.blocks[1], ImageBlock)
    assert isinstance(parsed.blocks[2], BinaryBlock)
    # 二进制块：写入时未压缩 -> 读回解压失败 -> 原样保留
    assert parsed.blocks[2].decompressed_ok is False
    assert parsed.blocks[2].raw_compressed == b"hello binary"
    assert parsed.to_bytes() == data


def test_synthetic_unknown_size_then_assign():
    unknown_pixels = b"\x01\x02" * (5 * 10)  # 100 字节，不是任何 table 项
    data = make_container_bytes([unknown_pixels])
    container = LogoContainer.parse(data, db=SizeDB())
    assert container.pending_image_blocks == [0]
    assert isinstance(container.blocks[0], BinaryBlock)
    image_block = container.assign_size(0, LogoSize(5, 10, 2))
    assert isinstance(image_block, ImageBlock)
    assert image_block.size == LogoSize(5, 10, 2)
    assert len(image_block.pixels) == 100
    assert container.pending_image_blocks == []
    # 原始压缩字节被复用 -> 逐字节一致
    assert container.to_bytes() == data


def test_assign_size_errors():
    data = make_container_bytes([b"a" * 10, b"b" * 20])
    parsed = LogoContainer.parse(data, db=SizeDB())
    with pytest.raises(CorruptContainer):
        parsed.assign_size(0, LogoSize(5, 10, 2))
    with pytest.raises(IndexError):
        parsed.assign_size(9, LogoSize(5, 10, 2))
    with pytest.raises(TypeError):
        parsed.assign_size(0, "5x10x2")  # type: ignore[arg-type]


def test_synthetic_modified_image_recompressed():
    from PIL import Image

    from logobuilder.core import image_to_pixels

    size = LogoSize(16, 8, 4)
    image = Image.new("RGBA", (16, 8), (1, 2, 3, 255))
    original = LogoContainer.create([ImageBlock(0, size, image_to_pixels(image, size))]).to_bytes()
    container = LogoContainer.parse(original, db=SizeDB(), block_sizes=[size])
    block = container.blocks[0]
    assert isinstance(block, ImageBlock)
    new_image = Image.new("RGBA", (16, 8), (200, 100, 50, 255))
    block.pixels = image_to_pixels(new_image, size)
    assert block.modified
    new_bytes = container.to_bytes()
    assert new_bytes != original
    reparsed = LogoContainer.parse(new_bytes, db=SizeDB(), block_sizes=[size])
    assert isinstance(reparsed.blocks[0], ImageBlock)
    assert reparsed.blocks[0].to_image().getpixel((0, 0)) == (200, 100, 50, 255)


def test_synthetic_binary_preserve_flag():
    data = make_container_bytes([b"some raw data" * 3])
    parsed = LogoContainer.parse(data, db=SizeDB())
    block = parsed.blocks[0]
    assert isinstance(block, BinaryBlock)
    assert block.decompressed_ok
    assert parsed.to_bytes() == data
    alt = parsed.to_bytes(preserve_binary=False)
    reparsed = LogoContainer.parse(alt, db=SizeDB())
    alt_block = reparsed.blocks[0]
    assert alt_block.decompressed_ok is False
    assert alt_block.raw_compressed == b"some raw data" * 3


def test_synthetic_with_block_sizes():
    data = make_container_bytes([b"\x00" * 32, b"\x01" * 48])
    sizes = [LogoSize(4, 4, 2), LogoSize(4, 6, 2)]
    parsed = LogoContainer.parse(data, db=SizeDB(), block_sizes=sizes)
    assert all(isinstance(block, ImageBlock) for block in parsed.blocks)
    assert [block.size for block in parsed.blocks] == sizes


def test_to_image_map():
    from PIL import Image

    from logobuilder.core import image_to_pixels

    size = LogoSize(8, 4, 4)
    image = Image.new("RGBA", (8, 4), (9, 8, 7, 255))
    container = LogoContainer.create(
        [
            ImageBlock(0, size, image_to_pixels(image, size)),
            BinaryBlock(1, b"x"),
            ImageBlock(2, size, image_to_pixels(image, size)),
        ]
    )
    mapping = container.to_image_map()
    assert [index for index, _ in mapping] == [0, 2]
    assert all(img.size == (8, 4) for _, img in mapping)


# ----------------------------------------------------------------------
# 空容器 / 填充
# ----------------------------------------------------------------------
def test_empty_container_roundtrip():
    container = LogoContainer.create([])
    data = container.to_bytes()
    assert len(data) == HEADER_SIZE + 8
    assert container.data_len == 8
    parsed = LogoContainer.parse(data)
    assert parsed.count == 0
    assert parsed.data_len == 8
    assert parsed.to_bytes() == data


def test_pad_to_size():
    container = LogoContainer.create([])
    base = container.to_bytes()
    padded = container.to_bytes(pad_to_size=4096)
    assert len(padded) == 4096
    assert padded[: len(base)] == base
    assert padded[len(base) :] == b"\x00" * (4096 - len(base))
    assert container.to_bytes(pad_to_size=4096, pad_byte=0xFF)[-1] == 0xFF
    with pytest.raises(ExportError):
        container.to_bytes(pad_to_size=len(base) - 1)
    with pytest.raises(ValueError):
        container.to_bytes(pad_to_size=1024, pad_byte=256)


def test_create_uses_custom_header():
    header = bytes(make_header("LOGO", 0))
    container = LogoContainer.create([], header=header)
    assert container.header == header
    assert container.to_bytes()[:8] == header[:4] + struct.pack("<I", 8)


# ----------------------------------------------------------------------
# 损坏输入 -> typed 异常
# ----------------------------------------------------------------------
@requires_samples
@pytest.mark.parametrize(
    "mutator",
    [
        lambda data: data[:100],
        lambda data: data[:1000],
        lambda data: patch_u32(data, 4, len(data)),
        lambda data: patch_u32(data, HEADER_SIZE, MAX_BLOCKS + 1),
        lambda data: patch_u32(data, HEADER_SIZE, 0x7FFFFFFF),
        lambda data: patch_u32(data, HEADER_SIZE + 8, 99),
        lambda data: patch_u32(data, HEADER_SIZE + 12, 212),
        lambda data: patch_u32(data, HEADER_SIZE + 8 + 4 * 50, 2_005_959),
    ],
)
def test_corrupt_inputs_raise_typed_errors(real_logo_bytes, mutator):
    corrupted = mutator(real_logo_bytes)
    start = time.perf_counter()
    with pytest.raises(LogoBuilderError) as excinfo:
        LogoContainer.parse(corrupted)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, "损坏输入必须快速失败（防 OOM/DoS）"
    assert isinstance(excinfo.value, (CorruptContainer, NotLogoFile))


def test_empty_and_garbage_inputs():
    with pytest.raises(NotLogoFile):
        LogoContainer.parse(b"")
    with pytest.raises(NotLogoFile):
        LogoContainer.parse(b"hello world" * 100)
    with pytest.raises(CorruptContainer):
        LogoContainer.parse(struct.pack("<I", MAGIC) + b"\x00" * 100)
    with pytest.raises(TypeError):
        LogoContainer.parse("not bytes")  # type: ignore[arg-type]


def test_corrupt_block_data_kept_losslessly():
    good_raw = zlib.compress(b"\x00" * 64, 9)
    data = make_container_bytes([good_raw], compress=False)
    offsets, _, _ = block_offsets(data)
    corrupted = bytearray(data)
    start = HEADER_SIZE + offsets[0]
    corrupted[start : start + 8] = b"\x00" * 8
    corrupted = bytes(corrupted)
    parsed = LogoContainer.parse(corrupted, db=SizeDB())
    assert parsed.count == 1
    block = parsed.blocks[0]
    assert isinstance(block, BinaryBlock)
    assert block.decompressed_ok is False
    assert block.raw_compressed is not None
    assert parsed.to_bytes() == corrupted


def test_data_len_mismatch_tolerated_with_warning(real_logo_bytes):
    data_len = struct.unpack_from("<I", real_logo_bytes, 4)[0]
    stale = patch_u32(real_logo_bytes, 4, data_len - 100)
    container = LogoContainer.parse(stale)
    assert container.count == 51
    assert container.warnings
    assert "dataLen" in container.warnings[0]
    assert container.to_bytes() == real_logo_bytes


def test_block_table_len_mismatch_tolerated(real_logo_bytes):
    data_len = struct.unpack_from("<I", real_logo_bytes, 4)[0]
    patched = patch_u32(real_logo_bytes, HEADER_SIZE + 4, data_len - 10)
    container = LogoContainer.parse(patched)
    assert container.count == 51
    assert container.warnings
    assert not container.blocks[-1].decompressed_ok
    reparsed = LogoContainer.parse(container.to_bytes())
    assert reparsed.count == 51


def test_count_zero_with_nonstandard_header_data_len(real_logo_bytes):
    """QA BUG-1：count==0 且 header dataLen≠8 -> 宽容读 + 明确忽略尾部警告。"""
    garbage = b"\x00\x00\x00\x00" + b"\x08\x00\x00\x00" + b"\xab" * 92  # count=0, tableLen=8
    header = make_header("LOGO", 100)
    data = bytes(header) + garbage
    assert len(garbage) == 100
    container = LogoContainer.parse(data)
    assert container.count == 0
    assert container.blocks == []
    assert container.warnings
    joined = " ".join(container.warnings)
    assert "块数量为 0" in joined
    assert "忽略" in joined and "92" in joined
    assert container.trailing == garbage[8:]
    # 写回恢复为合法空容器
    recovered = container.to_bytes()
    assert len(recovered) == HEADER_SIZE + 8
    assert struct.unpack_from("<I", recovered, 4)[0] == 8
    assert LogoContainer.parse(recovered).count == 0


@requires_samples
def test_trailing_bytes_ignored_but_recorded(real_logo_bytes):
    padded = real_logo_bytes + b"\x00\x01\x02\x03garbage"
    container = LogoContainer.parse(padded)
    assert container.trailing == b"\x00\x01\x02\x03garbage"
    assert container.to_bytes() == real_logo_bytes
    filled = container.to_bytes(pad_to_size=len(padded))
    assert len(filled) == len(padded)
    assert filled[-9:] == b"\x00" * 9


def test_missing_file_raises_export_error(tmp_path):
    with pytest.raises(ExportError):
        LogoContainer.load(tmp_path / "nope.bin")


def test_header_must_be_512():
    with pytest.raises(CorruptContainer):
        LogoContainer(header=b"short", tag="LOGO", blocks=[])
