"""工程（项目目录）模型。

新工程目录布局（与原版兼容命名）::

    <project>/
    ├─ header.bin          # 原文件前 512 字节
    ├─ project.json        # 本重建版的工程描述（块顺序/类型/尺寸/文件）
    ├─ .project            # 同时导出的原版文本工程（兼容旧工具）
    ├─ sizes.txt           # 工程用到的分辨率表（兼容旧工具）
    ├─ img1.png, img2.png  # 图像块（N 从 1 连续编号）
    ├─ block5.dat          # 非图像块（下标 = 块索引）
    └─ raw/5.z             # 原始 zlib 字节（无损回包用，可选）

``project.json`` 是本重建版的权威描述；``.project`` / ``imgN.png`` / ``blockN.dat``
的命名与原版一致，因此导出的目录也能被原版 LogoBuilder 打开。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from . import pixel
from .container import (
    HEADER_SIZE,
    BinaryBlock,
    Block,
    ImageBlock,
    LogoContainer,
    _default_header,
    decode_tag,
)
from .errors import CorruptContainer, ExportError, ProjectError
from .sizes import LogoSize

__all__ = [
    "PROJECT_FILE",
    "HEADER_FILE",
    "LEGACY_HEADER_FILE",
    "LEGACY_PROJECT_FILE",
    "RAW_DIR",
    "PROJECT_FORMAT",
    "PROJECT_VERSION",
    "ProjectBlock",
    "ProjectIssue",
    "LogoProject",
]

PROJECT_FILE = "project.json"
HEADER_FILE = "header.bin"
#: 原版 LogoBuilder 的工程头文件名叫 ``header``（无扩展名），为双向兼容同时支持两种。
LEGACY_HEADER_FILE = "header"
LEGACY_PROJECT_FILE = ".project"
RAW_DIR = "raw"
PROJECT_FORMAT = "logobuilder-project"
PROJECT_VERSION = 1

_LEGACY_IMAGE_PREFIX = "image:"
_LEGACY_BINARY_PREFIX = "binary:"
_IMG_RE = re.compile(r"^img(\d+)\.png$", re.IGNORECASE)
_BLOCK_RE = re.compile(r"^block(\d+)\.dat$", re.IGNORECASE)


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------
@dataclass
class ProjectBlock:
    """工程中的一个块。"""

    index: int
    kind: str  # "image" | "binary"
    size: LogoSize | None = None
    image_path: Path | None = None
    binary_path: Path | None = None
    raw_compressed_path: Path | None = None
    modified: bool = False
    # 以下为 additive 扩展（GUI 通常不需要关心）
    decompressed_ok: bool = True
    image_sha256: str | None = None
    binary_sha256: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("image", "binary"):
            raise ValueError(f"ProjectBlock.kind 必须为 'image' 或 'binary'，收到 {self.kind!r}")
        for name in ("image_path", "binary_path", "raw_compressed_path"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Path):
                setattr(self, name, Path(value))
        if self.size is not None and not isinstance(self.size, LogoSize):
            raise ValueError(f"ProjectBlock.size 必须是 LogoSize 或 None，收到 {type(self.size).__name__}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"index": self.index, "kind": self.kind}
        if self.size is not None:
            data["size"] = str(self.size)
        if self.image_path is not None:
            data["image"] = self.image_path.as_posix()
        if self.binary_path is not None:
            data["binary"] = self.binary_path.as_posix()
        if self.raw_compressed_path is not None:
            data["raw"] = self.raw_compressed_path.as_posix()
        if self.modified:
            data["modified"] = True
        if self.kind == "binary" and not self.decompressed_ok:
            data["decompressed_ok"] = False
        if self.image_sha256 is not None:
            data["image_sha256"] = self.image_sha256
        if self.binary_sha256 is not None:
            data["binary_sha256"] = self.binary_sha256
        return data


@dataclass
class ProjectIssue:
    """工程自检的一项问题。"""

    level: str  # "error" | "warning"
    block_index: int | None
    message: str

    def __post_init__(self) -> None:
        if self.level not in ("error", "warning"):
            raise ValueError(f"ProjectIssue.level 必须为 'error'/'warning'，收到 {self.level!r}")

    def __str__(self) -> str:
        prefix = f"块 {self.block_index}：" if self.block_index is not None else ""
        return f"[{self.level}] {prefix}{self.message}"


# ----------------------------------------------------------------------
# 路径/IO 辅助
# ----------------------------------------------------------------------
def _find_header_file(directory: Path) -> Path | None:
    """按优先级查找工程头文件：``header.bin``（本版）→ ``header``（原版）。"""
    for name in (HEADER_FILE, LEGACY_HEADER_FILE):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def _safe_relative(path: Path | str, *, project_dir: Path) -> Path:
    """校验并返回项目内相对路径（禁止绝对路径与 ``..`` 逃逸）。"""
    value = Path(path)
    if value.is_absolute() or ".." in value.parts:
        raise ProjectError(
            f"工程文件路径必须是项目目录内的相对路径：{path}",
            context={"path": str(path)},
        )
    return value


def _atomic_write(path: Path, data: bytes) -> Path:
    """原子写文件。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover
                pass
            raise
    except OSError as exc:
        raise ExportError(
            f"无法写入工程文件：{path}（{exc}）",
            hint="请检查项目目录写权限，或关闭占用该文件的程序。",
            context={"path": str(path)},
        ) from exc
    return path


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_image_rgba(path: Path) -> Image.Image:
    try:
        with Image.open(path) as handle:
            handle.load()
            return handle.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise ProjectError(
            f"无法读取图片：{path}（{exc}）",
            hint="请确认图片文件完整（PNG/BMP/JPG），或重新替换该块的图片。",
            context={"path": str(path)},
        ) from exc


def _save_png(path: Path, image: Image.Image) -> None:
    buffer = _BytesBuffer()
    try:
        image.save(buffer, format="PNG")
    except (OSError, ValueError) as exc:  # pragma: no cover - Pillow 编码失败极少见
        raise ExportError(f"PNG 编码失败：{exc}", context={"path": str(path)}) from exc
    _atomic_write(path, buffer.getvalue())


class _BytesBuffer:
    """极简 ``io.BytesIO`` 替代（避免额外 import 噪音，行为一致）。"""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def getvalue(self) -> bytes:
        return b"".join(self._chunks)

    # Pillow 需要 tell/seek 以写多帧（PNG 单帧用不到，但保持接口完整）
    def tell(self) -> int:
        return sum(len(chunk) for chunk in self._chunks)

    def seek(self, offset: int, whence: int = 0) -> int:  # pragma: no cover - PNG 不使用
        raise OSError("stream is write-only")


# ----------------------------------------------------------------------
# 工程
# ----------------------------------------------------------------------
class LogoProject:
    """一个 LOGO 重建工程（项目目录）。"""

    def __init__(
        self,
        path: Path | str,
        header: bytes,
        blocks: list[ProjectBlock],
        *,
        tag: str | None = None,
        mode: str = "block-table",
        pending: Iterable[int] = (),
        warnings: Iterable[str] = (),
    ) -> None:
        self.path = Path(path)
        self.header = bytes(header)
        if len(self.header) != HEADER_SIZE:
            raise ProjectError(
                f"header 必须恰好 {HEADER_SIZE} 字节，收到 {len(self.header)} 字节",
                hint="工程 header.bin 已损坏，可运行 repair() 修复或重建工程。",
            )
        self.blocks = list(blocks)
        self.tag = tag if tag is not None else decode_tag(self.header[8:16])
        if mode not in ("block-table", "raw"):
            raise ValueError(f"未知工程模式 {mode!r}")
        self.mode = mode
        self.pending: list[int] = [int(i) for i in pending]
        self.warnings: list[str] = [str(w) for w in warnings]
        self._dirty = False
        self._header_synthesized = False

    # -- 基本属性 ------------------------------------------------------
    @property
    def project_dir(self) -> Path:
        return self.path

    @property
    def count(self) -> int:
        return len(self.blocks)

    def __len__(self) -> int:
        return len(self.blocks)

    def __iter__(self):
        return iter(self.blocks)

    def block_at(self, index: int) -> ProjectBlock:
        try:
            return self.blocks[index]
        except IndexError as exc:
            raise ProjectError(f"块索引越界：{index}（共 {self.count} 块）") from exc

    def image_file(self, index: int) -> Path:
        """返回图像块的图片绝对路径；非图像块抛 :class:`ProjectError`。"""
        block = self.block_at(index)
        if block.kind != "image" or block.image_path is None:
            raise ProjectError(f"块 {index} 不是图像块，没有图片文件")
        return self.project_dir / block.image_path

    def binary_file(self, index: int) -> Path:
        """返回二进制块的数据文件绝对路径；非二进制块抛 :class:`ProjectError`。"""
        block = self.block_at(index)
        if block.kind != "binary" or block.binary_path is None:
            raise ProjectError(f"块 {index} 不是二进制块，没有数据文件")
        return self.project_dir / block.binary_path

    @property
    def dirty(self) -> bool:
        """工程是否有未保存的修改。

        注意 ``block.modified`` 表示"该块已脱离原始数据、打包时必须重新编码"，
        与"未保存"无关：一个已保存的工程 ``dirty`` 为 False。
        """
        return self._dirty

    # -- 新建 / 打开 ---------------------------------------------------
    @classmethod
    def new(
        cls,
        project_dir: str | Path,
        source: LogoContainer,
        *,
        overwrite: bool = False,
    ) -> "LogoProject":
        """由 :class:`LogoContainer` 解包出新的工程目录。"""
        if not isinstance(source, LogoContainer):
            raise TypeError(f"source 必须是 LogoContainer，收到 {type(source).__name__}")
        directory = Path(project_dir)
        if directory.exists():
            if not directory.is_dir():
                raise ProjectError(f"目标路径已存在且不是目录：{directory}")
            if any(directory.iterdir()) and not overwrite:
                raise ProjectError(
                    f"目标目录非空：{directory}",
                    hint="请选择一个空目录，或允许覆盖已有工程。",
                    context={"path": str(directory)},
                )
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectError(
                f"无法创建工程目录：{directory}（{exc}）",
                hint="请检查路径是否合法、是否有写权限。",
                context={"path": str(directory)},
            ) from exc

        blocks: list[ProjectBlock] = []
        image_sequence = 0
        for block in source.blocks:
            index = block.index
            if isinstance(block, ImageBlock):
                image_sequence += 1
                image_rel = Path(f"img{image_sequence}.png")
                image = pixel.pixels_to_image(block.pixels, block.size)
                _save_png(directory / image_rel, image)
                raw_rel: Path | None = None
                if block.raw_compressed is not None:
                    raw_rel = Path(RAW_DIR) / f"{index}.z"
                    _atomic_write(directory / raw_rel, block.raw_compressed)
                blocks.append(
                    ProjectBlock(
                        index=index,
                        kind="image",
                        size=block.size,
                        image_path=image_rel,
                        raw_compressed_path=raw_rel,
                        modified=False,
                        image_sha256=_sha256_file(directory / image_rel),
                    )
                )
            else:
                binary_rel = Path(f"block{index}.dat")
                raw_rel = None
                if block.decompressed_ok:
                    data = block.data
                    if block.raw_compressed is not None:
                        raw_rel = Path(RAW_DIR) / f"{index}.z"
                        _atomic_write(directory / raw_rel, block.raw_compressed)
                else:
                    # 不透明块：文件内容就是写回时的原始字节
                    data = block.raw_compressed if block.raw_compressed is not None else block.data
                # 解压成功但尺寸未知的块用 kind="image" + size=None 表示：
                # 这样 GUI 会把它显示为"待识别分辨率"，用户可指定尺寸转为图像块；
                # 未指定时 to_container() 仍按二进制原样保留（不丢数据）。
                pending_image = block.decompressed_ok and index in source.pending_image_blocks
                _atomic_write(directory / binary_rel, data)
                blocks.append(
                    ProjectBlock(
                        index=index,
                        kind="image" if pending_image else "binary",
                        binary_path=binary_rel,
                        raw_compressed_path=raw_rel,
                        modified=False,
                        decompressed_ok=block.decompressed_ok,
                        binary_sha256=_sha256_file(directory / binary_rel),
                    )
                )

        project = cls(
            directory,
            source.header,
            blocks,
            tag=source.tag,
            mode=source.mode,
            pending=source.pending_image_blocks,
            warnings=source.warnings,
        )
        project.save()
        return project

    @classmethod
    def open(cls, project_dir: str | Path) -> "LogoProject":
        """打开已有工程（自动识别原版 ``.project`` 工程并转换）。"""
        directory = Path(project_dir)
        if not directory.is_dir():
            raise ProjectError(
                f"工程目录不存在或不是目录：{directory}",
                hint="请选择包含 header.bin + project.json 的工程目录。",
                context={"path": str(directory)},
            )
        project_file = directory / PROJECT_FILE
        if not project_file.exists():
            if (directory / LEGACY_PROJECT_FILE).exists():
                return cls.import_legacy(directory)
            raise ProjectError(
                f"不是有效的 LogoBuilder 工程：缺少 {PROJECT_FILE}（也没有旧版 {LEGACY_PROJECT_FILE}）",
                hint="请选择工程目录，或用“从 logo.bin 新建工程”重新解包。",
                context={"path": str(directory)},
            )

        warnings: list[str] = []
        try:
            payload = json.loads(project_file.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectError(
                f"无法解析 {PROJECT_FILE}：{exc}",
                hint="工程描述文件损坏；可尝试用旧版 .project 重新导入（import_legacy）。",
                context={"path": str(project_file)},
            ) from exc
        if not isinstance(payload, dict):
            raise ProjectError(f"{PROJECT_FILE} 顶层必须是对象")

        project_format = payload.get("format")
        if project_format != PROJECT_FORMAT:
            warnings.append(f"未知工程格式标识 {project_format!r}，已尽力解析")
        version = payload.get("version")
        if version not in (None, PROJECT_VERSION):
            warnings.append(f"工程版本 {version!r} 与当前版本 {PROJECT_VERSION} 不同，已尽力解析")

        tag = str(payload.get("tag") or "LOGO")
        mode = str(payload.get("mode") or "block-table")
        pending = payload.get("pending") or []
        if not isinstance(pending, (list, tuple)):
            pending = []

        header_path = _find_header_file(directory)
        header_synthesized = False
        if header_path is not None:
            header_bytes = header_path.read_bytes()
            if len(header_bytes) != HEADER_SIZE:
                warnings.append(
                    f"{header_path.name} 长度异常（{len(header_bytes)} 字节，应为 {HEADER_SIZE}），已用默认头替代"
                )
                header_bytes = _default_header(tag)
                header_synthesized = True
            elif header_path.name == LEGACY_HEADER_FILE:
                warnings.append(
                    f"使用旧版工程头文件名 {LEGACY_HEADER_FILE!r}，保存后会自动补齐 {HEADER_FILE}"
                )
        else:
            warnings.append(
                f"缺少 {HEADER_FILE} / {LEGACY_HEADER_FILE}，已用默认头替代（请运行 repair() 修复）"
            )
            header_bytes = _default_header(tag)
            header_synthesized = True

        raw_blocks = payload.get("blocks") or []
        if not isinstance(raw_blocks, list):
            raise ProjectError(f"{PROJECT_FILE} 的 blocks 必须是数组")
        blocks: list[ProjectBlock] = []
        for position, item in enumerate(raw_blocks):
            if not isinstance(item, dict):
                raise ProjectError(f"{PROJECT_FILE} 的 blocks[{position}] 必须是对象")
            kind = str(item.get("kind") or "")
            if kind not in ("image", "binary"):
                raise ProjectError(f"blocks[{position}].kind 非法：{kind!r}")
            try:
                size = LogoSize.parse(str(item["size"])) if item.get("size") else None
            except (ValueError, KeyError, TypeError) as exc:
                raise ProjectError(f"blocks[{position}].size 非法：{item.get('size')!r}（{exc}）") from exc
            image_path = _safe_relative(str(item["image"]), project_dir=directory) if item.get("image") else None
            binary_path = _safe_relative(str(item["binary"]), project_dir=directory) if item.get("binary") else None
            raw_path = _safe_relative(str(item["raw"]), project_dir=directory) if item.get("raw") else None
            index = int(item.get("index", position))
            blocks.append(
                ProjectBlock(
                    index=index,
                    kind=kind,
                    size=size,
                    image_path=image_path,
                    binary_path=binary_path,
                    raw_compressed_path=raw_path,
                    modified=bool(item.get("modified", False)),
                    decompressed_ok=bool(item.get("decompressed_ok", True)),
                    image_sha256=item.get("image_sha256"),
                    binary_sha256=item.get("binary_sha256"),
                )
            )

        project = cls(
            directory,
            header_bytes,
            blocks,
            tag=tag,
            mode=mode,
            pending=pending,
            warnings=warnings,
        )
        project._header_synthesized = header_synthesized
        return project

    # -- 原版 .project 兼容 --------------------------------------------
    @classmethod
    def import_legacy(
        cls,
        project_dir: str | Path,
        *,
        source_container: LogoContainer | None = None,
    ) -> "LogoProject":
        """导入原版 LogoBuilder 工程（header.bin + .project + imgN.png/blockN.dat）。"""
        directory = Path(project_dir)
        if not directory.is_dir():
            raise ProjectError(f"工程目录不存在：{directory}")
        legacy_file = directory / LEGACY_PROJECT_FILE
        if not legacy_file.exists():
            raise ProjectError(
                f"目录中没有旧版工程文件 {LEGACY_PROJECT_FILE}",
                hint="旧版工程必须包含 .project（每行 image:/binary: 描述）。",
                context={"path": str(legacy_file)},
            )
        header_path = _find_header_file(directory)
        if header_path is None:
            raise ProjectError(
                f"旧版工程缺少 {HEADER_FILE}（或原版名 {LEGACY_HEADER_FILE}）",
                hint="请把原版工程目录完整复制后再导入。",
                context={"path": str(directory)},
            )
        header_bytes = header_path.read_bytes()
        if len(header_bytes) != HEADER_SIZE:
            raise ProjectError(
                f"{header_path.name} 长度 {len(header_bytes)} 字节，应为 {HEADER_SIZE} 字节（文件可能损坏）",
                context={"path": str(header_path)},
            )
        try:
            text = legacy_file.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = legacy_file.read_text(encoding="gbk", errors="replace")

        blocks: list[ProjectBlock] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            lowered = stripped.lower()
            try:
                if lowered.startswith(_LEGACY_IMAGE_PREFIX):
                    rest = stripped[len(_LEGACY_IMAGE_PREFIX) :].strip()
                    parts = [piece for piece in rest.split(" ") if piece]
                    if len(parts) < 3:
                        raise ValueError("图像行至少需要 文件名 宽 高")
                    image_path = _safe_relative(parts[0], project_dir=directory)
                    width, height = int(parts[1]), int(parts[2])
                    depth = int(parts[3]) if len(parts) >= 4 else 2
                    if depth not in (2, 4):
                        depth = 2
                    blocks.append(
                        ProjectBlock(
                            index=len(blocks),
                            kind="image",
                            size=LogoSize(width, height, depth),
                            image_path=image_path,
                        )
                    )
                elif lowered.startswith(_LEGACY_BINARY_PREFIX):
                    rest = stripped[len(_LEGACY_BINARY_PREFIX) :].strip()
                    if not rest:
                        raise ValueError("binary: 行缺少文件名")
                    blocks.append(
                        ProjectBlock(
                            index=len(blocks),
                            kind="binary",
                            binary_path=_safe_relative(rest, project_dir=directory),
                            decompressed_ok=False,
                        )
                    )
                else:
                    raise ValueError("既不是 image: 也不是 binary: 行")
            except (ValueError, IndexError) as exc:
                raise ProjectError(
                    f"无法解析 {LEGACY_PROJECT_FILE} 第 {line_no} 行：{stripped!r}（{exc}）",
                    hint="旧版工程行格式应为 image:img1.png 540 960 4 或 binary:block5.dat。",
                    context={"path": str(legacy_file), "line": line_no},
                ) from exc

        if not blocks:
            raise ProjectError(
                f"旧版工程 {LEGACY_PROJECT_FILE} 中没有任何块",
                hint="工程已损坏，请重新从 logo.bin 解包。",
                context={"path": str(legacy_file)},
            )

        pending: list[int] = []
        warnings: list[str] = []
        if source_container is not None:
            if source_container.count != len(blocks):
                raise ProjectError(
                    f"旧版工程有 {len(blocks)} 个块，但源镜像有 {source_container.count} 个块，无法对应",
                    hint="请使用与工程对应的源 logo.bin 重新导入。",
                )
            for position, source_block in enumerate(source_container.blocks):
                project_block = blocks[position]
                if project_block.kind == "binary" and isinstance(source_block, BinaryBlock):
                    project_block.decompressed_ok = source_block.decompressed_ok
                raw = getattr(source_block, "raw_compressed", None)
                if raw is not None:
                    raw_rel = Path(RAW_DIR) / f"{position}.z"
                    _atomic_write(directory / raw_rel, raw)
                    project_block.raw_compressed_path = raw_rel
                if position in source_container.pending_image_blocks:
                    pending.append(position)
                    if project_block.kind == "binary":
                        # 还原"尺寸未知的待识别块"语义（GUI 显示为待识别分辨率）
                        project_block.kind = "image"
            warnings.extend(source_container.warnings)
        else:
            # 无源镜像时，若目录里已有本重建版保存的 raw/N.z，自动挂接以保持无损
            for project_block in blocks:
                candidate = Path(RAW_DIR) / f"{project_block.index}.z"
                if project_block.raw_compressed_path is None and (directory / candidate).exists():
                    project_block.raw_compressed_path = candidate
                    if project_block.kind == "binary":
                        try:
                            raw = (directory / candidate).read_bytes()
                            data_file = (
                                directory / project_block.binary_path
                                if project_block.binary_path is not None
                                else None
                            )
                            if data_file is not None and data_file.exists():
                                project_block.decompressed_ok = zlib.decompress(raw) == data_file.read_bytes()
                        except (OSError, zlib.error):
                            project_block.decompressed_ok = False

        project = cls(
            directory,
            header_bytes,
            blocks,
            tag=decode_tag(header_bytes[8:16]),
            pending=pending,
            warnings=warnings,
        )
        return project

    def legacy_lines(self) -> list[str]:
        """生成原版 ``.project`` 每一行的内容（按块顺序）。"""
        lines: list[str] = []
        for block in self.blocks:
            if block.kind == "image" and block.size is None:
                # 尺寸未知的"待识别"块在原版工程里只能按二进制保存
                if block.binary_path is None:
                    raise ProjectError(f"块 {block.index} 尺寸未知且没有数据文件，无法导出旧版 .project")
                lines.append(f"{_LEGACY_BINARY_PREFIX}{block.binary_path.as_posix()}")
                continue
            if block.kind == "image":
                if block.image_path is None:
                    raise ProjectError(f"块 {block.index} 没有图片文件，无法导出旧版 .project")
                lines.append(
                    f"{_LEGACY_IMAGE_PREFIX}{block.image_path.as_posix()} "
                    f"{block.size.width} {block.size.height} {block.size.color_depth}"
                )
            else:
                if block.binary_path is None:
                    raise ProjectError(f"块 {block.index} 没有二进制文件，无法导出旧版 .project")
                lines.append(f"{_LEGACY_BINARY_PREFIX}{block.binary_path.as_posix()}")
        return lines

    def export_legacy(self, path: str | Path | None = None) -> Path:
        """导出原版工程文件：``.project`` 文本 + 无扩展名 ``header``。

        原版 LogoBuilder 的工程头文件叫 ``header``（无扩展名），这里一并写出，
        使本工具导出的工程可被原版直接打开（双向兼容）。
        """
        target = Path(path) if path is not None else self.project_dir / LEGACY_PROJECT_FILE
        content = "\n".join(self.legacy_lines()) + ("\n" if self.blocks else "")
        _atomic_write(target, content.encode("utf-8"))
        try:
            _atomic_write(self.project_dir / LEGACY_HEADER_FILE, self.header)
        except ExportError:  # pragma: no cover - 旧版 header 写失败不阻塞主流程
            pass
        return target

    # -- 保存 ----------------------------------------------------------
    def save(self) -> None:
        """把 header.bin / header（旧版名）/ project.json / .project / sizes.txt 写入工程目录。"""
        directory = self.project_dir
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # pragma: no cover - 目录创建失败
            raise ProjectError(f"无法创建工程目录：{directory}（{exc}）") from exc

        _atomic_write(directory / HEADER_FILE, self.header)
        # 原版文件名 header（无扩展名）——保证原版 LogoBuilder 能打开本工程
        _atomic_write(directory / LEGACY_HEADER_FILE, self.header)
        payload = {
            "format": PROJECT_FORMAT,
            "version": PROJECT_VERSION,
            "tag": self.tag,
            "mode": self.mode,
            "header": HEADER_FILE,
            "blocks": [block.to_dict() for block in self.blocks],
            "pending": list(self.pending),
            "sizes": sorted({str(block.size) for block in self.blocks if block.size is not None}),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _atomic_write(directory / PROJECT_FILE, text.encode("utf-8"))
        self.export_legacy()
        self._write_sizes_txt()
        self._header_synthesized = False
        self._dirty = False

    def _write_sizes_txt(self) -> None:
        sizes = sorted({str(block.size) for block in self.blocks if block.size is not None})
        _atomic_write(self.project_dir / "sizes.txt", ("\n".join(sizes) + ("\n" if sizes else "")).encode("utf-8"))

    # -- 编辑 ----------------------------------------------------------
    def replace_image(self, index: int, image: Any, *, fit: str = "stretch") -> None:
        """替换某图像块的图片（自动按块尺寸缩放），并写回 PNG。"""
        block = self.block_at(index)
        if block.kind != "image":
            raise ProjectError(f"块 {index} 不是图像块，不能替换图片")
        if block.size is None:
            raise ProjectError(
                f"块 {index} 尺寸未知，请先调用 set_block_size() 指定分辨率",
                hint="尺寸未知的块无法确定图片目标大小。",
            )
        if block.image_path is None:
            raise ProjectError(f"块 {index} 没有图片文件路径")
        resized = pixel.resize_image(image, block.size, fit)
        target = self.project_dir / block.image_path
        _save_png(target, resized)
        block.modified = True
        block.image_sha256 = _sha256_file(target)
        self._dirty = True

    def set_block_size(self, index: int, size: LogoSize) -> None:
        """设置/修改块尺寸。

        * 已有尺寸的图像块：更新尺寸（打包时按新尺寸重新缩放 PNG）；
        * 尺寸未知的待识别块 / 已解压二进制块：指定尺寸后转为图像块并生成 PNG。
        """
        if not isinstance(size, LogoSize):
            raise TypeError(f"size 必须是 LogoSize，收到 {type(size).__name__}")
        block = self.block_at(index)
        if block.kind == "image" and block.size is not None:
            block.size = size
            block.modified = True
            block.image_sha256 = None  # 尺寸变了，需要重新编码
            self._dirty = True
            return

        # 以下为"二进制块 / 待识别块 -> 图像块"转换
        if not block.decompressed_ok:
            raise ProjectError(
                f"块 {index} 的数据无法解压，不能转换为图像块",
                hint="该块只能作为二进制块原样保留。",
            )
        if block.binary_path is None:
            raise ProjectError(f"块 {index} 没有二进制数据文件")
        data_file = self.project_dir / block.binary_path
        if data_file.exists():
            data = data_file.read_bytes()
        elif block.raw_compressed_path is not None and (self.project_dir / block.raw_compressed_path).exists():
            data = zlib.decompress((self.project_dir / block.raw_compressed_path).read_bytes())
        else:
            raise ProjectError(f"块 {index} 缺少数据文件，无法设置尺寸")
        if len(data) != size.key:
            raise ProjectError(
                f"尺寸 {size} 需要 {size.key} 字节，但块 {index} 的数据为 {len(data)} 字节",
                hint="请选择与块数据长度匹配的分辨率/色深。",
                context={"block_index": index, "actual": len(data), "expected": size.key},
            )
        image_rel = self._next_image_path()
        _save_png(self.project_dir / image_rel, pixel.pixels_to_image(data, size))
        old_binary_rel = block.binary_path
        block.kind = "image"
        block.size = size
        block.image_path = image_rel
        block.binary_path = None
        block.binary_sha256 = None
        block.modified = False
        block.image_sha256 = _sha256_file(self.project_dir / image_rel)
        if index in self.pending:
            self.pending.remove(index)
        # 清理转换前遗留、且 project.json 不再引用的同名数据文件（QA BUG-2）
        self._delete_self_named_orphan(old_binary_rel)
        self._dirty = True

    # -- 工程内文件清理 ------------------------------------------------
    def _referenced_paths(self) -> set[Path]:
        references: set[Path] = set()
        for block in self.blocks:
            for value in (block.image_path, block.binary_path, block.raw_compressed_path):
                if value is not None:
                    references.add(value)
        return references

    def _delete_self_named_orphan(self, relative: Path | None) -> None:
        """删除不再被引用、且符合本工具命名规则（imgN.png / blockN.dat）的文件。

        只处理项目根目录下的 ``blockN.dat`` / ``imgN.png``，绝不碰用户手动放入的
        其它文件（以及 raw/ 下的原始压缩数据）。
        """
        if relative is None:
            return
        if len(relative.parts) != 1:
            return
        if not (_BLOCK_RE.match(relative.name) or _IMG_RE.match(relative.name)):
            return
        if relative in self._referenced_paths():
            return
        target = self.project_dir / relative
        try:
            if target.is_file():
                target.unlink()
        except OSError:  # pragma: no cover - 删除失败不阻塞转换
            pass

    def _next_image_path(self) -> Path:
        used: set[int] = set()
        for block in self.blocks:
            if block.image_path is not None:
                match = _IMG_RE.match(block.image_path.name)
                if match:
                    used.add(int(match.group(1)))
        number = 1
        while number in used or (self.project_dir / f"img{number}.png").exists():
            number += 1
        return Path(f"img{number}.png")

    # -- 组包 ----------------------------------------------------------
    def to_container(self) -> LogoContainer:
        """按工程当前内容组装 :class:`LogoContainer`。"""
        if self.mode == "raw":
            payload_chunks: list[bytes] = []
            blocks: list[Block] = []
            for block in self.blocks:
                data = self._read_binary_payload(block)
                payload_chunks.append(data)
                opaque = BinaryBlock(index=block.index, data=data, raw_compressed=None, decompressed_ok=False)
                opaque._parsed_data = opaque.data
                blocks.append(opaque)
            payload = b"".join(payload_chunks)
            return LogoContainer(
                header=self.header,
                tag=self.tag,
                blocks=blocks,
                mode="raw",
                raw_payload=payload,
                pending_image_blocks=list(self.pending),
            )

        if not self.blocks:
            raise ProjectError("工程没有任何块，无法打包")
        blocks = []
        for project_block in self.blocks:
            if project_block.kind == "image" and project_block.size is not None:
                blocks.append(self._build_image_block(project_block))
            else:
                # 二进制块，以及"尺寸未知、原样保留"的待识别块
                blocks.append(self._build_binary_block(project_block))
        return LogoContainer(
            header=self.header,
            tag=self.tag,
            blocks=blocks,
            pending_image_blocks=list(self.pending),
        )

    def _read_binary_payload(self, block: ProjectBlock) -> bytes:
        if block.binary_path is None:
            raise ProjectError(f"块 {block.index} 缺少二进制文件路径")
        path = self.project_dir / block.binary_path
        if not path.exists():
            if block.raw_compressed_path is not None:
                raw_path = self.project_dir / block.raw_compressed_path
                if raw_path.exists():
                    return zlib.decompress(raw_path.read_bytes())
            raise ProjectError(
                f"块 {block.index} 的数据文件缺失：{block.binary_path}",
                hint="请运行 repair() 尝试从原始压缩数据恢复，或重新解包工程。",
            )
        return path.read_bytes()

    def _image_file_changed(self, block: ProjectBlock, path: Path) -> bool:
        if block.image_sha256 is None:
            return False
        try:
            return _sha256_file(path) != block.image_sha256
        except OSError:  # pragma: no cover - 读取失败按已更改处理
            return True

    def _build_image_block(self, block: ProjectBlock) -> ImageBlock:
        if block.size is None:
            raise ProjectError(
                f"块 {block.index} 尺寸未知，无法打包",
                hint="请先调用 set_block_size() 指定分辨率/色深。",
                context={"block_index": block.index},
            )
        if block.image_path is None:
            raise ProjectError(f"块 {block.index} 缺少图片文件路径")
        image_path = self.project_dir / block.image_path
        use_raw = (
            not block.modified
            and block.raw_compressed_path is not None
            and (self.project_dir / block.raw_compressed_path).exists()
        )
        if use_raw:
            raw_path = self.project_dir / block.raw_compressed_path  # type: ignore[arg-type]
            if not image_path.exists() or not self._image_file_changed(block, image_path):
                try:
                    return ImageBlock.preserved(block.index, block.size, raw_path.read_bytes())
                except (zlib.error, CorruptContainer):
                    pass
        if not image_path.exists():
            raise ProjectError(
                f"块 {block.index} 的图片缺失：{block.image_path}",
                hint="请运行 repair() 用原始数据恢复图片，或重新替换该块。",
            )
        image = _load_image_rgba(image_path)
        pixels = pixel.image_to_pixels(image, block.size, "stretch")
        return ImageBlock(index=block.index, size=block.size, pixels=pixels, raw_compressed=None)

    def _build_binary_block(self, block: ProjectBlock) -> BinaryBlock:
        if block.binary_path is None:
            raise ProjectError(f"块 {block.index} 缺少二进制文件路径")
        path = self.project_dir / block.binary_path
        data: bytes | None = None
        if path.exists():
            data = path.read_bytes()
        elif block.raw_compressed_path is not None and (self.project_dir / block.raw_compressed_path).exists():
            data = zlib.decompress((self.project_dir / block.raw_compressed_path).read_bytes())
        else:
            raise ProjectError(
                f"块 {block.index} 的数据文件缺失：{block.binary_path}",
                hint="请运行 repair() 尝试恢复，或重新解包工程。",
            )
        if block.decompressed_ok and block.raw_compressed_path is not None:
            raw_path = self.project_dir / block.raw_compressed_path
            if raw_path.exists():
                raw = raw_path.read_bytes()
                try:
                    if zlib.decompress(raw) == data:
                        return BinaryBlock.preserved(block.index, data, raw, decompressed_ok=True)
                except zlib.error:  # pragma: no cover - raw 损坏则退回普通块
                    pass
        if block.decompressed_ok:
            binary = BinaryBlock(index=block.index, data=data, raw_compressed=None, decompressed_ok=True)
            binary._parsed_data = binary.data
            return binary
        # 不透明块：文件内容即写回字节，无需 raw 复用
        opaque = BinaryBlock(index=block.index, data=data, raw_compressed=None, decompressed_ok=False)
        opaque._parsed_data = opaque.data
        return opaque

    # -- 校验 / 修复 ---------------------------------------------------
    def validate(self) -> list[ProjectIssue]:
        """检查工程一致性，返回问题列表（error 必须修复，warning 建议修复）。"""
        issues: list[ProjectIssue] = []
        directory = self.project_dir

        if not directory.is_dir():
            issues.append(ProjectIssue("error", None, f"工程目录不存在：{directory}"))
            return issues
        header_file = directory / HEADER_FILE
        if header_file.exists():
            size = header_file.stat().st_size
            if size != HEADER_SIZE:
                issues.append(
                    ProjectIssue("error", None, f"header.bin 长度 {size} 字节（应为 {HEADER_SIZE}）")
                )
        else:
            legacy_header = directory / LEGACY_HEADER_FILE
            if legacy_header.exists():
                size = legacy_header.stat().st_size
                if size != HEADER_SIZE:
                    issues.append(
                        ProjectIssue(
                            "error",
                            None,
                            f"旧版 {LEGACY_HEADER_FILE} 长度 {size} 字节（应为 {HEADER_SIZE}）",
                        )
                    )
                else:
                    issues.append(
                        ProjectIssue(
                            "warning",
                            None,
                            f"缺少 header.bin（存在旧版 {LEGACY_HEADER_FILE}；保存工程会自动补齐）",
                        )
                    )
            else:
                issues.append(
                    ProjectIssue(
                        "error",
                        None,
                        f"缺少 {HEADER_FILE} / {LEGACY_HEADER_FILE}（打包时需要 512 字节文件头）",
                    )
                )
        project_file = directory / PROJECT_FILE
        if not project_file.exists():
            issues.append(ProjectIssue("error", None, f"缺少 {PROJECT_FILE}"))
        if not self.blocks:
            issues.append(ProjectIssue("error", None, "工程没有任何块"))
        if self.mode not in ("block-table", "raw"):
            issues.append(ProjectIssue("error", None, f"未知工程模式：{self.mode!r}"))

        referenced: set[Path] = set()
        for position, block in enumerate(self.blocks):
            if block.index != position:
                issues.append(
                    ProjectIssue("warning", block.index, f"块索引 {block.index} 与顺序位置 {position} 不一致")
                )
            if block.kind == "image":
                if block.size is None:
                    # 尺寸未知的待识别块：原样保留（数据在二进制文件/原始压缩数据里）
                    if block.image_path is not None:
                        issues.append(
                            ProjectIssue("error", block.index, "图像块尺寸未知却带有图片路径，工程状态异常")
                        )
                    if block.binary_path is not None:
                        referenced.add(block.binary_path)
                        if not (directory / block.binary_path).exists():
                            issues.append(
                                ProjectIssue("error", block.index, f"二进制数据缺失：{block.binary_path}")
                            )
                    if block.raw_compressed_path is not None:
                        referenced.add(block.raw_compressed_path)
                        if not (directory / block.raw_compressed_path).exists():
                            issues.append(
                                ProjectIssue(
                                    "warning",
                                    block.index,
                                    f"原始压缩数据缺失：{block.raw_compressed_path}",
                                )
                            )
                    if block.binary_path is None and block.raw_compressed_path is None:
                        issues.append(
                            ProjectIssue("error", block.index, "尺寸未知的待识别块缺少数据文件")
                        )
                    else:
                        issues.append(
                            ProjectIssue(
                                "warning",
                                block.index,
                                "该块尺寸未知（疑似现代变体：bpp=3/stride/前缀，将按二进制原样保留；"
                                "也可在界面中指定分辨率转为图像块）",
                            )
                        )
                else:
                    if block.image_path is None:
                        issues.append(ProjectIssue("error", block.index, "图像块缺少图片路径"))
                    else:
                        referenced.add(block.image_path)
                        image_file = directory / block.image_path
                        if not image_file.exists():
                            issues.append(
                                ProjectIssue("error", block.index, f"图片缺失：{block.image_path}")
                            )
                        else:
                            try:
                                with Image.open(image_file) as handle:
                                    width, height = handle.size
                            except (OSError, ValueError) as exc:
                                issues.append(
                                    ProjectIssue(
                                        "error",
                                        block.index,
                                        f"图片无法读取：{block.image_path}（{exc}）",
                                    )
                                )
                            else:
                                if (width, height) != (block.size.width, block.size.height):
                                    issues.append(
                                        ProjectIssue(
                                            "warning",
                                            block.index,
                                            f"图片尺寸 {width}x{height} 与块尺寸 {block.size} 不一致"
                                            "（打包时自动拉伸）",
                                        )
                                    )
                    if block.raw_compressed_path is not None:
                        referenced.add(block.raw_compressed_path)
                        if not (directory / block.raw_compressed_path).exists():
                            issues.append(
                                ProjectIssue(
                                    "warning",
                                    block.index,
                                    f"原始压缩数据缺失：{block.raw_compressed_path}"
                                    "（重新压缩后可能与源文件不逐字节一致）",
                                )
                            )
            else:
                if block.binary_path is None:
                    issues.append(ProjectIssue("error", block.index, "二进制块缺少数据文件"))
                else:
                    referenced.add(block.binary_path)
                    if not (directory / block.binary_path).exists():
                        issues.append(
                            ProjectIssue("error", block.index, f"二进制数据缺失：{block.binary_path}")
                        )
                if block.raw_compressed_path is not None:
                    referenced.add(block.raw_compressed_path)
                    if not (directory / block.raw_compressed_path).exists():
                        issues.append(
                            ProjectIssue(
                                "warning",
                                block.index,
                                f"原始压缩数据缺失：{block.raw_compressed_path}",
                            )
                        )
                if block.decompressed_ok and block.index in self.pending:
                    issues.append(
                        ProjectIssue(
                            "warning",
                            block.index,
                            "该块解压成功但尺寸未知（疑似现代变体：bpp=3/stride/前缀，"
                            "将按二进制原样保留；也可手动指定尺寸转为图像块）",
                        )
                    )

        # 未引用的多余文件（原版缺陷 B9：靠用户自己清理）
        if directory.is_dir():
            for child in sorted(directory.iterdir()):
                if not child.is_file():
                    continue
                name_lower = child.name.lower()
                if name_lower in {
                    PROJECT_FILE,
                    HEADER_FILE,
                    LEGACY_HEADER_FILE,
                    LEGACY_PROJECT_FILE,
                    "sizes.txt",
                    "updater-script",
                    "update-binary",
                    "ignored.txt",
                }:
                    continue
                if re.match(r"^(img\d+\.png|block\d+\.dat)$", child.name, re.IGNORECASE):
                    if child.relative_to(directory) not in referenced:
                        issues.append(
                            ProjectIssue("warning", None, f"存在未引用的文件：{child.name}（可删除）")
                        )
        return issues

    def repair(self, *, repair: bool = False) -> list[str]:
        """检查并（可选）修复工程。返回问题/修复描述列表。"""
        issues = self.validate()
        if not repair:
            return [str(issue) for issue in issues]

        messages: list[str] = []
        directory = self.project_dir

        # 1) header.bin + 原版文件名 header
        header_to_write = self.header if len(self.header) == HEADER_SIZE else _default_header(self.tag)
        for header_name in (HEADER_FILE, LEGACY_HEADER_FILE):
            header_file = directory / header_name
            if not header_file.exists() or header_file.stat().st_size != HEADER_SIZE:
                _atomic_write(header_file, header_to_write)
                messages.append(f"[已修复] 重建 {header_name}（512 字节）")

        # 2) 逐块修复
        for block in self.blocks:
            if block.kind == "image" and block.size is not None:
                messages.extend(self._repair_image_block(block))
            else:
                messages.extend(self._repair_binary_block(block))

        # 3) 清理不再被引用的本工具命名文件（QA BUG-2：转换后遗留的 blockN.dat 等）
        messages.extend(self._cleanup_orphan_files())

        # 4) 重新保存描述文件
        try:
            self.save()
            messages.append("[已修复] 重新保存 project.json / .project / sizes.txt")
        except (ProjectError, ExportError) as exc:
            messages.append(f"[未修复] 保存工程失败：{exc}")

        remaining = self.validate()
        for issue in remaining:
            prefix = "[未修复]" if issue.level == "error" else "[提示]"
            messages.append(f"{prefix} {issue}")
        return messages

    def _cleanup_orphan_files(self) -> list[str]:
        """删除项目根目录中不再被引用的 imgN.png / blockN.dat 文件。"""
        messages: list[str] = []
        if not self.project_dir.is_dir():
            return messages
        references = self._referenced_paths()
        for child in sorted(self.project_dir.iterdir()):
            if not child.is_file():
                continue
            relative = child.relative_to(self.project_dir)
            if not (_BLOCK_RE.match(child.name) or _IMG_RE.match(child.name)):
                continue
            if relative in references:
                continue
            try:
                child.unlink()
                messages.append(f"[已修复] 删除未引用的陈旧文件：{child.name}")
            except OSError as exc:  # pragma: no cover - 删除失败
                messages.append(f"[未修复] 无法删除陈旧文件 {child.name}：{exc}")
        return messages

    def _repair_image_block(self, block: ProjectBlock) -> list[str]:
        messages: list[str] = []
        directory = self.project_dir
        if block.size is None:
            # 尺寸未知的待识别块：按二进制块恢复缺失的数据文件即可
            return self._repair_binary_block(block)
        if block.image_path is None:
            return [f"[未修复] 块 {block.index} 缺少图片路径，无法自动修复"]
        image_file = directory / block.image_path
        raw: bytes | None = None
        if block.raw_compressed_path is not None:
            raw_file = directory / block.raw_compressed_path
            if raw_file.exists():
                raw = raw_file.read_bytes()
        if not image_file.exists():
            if raw is None:
                return [f"[未修复] 块 {block.index} 图片缺失且没有原始压缩数据可用"]
            try:
                pixels = zlib.decompress(raw)
                if len(pixels) != block.size.key:
                    raise CorruptContainer(
                        f"原始数据长度 {len(pixels)} 与块尺寸 {block.size}（需 {block.size.key} 字节）不符"
                    )
                _save_png(image_file, pixel.pixels_to_image(pixels, block.size))
                block.image_sha256 = _sha256_file(image_file)
                messages.append(f"[已修复] 块 {block.index} 从原始压缩数据重建图片 {block.image_path}")
            except (zlib.error, CorruptContainer) as exc:
                return [f"[未修复] 块 {block.index} 无法从原始数据重建图片：{exc}"]
            return messages
        try:
            with Image.open(image_file) as handle:
                width, height = handle.size
                image = handle.convert("RGBA")
        except (OSError, ValueError) as exc:
            return [f"[未修复] 块 {block.index} 图片无法读取：{exc}"]
        if (width, height) != (block.size.width, block.size.height):
            resized = pixel.resize_image(image, block.size, "stretch")
            _save_png(image_file, resized)
            block.image_sha256 = _sha256_file(image_file)
            block.modified = True
            messages.append(
                f"[已修复] 块 {block.index} 图片 {width}x{height} 已缩放为 {block.size}"
            )
        return messages

    def _repair_binary_block(self, block: ProjectBlock) -> list[str]:
        if block.binary_path is None:
            return [f"[未修复] 块 {block.index} 缺少二进制文件路径"]
        directory = self.project_dir
        binary_file = directory / block.binary_path
        if binary_file.exists():
            return []
        if block.raw_compressed_path is None:
            return [f"[未修复] 块 {block.index} 数据缺失且没有原始压缩数据"]
        raw_file = directory / block.raw_compressed_path
        if not raw_file.exists():
            return [f"[未修复] 块 {block.index} 数据与原始压缩数据都缺失"]
        raw = raw_file.read_bytes()
        try:
            data = zlib.decompress(raw)
        except zlib.error:
            data = raw
            block.decompressed_ok = False
        _atomic_write(binary_file, data)
        block.binary_sha256 = _sha256_file(binary_file)
        return [f"[已修复] 块 {block.index} 从原始压缩数据恢复 {block.binary_path}"]
