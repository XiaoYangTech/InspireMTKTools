"""界面数据模型：把 core 的「容器 / 项目」包装成界面直接消费的块视图。

设计原则：
* 本模块是 UI 里 **唯一** 直接调用 ``logobuilder.core`` 的地方（以及 app 的导出动作），
  布局/控件代码不碰格式细节；
* 所有 core 调用都按 ``docs/spec_api.md`` 的契约书写，并对可选接口做防御式回退，
  核心库小版本差异不应让界面崩溃；
* 工作线程只创建本模块对象，PIL 解码可以发生在工作线程（线程安全）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image

from . import core_bridge

# 允许的缩放方式（与 core.pixel.image_to_pixels 的 fit 参数一致）
FIT_MODES: tuple[str, ...] = ("stretch", "contain", "cover")
FIT_LABEL_KEYS: dict[str, str] = {
    "stretch": "replace.fit_stretch",
    "contain": "replace.fit_contain",
    "cover": "replace.fit_cover",
}

_db_lock = threading.Lock()
_db_cache: Any = None


# ── 尺寸库 ─────────────────────────────────────────────────
def get_size_db() -> Any:
    """惰性加载默认尺寸数据库（内置 sizes.txt + 用户增量）。"""
    global _db_cache
    with _db_lock:
        if _db_cache is None:
            core = core_bridge.get_core()
            _db_cache = core.SizeDB.default()
        return _db_cache


def reset_size_db() -> None:
    """清空缓存，下次重新加载（测试/清空记忆后使用）。"""
    global _db_cache
    with _db_lock:
        _db_cache = None


def remember_size(size: Any) -> Path | None:
    """把用户选择的分辨率写入本地尺寸库；失败不抛异常（返回 ``None``）。"""
    try:
        db = get_size_db()
        db.add(size)
        path = db.save_user()
        reset_size_db()
        return Path(path) if path else None
    except Exception:
        return None


def clear_remembered_sizes() -> Path | None:
    """尽力删除用户自定义尺寸文件（界面「清空记忆」用）。"""
    try:
        core = core_bridge.get_core()
        user_db = core.SizeDB.load_user()
        path = user_db.save_user()
        p = Path(path)
        if p.exists():
            p.unlink()
        reset_size_db()
        return p
    except Exception:
        return None


def size_to_text(size: Any, *, compact: bool = True) -> str:
    """``LogoSize`` → 显示文本，例如 ``540×960×4``。"""
    if size is None:
        return "—"
    width = getattr(size, "width", None)
    height = getattr(size, "height", None)
    depth = getattr(size, "color_depth", getattr(size, "bpp", 2))
    if width is None or height is None:
        return str(size)
    sep = "×" if compact else " × "
    return f"{width}{sep}{height}{sep}{depth}"


def size_pixels(size: Any) -> int:
    try:
        return int(size.width) * int(size.height)
    except Exception:
        return 0


def size_key(size: Any) -> int:
    try:
        return int(getattr(size, "key"))
    except Exception:
        try:
            return int(size.width) * int(size.height) * int(
                getattr(size, "color_depth", getattr(size, "bpp", 2))
            )
        except Exception:
            return 0


def size_from_parts(width: int, height: int, color_depth: int) -> Any:
    """构造 core ``LogoSize``（失败抛 ValueError，由调用方转成可读错误）。"""
    core = core_bridge.get_core()
    try:
        return core.LogoSize(int(width), int(height), int(color_depth))
    except TypeError:
        return core.LogoSize(int(width), int(height))


def size_candidates(
    decompressed_len: int,
    *,
    db: Any = None,
    aspect_hint: float | None = None,
    neighbors: Sequence[Any] = (),
) -> list[Any]:
    """按解压长度取候选分辨率；接口差异时逐级降级。"""
    try:
        database = db if db is not None else get_size_db()
        try:
            return list(
                database.candidates(
                    int(decompressed_len),
                    aspect_hint=aspect_hint,
                    neighbors=tuple(neighbors),
                )
            )
        except TypeError:
            return list(database.candidates(int(decompressed_len)))
    except core_bridge.CoreUnavailable:
        return []
    except Exception:
        return []


def guess_all_sizes(decompressed_lens: Sequence[int], db: Any = None) -> list[list[Any]]:
    """批量推断（相邻块交叉投票）；不可用时返回空。"""
    try:
        core = core_bridge.get_core()
        database = db if db is not None else get_size_db()
        return list(core.guess_sizes_for_blocks(list(decompressed_lens), database))
    except Exception:
        return []


# ── 块视图 ─────────────────────────────────────────────────
class BlockView:
    """界面展示用的块信息（不持有 core 对象，避免跨线程误用）。"""

    __slots__ = (
        "index",
        "kind",
        "size",
        "decompressed_len",
        "compressed_len",
        "modified",
        "note",
    )

    def __init__(
        self,
        index: int,
        kind: str,
        *,
        size: Any = None,
        decompressed_len: int | None = None,
        compressed_len: int | None = None,
        modified: bool = False,
        note: str | None = None,
    ) -> None:
        self.index = index
        self.kind = kind  # "image" | "binary" | "unknown"
        self.size = size
        self.decompressed_len = decompressed_len
        self.compressed_len = compressed_len
        self.modified = modified
        self.note = note

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    @property
    def is_binary(self) -> bool:
        return self.kind == "binary"

    @property
    def is_unknown(self) -> bool:
        return self.kind == "unknown"

    @property
    def status(self) -> str:
        """``ok`` / ``modified`` / ``unknown`` / ``binary``，供列表着色。"""
        if self.is_unknown:
            return "unknown"
        if self.modified:
            return "modified"
        if self.is_binary:
            return "binary"
        return "ok"

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"BlockView(index={self.index}, kind={self.kind}, size={self.size})"


# ── 会话基类 ───────────────────────────────────────────────
class UiSession:
    """一次打开（镜像或项目）的界面会话。"""

    mode: str = "container"

    def __init__(self, db: Any = None) -> None:
        self.db = db
        self._blocks: list[BlockView] = []
        self._image_cache: dict[int, Image.Image] = {}
        self._revision = 0

    # 子类实现 ──
    def _image_impl(self, index: int) -> Image.Image:
        raise NotImplementedError

    def replace_image(self, index: int, image: Image.Image, *, fit: str = "stretch") -> None:
        raise NotImplementedError

    def restore_original(self, index: int) -> bool:
        return False

    def can_restore(self, index: int) -> bool:
        return False

    def assign_size(self, index: int, size: Any) -> None:
        raise NotImplementedError

    def save(self) -> None:
        """保存项目（镜像会话为 no-op）。"""

    def to_bytes(self, *, pad_to_size: int | None = None, pad_byte: int = 0x00) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        self._image_cache.clear()

    @property
    def raw_mode(self) -> bool:
        """是否为「无块表」的原始负载镜像（如部分 lk.bin），此类镜像只能原样导出。"""
        return False

    @property
    def estimated_size(self) -> int | None:
        """打包后的预估字节数（未知时返回 None）。"""
        return None

    # 通用 ──
    @property
    def blocks(self) -> list[BlockView]:
        return self._blocks

    @property
    def count(self) -> int:
        return len(self._blocks)

    @property
    def tag(self) -> str:
        return "LOGO"

    @property
    def source_path(self) -> Path | None:
        return None

    @property
    def project_dir(self) -> Path | None:
        return None

    @property
    def display_name(self) -> str:
        path = self.project_dir or self.source_path
        return path.name if path else ""

    @property
    def dirty(self) -> bool:
        return any(b.modified for b in self._blocks)

    @property
    def raw_size(self) -> int | None:
        """镜像会话：原始文件大小；项目会话：无（打包前未知）。"""
        return None

    @property
    def revision(self) -> int:
        return self._revision

    def block(self, index: int) -> BlockView | None:
        if 0 <= index < len(self._blocks):
            return self._blocks[index]
        return None

    def image(self, index: int, *, refresh: bool = False) -> Image.Image | None:
        """取块的显示图像（RGBA）；二进制/尺寸未知块返回 ``None``。"""
        view = self.block(index)
        if view is None or not view.is_image:
            return None
        if refresh or index not in self._image_cache:
            try:
                img = self._image_impl(index)
            except Exception as exc:  # 单块失败不应毁掉整个会话
                view.note = f"{type(exc).__name__}: {exc}"
                return None
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            self._image_cache[index] = img
        return self._image_cache[index]

    def invalidate(self, index: int | None = None) -> None:
        if index is None:
            self._image_cache.clear()
        else:
            self._image_cache.pop(index, None)
        self._revision += 1

    def pending_unknowns(self) -> list[int]:
        return [b.index for b in self._blocks if b.is_unknown]

    def preview_for_size(self, index: int, size: Any) -> Image.Image | None:
        """按候选尺寸渲染未知尺寸块的预览（用于分辨率选择对话框）。"""
        return None

    def unknown_groups(self) -> dict[int, list[int]]:
        """``{解压字节数: [块索引...]}``，用于「应用到所有相同大小的块」。"""
        groups: dict[int, list[int]] = {}
        for view in self._blocks:
            if view.is_unknown and view.decompressed_len:
                groups.setdefault(int(view.decompressed_len), []).append(view.index)
        return groups

    def image_blocks(self) -> list[BlockView]:
        return [b for b in self._blocks if b.is_image]

    def total_compressed_len(self) -> int | None:
        values = [b.compressed_len for b in self._blocks if b.compressed_len is not None]
        return sum(values) if values else None

    def mark_all_saved(self) -> None:
        for view in self._blocks:
            view.modified = False


# ── 镜像会话（直接打开 logo.bin / logo.img，不落盘）────────
class ContainerSession(UiSession):
    mode = "container"

    def __init__(
        self,
        container: Any,
        *,
        source_path: Path | None = None,
        db: Any = None,
    ) -> None:
        super().__init__(db=db)
        self.container = container
        self._source_path = Path(source_path) if source_path else None
        self._originals: dict[int, Any] = {}
        self._pending: set[int] = set()
        self._build_views()

    # ── 构建 ─────────────────────────────────────────────
    def _build_views(self) -> None:
        pending_raw = getattr(self.container, "pending_image_blocks", None) or ()
        self._pending = set(int(i) for i in pending_raw)
        views: list[BlockView] = []
        for i, block in enumerate(getattr(self.container, "blocks", []) or []):
            views.append(self._view_for_block(i, block))
        self._blocks = views
        self._revision += 1

    def _view_for_block(self, index: int, block: Any) -> BlockView:
        size = getattr(block, "size", None)
        pixels = getattr(block, "pixels", None)
        raw = getattr(block, "raw_compressed", None)
        compressed_len = len(raw) if raw else None
        if size is not None and hasattr(size, "key"):
            return BlockView(
                index,
                "image",
                size=size,
                decompressed_len=size_key(size),
                compressed_len=compressed_len,
            )
        if index in self._pending and pixels is not None:
            return BlockView(
                index,
                "unknown",
                decompressed_len=len(pixels),
                compressed_len=compressed_len,
                note="待识别分辨率",
            )
        data = getattr(block, "data", None)
        return BlockView(
            index,
            "binary",
            decompressed_len=len(data) if data is not None else None,
            compressed_len=compressed_len,
        )

    def _sync_block(self, index: int) -> None:
        block = self.container.blocks[index]
        self._blocks[index] = self._view_for_block(index, block)
        if self._blocks[index].is_unknown:
            self._pending.add(index)

    # ── 会话接口 ─────────────────────────────────────────
    @property
    def tag(self) -> str:
        tag = getattr(self.container, "tag", None)
        return str(tag) if tag else "LOGO"

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def raw_size(self) -> int | None:
        try:
            if self._source_path and self._source_path.exists():
                return self._source_path.stat().st_size
        except OSError:
            return None
        return None

    @property
    def data_len(self) -> int | None:
        try:
            return int(self.container.data_len)
        except Exception:
            return None

    def _image_impl(self, index: int) -> Image.Image:
        block = self.container.blocks[index]
        size = getattr(block, "size", None)
        if size is None:
            raise ValueError("该块尚未识别分辨率")
        core = core_bridge.get_core()
        if hasattr(block, "to_image"):
            return block.to_image()
        return core.pixels_to_image(bytes(block.pixels), size)

    def replace_image(self, index: int, image: Image.Image, *, fit: str = "stretch") -> None:
        view = self.block(index)
        if view is None:
            raise IndexError(f"block index out of range: {index}")
        if not view.is_image or view.size is None:
            raise ValueError("该块尚未识别分辨率，请先识别尺寸再替换图片")
        core = core_bridge.get_core()
        pixels = core.image_to_pixels(image, view.size, fit=fit)
        new_block = self._make_image_block(index, view.size, pixels)
        # 第一次修改时保存原始块，供「恢复原图」
        if index not in self._originals:
            self._originals[index] = self.container.blocks[index]
        self.container.blocks[index] = new_block
        self._pending.discard(index)
        self._sync_block(index)
        self._blocks[index].modified = True
        self.invalidate(index)

    def _make_image_block(self, index: int, size: Any, pixels: bytes) -> Any:
        core = core_bridge.get_core()
        cls = core.ImageBlock
        try:
            return cls(index=index, size=size, pixels=pixels, raw_compressed=None)
        except TypeError:
            try:
                return cls(index, size, pixels, None)
            except TypeError:
                return cls(index, size, pixels)

    def restore_original(self, index: int) -> bool:
        original = self._originals.pop(index, None)
        if original is None:
            return False
        self.container.blocks[index] = original
        self._sync_block(index)
        self._blocks[index].modified = False
        self.invalidate(index)
        return True

    def can_restore(self, index: int) -> bool:
        return index in self._originals

    def assign_size(self, index: int, size: Any) -> None:
        view = self.block(index)
        if view is None:
            raise IndexError(f"block index out of range: {index}")
        result = self.container.assign_size(index, size)
        if result is not None and not hasattr(self.container.blocks[index], "size"):
            # 老实现通过返回值给出新块
            self.container.blocks[index] = result
        self._pending.discard(index)
        self._sync_block(index)
        if self._blocks[index].kind == "unknown":
            self._blocks[index].kind = "image"
        self._blocks[index].modified = True
        self.invalidate(index)

    def to_bytes(self, *, pad_to_size: int | None = None, pad_byte: int = 0x00) -> bytes:
        if pad_to_size:
            return self.container.to_bytes(pad_to_size=int(pad_to_size), pad_byte=int(pad_byte))
        return self.container.to_bytes()

    @property
    def raw_mode(self) -> bool:
        return getattr(self.container, "mode", "block-table") == "raw"

    @property
    def estimated_size(self) -> int | None:
        try:
            return 512 + int(self.container.data_len)
        except Exception:
            return None

    def pending_unknowns(self) -> list[int]:
        for view in self._blocks:
            if view.is_unknown:
                self._pending.add(view.index)
        return sorted(self._pending)

    def preview_for_size(self, index: int, size: Any) -> Image.Image | None:
        try:
            core = core_bridge.get_core()
            block = self.container.blocks[index]
            data = getattr(block, "data", None)
            if data is None:
                return None
            image = core.pixels_to_image(bytes(data), size)
            if image.mode != "RGBA":
                image = image.convert("RGBA")
            return image
        except Exception:
            return None


# ── 项目会话（解包后的可编辑工作目录）──────────────────────
class ProjectSession(UiSession):
    mode = "project"

    def __init__(self, project: Any, *, db: Any = None) -> None:
        super().__init__(db=db)
        self.project = project
        self._dir = Path(getattr(project, "project_dir", ".") or ".")
        self._originals_pil: dict[int, Image.Image] = {}
        self._build_views()

    def _build_views(self) -> None:
        views: list[BlockView] = []
        for i, pb in enumerate(getattr(self.project, "blocks", []) or []):
            is_image = getattr(pb, "kind", "binary") == "image"
            size = getattr(pb, "size", None)
            if is_image and size is None:
                kind = "unknown"
            else:
                kind = "image" if is_image else "binary"
            views.append(
                BlockView(
                    i,
                    kind,
                    size=size,
                    decompressed_len=size_key(size) if size is not None else None,
                    modified=bool(getattr(pb, "modified", False)),
                )
            )
        self._blocks = views
        self._revision += 1

    # ── 会话接口 ─────────────────────────────────────────
    @property
    def tag(self) -> str:
        tag = getattr(self.project, "tag", None)
        if tag:
            return str(tag)
        return "LOGO"

    @property
    def project_dir(self) -> Path:
        return self._dir

    @property
    def source_path(self) -> Path | None:
        return self._dir

    @property
    def display_name(self) -> str:
        return self._dir.name

    @property
    def dirty(self) -> bool:
        if any(b.modified for b in self._blocks):
            return True
        try:
            return bool(getattr(self.project, "dirty", False))
        except Exception:
            return False

    def _image_impl(self, index: int) -> Image.Image:
        path = self.project.image_file(index)
        with Image.open(path) as img:
            img.load()
            image = img.copy()
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        if index not in self._originals_pil:
            self._originals_pil[index] = image.copy()
        return image

    def replace_image(self, index: int, image: Image.Image, *, fit: str = "stretch") -> None:
        view = self.block(index)
        if view is None:
            raise IndexError(f"block index out of range: {index}")
        if not view.is_image:
            raise ValueError("该块不是图像块，无法替换图片")
        if index not in self._originals_pil and view.size is not None:
            try:
                current = self.image(index)
                if current is not None:
                    self._originals_pil[index] = current.copy()
            except Exception:
                pass
        self.project.replace_image(index, image, fit=fit)
        self.project.save()
        view.modified = True
        self.invalidate(index)

    def restore_original(self, index: int) -> bool:
        original = self._originals_pil.pop(index, None)
        view = self.block(index)
        if original is None or view is None or view.size is None:
            return False
        self.project.replace_image(index, original, fit="stretch")
        self.project.save()
        view.modified = False
        self.invalidate(index)
        return True

    def can_restore(self, index: int) -> bool:
        view = self.block(index)
        return view is not None and view.is_image and self._originals_pil.get(index) is not None

    def assign_size(self, index: int, size: Any) -> None:
        view = self.block(index)
        if view is None:
            raise IndexError(f"block index out of range: {index}")
        self.project.set_block_size(index, size)
        self.project.save()
        view.size = size
        view.decompressed_len = size_key(size)
        if view.kind == "unknown":
            view.kind = "image"  # 尺寸已知后转为图像块，预览/替换立即可用
        view.modified = True
        self.invalidate(index)

    def pending_unknowns(self) -> list[int]:
        return [b.index for b in self._blocks if (b.is_unknown or (b.is_image and b.size is None))]

    def to_bytes(self, *, pad_to_size: int | None = None, pad_byte: int = 0x00) -> bytes:
        self.project.save()
        container = self.project.to_container()
        if pad_to_size:
            return container.to_bytes(pad_to_size=int(pad_to_size), pad_byte=int(pad_byte))
        return container.to_bytes()

    @property
    def raw_mode(self) -> bool:
        return getattr(self.project, "mode", "block-table") == "raw"

    def save(self) -> None:
        self.project.save()
        self.mark_all_saved()

    # 卡刷包自定义文件（项目目录里的 updater-script / update-binary）
    def custom_zip_assets(self) -> tuple[str | None, Path | None]:
        script_text: str | None = None
        binary_path: Path | None = None
        script_path = self._dir / "updater-script"
        binary = self._dir / "update-binary"
        try:
            if script_path.is_file():
                script_text = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            script_text = None
        try:
            if binary.is_file():
                binary_path = binary
        except OSError:
            binary_path = None
        return script_text, binary_path


# ── boot_logo（裸 RGB565）尺寸候选 ─────────────────────────
class _BootLogoCandidate:
    """boot_logo 候选（与 core.SizeCandidate 属性兼容，供分辨率对话框复用）。"""

    __slots__ = ("size", "score", "reason")

    def __init__(self, size: Any, score: float, reason: str) -> None:
        self.size = size
        self.score = float(score)
        self.reason = reason


def _boot_logo_factor_candidates(file_length: int) -> list[Any]:
    """把裸 RGB565 长度反解成 W×H 候选（数据里没有宽高信息，只能因式分解）。"""
    if file_length <= 0 or file_length % 2:
        return []
    pixels = file_length // 2
    pairs: list[tuple[int, int]] = []
    try:
        import math

        for width in range(2, int(math.isqrt(pixels)) + 1):
            if pixels % width == 0:
                height = pixels // width
                if width >= 2 and height >= 2:
                    pairs.append((width, height))
                    if width != height:
                        pairs.append((height, width))
    except Exception:
        return []
    result: list[Any] = []
    for width, height in pairs:
        try:
            size = size_from_parts(width, height, 2)
        except Exception:
            continue
        aspect = width / height if height else 1.0
        landscape = aspect >= 1.0
        score = 0.55 if landscape else 0.50
        if 0.3 <= aspect <= 3.0:
            score += 0.1
        reason = "由 boot_logo 长度反解（裸 RGB565 无宽高信息）"
        result.append(_BootLogoCandidate(size, min(score, 0.85), reason))
    result.sort(key=lambda item: (-item.score, item.size.width, item.size.height))
    return result[:16]


def boot_logo_size_candidates(file_length: int) -> list[Any]:
    """根据裸 boot_logo 文件长度反解候选尺寸（仅 RGB565 / 色深 2）。

    裸数据没有宽高字段，同一长度往往对应多个 WxH，界面应让用户确认。
    """
    candidates = size_candidates(int(file_length), db=get_size_db())
    result: list[Any] = []
    for candidate in candidates:
        size = getattr(candidate, "size", candidate)
        if int(getattr(size, "color_depth", getattr(size, "bpp", 0)) or 0) != 2:
            continue
        if int(getattr(size, "key", 0) or 0) != int(file_length):
            continue
        result.append(candidate)
    if not result:
        # 尺寸库里只有 4bpp 命中（key 相同）或完全没命中：用因式分解补充 RGB565 候选
        result = _boot_logo_factor_candidates(int(file_length))
    return result


def boot_logo_unique_size(file_length: int) -> Any | None:
    """若裸 boot_logo 尺寸唯一则返回该 ``LogoSize``，否则返回 ``None``。"""
    candidates = boot_logo_size_candidates(file_length)
    if len(candidates) == 1:
        return getattr(candidates[0], "size", candidates[0])
    return None


# ── 工厂 ───────────────────────────────────────────────────
def open_container(path: str | Path, *, db: Any = None) -> ContainerSession:
    """解析镜像文件为会话（可能抛 core 异常，由调用方转成可读错误）。"""
    core = core_bridge.get_core()
    database = db if db is not None else get_size_db()
    src = Path(path)
    container = core.LogoContainer.load(src, db=database)
    return ContainerSession(container, source_path=src, db=database)


def new_project(
    project_dir: str | Path,
    container: Any,
    *,
    db: Any = None,
    overwrite: bool = False,
) -> ProjectSession:
    """由容器创建项目目录并打开（``overwrite`` 用于界面已确认的非空目录）。"""
    core = core_bridge.get_core()
    target = Path(project_dir)
    try:
        project = core.LogoProject.new(target, container, overwrite=overwrite)
    except TypeError:  # 兼容旧版 core 没有 overwrite 参数
        target.mkdir(parents=True, exist_ok=True)
        project = core.LogoProject.new(target, container)
    project.save()
    reopened = core.LogoProject.open(target)
    return ProjectSession(reopened, db=db)


def open_project(project_dir: str | Path, *, db: Any = None) -> ProjectSession:
    core = core_bridge.get_core()
    project = core.LogoProject.open(Path(project_dir))
    return ProjectSession(project, db=db)


def is_project_dir(path: str | Path) -> bool:
    """判断目录是否像 LogoBuilder 项目（用于拖放/打开时的友好提示）。"""
    p = Path(path)
    if not p.is_dir():
        return False
    if (p / "project.json").is_file():
        return True
    return (p / "header.bin").is_file() and (
        (p / ".project").is_file() or (p / "project.json").is_file()
    )


def snapshot_images(
    session: UiSession, indices: Iterable[int] | None = None
) -> dict[int, Image.Image]:
    """把若干块的当前图像读入内存（导出/对比用）。"""
    result: dict[int, Image.Image] = {}
    targets = list(indices) if indices is not None else [b.index for b in session.blocks]
    for index in targets:
        img = session.image(index)
        if img is not None:
            result[index] = img.copy()
    return result
