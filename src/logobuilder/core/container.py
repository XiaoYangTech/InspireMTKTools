"""MTK LOGO 分区镜像容器：解析 / 校验 / 无损组包。

文件布局（与原版 & YAMLP 一致）::

    +0x000  u32 magic = 0x58881688
    +0x004  u32 dataLen            # 0x200 之后的负载总长
    +0x008  char[8] tag            # "LOGO" / "LK" / ...
    +0x010  .. 0x028               # 0x00 填充（原样保留）
    +0x028  .. 0x200               # 0xFF 填充（原样保留）
    +0x200  u32 blockCount
    +0x204  u32 dataLen（块表内自校验，理论上与 +0x004 相同）
    +0x208  u32 offsets[blockCount]  # 相对 0x200
            block[0..count-1]        # 每块 = zlib 压缩的数据

对未修改的块，``to_bytes()`` 优先复用原始 zlib 字节，从而保证
"读入 -> 原样写回" 与源文件逐字节一致（见测试）。

现代变体（YAMLP ``detect_layout``：bpp=3 / stride 对齐 / 块前缀 0..512）
本版不解析为图像；解压失败或长度不匹配的块一律作为 :class:`BinaryBlock`
原样保留（raw_compressed），保证不丢数据。
"""

from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image

from . import pixel
from .errors import CorruptContainer, ExportError, NotLogoFile
from .sizes import LogoSize, SizeDB

__all__ = [
    "HEADER_SIZE",
    "MAGIC",
    "MAX_BLOCKS",
    "LOGO_TAG",
    "ImageBlock",
    "BinaryBlock",
    "Block",
    "LogoContainer",
    "sniff_format",
    "is_mtk_logo",
    "decode_tag",
]

#: 固定头长度。
HEADER_SIZE = 512
#: 头部魔数（小端字节序为 88 16 88 58）。
MAGIC = 0x58881688
#: 块数量安全上限（防 ``count*4`` 派生出的超大内存分配 / OOM）。
MAX_BLOCKS = 1 << 16
#: 原版 "LOGO" 分区使用的 tag。
LOGO_TAG = "LOGO"

_HEADER_STRUCT = struct.Struct("<II")
_U32 = struct.Struct("<I")
_TAG_OFFSET = 8
_TAG_SIZE = 8


# ----------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------
def decode_tag(raw: bytes) -> str:
    """把 8 字节 tag 去 NUL/去空白后转成字符串（不可解码字符用替换符）。"""
    text = raw.split(b"\x00", 1)[0]
    try:
        return text.decode("ascii")
    except UnicodeDecodeError:  # pragma: no cover - 极少见的非 ASCII tag
        return text.decode("utf-8", errors="replace")


def _default_header(tag: str = LOGO_TAG) -> bytes:
    """生成与原版一致的 512 字节空头（0x00 填充到 0x28，之后 0xFF 填充）。"""
    buffer = bytearray(HEADER_SIZE)
    _HEADER_STRUCT.pack_into(buffer, 0, MAGIC, 0)
    encoded = str(tag).encode("ascii", "replace")[:_TAG_SIZE]
    buffer[_TAG_OFFSET : _TAG_OFFSET + len(encoded)] = encoded
    buffer[0x28:] = b"\xff" * (HEADER_SIZE - 0x28)
    return bytes(buffer)


def is_mtk_logo(data: bytes | bytearray | memoryview) -> bool:
    """是否带有 MTK 通用 512 字节头 magic（并不保证 payload 是 LOGO 块表）。"""
    blob = bytes(data)
    if len(blob) < 4:
        return False
    return _U32.unpack_from(blob, 0)[0] == MAGIC



def _opaque_alpha_ratio(data: bytes) -> float:
    """BGRA8888 数据中 alpha 字节为 0xFF 的比例。

    RGB565 图像每 4 字节中第 4 字节是下一个像素的高字节（随机分布），该比例通常
    接近 1/256；而不透明的 BGRA8888 图像该比例接近 1.0，可用于区分"同为
    ``w*h*2`` 与 ``w*h*4`` 字节数的两种布局"（例如 320x480x2 与 480x160x4）。
    """
    if len(data) < 4:
        return 0.0
    alpha = data[3::4]
    if not alpha:
        return 0.0
    return sum(1 for value in alpha if value == 0xFF) / len(alpha)


def _looks_like_block_table(blob: bytes) -> bool:
    """轻量结构探测：payload 是否像一个自洽的块表（不解压数据）。"""
    available = len(blob) - HEADER_SIZE
    if available < 8:
        return False
    try:
        count, table_len = struct.unpack_from("<II", blob, HEADER_SIZE)
    except struct.error:  # pragma: no cover - available>=8 时不会发生
        return False
    header_len = _U32.unpack_from(blob, 4)[0]
    if header_len > available:
        return False
    if count == 0:
        # 合法的空容器：payload 恰好 8 字节（count + dataLen）
        return header_len == 8 and table_len == 8
    if count > MAX_BLOCKS:
        return False
    if count > (available - 8) // 4:
        return False
    table_end = 8 + 4 * count
    effective = table_len if 8 <= table_len <= available else header_len
    if table_end > effective:
        return False
    try:
        offsets = struct.unpack_from(f"<{count}I", blob, HEADER_SIZE + 8)
    except struct.error:  # pragma: no cover
        return False
    if offsets[0] != table_end:
        return False
    previous = offsets[0]
    for offset in offsets[1:]:
        if offset <= previous or offset >= effective:
            return False
        previous = offset
    return True


def sniff_format(data: bytes | bytearray | memoryview) -> str:
    """嗅探输入类型。

    返回：

    * ``"mtk-logo-block"``：magic 正确且 payload 是自洽的 LOGO 块表；
    * ``"mtk-logo-raw"``：magic 正确但 payload 无块表（如 LK bootloader 原始负载）；
    * ``"unknown"``：空数据或不是 MTK 镜像。
    """
    blob = bytes(data)
    if len(blob) < 4:
        return "unknown"
    if _U32.unpack_from(blob, 0)[0] != MAGIC:
        return "unknown"
    if len(blob) >= HEADER_SIZE and _looks_like_block_table(blob):
        return "mtk-logo-block"
    return "mtk-logo-raw"


# ----------------------------------------------------------------------
# 块数据模型
# ----------------------------------------------------------------------
@dataclass
class ImageBlock:
    """图像块：解压后的像素数据（chunk 尺寸已知）。"""

    index: int
    size: LogoSize
    pixels: bytes
    raw_compressed: bytes | None = None

    # 私有字段：用于判断"内容是否被修改过"，未修改则回包复用原始 zlib 字节。
    _parsed_pixels: bytes | None = field(default=None, repr=False, compare=False, kw_only=True)
    _parsed_key: int | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if not isinstance(self.size, LogoSize):
            raise TypeError(f"ImageBlock.size 必须是 LogoSize，收到 {type(self.size).__name__}")
        self.pixels = bytes(self.pixels)
        if len(self.pixels) != self.size.key:
            raise ValueError(
                f"ImageBlock({self.index}) 像素长度 {len(self.pixels)} 与尺寸 {self.size}"
                f"（需 {self.size.key} 字节）不符"
            )
        if self.raw_compressed is not None:
            self.raw_compressed = bytes(self.raw_compressed)

    # -- 便捷方法 ------------------------------------------------------
    def to_image(self) -> Image.Image:
        """转换为 Pillow RGBA 图像。"""
        return pixel.pixels_to_image(self.pixels, self.size)

    @property
    def modified(self) -> bool:
        """像素是否被替换过（替换后将重新压缩，不再保证与源文件逐字节一致）。"""
        return self._parsed_pixels is not self.pixels or self._parsed_key != self.size.key

    @classmethod
    def preserved(cls, index: int, size: LogoSize, raw_compressed: bytes) -> "ImageBlock":
        """由原始 zlib 字节构造"未修改"图像块（回包时逐字节复用）。"""
        raw = bytes(raw_compressed)
        try:
            pixels = zlib.decompress(raw)
        except zlib.error as exc:  # pragma: no cover - 调用方一般已解压过
            raise CorruptContainer(
                f"第 {index} 块 zlib 解压失败：{exc}",
                context={"block_index": index},
            ) from exc
        block = cls(index, size, pixels, raw)
        block._parsed_pixels = block.pixels
        block._parsed_key = size.key
        return block


@dataclass
class BinaryBlock:
    """二进制块：非图像 / 无法推断尺寸 / 无法解压的数据。"""

    index: int
    data: bytes
    raw_compressed: bytes | None = None
    decompressed_ok: bool = True

    _parsed_data: bytes | None = field(default=None, repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        self.data = bytes(self.data)
        if self.raw_compressed is not None:
            self.raw_compressed = bytes(self.raw_compressed)

    @property
    def modified(self) -> bool:
        """数据是否被修改过。"""
        return self._parsed_data is not self.data

    #: ``data`` 的别名：部分 UI 用 ``getattr(block, "pixels", None)`` 统一探测
    #: "解压成功但尺寸未知"的块（BinaryBlock.data 即解压后的数据）。
    @property
    def pixels(self) -> bytes:
        return self.data

    @classmethod
    def preserved(
        cls,
        index: int,
        data: bytes,
        raw_compressed: bytes,
        *,
        decompressed_ok: bool = True,
    ) -> "BinaryBlock":
        """构造"未修改"的二进制块（回包时优先复用原始 zlib 字节）。"""
        block = cls(index, data, raw_compressed, decompressed_ok)
        block._parsed_data = block.data
        return block


Block = ImageBlock | BinaryBlock


# ----------------------------------------------------------------------
# 容器
# ----------------------------------------------------------------------
@dataclass
class LogoContainer:
    """一个 MTK LOGO 分区镜像。

    ``mode`` 为 ``"block-table"``（标准 LOGO 容器）或 ``"raw"``
    （只有通用头、payload 没有块表，例如 ``lk.bin`` bootloader 原始负载）。
    """

    header: bytes
    tag: str
    blocks: list[Block] = field(default_factory=list)
    source_path: Path | None = None

    # 容器模式（additive；GUI 用于区分普通 LOGO 与不透明分区镜像）
    mode: str = "block-table"
    raw_payload: bytes = b""
    pending_image_blocks: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    trailing: bytes = b""

    def __post_init__(self) -> None:
        self.header = bytes(self.header)
        if len(self.header) != HEADER_SIZE:
            raise CorruptContainer(
                f"文件头长度 {len(self.header)} 字节，MTK LOGO 头必须恰好 {HEADER_SIZE} 字节",
                context={"header_len": len(self.header)},
            )
        if self.mode not in ("block-table", "raw"):
            raise ValueError(f"未知容器模式 {self.mode!r}")
        self.blocks = list(self.blocks)
        for block in self.blocks:
            if not isinstance(block.index, int):
                raise TypeError(f"块 {block!r} 的 index 必须是整数")
        self.pending_image_blocks = [int(i) for i in self.pending_image_blocks]
        self.warnings = [str(w) for w in self.warnings]
        self.raw_payload = bytes(self.raw_payload)
        self.trailing = bytes(self.trailing)

    # -- 基本属性 ------------------------------------------------------
    @property
    def count(self) -> int:
        """块数量。"""
        return len(self.blocks)

    @property
    def data_len(self) -> int:
        """按当前内容计算出的负载总长（写回时写入头部 +0x04）。"""
        if self.mode == "raw":
            return len(self.raw_payload)
        return 8 + 4 * self.count + sum(len(self._encoded_block(b)) for b in self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)

    def __iter__(self):
        return iter(self.blocks)

    def __getitem__(self, index: int) -> Block:
        return self.blocks[index]

    # -- 尺寸/元信息 ---------------------------------------------------
    def set_tag(self, tag: str) -> None:
        """修改 tag（写回时随 header 一起保存，例如把通用头改成 LOGO）。"""
        encoded = str(tag).encode("ascii", "replace")[:_TAG_SIZE]
        header = bytearray(self.header)
        header[_TAG_OFFSET : _TAG_OFFSET + _TAG_SIZE] = encoded.ljust(_TAG_SIZE, b"\x00")
        self.header = bytes(header)
        self.tag = decode_tag(self.header[_TAG_OFFSET : _TAG_OFFSET + _TAG_SIZE])

    def block_at(self, index: int) -> Block:
        """按块索引取块，越界抛 ``IndexError``（带中文信息）。"""
        try:
            return self.blocks[index]
        except IndexError as exc:
            raise IndexError(f"块索引越界：{index}（共 {self.count} 块）") from exc

    # -- 回包 ----------------------------------------------------------
    def _encoded_block(self, block: Block, *, preserve_binary: bool = True) -> bytes:
        """返回该块写回文件时应使用的字节（未修改优先复用原始 zlib 字节）。"""
        if isinstance(block, ImageBlock):
            if block.raw_compressed is not None and not block.modified:
                return block.raw_compressed
            if len(block.pixels) != block.size.key:  # pragma: no cover - __post_init__ 已校验
                raise ExportError(
                    f"第 {block.index} 块像素长度 {len(block.pixels)} 与尺寸 {block.size} 不符",
                )
            return zlib.compress(block.pixels, 9)
        # BinaryBlock
        if preserve_binary and block.raw_compressed is not None and not block.modified:
            return block.raw_compressed
        return block.data

    def to_bytes(
        self,
        *,
        preserve_binary: bool = True,
        pad_to_size: int | None = None,
        pad_byte: int = 0x00,
    ) -> bytes:
        """序列化为完整文件字节。

        Parameters
        ----------
        preserve_binary:
            二进制块若保存了原始 zlib 字节且未修改，优先原样写回（无损）。
        pad_to_size:
            可选：把结果用 ``pad_byte`` 填充到指定大小（导出原始分区镜像用）。
            目标大小小于实际大小时抛 :class:`ExportError`。
        pad_byte:
            填充字节，默认 ``0x00``。
        """
        if self.mode == "raw":
            result = bytes(self.header) + bytes(self.raw_payload)
        else:
            count = len(self.blocks)
            encoded: list[bytes] = [self._encoded_block(block, preserve_binary=preserve_binary) for block in self.blocks]
            data_len = 8 + 4 * count + sum(len(chunk) for chunk in encoded)
            header = bytearray(self.header)
            _HEADER_STRUCT.pack_into(header, 0, MAGIC, data_len)
            parts: list[bytes] = [bytes(header), struct.pack("<II", count, data_len)]
            if count:
                offsets: list[int] = []
                running = 8 + 4 * count
                for chunk in encoded:
                    offsets.append(running)
                    running += len(chunk)
                parts.append(struct.pack(f"<{count}I", *offsets))
            parts.extend(encoded)
            result = b"".join(parts)

        if pad_to_size is not None:
            try:
                target = int(pad_to_size)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"pad_to_size 必须是整数，收到 {pad_to_size!r}") from exc
            if target < 0:
                raise ValueError(f"pad_to_size 不能为负数：{target}")
            pad_value = int(pad_byte)
            if not 0 <= pad_value <= 0xFF:
                raise ValueError(f"pad_byte 必须在 0..255，收到 {pad_byte!r}")
            if len(result) > target:
                raise ExportError(
                    f"目标大小 {target} 字节小于实际镜像大小 {len(result)} 字节，无法填充",
                    hint="请把 pad_to_size 设置为不小于原分区大小的值，或直接导出未填充镜像。",
                    context={"size": len(result), "pad_to_size": target},
                )
            if len(result) < target:
                result += bytes([pad_value]) * (target - len(result))
        return result

    def save(self, path: str | Path, **kwargs: Any) -> Path:
        """原子写文件（临时文件 + ``os.replace``），返回目标路径。"""
        target = Path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = self.to_bytes(**kwargs)
        except OSError as exc:
            raise ExportError(f"无法写入镜像文件：{target}（{exc}）", context={"path": str(target)}) from exc
        return _atomic_write(target, data)

    # -- 图像视图 ------------------------------------------------------
    def to_image_map(self) -> list[tuple[int, Image.Image]]:
        """返回 ``[(块索引, Pillow RGBA 图)]``（仅图像块）。"""
        result: list[tuple[int, Image.Image]] = []
        for block in self.blocks:
            if isinstance(block, ImageBlock):
                result.append((block.index, block.to_image()))
        return result

    def assign_size(self, block_index: int, size: LogoSize) -> ImageBlock:
        """把一个尺寸未知的块（BinaryBlock）指定尺寸并转为 ImageBlock。"""
        if not isinstance(size, LogoSize):
            raise TypeError(f"size 必须是 LogoSize，收到 {type(size).__name__}")
        block = self.block_at(block_index)
        if isinstance(block, ImageBlock):
            if block.size.key != size.key:
                raise CorruptContainer(
                    f"第 {block_index} 块当前尺寸 {block.size} 与新尺寸 {size} 的数据量不同，"
                    "请改用工程块的 set_block_size 重新生成图像",
                    context={"block_index": block_index},
                )
            block.size = size
            return block
        if not block.decompressed_ok:
            raise CorruptContainer(
                f"第 {block_index} 块数据无法解压，不能作为图像处理",
                context={"block_index": block_index},
            )
        if size.key != len(block.data):
            raise CorruptContainer(
                f"尺寸 {size} 需要 {size.key} 字节，但第 {block_index} 块解压后为 {len(block.data)} 字节",
                hint="请选择与块数据长度匹配的分辨率/色深，或把该分辨率加入 sizes.txt。",
                context={"block_index": block_index, "actual": len(block.data), "expected": size.key},
            )
        image_block = ImageBlock(
            index=block_index,
            size=size,
            pixels=block.data,
            raw_compressed=block.raw_compressed,
        )
        # 数据未修改：允许回包复用原始 zlib 字节
        if block.raw_compressed is not None:
            image_block._parsed_pixels = image_block.pixels
            image_block._parsed_key = size.key
        self.blocks[block_index] = image_block
        if block_index in self.pending_image_blocks:
            self.pending_image_blocks.remove(block_index)
        return image_block

    # -- 构造 / 解析 ---------------------------------------------------
    @classmethod
    def create(
        cls,
        blocks: Iterable[Block],
        *,
        tag: str = LOGO_TAG,
        header: bytes | None = None,
        path: Path | None = None,
    ) -> "LogoContainer":
        """从块列表创建容器（写回时重算 dataLen / offsets）。"""
        items = list(blocks)
        for position, block in enumerate(items):
            block.index = position
        base_header = bytes(header) if header is not None else _default_header(tag)
        container = cls(
            header=base_header,
            tag=decode_tag(base_header[8:16]),
            blocks=items,
            source_path=path,
        )
        return container

    @classmethod
    def parse(
        cls,
        data: bytes | bytearray | memoryview,
        *,
        path: Path | None = None,
        db: SizeDB | None = None,
        block_sizes: Sequence[LogoSize | None] | None = None,
    ) -> "LogoContainer":
        """解析 MTK LOGO 镜像字节。

        * magic 不符 -> :class:`NotLogoFile`；
        * magic 正确但 ``tag == "LOGO"`` 且块表损坏 -> :class:`CorruptContainer`；
        * magic 正确、``tag != "LOGO"``、payload 无块表 -> raw 模式（如 LK）。
        """
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError(f"data 必须是 bytes，收到 {type(data).__name__}")
        blob = bytes(data)
        if len(blob) < HEADER_SIZE:
            if len(blob) >= 4 and _U32.unpack_from(blob, 0)[0] == MAGIC:
                raise CorruptContainer(
                    f"文件被截断：只有 {len(blob)} 字节，MTK LOGO 头需要 {HEADER_SIZE} 字节",
                    context={"size": len(blob)},
                )
            raise NotLogoFile(
                f"不是 MTK LOGO 镜像（文件过小：{len(blob)} 字节，magic 不符）",
                context={"size": len(blob)},
            )
        magic = _U32.unpack_from(blob, 0)[0]
        if magic != MAGIC:
            raise NotLogoFile(
                f"不是 MTK LOGO 镜像：magic=0x{magic:08X}，期望 0x{MAGIC:08X}",
                context={"magic": magic, "expected": MAGIC},
            )

        header = blob[:HEADER_SIZE]
        tag = decode_tag(header[_TAG_OFFSET : _TAG_OFFSET + _TAG_SIZE])
        available = len(blob) - HEADER_SIZE
        header_data_len = _U32.unpack_from(header, 4)[0]
        database = db if db is not None else SizeDB.default()

        warnings: list[str] = []
        if header_data_len > available:
            # 原版行为：dataLen 超过文件大小 -> 视为损坏/截断（不允许静默丢掉块）
            raise CorruptContainer(
                f"dataLen={header_data_len} 超出文件可用数据 {available} 字节，文件可能被截断",
                hint="请重新从官方线刷包提取完整的 logo.bin。",
                context={"data_len": header_data_len, "available": available},
            )

        try:
            blocks, pending, table_warnings, consumed = cls._parse_block_table(
                blob,
                header_data_len=header_data_len,
                db=database,
                block_sizes=block_sizes,
            )
            mode = "block-table"
            raw_payload = b""
            warnings.extend(table_warnings)
        except CorruptContainer as exc:
            if tag.upper() == LOGO_TAG:
                raise
            # 非 LOGO tag：通用头 + 不透明 payload（如 LK bootloader），合法
            payload = blob[HEADER_SIZE : HEADER_SIZE + header_data_len]
            block = BinaryBlock(index=0, data=payload, raw_compressed=None, decompressed_ok=False)
            block._parsed_data = block.data
            blocks = [block]
            pending = []
            mode = "raw"
            raw_payload = payload
            warnings.append(
                f"payload 不是 LOGO 块表（{exc.message}），已按 raw 模式原样保留（tag={tag or '<空>'}）"
            )
            consumed = header_data_len

        trailing = blob[HEADER_SIZE + consumed :]
        return cls(
            header=header,
            tag=tag,
            blocks=blocks,
            source_path=Path(path) if path is not None else None,
            mode=mode,
            raw_payload=raw_payload,
            pending_image_blocks=pending,
            warnings=warnings,
            trailing=trailing,
        )

    @classmethod
    def _parse_block_table(
        cls,
        blob: bytes,
        *,
        header_data_len: int,
        db: SizeDB,
        block_sizes: Sequence[LogoSize | None] | None,
    ) -> tuple[list[Block], list[int], list[str], int]:
        """解析块表；返回 ``(blocks, pending, warnings, consumed_payload_len)``。"""
        available = len(blob) - HEADER_SIZE
        if available < 8:
            raise CorruptContainer(
                f"数据区不足 8 字节（实际 {available}），无法读取块数量/长度",
                context={"available": available},
            )
        count, table_data_len = struct.unpack_from("<II", blob, HEADER_SIZE)
        if count > MAX_BLOCKS:
            raise CorruptContainer(
                f"块数量异常：{count}（上限 {MAX_BLOCKS}），文件可能损坏",
                context={"count": count, "max_blocks": MAX_BLOCKS},
            )
        max_count = (available - 8) // 4
        if count > max_count:
            raise CorruptContainer(
                f"块数量 {count} 超过文件可容纳的偏移表大小（最多 {max_count} 块）",
                context={"count": count, "max_count": max_count},
            )
        table_end = 8 + 4 * count

        warnings: list[str] = []
        if table_data_len == header_data_len:
            payload_end = header_data_len
        elif 8 <= table_data_len <= available:
            payload_end = table_data_len
            warnings.append(
                f"dataLen 不一致：头部声明 {header_data_len} 字节，块表声明 {table_data_len} 字节；"
                f"已按块表的 {table_data_len} 字节继续解析（写回时会自动校正）"
            )
        else:
            payload_end = header_data_len
            warnings.append(
                f"dataLen 不一致：块表声明 {table_data_len} 字节，头部声明 {header_data_len} 字节"
                f"（可用 {available} 字节）；已按头部长度继续解析（写回时会自动校正）"
            )
        if table_end > payload_end:
            raise CorruptContainer(
                f"块表需要 {table_end} 字节，但 payload 仅 {payload_end} 字节",
                context={"table_end": table_end, "payload_end": payload_end},
            )

        try:
            offsets = struct.unpack_from(f"<{count}I", blob, HEADER_SIZE + 8)
        except struct.error as exc:  # pragma: no cover - 上面已保证可读
            raise CorruptContainer(f"偏移表读取失败：{exc}") from exc

        if count:
            if offsets[0] != table_end:
                raise CorruptContainer(
                    f"首个块偏移应为 {table_end}（8 + 4*块数），实际为 {offsets[0]}",
                    context={"expected": table_end, "actual": offsets[0]},
                )
            previous = offsets[0]
            for position in range(1, count):
                offset = offsets[position]
                if offset <= previous:
                    raise CorruptContainer(
                        f"块偏移未严格递增：offsets[{position}]={offset} 不大于 offsets[{position - 1}]={previous}",
                        context={"index": position, "offset": offset, "previous": previous},
                    )
                if offset >= payload_end:
                    raise CorruptContainer(
                        f"块偏移 {offset}（第 {position} 块）越界：payload 长度仅 {payload_end} 字节",
                        context={"index": position, "offset": offset, "payload_end": payload_end},
                    )
                previous = offset
            if offsets[-1] >= payload_end:
                raise CorruptContainer(
                    f"最后一个块偏移越界：{offsets[-1]} >= payload 长度 {payload_end}",
                    context={"offset": offsets[-1], "payload_end": payload_end},
                )
        else:
            # count==0：空容器。宽容处理头部 dataLen 声明不一致，忽略残余字节并警告。
            if header_data_len != 8 or payload_end != 8:
                ignored = max(0, available - 8)
                warnings.append(
                    f"块数量为 0：忽略 {ignored} 字节残余负载"
                    f"（头部 dataLen 声明 {header_data_len} 字节，空容器应为 8 字节；"
                    "写回时会恢复为合法的空容器）"
                )
            payload_end = 8

        blocks: list[Block] = []
        pending: list[int] = []
        for index in range(count):
            start = offsets[index]
            end = offsets[index + 1] if index + 1 < count else payload_end
            raw = blob[HEADER_SIZE + start : HEADER_SIZE + end]
            block, is_pending, block_warnings = cls._build_block(
                index, raw, db=db, block_sizes=block_sizes
            )
            blocks.append(block)
            if is_pending:
                pending.append(index)
            warnings.extend(block_warnings)
        return blocks, pending, warnings, payload_end

    @classmethod
    def _build_block(
        cls,
        index: int,
        raw: bytes,
        *,
        db: SizeDB,
        block_sizes: Sequence[LogoSize | None] | None,
    ) -> tuple[Block, bool, list[str]]:
        """解压一个块并尽可能推断尺寸。"""
        try:
            decompressed = zlib.decompress(raw)
        except zlib.error:
            block = BinaryBlock(index=index, data=b"", raw_compressed=raw, decompressed_ok=False)
            block._parsed_data = block.data
            return (
                block,
                False,
                [f"第 {index} 块不是有效的 zlib 数据（或为现代变体），已按二进制块原样保留"],
            )

        if block_sizes is not None and index < len(block_sizes) and block_sizes[index] is not None:
            size = block_sizes[index]
            if not isinstance(size, LogoSize):
                raise TypeError(
                    f"block_sizes[{index}] 必须是 LogoSize 或 None，收到 {type(size).__name__}"
                )
            if size.key != len(decompressed):
                raise CorruptContainer(
                    f"第 {index} 块指定尺寸 {size} 需要 {size.key} 字节，"
                    f"但解压后为 {len(decompressed)} 字节",
                    hint="请检查手动指定的尺寸，或改用自动识别。",
                    context={"block_index": index, "expected": size.key, "actual": len(decompressed)},
                )
        else:
            size = None
            if decompressed:
                candidates = db.candidates(len(decompressed))
                confident = [c for c in candidates if c.score >= 0.9]
                if len(confident) == 1:
                    size = confident[0].size
                elif db.exact(len(decompressed)):
                    # 同一解压长度在尺寸表中同时登记了 RGB565 与 BGRA8888 两种布局
                    # （典型：320x480x2 与 480x160x4 都是 307200 字节）。按 alpha 字节
                    # 判定：几乎全 0xFF 视为不透明 BGRA，否则按 RGB565。
                    exact_sizes = set(db.exact(len(decompressed)))
                    if len(decompressed) % 4 == 0 and _opaque_alpha_ratio(decompressed) >= 0.99:
                        # 优先采用尺寸表中的精确 BGRA 条目（如 480x160x4），
                        # 避免被"由长度反解"的 160x480x4 等旋转候选抢占。
                        bgra_exact = [
                            c for c in candidates
                            if c.size.color_depth == 4 and c.size in exact_sizes
                        ]
                        bgra = bgra_exact or [c for c in candidates if c.size.color_depth == 4]
                        if bgra:
                            size = max(bgra, key=lambda c: c.score).size
                    else:
                        rgb565_exact = [
                            c for c in candidates
                            if c.size.color_depth == 2 and c.size in exact_sizes
                        ]
                        rgb565 = rgb565_exact or [c for c in candidates if c.size.color_depth == 2]
                        if rgb565:
                            size = max(rgb565, key=lambda c: c.score).size

        if size is not None:
            block = ImageBlock(index=index, size=size, pixels=decompressed, raw_compressed=raw)
            block._parsed_pixels = block.pixels
            block._parsed_key = size.key
            return block, False, []
        block = BinaryBlock(index=index, data=decompressed, raw_compressed=raw, decompressed_ok=True)
        block._parsed_data = block.data
        is_pending = len(decompressed) > 0
        return block, is_pending, []

    # -- 文件 IO -------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path, *, db: SizeDB | None = None) -> "LogoContainer":
        """读取并解析镜像文件。"""
        target = Path(path)
        try:
            data = target.read_bytes()
        except OSError as exc:
            raise ExportError(
                f"无法读取镜像文件：{target}（{exc}）",
                hint="请检查文件是否存在、路径是否正确、是否有读取权限。",
                context={"path": str(target)},
            ) from exc
        return cls.parse(data, path=target, db=db)

    # -- 调试/信息 -----------------------------------------------------
    def summary(self) -> dict[str, Any]:
        """返回适合 CLI/日志展示的摘要信息。"""
        block_info: list[dict[str, Any]] = []
        for block in self.blocks:
            if isinstance(block, ImageBlock):
                block_info.append(
                    {
                        "index": block.index,
                        "kind": "image",
                        "size": str(block.size),
                        "width": block.size.width,
                        "height": block.size.height,
                        "bpp": block.size.color_depth,
                        "pixels_len": len(block.pixels),
                        "compressed_len": None if block.raw_compressed is None else len(block.raw_compressed),
                        "modified": block.modified,
                    }
                )
            else:
                block_info.append(
                    {
                        "index": block.index,
                        "kind": "binary",
                        "size": None,
                        "data_len": len(block.data),
                        "compressed_len": None if block.raw_compressed is None else len(block.raw_compressed),
                        "decompressed_ok": block.decompressed_ok,
                        "pending": block.index in self.pending_image_blocks,
                        "modified": block.modified,
                    }
                )
        return {
            "path": None if self.source_path is None else str(self.source_path),
            "mode": self.mode,
            "tag": self.tag,
            "magic": f"0x{MAGIC:08X}",
            "count": self.count,
            "data_len": self.data_len,
            "header_data_len": _U32.unpack_from(self.header, 4)[0],
            "pending_image_blocks": list(self.pending_image_blocks),
            "warnings": list(self.warnings),
            "trailing_len": len(self.trailing),
            "blocks": block_info,
        }

    def sha256(self, **kwargs: Any) -> str:
        """当前序列化结果的 SHA-256（便于校验无损回包）。"""
        return hashlib.sha256(self.to_bytes(**kwargs)).hexdigest()


def _atomic_write(target: Path, data: bytes) -> Path:
    """写入同目录临时文件后原子替换；失败转 :class:`ExportError`。"""
    directory = target.parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(directory))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover - 清理失败不掩盖原始异常
                pass
            raise
    except OSError as exc:
        raise ExportError(
            f"无法写入文件：{target}（{exc}）",
            hint="请检查目标目录是否存在、是否有写权限，以及文件是否被占用。",
            context={"path": str(target)},
        ) from exc
    return target
