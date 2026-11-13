"""分辨率/色深数据库（sizes.txt）与尺寸推断。

原版 LogoBuilder 的 ``sizes.txt`` 本质是"解压后块字节数 -> 分辨率"的查表：

    WIDTHxHEIGHTxD          # D = 2 (RGB565) 或 4 (BGRA8888)，缺省 2
    key = WIDTH * HEIGHT * D

本模块在查表之外增加了更聪明的推断（宽高比、相邻块一致性、由长度反解），
供 GUI 在"块尺寸未知"时给出候选而不是强制弹窗（对应原版缺陷 B1/B6）。

现代 MTK 变体（YAMLP 的 detect_layout）可能使用 bpp=3 / stride 对齐 / 块前缀，
这些"解不成标准 RGB565/BGRA 图像"的块本版按二进制原样保留即可；
如需支持，应在这里扩展 :meth:`SizeDB.candidates` / :func:`guess_sizes_for_blocks`。
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

#: 允许的色深（每像素字节数）。
VALID_COLOR_DEPTHS: tuple[int, ...] = (2, 4)

#: 内置尺寸表文件名。
SIZES_FILE = "sizes.txt"

#: 用户尺寸表文件名（JSON；也可读取纯文本的 sizes.txt）。
USER_SIZES_FILE = "user_sizes.json"


def assets_dir() -> Path:
    """返回内置资源目录 ``src/logobuilder/assets``。"""
    return Path(__file__).resolve().parent.parent / "assets"


def builtin_sizes_file() -> Path:
    """内置分辨率表路径。"""
    return assets_dir() / SIZES_FILE


def user_sizes_path() -> Path:
    """默认的用户尺寸配置路径（按平台放在用户配置目录）。

    * Windows: ``%APPDATA%/LogoBuilder/user_sizes.json``
    * 其它: ``$XDG_CONFIG_HOME/logobuilder/user_sizes.json`` 或 ``~/.config/logobuilder/...``

    可用环境变量 ``LOGOBUILDER_CONFIG_DIR`` 覆盖配置目录（便于测试/便携模式）。
    """
    override = os.environ.get("LOGOBUILDER_CONFIG_DIR")
    if override:
        base = Path(override)
    elif sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        base = base / "LogoBuilder"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg else Path.home() / ".config"
        base = base / "logobuilder"
    return base / USER_SIZES_FILE


@dataclass(frozen=True)
class LogoSize:
    """一个"块尺寸"：宽 x 高 x 色深（每像素字节数）。

    ``key`` 等于该块解压后的字节数 ``width * height * color_depth``，
    这正是原版 ``sizesDict`` 的键。
    """

    width: int
    height: int
    color_depth: int = 2

    def __post_init__(self) -> None:
        # frozen dataclass 里用 object.__setattr__ 归一化 int
        for name in ("width", "height", "color_depth"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                try:
                    value = int(value)  # 允许 "540" 之类的字符串
                except (TypeError, ValueError) as exc:  # pragma: no cover - 防御
                    raise ValueError(f"LogoSize.{name} 必须是整数：{value!r}") from exc
                object.__setattr__(self, name, value)
        if self.width <= 0 or self.height <= 0:
            raise ValueError(
                f"尺寸必须为正整数：{self.width}x{self.height}（宽高需 > 0）"
            )
        if self.color_depth not in VALID_COLOR_DEPTHS:
            raise ValueError(
                f"色深必须为 2（RGB565）或 4（BGRA8888），收到 {self.color_depth!r}"
            )

    # ------------------------------------------------------------------
    @property
    def key(self) -> int:
        """解压后字节数 = width * height * color_depth。"""
        return self.width * self.height * self.color_depth

    @property
    def bpp(self) -> int:
        """色深别名（GUI 习惯用 bpp）。"""
        return self.color_depth

    @property
    def pixel_count(self) -> int:
        """像素个数 = width * height。"""
        return self.width * self.height

    @property
    def aspect(self) -> float:
        """宽高比 width / height。"""
        return self.width / self.height

    def __str__(self) -> str:  # "540x960x4"
        return f"{self.width}x{self.height}x{self.color_depth}"

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"LogoSize({self.width}x{self.height}x{self.color_depth})"

    # ------------------------------------------------------------------
    @staticmethod
    def parse(text: str) -> "LogoSize":
        """解析 ``"540x960x4"`` / ``"540x960"``（缺省色深 2）。

        同时容忍 ``X``、``*`` 分隔与首尾空白/``"x"`` 重复；非法抛 ``ValueError``。
        """
        if not isinstance(text, str):
            raise ValueError(f"尺寸文本必须是字符串，收到 {type(text).__name__}")
        raw = text.strip().lower().replace("*", "x").replace(" ", "")
        if not raw:
            raise ValueError("尺寸文本为空，应为 WIDTHxHEIGHTxD，如 540x960x4")
        parts = [p for p in raw.split("x") if p != ""]
        if len(parts) == 2:
            parts.append("2")
        if len(parts) != 3:
            raise ValueError(f"无法解析尺寸 {text!r}，格式应为 WIDTHxHEIGHTxD（如 540x960x4）")
        try:
            width, height, depth = (int(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"无法解析尺寸 {text!r}：宽/高/色深必须是整数") from exc
        return LogoSize(width, height, depth)

    @staticmethod
    def from_key(key: int) -> list["LogoSize"]:
        """由解压后字节数反解所有候选尺寸（不保证唯一）。

        实现上对 ``key / 2`` 与 ``key / 4`` 的每个因子对都生成候选（含宽高对调），
        因此可能很多；调用方一般应使用 :meth:`SizeDB.candidates` 获得带评分的候选。
        """
        key = int(key)
        if key <= 0:
            return []
        results: list[LogoSize] = []
        seen: set[tuple[int, int, int]] = set()
        for depth in VALID_COLOR_DEPTHS:
            if key % depth:
                continue
            pixels = key // depth
            limit = math.isqrt(pixels)
            for width in range(1, limit + 1):
                if pixels % width:
                    continue
                height = pixels // width
                for w, h in ((width, height), (height, width)):
                    if (w, h, depth) in seen:
                        continue
                    seen.add((w, h, depth))
                    results.append(LogoSize(w, h, depth))
        results.sort(key=lambda s: (s.color_depth, s.width, s.height))
        return results

    @staticmethod
    def from_image(image: Any) -> "LogoSize":
        """由 Pillow 图像尺寸构造 RGB565 尺寸（便捷函数）。"""
        return LogoSize(int(image.width), int(image.height), 2)


@dataclass(frozen=True)
class SizeCandidate:
    """带评分与原因的尺寸候选。"""

    size: LogoSize
    score: float  # 0..1，越大越可信
    reason: str  # 中文原因

    def __post_init__(self) -> None:
        score = float(self.score)
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"score 必须在 0..1 之间，收到 {self.score!r}")
        object.__setattr__(self, "score", score)
        if not self.reason:
            raise ValueError("SizeCandidate.reason 不能为空")

    def __repr__(self) -> str:  # pragma: no cover
        return f"SizeCandidate({self.size}, score={self.score:.2f}, {self.reason!r})"


# ----------------------------------------------------------------------
# 尺寸推断的辅助函数
# ----------------------------------------------------------------------

#: 常见分辨率宽高比优先区间（竖屏/横屏都常见）。
_COMMON_ASPECT_MIN = 0.25
_COMMON_ASPECT_MAX = 4.0


def _aspect_score(aspect: float, hint: float) -> float:
    """宽高比接近程度的评分（对数距离，0.6..0.85）。"""
    if aspect <= 0 or hint <= 0:
        return 0.6
    distance = abs(math.log(aspect / hint))
    # 完全相同 -> 0.85；相差 2 倍 -> 0.6 附近
    score = 0.85 - 0.25 * min(distance / math.log(2.0), 1.0)
    return round(max(0.6, min(0.85, score)), 4)


def _plausibility_score(width: int, height: int, depth: int) -> float:
    """无 aspect_hint 时，由"像不像真实分辨率"给出的基础分（0.6..0.75）。"""
    score = 0.6
    if width >= 16 and height >= 16:
        score += 0.05
    if width % 8 == 0 or width in (15, 27, 30, 32, 36, 38, 45, 48, 57, 63, 84, 102, 108, 135, 138, 163, 169, 218, 304):
        score += 0.03
    if height % 8 == 0 or height in (1, 2, 20, 24, 27, 28, 29, 36, 51, 52, 54, 64, 105, 121, 139, 480):
        score += 0.03
    if _COMMON_ASPECT_MIN <= width / height <= _COMMON_ASPECT_MAX:
        score += 0.04
    if depth == 4:
        score += 0.01
    return round(min(0.75, score), 4)


def _factor_pairs(pixels: int, *, limit: int = 4096, max_results: int = 256) -> list[tuple[int, int]]:
    """把像素数分解为 (宽, 高) 对，按"常见程度"粗略排序，限制数量防止爆炸。

    仅用于 ``sizes.txt`` 未命中时由解压长度反解候选；数量受 ``max_results`` 约束。
    """
    if pixels <= 0:
        return []
    pairs: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    if pixels <= limit:
        # 极端小图（如 sizes.txt 里的 135x1、102x1）也要能反解出来
        for w, h in ((1, pixels), (pixels, 1)):
            seen.add((w, h))
            pairs.append((w, h))
    # 只枚举较小的因子，宽高都不超过 limit，避免出现 1xN 这种无意义候选
    upper = min(math.isqrt(pixels), limit)
    for width in range(2, upper + 1):
        if pixels % width:
            continue
        height = pixels // width
        if height > limit:
            continue
        for w, h in ((width, height), (height, width)):
            if w < 2 or h < 2 or (w, h) in seen:
                continue
            seen.add((w, h))
            pairs.append((w, h))
            if len(pairs) >= max_results * 2:
                break
        if len(pairs) >= max_results * 2:
            break
    return pairs


class SizeDB:
    """分辨率数据库：内置 sizes.txt + 用户增量，支持精确查询与候选推断。"""

    def __init__(self, sizes: Iterable[LogoSize] = ()) -> None:
        #: key -> [LogoSize]（同一解压长度可能对应多个尺寸）
        self._by_key: dict[int, list[LogoSize]] = {}
        #: 保持插入顺序，便于稳定输出
        self._all: list[LogoSize] = []
        for size in sizes:
            self.add(size)

    # -- 构造/持久化 ---------------------------------------------------
    @classmethod
    def default(cls) -> "SizeDB":
        """载入内置 ``assets/sizes.txt`` 并合并用户配置（若存在）。"""
        db = cls()
        db._load_builtin()
        try:
            user_path = user_sizes_path()
            if user_path.exists():
                db.merge(cls.load_user(user_path))
        except OSError:
            # 用户配置不可读不应影响主流程
            pass
        return db

    def _load_builtin(self) -> None:
        path = builtin_sizes_file()
        if not path.exists():  # pragma: no cover - 兜底（资源缺失时不崩）
            return
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:  # pragma: no cover - 兼容 GBK 编写的旧表
            text = path.read_text(encoding="gbk", errors="replace")
        for size in _parse_sizes_text(text):
            self.add(size)

    @classmethod
    def load_user(cls, path: Path | str | None = None) -> "SizeDB":
        """读取用户尺寸配置（JSON 或纯文本 sizes.txt 格式），返回只含用户项的 DB。

        文件不存在/损坏时返回空 DB（不抛异常）；用 :meth:`save_user` 写回。
        """
        target = Path(path) if path is not None else user_sizes_path()
        db = cls()
        try:
            if not target.exists():
                return db
            text = target.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return db
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return db
            items: list[Any]
            if isinstance(payload, dict):
                items = payload.get("sizes", [])
            elif isinstance(payload, list):  # pragma: no cover - 兼容裸数组
                items = payload
            else:  # pragma: no cover
                items = []
            for item in items:
                try:
                    if isinstance(item, dict):
                        size = LogoSize(int(item["width"]), int(item["height"]), int(item.get("color_depth", 2)))
                    else:
                        size = LogoSize.parse(str(item))
                except (KeyError, TypeError, ValueError):
                    continue
                db.add(size)
        else:
            for size in _parse_sizes_text(text):
                db.add(size)
        return db

    def save_user(self, path: Path | str | None = None) -> Path:
        """把当前数据库写入用户配置文件（默认 :func:`user_sizes_path`），返回路径。

        写入格式为 JSON：``{"format": "logobuilder-sizes", "version": 1, "sizes": [...]}``；
        ``load_user`` 同时兼容纯文本 ``WxHxD`` 行。
        """
        target = Path(path) if path is not None else user_sizes_path()
        payload = {
            "format": "logobuilder-sizes",
            "version": 1,
            "sizes": [str(size) for size in self._all],
        }
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            from .errors import ExportError

            raise ExportError(
                f"无法保存用户尺寸配置：{target}（{exc}）",
                hint="请检查目录权限，或设置环境变量 LOGOBUILDER_CONFIG_DIR 指向可写目录。",
                context={"path": str(target)},
            ) from exc
        return target

    # -- 增删查 --------------------------------------------------------
    def add(self, size: LogoSize) -> None:
        """加入一个尺寸（重复自动忽略）。"""
        if not isinstance(size, LogoSize):
            raise TypeError(f"add() 需要 LogoSize，收到 {type(size).__name__}")
        bucket = self._by_key.setdefault(size.key, [])
        if size in bucket:
            return
        bucket.append(size)
        self._all.append(size)

    def merge(self, other: "SizeDB") -> None:
        """把另一个 DB 的尺寸并入当前 DB。"""
        for size in other:
            self.add(size)

    def __iter__(self):
        return iter(self._all)

    def __len__(self) -> int:
        return len(self._all)

    def __contains__(self, size: object) -> bool:
        if not isinstance(size, LogoSize):
            return False
        return size in self._by_key.get(size.key, ())

    @property
    def sizes(self) -> tuple[LogoSize, ...]:
        """当前全部尺寸（不可变视图）。"""
        return tuple(self._all)

    def exact(self, decompressed_len: int) -> list[LogoSize]:
        """返回解压长度为 ``decompressed_len`` 的所有内置/用户尺寸。"""
        try:
            length = int(decompressed_len)
        except (TypeError, ValueError):
            return []
        return list(self._by_key.get(length, ()))

    # -- 候选推断 ------------------------------------------------------
    def candidates(
        self,
        decompressed_len: int,
        *,
        aspect_hint: float | None = None,
        neighbors: Sequence[LogoSize] = (),
    ) -> list[SizeCandidate]:
        """为解压长度推断候选尺寸，按可信度降序返回。

        评分约定（与 spec_api 一致）：

        * 精确 key 匹配（尺寸表命中）：1.0；
        * 与相邻块分辨率一致：0.9（宽高相同仅色深不同：0.85）；
        * 宽高比匹配：0.6 ~ 0.85；
        * 其它同 key 候选：0.4。
        """
        try:
            length = int(decompressed_len)
        except (TypeError, ValueError):
            return []
        if length <= 0:
            return []

        best: dict[LogoSize, SizeCandidate] = {}

        def offer(size: LogoSize, score: float, reason: str) -> None:
            old = best.get(size)
            if old is None or score > old.score:
                best[size] = SizeCandidate(size, score, reason)

        exact = self.exact(length)
        for size in exact:
            if len(exact) == 1:
                offer(size, 1.0, "内置尺寸表精确匹配")
            elif aspect_hint and _aspect_close(size.aspect, aspect_hint):
                offer(size, 0.98, "尺寸表精确匹配且宽高比吻合")
            else:
                offer(size, 0.4, "尺寸表同长度候选（需确认）")

        neighbor_sizes: list[LogoSize] = [n for n in neighbors if isinstance(n, LogoSize)]
        for neighbor in neighbor_sizes:
            if neighbor.key == length:
                offer(neighbor, 0.9, "与相邻块分辨率一致")
                continue
            for depth in VALID_COLOR_DEPTHS:
                if neighbor.width * neighbor.height * depth == length:
                    offer(
                        LogoSize(neighbor.width, neighbor.height, depth),
                        0.85,
                        "与相邻块宽高相同、仅色深不同",
                    )

        # 尺寸表未命中（或命中多个）时，由长度反解宽高对，提供更多候选
        need_derived = not exact or len(exact) > 1
        if need_derived:
            for depth in VALID_COLOR_DEPTHS:
                if length % depth:
                    continue
                pixels = length // depth
                for width, height in _factor_pairs(pixels):
                    size = LogoSize(width, height, depth)
                    if aspect_hint is not None:
                        if not _aspect_close(size.aspect, aspect_hint, tolerance=1.5):
                            continue
                        score = _aspect_score(size.aspect, aspect_hint)
                        reason = "由解压长度反解，宽高比接近提示值"
                    else:
                        score = _plausibility_score(width, height, depth)
                        reason = "由解压长度反解（像素总数因式分解）"
                    offer(size, score, reason)

        ranked = sorted(
            best.values(),
            key=lambda c: (-c.score, c.size.color_depth, c.size.width, c.size.height),
        )
        # 候选数量上限：给 UI 一个可用的短列表
        return ranked[:32]


def _aspect_close(aspect: float, hint: float, *, tolerance: float = 1.15) -> bool:
    if aspect <= 0 or hint <= 0:
        return False
    return (1.0 / tolerance) <= (aspect / hint) <= tolerance


def _parse_sizes_text(text: str) -> list[LogoSize]:
    """解析 sizes.txt 文本（每行 WxHxD，容忍注释/空行/坏行）。"""
    results: list[LogoSize] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        try:
            results.append(LogoSize.parse(line))
        except ValueError:
            continue
    return results


def guess_sizes_for_blocks(
    blocks_len: Sequence[int],
    db: SizeDB | None = None,
) -> list[list[SizeCandidate]]:
    """对一串块解压长度批量推断尺寸，利用相邻块一致性交叉投票。

    返回与 ``blocks_len`` 等长的列表，每项是该块的候选（可能为空）。
    """
    database = db if db is not None else SizeDB.default()
    lengths = [int(n) for n in blocks_len]

    # 第一轮：每个块自己的精确匹配，作为"已知块"
    known: list[LogoSize | None] = []
    for length in lengths:
        exact = database.exact(length)
        known.append(exact[0] if len(exact) == 1 else None)

    # 全局最常见尺寸（机型通常统一分辨率），作为远离已知块时的相邻提示
    vote_counts: dict[LogoSize, int] = {}
    for size in known:
        if size is not None:
            vote_counts[size] = vote_counts.get(size, 0) + 1
    common = max(vote_counts, key=lambda s: (vote_counts[s], s.key)) if vote_counts else None

    results: list[list[SizeCandidate]] = []
    for index, length in enumerate(lengths):
        neighbors: list[LogoSize] = []
        for j in (index - 1, index + 1):
            if 0 <= j < len(known) and known[j] is not None:
                neighbors.append(known[j])  # type: ignore[arg-type]
        if not neighbors and common is not None:
            neighbors.append(common)
        results.append(database.candidates(length, neighbors=neighbors))
    return results


__all__ = [
    "LogoSize",
    "SizeCandidate",
    "SizeDB",
    "guess_sizes_for_blocks",
    "builtin_sizes",
    "assets_dir",
    "user_sizes_path",
]


def builtin_sizes() -> list[LogoSize]:
    """直接读取内置 sizes.txt（不含用户增量），主要为 CLI ``sizes`` 使用。"""
    db = SizeDB()
    db._load_builtin()
    return list(db)
