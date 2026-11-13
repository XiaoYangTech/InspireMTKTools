"""MTK LK 引导程序修补：解析、架构识别、警告补丁扫描 / 应用 / 校验、备份还原。

功能（详见 ``docs/spec_lkpatch.md``）：

1. 去除开机 "Orange State / Red State / 你已解锁 Bootloader" 警告及 5 秒等待；
2. 去除 dm-verity 校验失败警告及约 3.2 秒等待；
3. 可选清空警告文本（不改变长度/偏移，绝不触碰 ``androidboot.*`` 等内核启动参数）。

实现要点
--------
* 镜像：512 字节 MTK 头（magic ``0x58881688`` + size + name + ext magic ``0x58891689``）
  + payload；也支持无头裸 payload（自动识别）。
* ARM32 Thumb-2：内置 7 条社区公开特征码（精确字节匹配，命中不唯一需人工确认），
  补丁为 ``00207047``（``movs r0,#0; bx lr``）。
* AArch64：capstone 线性反汇编 → ``ADRP+ADD`` 窗口差引用的警告字符串 →
  向前扫描函数头（``stp x29,x30,[sp,#-imm]!`` / ``sub sp,sp,#imm``）→ 按
  Orange+Red 引用 / state ``cmp`` 链 / 延时对齐常量识别警告分发器，按 dm-verity 引用
  识别 dm-verity 警告函数；补丁为 ``e0031f2ac0035fd6``（``mov w0,wzr; ret``）。
* capstone 为**可选依赖**（``import capstone`` 延迟到真正需要 AArch64 时才执行）：
  未安装时 ARM32 精确特征码仍可用，AArch64 返回明确的"不支持（缺少 capstone）"。
* fail-safe：找不到候选 / 结构校验失败 / 会改动内核参数 → 拒绝写盘。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .errors import LogoBuilderError

__all__ = [
    "MTK_MAGIC",
    "LK_EXT_MAGIC",
    "HEADER_SIZE",
    "ARM32_ARCH",
    "AARCH64_ARCH",
    "UNKNOWN_ARCH",
    "PATCH_ARM32",
    "PATCH_AARCH64",
    "LK_PATCH_RULES_FILE",
    "DEFAULT_ARM32_RULES",
    "DEFAULT_RULES",
    "LkPatchError",
    "LkImage",
    "LkFinding",
    "LkWarningString",
    "LkScanReport",
    "parse_lk_image",
    "detect_arch",
    "scan",
    "apply_patches",
    "verify_patch",
    "disassemble_patch_points",
    "load_custom_rules",
    "backup_path",
    "patched_path",
    "save_backup",
    "restore_from_backup",
    "find_sibling_slots",
]

#: MTK 通用分区镜像头 magic（小端字节序 88 16 88 58）。
MTK_MAGIC = 0x58881688
#: 新版 LK 镜像扩展 magic（位于头 0x30）。
LK_EXT_MAGIC = 0x58891689
#: 固定头长度。
HEADER_SIZE = 0x200

ARM32_ARCH = "arm32-thumb"
AARCH64_ARCH = "aarch64"
UNKNOWN_ARCH = "unknown"

#: ARM32 Thumb 补丁字节：``movs r0,#0; bx lr``。
PATCH_ARM32 = bytes.fromhex("00207047")
#: AArch64 补丁字节：``mov w0, wzr; ret``。
PATCH_AARCH64 = bytes.fromhex("e0031f2ac0035fd6")

#: 内置规则资产路径。
LK_PATCH_RULES_FILE = Path(__file__).resolve().parent.parent / "assets" / "lk_patch_rules.json"

#: 补丁类别（可供 CLI/GUI 勾选）。
CATEGORIES: tuple[str, ...] = ("orange_state", "red_state", "dm_verity", "warning_text")


class LkPatchError(LogoBuilderError):
    """LK 镜像解析/修补相关的业务错误（文件损坏、不支持、拒绝写入等）。"""

    default_hint = (
        "请确认选择的是 MTK LK 分区镜像（lk.img / lk.bin）或对应的裸 LK payload；"
        "不确定时先备份原始文件，并优先使用 fastboot flash lk 刷写。"
    )


# ----------------------------------------------------------------------
# 数据模型（与 docs/spec_lkpatch.md §4 契约一致）
# ----------------------------------------------------------------------
@dataclass
class LkImage:
    """一个 LK 镜像（分区镜像或裸 payload）。"""

    path: Path | None
    data: bytes
    header: bytes | None
    payload: bytes
    payload_offset: int
    is_container: bool
    sha256: str
    name: str
    #: 头部 0x30 处的扩展 magic（旧格式/合成镜像可能缺失）
    ext_magic: int | None = None

    @property
    def size_field(self) -> int | None:
        """头部 @4 声明的 payload 长度（裸 payload 为 None）。"""
        if not self.is_container or self.header is None:
            return None
        return struct.unpack_from("<I", self.header, 4)[0]

    @property
    def file_size(self) -> int:
        return len(self.data)


@dataclass
class LkFinding:
    """一个候选补丁点。"""

    category: str
    arch: str
    kind: str  # "signature" | "heuristic"
    offset: int  # 文件绝对偏移（补丁起点）
    function_start: int | None  # 文件绝对偏移
    before: bytes
    after: bytes
    confidence: float  # 0..1
    evidence: list[str]
    selected: bool = True

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"未知补丁类别：{self.category!r}（可选 {', '.join(CATEGORIES)}）")
        self.before = bytes(self.before)
        self.after = bytes(self.after)
        if not self.before or len(self.before) != len(self.after):
            raise ValueError("补丁 before/after 长度必须相同且非空")
        self.confidence = float(self.confidence)
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence 必须在 0..1 之间")
        self.evidence = [str(item) for item in self.evidence]


@dataclass
class LkWarningString:
    """镜像中的一条已知警告文本。"""

    offset: int  # 文件绝对偏移
    text: str

    @property
    def length(self) -> int:
        return len(self.text.encode("utf-8", errors="replace"))


@dataclass
class LkScanReport:
    """扫描结果。"""

    image: LkImage
    arch: str
    findings: list[LkFinding]
    warnings: list[LkWarningString]
    guard_params: dict[str, list[str]]
    already_patched: bool
    verdict: str
    notes: list[str] = field(default_factory=list)

    @property
    def supported(self) -> bool:
        return self.verdict in ("supported-arm32", "supported-aarch64")

    def categories(self) -> list[str]:
        """当前候选里出现的类别（去重、稳定顺序）。"""
        seen: list[str] = []
        for finding in self.findings:
            if finding.category not in seen:
                seen.append(finding.category)
        return seen


@dataclass(frozen=True)
class _WarningOccurrence:
    offset: int  # payload 相对偏移
    text: str  # 解码后的文本

    @property
    def encoded(self) -> bytes:
        return self.text.encode("utf-8", errors="replace")


@dataclass(frozen=True)
class _StringRef:
    """一条 "adrp+add 指向警告字符串" 的代码引用（payload 相对地址）。"""

    address: int
    target: int
    text: str


# ----------------------------------------------------------------------
# 规则加载
# ----------------------------------------------------------------------
#: 内置 rules JSON 读取失败时的最小兜底（与资产内容一致的核心 7 条特征码）。
_FALLBACK_RULES_DOC: dict[str, Any] = {
    "format": "logobuilder-lk-patch-rules",
    "version": 1,
    "arm32": {
        "patch": "00207047",
        "rules": [
            {"category": "orange_state", "pattern": "08b50a4b7b441b681b68022b", "patch": "00207047"},
            {"category": "orange_state", "pattern": "08b50e4b7b441b681b68022b", "patch": "00207047"},
            {"category": "orange_state", "pattern": "08b50a4b7b441b681b68012b", "patch": "00207047"},
            {"category": "orange_state", "pattern": "08b50e4b7b441b681b68012b", "patch": "00207047"},
            {"category": "red_state", "pattern": "f0b5002489b0", "patch": "00207047"},
            {"category": "dm_verity", "pattern": "30b583b002ab0022", "patch": "00207047"},
            {"category": "dm_verity", "pattern": "022800d0704738b53224e0f7", "patch": "00207047"},
        ],
    },
    "aarch64": {
        "patch": "e0031f2ac0035fd6",
        "max_backtrack": 1024,
        "ref_delta_max": 8,
        "delay_constants": [5000000, 5000, 100000, 200000, 3200000],
        "min_confidence": 0.6,
        "min_selected_confidence": 0.8,
        "warning_ref_texts": [
            "Orange State",
            "OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.",
            "Red State",
            "Device Cannot Boot. Enter Repair Mode.",
            "Dismiss After 5 Seconds.",
            "dm-verity corruption",
            "Your device is corrupt.",
            "It can't be trusted and may not work properly.",
            "Device will continue to bootup in %ds",
            "Device occur dm vertity corruption",
            "tran_dm_fix=1",
            "dm-verity",
            "dm_verity",
        ],
        "warning_display_texts": [
            "Orange State",
            "OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.",
            "Red State",
            "Device Cannot Boot. Enter Repair Mode.",
            "Dismiss After 5 Seconds.",
            "dm-verity corruption",
            "Your device is corrupt.",
            "It can't be trusted and may not work properly.",
            "Device will continue to bootup in %ds",
            "Device occur dm vertity corruption",
        ],
        "orange_tokens": ["orange"],
        "red_tokens": ["red state", "failed verification", "device cannot boot"],
        "dm_tokens": ["dm-verity", "dm_verity", "verity", "corrupt", "it can't be trusted", "vertity"],
        "state_mapping_note": "真实样本逆向：w8==1/3 走 Red 路径，w8==2 走 Orange 路径。",
        "expected_delay": {"orange_state": 5000000, "dm_verity": 100000},
        "expected_delay_count": {"dm_verity": 30},
    },
    "guard_patterns": ["androidboot.", "verifiedbootstate", "veritymode", "androidboot.atm"],
}


def _hex_to_bytes(value: str, *, what: str) -> bytes:
    text = str(value or "").strip().lower().replace(" ", "").replace("0x", "")
    if not text or len(text) % 2:
        raise LkPatchError(
            f"{what} 不是合法的十六进制字节串：{value!r}",
            hint="特征码/补丁必须是偶数长度的 hex 字符串，例如 08b50a4b。",
        )
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise LkPatchError(f"{what} 含非十六进制字符：{value!r}", hint="请检查规则 JSON。") from exc


def _normalize_rule(raw: dict[str, Any], *, default_arch: str = ARM32_ARCH) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise LkPatchError(f"规则必须是对象，收到 {type(raw).__name__}")
    category = str(raw.get("category") or "").strip()
    if category not in CATEGORIES:
        raise LkPatchError(
            f"规则类别非法：{category!r}（可选 {', '.join(CATEGORIES)}）",
            hint="请检查自定义规则 JSON 的 category 字段。",
        )
    arch = str(raw.get("arch") or default_arch)
    if arch not in (ARM32_ARCH, AARCH64_ARCH):
        raise LkPatchError(f"规则架构非法：{arch!r}（可选 {ARM32_ARCH}/{AARCH64_ARCH}）")
    pattern = _hex_to_bytes(raw.get("pattern", ""), what="规则特征码")
    patch = _hex_to_bytes(raw.get("patch", DEFAULT_RULES_DOC["arm32"]["patch"]), what="规则补丁")
    if patch != PATCH_ARM32 and len(patch) != 4:
        raise LkPatchError(
            f"ARM32 补丁必须为 4 字节（如 00207047），收到 {patch.hex()}",
            hint="本项目统一使用 00207047 = movs r0,#0; bx lr。",
        )
    return {
        "category": category,
        "arch": arch,
        "pattern": pattern.hex(),
        "patch": patch.hex(),
        "description": str(raw.get("description") or ""),
        "source": str(raw.get("source") or ""),
    }


def _load_rules_document(path: Path | None = None) -> dict[str, Any]:
    target = path if path is not None else LK_PATCH_RULES_FILE
    try:
        text = target.read_text(encoding="utf-8-sig")
        payload = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return json.loads(json.dumps(_FALLBACK_RULES_DOC))
    if not isinstance(payload, dict) or payload.get("format") != "logobuilder-lk-patch-rules":
        return json.loads(json.dumps(_FALLBACK_RULES_DOC))
    return payload


def _normalize_rules_document(document: dict[str, Any]) -> dict[str, Any]:
    """把 rules JSON 归一化为内部使用的结构（bytes 字段为小写 hex 字符串）。"""
    arm32 = document.get("arm32") or {}
    raw_rules = arm32.get("rules") or []
    if not isinstance(raw_rules, list):
        raise LkPatchError("规则 JSON 的 arm32.rules 必须是数组")
    rules = [_normalize_rule(rule, default_arch=ARM32_ARCH) for rule in raw_rules]
    arm32_patch = _hex_to_bytes(arm32.get("patch") or "00207047", what="ARM32 全局补丁")
    aarch64 = document.get("aarch64") or {}
    aarch64_patch = _hex_to_bytes(aarch64.get("patch") or "e0031f2ac0035fd6", what="AArch64 全局补丁")
    if len(aarch64_patch) != 8:
        raise LkPatchError("AArch64 补丁必须为 8 字节（mov w0,wzr; ret）")
    delay_constants = [int(value) for value in (aarch64.get("delay_constants") or [5000000, 100000])]
    guard_patterns = [str(item) for item in (document.get("guard_patterns") or _FALLBACK_RULES_DOC["guard_patterns"])]
    return {
        "format": document.get("format"),
        "version": document.get("version"),
        "arm32_rules": rules,
        "arm32_patch": arm32_patch.hex(),
        "aarch64": {
            "patch": aarch64_patch.hex(),
            "max_backtrack": int(aarch64.get("max_backtrack", 1024)),
            "ref_delta_max": int(aarch64.get("ref_delta_max", 8)),
            "delay_constants": delay_constants,
            "min_confidence": float(aarch64.get("min_confidence", 0.6)),
            "min_selected_confidence": float(aarch64.get("min_selected_confidence", 0.8)),
            "warning_ref_texts": [str(item) for item in (aarch64.get("warning_ref_texts") or [])],
            "warning_display_texts": [str(item) for item in (aarch64.get("warning_display_texts") or [])],
            "orange_tokens": [str(item).lower() for item in (aarch64.get("orange_tokens") or ["orange"])],
            "red_tokens": [
                str(item).lower() for item in (aarch64.get("red_tokens") or ["red state", "failed verification"])
            ],
            "dm_tokens": [
                str(item).lower()
                for item in (aarch64.get("dm_tokens") or ["dm-verity", "dm_verity", "verity"])
            ],
            "state_mapping_note": str(aarch64.get("state_mapping_note") or ""),
            "expected_delay": {
                str(key): int(value) for key, value in (aarch64.get("expected_delay") or {}).items()
            },
            "expected_delay_count": {
                str(key): int(value) for key, value in (aarch64.get("expected_delay_count") or {}).items()
            },
            "category_confidence": {
                str(key): float(value) for key, value in (aarch64.get("category_confidence") or {}).items()
            },
        },
        "guard_patterns": guard_patterns,
        "safety": document.get("safety") or {},
    }


#: 原始 rules JSON（dict）。
DEFAULT_RULES_DOC: dict[str, Any] = _load_rules_document()
#: 归一化后的规则（内部用）。
DEFAULT_RULES: dict[str, Any] = _normalize_rules_document(DEFAULT_RULES_DOC)
#: 内置 ARM32 规则（对外暴露的 list[dict]，pattern/patch 为 hex 字符串）。
DEFAULT_ARM32_RULES: list[dict[str, Any]] = list(DEFAULT_RULES["arm32_rules"])


def load_custom_rules(path: str | Path) -> list[dict[str, Any]]:
    """加载自定义 ARM32 规则 JSON。

    支持两种顶层结构：``{"rules": [...]}`` 或直接数组；每条规则至少包含
    ``category`` / ``pattern`` / ``patch``。返回归一化后的 ``list[dict]``。
    """
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise LkPatchError(
            f"无法读取自定义规则文件：{target}（{exc}）",
            hint="请检查路径与文件权限。",
            context={"path": str(target)},
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LkPatchError(
            f"自定义规则 JSON 解析失败：{exc}",
            hint="请检查 JSON 语法（尤其是引号与逗号）。",
            context={"path": str(target)},
        ) from exc
    if isinstance(payload, dict):
        raw_rules = payload.get("rules")
        if raw_rules is None:
            raw_rules = payload.get("arm32_rules")
    elif isinstance(payload, list):
        raw_rules = payload
    else:
        raw_rules = None
    if not isinstance(raw_rules, list) or not raw_rules:
        raise LkPatchError(
            "自定义规则文件里没有规则数组（需要 {'rules': [...]} 或 [...]）",
            hint="每条规则至少包含 category / pattern / patch 三个字段。",
            context={"path": str(target)},
        )
    rules: list[dict[str, Any]] = []
    for position, raw in enumerate(raw_rules):
        try:
            rule = _normalize_rule(raw, default_arch=ARM32_ARCH)
        except LkPatchError as exc:
            raise LkPatchError(
                f"自定义规则第 {position + 1} 条非法：{exc.message}",
                hint=exc.hint,
                context={"path": str(target), "index": position},
            ) from exc
        if rule["arch"] != ARM32_ARCH:
            raise LkPatchError(
                f"自定义规则第 {position + 1} 条暂只支持 {ARM32_ARCH}（AArch64 走启发式检测）",
                context={"path": str(target), "index": position},
            )
        rules.append(rule)
    return rules


def _rules_for_scan(rules: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if rules is None:
        return list(DEFAULT_RULES["arm32_rules"])
    normalized = [_normalize_rule(rule, default_arch=ARM32_ARCH) for rule in rules]
    return [rule for rule in normalized if rule["arch"] == ARM32_ARCH]


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> Path:
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
            except OSError:  # pragma: no cover - 清理失败不掩盖原始异常
                pass
            raise
    except OSError as exc:
        from .errors import ExportError

        raise ExportError(
            f"无法写入文件：{path}（{exc}）",
            hint="请检查目标目录是否存在、是否有写权限，以及文件是否被占用。",
            context={"path": str(path)},
        ) from exc
    return path


def _decode_ascii(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


# ----------------------------------------------------------------------
# 规则 JSON 中文本列表（扫描用）
# ----------------------------------------------------------------------
def _rule_strings() -> tuple[list[str], list[str]]:
    aarch64 = DEFAULT_RULES["aarch64"]
    ref_texts = list(aarch64["warning_ref_texts"])
    display_texts = list(aarch64["warning_display_texts"])
    return ref_texts, display_texts


# ----------------------------------------------------------------------
# 镜像解析
# ----------------------------------------------------------------------
def _looks_like_mtk_header(data: bytes) -> bool:
    if len(data) < HEADER_SIZE:
        return False
    try:
        ext_magic = _u32(data, 0x30)
    except struct.error:  # pragma: no cover
        return False
    if ext_magic == LK_EXT_MAGIC or ext_magic == MTK_MAGIC:
        return True
    name_field = data[8:20].split(b"\x00", 1)[0].strip().lower()
    return name_field in (b"lk", b"lk_b", b"lk_a", b"bootloader")


def _find_aarch64_prologue_offsets(payload: bytes, *, limit: int | None = None) -> list[int]:
    """扫描 ``stp x29,x30,[sp,#-imm]!`` 的字节特征（无 capstone 也能识别架构）。"""
    results: list[int] = []
    boundary = min(len(payload) - 4, len(payload) if limit is None else limit)
    index = payload.find(b"\xfd\x7b")
    while index >= 0 and index <= boundary:
        if index % 4 == 0 and payload[index + 3] == 0xA9:
            results.append(index)
        index = payload.find(b"\xfd\x7b", index + 1)
    return results


def _find_thumb_prologue_offsets(payload: bytes, *, limit: int | None = None) -> list[int]:
    """扫描 Thumb-2 函数入口特征（push {..,lr} / 32 位 push.w）。"""
    patterns = (b"\x2d\xe9", b"\x08\xb5", b"\xf0\xb5", b"\x0f\xb5", b"\x10\xb5", b"\x38\xb5")
    boundary = len(payload) if limit is None else min(len(payload), limit)
    hits: list[int] = []
    for pattern in patterns:
        index = payload.find(pattern, 0, boundary)
        while index >= 0:
            if index % 2 == 0:
                hits.append(index)
                break
            index = payload.find(pattern, index + 1, boundary)
    return hits


def _looks_like_payload_code(payload: bytes) -> bool:
    """裸 payload 是否像 LK 代码（用于拒绝全零/随机文件）。"""
    if len(payload) < 16:
        return False
    if _find_aarch64_prologue_offsets(payload, limit=min(len(payload), 0x10000)):
        return True
    if _find_thumb_prologue_offsets(payload, limit=min(len(payload), 0x400)):
        return True
    ref_texts, display_texts = _rule_strings()
    for text in set(ref_texts) | set(display_texts):
        if text and text.encode("utf-8") in payload:
            return True
    return False


def parse_lk_image(data: bytes | bytearray | memoryview, path: str | Path | None = None) -> LkImage:
    """解析 LK 镜像（MTK 512 字节头分区镜像，或无头裸 payload）。

    校验头部 ``size`` 必须落在文件范围内；``magic`` 不符但结构明显是 MTK 头
    （name/ext magic）时抛 :class:`LkPatchError`，避免把坏头当裸 payload 误修补。
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(f"data 必须是 bytes，收到 {type(data).__name__}")
    blob = bytes(data)
    source_path = Path(path) if path is not None else None
    if not blob:
        raise LkPatchError("文件为空（0 字节），不是有效的 LK 镜像")

    if len(blob) >= HEADER_SIZE and _u32(blob, 0) == MTK_MAGIC:
        header = blob[:HEADER_SIZE]
        size = _u32(header, 4)
        available = len(blob) - HEADER_SIZE
        if size == 0:
            raise LkPatchError(
                "LK 头部 size 字段为 0，文件可能已损坏（不是有效的 LK 分区镜像）",
                hint="请重新从官方固件/分区备份中提取 lk.img。",
                context={"path": str(source_path) if source_path else None},
            )
        if size > available:
            raise LkPatchError(
                f"LK 头部声明 size={size} 字节，但文件在 512 字节头之后只剩 {available} 字节，"
                "文件可能被截断",
                hint="请确认文件完整（分区镜像通常为 4MB 等固定大小，尾部 0x00 填充）。",
                context={"size": size, "available": available},
            )
        payload = blob[HEADER_SIZE : HEADER_SIZE + size]
        name = _decode_ascii(header[8:20]) or "lk"
        ext_magic = _u32(header, 0x30) if len(header) >= 0x34 else None
        return LkImage(
            path=source_path,
            data=blob,
            header=header,
            payload=payload,
            payload_offset=HEADER_SIZE,
            is_container=True,
            sha256=_sha256(blob),
            name=name,
            ext_magic=ext_magic,
        )

    if len(blob) >= HEADER_SIZE and _looks_like_mtk_header(blob):
        magic = _u32(blob, 0)
        raise LkPatchError(
            f"不是有效的 MTK LK 镜像：magic=0x{magic:08X}，期望 0x{MTK_MAGIC:08X}",
            hint="请确认文件来自 MTK 机型的 lk 分区（注意不要选错 boot/preloader 镜像）。",
            context={"magic": magic, "expected": MTK_MAGIC},
        )
    if not _looks_like_payload_code(blob):
        raise LkPatchError(
            "不是有效的 LK 镜像：既没有 MTK 512 字节头，内容也不像 ARM 引导代码",
            hint="支持 MTK LK 分区镜像（lk.img/lk.bin）或从 bootloader 组合镜像中拆出的裸 LK payload。",
        )
    return LkImage(
        path=source_path,
        data=blob,
        header=None,
        payload=blob,
        payload_offset=0,
        is_container=False,
        sha256=_sha256(blob),
        name="lk",
        ext_magic=None,
    )


# ----------------------------------------------------------------------
# capstone（可选依赖，延迟加载）
# ----------------------------------------------------------------------
_CAPSTONE_SENTINEL: Any = "unset"
_CAPSTONE_MODULE: Any = _CAPSTONE_SENTINEL


def _try_capstone() -> Any | None:
    """返回 capstone 模块；未安装返回 None（模块级缓存）。"""
    global _CAPSTONE_MODULE
    if _CAPSTONE_MODULE is _CAPSTONE_SENTINEL:
        try:  # pragma: no cover - 环境相关
            import capstone  # type: ignore[import-not-found]

            _CAPSTONE_MODULE = capstone
        except Exception:
            _CAPSTONE_MODULE = None
    return None if _CAPSTONE_MODULE is _CAPSTONE_SENTINEL else _CAPSTONE_MODULE


def _require_capstone() -> Any:
    capstone = _try_capstone()
    if capstone is None:
        raise LkPatchError(
            "AArch64 启发式识别需要 capstone（未安装）",
            hint="请安装依赖：pip install capstone（或 pip install -e .[lk]），"
            "ARM32 精确特征码匹配不需要 capstone。",
        )
    return capstone


def _aarch64_disassembler() -> Any:
    capstone = _require_capstone()
    from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = False  # 用 op_str 正则解析操作数：比访问 operands 快一个数量级
    md.skipdata = True
    return md


def _iter_aarch64(payload: bytes, *, address: int = 0) -> Iterator[Any]:
    md = _aarch64_disassembler()
    for insn in md.disasm(payload, address):
        yield insn


def _disasm_lines(data: bytes, arch: str, *, address: int = 0, limit: int = 8) -> list[str]:
    """反汇编前 ``limit`` 条指令，返回 ``0x..: mnemonic op_str`` 文本。"""
    capstone = _try_capstone()
    lines: list[str] = []
    if capstone is None or not data:
        return lines
    try:
        if arch == AARCH64_ARCH:
            from capstone import CS_ARCH_ARM64, CS_MODE_ARM

            md = capstone.Cs(CS_ARCH_ARM64, CS_MODE_ARM)
            md.detail = False
            md.skipdata = False
            iterator = md.disasm(data, address)
        else:
            from capstone import CS_ARCH_ARM, CS_MODE_THUMB

            md = capstone.Cs(CS_ARCH_ARM, CS_MODE_THUMB)
            md.detail = False
            md.skipdata = False
            iterator = md.disasm(data, address)
        for count, insn in enumerate(iterator):
            if count >= limit:
                break
            text = f"0x{insn.address:x}: {insn.mnemonic} {insn.op_str}".rstrip()
            lines.append(text)
    except Exception:  # pragma: no cover - 反汇编失败不应崩溃
        return lines
    return lines


# ----------------------------------------------------------------------
# 架构识别
# ----------------------------------------------------------------------
def detect_arch(image: LkImage) -> str:
    """识别 LK payload 架构：``arm32-thumb`` / ``aarch64`` / ``unknown``。"""
    if not isinstance(image, LkImage):
        raise TypeError(f"image 必须是 LkImage，收到 {type(image).__name__}")
    payload = image.payload
    if len(payload) < 8:
        return UNKNOWN_ARCH

    # 1) AArch64 函数入口字节特征（不依赖 capstone）
    if _find_aarch64_prologue_offsets(payload, limit=min(len(payload), 0x10000)):
        return AARCH64_ARCH

    # 2) capstone 解码 payload 开头（真实 LK 通常以 bl / mrs / stp 等入口指令开始）
    capstone = _try_capstone()
    if capstone is not None:
        try:
            from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs

            md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
            md.detail = False
            md.skipdata = False
            first = list(md.disasm(payload[:64], 0))[:4]
            entry_mnemonics = set(DEFAULT_RULES["aarch64"].get("entry_mnemonics") or ())
            if first and first[0].mnemonic in ("bl", "b", "stp", "sub", "mrs", "ret"):
                return AARCH64_ARCH
            if first and sum(1 for insn in first if insn.mnemonic in entry_mnemonics) >= 3:
                return AARCH64_ARCH
        except Exception:  # pragma: no cover - 解码异常时继续启发式
            pass

    # 3) ARM32 Thumb 函数入口特征
    if _find_thumb_prologue_offsets(payload, limit=min(len(payload), 0x10000)):
        return ARM32_ARCH

    # 4) 有内置 ARM32 特征码命中 -> arm32
    for rule in DEFAULT_RULES["arm32_rules"]:
        if bytes.fromhex(rule["pattern"]) in payload:
            return ARM32_ARCH
    return UNKNOWN_ARCH


# ----------------------------------------------------------------------
# 警告字符串 / 内核参数
# ----------------------------------------------------------------------
def _warning_occurrences(payload: bytes, texts: Sequence[str]) -> list[_WarningOccurrence]:
    """payload 内所有已知警告文本出现位置（同偏移保留更长文本）。"""
    by_offset: dict[int, str] = {}
    for text in texts:
        if not text:
            continue
        encoded = text.encode("utf-8")
        start = 0
        while True:
            index = payload.find(encoded, start)
            if index < 0:
                break
            previous = by_offset.get(index)
            if previous is None or len(text) > len(previous):
                by_offset[index] = text
            start = index + 1
    return [_WarningOccurrence(offset, text) for offset, text in sorted(by_offset.items())]


def _warning_strings_from_occurrences(image: LkImage, occurrences: Sequence[_WarningOccurrence]) -> list[LkWarningString]:
    """合并被包含的警告文本区间（清空文本时用最长文本覆盖）。"""
    spans = [(occ.offset, occ.offset + len(occ.encoded), occ) for occ in occurrences]
    spans.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, _WarningOccurrence]] = []
    for span in spans:
        if any(existing[0] <= span[0] and span[1] <= existing[1] for existing in selected):
            continue
        selected.append(span)
    result = [
        LkWarningString(offset=image.payload_offset + start, text=occ.text)
        for start, _end, occ in sorted(selected, key=lambda item: item[0])
    ]
    return result


_GUARD_TOKEN_RE = re.compile(rb"androidboot\.[A-Za-z0-9_]+")
_GUARD_EXTRA_PATTERNS = (b"verifiedbootstate", b"veritymode", b"androidboot.atm")


def _collect_guard_params(image: LkImage) -> dict[str, list[str]]:
    """收集受保护的内核启动参数（人类可读，供 GUI 展示）。"""
    payload = image.payload
    base = image.payload_offset
    result: dict[str, list[str]] = {}
    for match in _GUARD_TOKEN_RE.finditer(payload):
        token = match.group().decode("ascii", errors="replace")
        end = match.end()
        if end < len(payload) and payload[end : end + 1] == b"=":
            stop = payload.find(b"\x00", end)
            if stop < 0:
                stop = min(len(payload), end + 128)
            snippet = payload[match.start() : stop].decode("ascii", errors="replace")
        else:
            snippet = token
        result.setdefault(token, []).append(f"0x{base + match.start():x}: {snippet}")
    for pattern in _GUARD_EXTRA_PATTERNS:
        literal = pattern.decode("ascii")
        start = 0
        while True:
            index = payload.find(pattern, start)
            if index < 0:
                break
            snippet = literal
            result.setdefault(literal, []).append(f"0x{base + index:x}: {snippet}")
            start = index + 1
    return result


def _guard_signature(payload: bytes) -> dict[str, int]:
    """守卫参数出现次数签名（apply/verify 前后必须一致）。"""
    patterns: list[str] = []
    for rule in DEFAULT_RULES["arm32_rules"]:  # 仅为稳定遍历，不参与
        break
    patterns.extend(str(item) for item in DEFAULT_RULES["guard_patterns"])
    signature: dict[str, int] = {}
    for pattern in patterns:
        encoded = pattern.encode("utf-8")
        if not encoded:
            continue
        signature[pattern] = payload.count(encoded)
    return signature


# ----------------------------------------------------------------------
# AArch64 扫描
# ----------------------------------------------------------------------
_CMP_RE = re.compile(r"cmp\s+(w\d+), #(\d+)")
_MOV_RE = re.compile(r"mov\s+(w\d+), #(0x[0-9a-fA-F]+|\d+)")
_MOVK_RE = re.compile(r"movk\s+(w\d+), #(0x[0-9a-fA-F]+|\d+), lsl #(\d+)")
_BL_RE = re.compile(r"#(0x[0-9a-fA-F]+|\d+)")
_ADRP_RE = re.compile(r"(x\d+), #(-?0x[0-9a-fA-F]+|-?\d+)")
_ADD_RE = re.compile(r"(x\d+), (x\d+), #(0x[0-9a-fA-F]+|\d+)")


def _string_refs_aarch64(
    payload: bytes,
    occurrence_map: dict[int, str],
    *,
    delta_max: int,
) -> tuple[list[_StringRef], dict[int, int]]:
    """扫描 ADRP+ADD 字符串引用，同时统计 bl 调用者数量。"""
    refs: list[_StringRef] = []
    callers: dict[int, int] = {}
    adrp_pages: dict[str, int] = {}
    for insn in _iter_aarch64(payload, address=0):
        # capstone 5.x 的 SKIPDATA 伪指令（id==0，mnemonic="data"）：
        # 只使用 address/mnemonic/op_str 等安全属性，绝不访问 insn.operands
        # （SKIPDATA 模式下会抛 capstone.CsError）。
        if getattr(insn, "id", 0) == 0:
            continue
        mnemonic = insn.mnemonic
        op_str = insn.op_str or ""
        if mnemonic == "adrp":
            match = _ADRP_RE.search(op_str)
            if match:
                adrp_pages[match.group(1)] = int(match.group(2), 0)
            continue
        if mnemonic == "bl":
            match = _BL_RE.search(op_str)
            if match:
                target = int(match.group(1), 0)
                callers[target] = callers.get(target, 0) + 1
            continue
        if mnemonic == "add":
            match = _ADD_RE.search(op_str)
            if match:
                rd, rn, immediate = match.group(1), match.group(2), int(match.group(3), 0)
                if rn in adrp_pages:
                    target = adrp_pages[rn] + immediate
                    text = occurrence_map.get(target)
                    if text is None and delta_max > 0:
                        for delta in range(1, delta_max + 1):
                            text = occurrence_map.get(target - delta)
                            if text is not None:
                                break
                    if text is not None:
                        refs.append(_StringRef(insn.address, target, text))
                    adrp_pages.pop(rd, None)
            continue
    return refs, callers


def _is_function_prologue(payload: bytes, address: int) -> bool:
    """该 4 字节地址是否是 AArch64 函数入口（stp x29,x30 前置 / sub sp）。"""
    if address < 0 or address + 4 > len(payload) or address % 4:
        return False
    word = struct.unpack_from("<I", payload, address)[0]
    # stp x29,x30,[sp,#-imm]! : imm7（bit21..15）非 0，其余固定
    # 编码 = 0xA9800000 | imm7<<15 | 30<<10 | 31<<5 | 29  -> 固定位 0xA9807BFD
    if (word & 0xFFC07FFF) == 0xA9807BFD and ((word >> 15) & 0x7F) != 0:
        return True
    # sub sp, sp, #imm : imm12（bit21..10）非 0
    if (word & 0xFFC003FF) == 0xD10003FF and ((word >> 10) & 0xFFF) != 0:
        return True
    return False


def _is_terminal_instruction(payload: bytes, address: int) -> bool:
    """是否是函数终止指令（ret / br）。

    反向找函数头时遇到 return/br 说明已经跨过上一个函数，必须停止；
    无条件 ``b`` 不在此列（编译器常用函数内跳转，不能当函数结尾）。
    """
    if address < 0 or address + 4 > len(payload) or address % 4:
        return False
    word = struct.unpack_from("<I", payload, address)[0]
    if (word & 0xFFFFFC1F) == 0xD65F0000:  # ret / ret xN
        return True
    if (word & 0xFFFFFC1F) == 0xD61F0000:  # br xN
        return True
    return False


def _find_function_start(
    payload: bytes, ref_address: int, *, max_backtrack: int
) -> tuple[int, bool] | None:
    """由字符串引用反推函数入口；返回 ``(入口, 是否已是补丁字节)``。

    只检查「refs 推导出的候选入口」是否是补丁字节，绝不在全 payload 模式搜索，
    避免把 AArch64 常见的自然 ``mov w0,wzr; ret`` 结尾误判为"已打补丁"。
    """
    address = ref_address & ~3
    limit = max(0, address - max_backtrack)
    while address >= limit:
        # ① 候选入口本身是补丁字节（完整 8 字节）
        if payload[address : address + len(PATCH_AARCH64)] == PATCH_AARCH64:
            return address, True
        # ② 当前地址正好落在补丁后半（ret 半字）时，回看 4 字节仍是候选入口
        if address >= 4 and payload[address - 4 : address - 4 + len(PATCH_AARCH64)] == PATCH_AARCH64:
            return address - 4, True
        # ③ 正常函数入口
        if _is_function_prologue(payload, address):
            return address, False
        # ④ 遇到 return/br：已跨出本函数，停止（避免把上一个函数入口当候选）
        if _is_terminal_instruction(payload, address):
            return None
        address -= 4
    return None


def _group_refs_by_function(
    payload: bytes,
    refs: Sequence[_StringRef],
    *,
    max_backtrack: int,
) -> tuple[dict[int, list[_StringRef]], list[int], list[_StringRef]]:
    groups: dict[int, list[_StringRef]] = {}
    patched_starts: set[int] = set()
    unmatched: list[_StringRef] = []
    for ref in refs:
        located = _find_function_start(payload, ref.address, max_backtrack=max_backtrack)
        if located is None:
            unmatched.append(ref)
            continue
        start, patched = located
        groups.setdefault(start, []).append(ref)
        if patched:
            patched_starts.add(start)
    for items in groups.values():
        items.sort(key=lambda ref: ref.address)
    return groups, sorted(patched_starts), unmatched


@dataclass
class _FunctionFeatures:
    head_lines: list[str]
    tail_lines: list[str]
    cmp_chain: list[tuple[str, int, int]]  # (reg, imm, address)
    delay_values: list[tuple[int, int, str]]  # (value, address, line)
    instruction_count: int


def _function_features(payload: bytes, start: int, end: int) -> _FunctionFeatures:
    head: list[str] = []
    tail: list[str] = []
    cmp_chain: list[tuple[str, int, int]] = []
    delay_values: list[tuple[int, int, str]] = []
    register_values: dict[str, int] = {}
    delay_constants = set(DEFAULT_RULES["aarch64"]["delay_constants"])
    lines: list[str] = []
    count = 0
    for insn in _iter_aarch64(payload[start:end], address=start):
        if getattr(insn, "id", 0) == 0:
            # SKIPDATA 数据伪指令：跳过（不可访问 operands，且不是有效指令）
            continue
        count += 1
        if count > 4096:  # pragma: no cover - 防御超大函数
            break
        line = f"0x{insn.address:x}: {insn.mnemonic} {insn.op_str}".rstrip()
        lines.append(line)
        if len(head) < 6:
            head.append(line)
        mnemonic = insn.mnemonic
        op_str = insn.op_str or ""
        match = _CMP_RE.search(f"{mnemonic} {op_str}")
        if match:
            cmp_chain.append((match.group(1), int(match.group(2), 0), insn.address))
        match = _MOVK_RE.search(f"{mnemonic} {op_str}")
        if match:
            register = match.group(1)
            immediate = int(match.group(2), 0)
            shift = int(match.group(3))
            previous = register_values.get(register, 0)
            value = (previous & ~(0xFFFF << shift)) | ((immediate & 0xFFFF) << shift)
            register_values[register] = value
            if value in delay_constants:
                delay_values.append((value, insn.address, line))
        else:
            match = _MOV_RE.search(f"{mnemonic} {op_str}")
            if match:
                register = match.group(1)
                value = int(match.group(2), 0) & 0xFFFFFFFF
                register_values[register] = value
                if value in delay_constants:
                    delay_values.append((value, insn.address, line))
    tail = lines[-4:] if lines else []
    return _FunctionFeatures(head, tail, cmp_chain, delay_values, count)


def _contains_any(texts: Sequence[str], tokens: Sequence[str]) -> bool:
    lowered = [text.lower() for text in texts]
    return any(token in text for token in tokens for text in lowered)


def _build_aarch64_evidence(
    image: LkImage,
    func_start: int,
    refs: Sequence[_StringRef],
    features: _FunctionFeatures,
    delay: int | None,
    delay_count: int,
    callers: int,
    *,
    next_start: int | None,
) -> list[str]:
    base = image.payload_offset
    evidence: list[str] = [
        f"函数入口：文件 0x{base + func_start:x}（payload +0x{func_start:x}）",
    ]
    for line in features.head_lines[:4]:
        evidence.append(f"函数头反汇编 {line}")
    for ref in refs[:8]:
        text = ref.text.replace("\n", " ")[:48]
        evidence.append(
            f"字符串引用：{text!r}（payload +0x{ref.target:x}，引用指令 payload +0x{ref.address:x}）"
        )
    if features.cmp_chain:
        chain = " / ".join(f"cmp {reg},#{imm}" for reg, imm, _addr in features.cmp_chain[:6])
        evidence.append(f"状态比较链：{chain}")
        state_imms = {imm for _reg, imm, _addr in features.cmp_chain}
        if {1, 3, 2} <= state_imms:
            evidence.append(
                "状态映射（真实样本逆向）：w8==1/3 走 Red 路径，w8==2 走 Orange 路径（同一入口，提前 return 即可全部跳过）"
            )
    if delay is not None:
        total = delay * max(1, delay_count)
        evidence.append(
            f"延时常量：{max(1, delay_count)} × {delay:,} µs = {total:,} µs"
            f"（约 {total / 1_000_000:.1f} 秒）"
            + (f"；单次常量 0x{delay:X}" if delay_count > 1 else f"；常量 0x{delay:X}")
        )
    if features.tail_lines:
        evidence.append("函数尾部反汇编 " + features.tail_lines[-1])
    evidence.append(f"调用者数量：{callers}（直接 bl 到该入口）")
    if next_start is not None:
        evidence.append(f"函数区间：payload +0x{func_start:x} .. +0x{next_start:x}")
    return evidence


def _scan_aarch64(
    image: LkImage,
    *,
    notes: list[str],
) -> tuple[list[LkFinding], list[_StringRef], list[LkFinding]]:
    payload = image.payload
    base = image.payload_offset
    capstone = _try_capstone()
    if capstone is None:
        notes.append(
            "AArch64 启发式识别需要 capstone（未安装）：无法自动定位警告函数；"
            "可 pip install capstone 后重试（ARM32 精确特征码不受影响）"
        )
        return [], [], []

    ref_texts, display_texts = _rule_strings()
    occurrences = _warning_occurrences(payload, ref_texts)
    occurrence_map: dict[int, str] = {}
    for occurrence in occurrences:
        # 同一偏移可能同时匹配长短文本，保留更长的（证据展示更准确）
        current = occurrence_map.get(occurrence.offset)
        if current is None or len(occurrence.text) > len(current):
            occurrence_map[occurrence.offset] = occurrence.text
    if not occurrence_map:
        notes.append("未找到已知的 LT 警告字符串（Orange/Red/dm-verity 等），无法启发式定位警告函数")
        return [], [], []

    aarch64_rules = DEFAULT_RULES["aarch64"]
    try:
        refs, callers = _string_refs_aarch64(
            payload,
            occurrence_map,
            delta_max=int(aarch64_rules["ref_delta_max"]),
        )
    except LkPatchError:
        raise
    if not refs:
        notes.append("找到警告字符串，但未找到 adrp+add 引用它们的代码（可能已清除引用/厂商定制）")
        return [], [], []

    max_backtrack = int(aarch64_rules["max_backtrack"])
    groups, patched_starts, unmatched = _group_refs_by_function(
        payload, refs, max_backtrack=max_backtrack
    )
    if unmatched:
        notes.append(
            f"有 {len(unmatched)} 处警告字符串引用未能定位函数入口"
            "（可能不是函数头格式，或入口已被补丁改动）"
        )
    if not groups:
        notes.append("未能根据字符串引用定位到任何警告函数（fail-safe：不提供自动补丁）")
        return [], refs, []

    confidence_map = aarch64_rules["category_confidence"]
    min_confidence = float(aarch64_rules["min_confidence"])
    min_selected = float(aarch64_rules["min_selected_confidence"])
    delay_constants = set(aarch64_rules["delay_constants"])
    ordered_starts = sorted(groups)
    findings: list[LkFinding] = []
    patched_candidates: list[LkFinding] = []
    patched_start_set = set(patched_starts)

    for position, func_start in enumerate(ordered_starts):
        group_refs = groups[func_start]
        texts = []
        for ref in group_refs:
            cleaned = ref.text.replace("\n", " ")
            if cleaned not in texts:
                texts.append(cleaned)
        next_start = ordered_starts[position + 1] if position + 1 < len(ordered_starts) else None
        window_end = len(payload)
        if next_start is not None:
            window_end = min(window_end, next_start)
        window_end = min(window_end, func_start + 0x2000)
        features = _function_features(payload, func_start, window_end)
        delays = [value for value, _addr, _line in features.delay_values]
        primary_delay = None
        for candidate in (5_000_000, 5_000, 100_000, 200_000, 3_200_000):
            if candidate in delays:
                primary_delay = candidate
                break
        delay_count = delays.count(primary_delay) if primary_delay is not None else 0

        has_orange = _contains_any(texts, aarch64_rules["orange_tokens"])
        has_red = _contains_any(texts, aarch64_rules["red_tokens"])
        dm_text_count = sum(
            1 for text in texts if _contains_any([text], aarch64_rules["dm_tokens"])
        )
        category: str | None = None
        confidence = 0.0
        if has_orange:
            strong = (
                has_red
                or (primary_delay is not None and primary_delay >= 5000)
                or len(features.cmp_chain) >= 2
            )
            if strong and has_red and primary_delay in (5_000_000, 5_000) and len(features.cmp_chain) >= 2:
                category = "orange_state"
                confidence = confidence_map.get("orange_state_full", 0.98)
            elif strong:
                category = "orange_state"
                confidence = confidence_map.get("orange_state", 0.9)
            else:
                category = "orange_state"
                confidence = confidence_map.get("orange_state_weak", 0.65)
        elif has_red and dm_text_count < 2:
            category = "red_state"
            confidence = confidence_map.get("red_state", 0.85)
        elif dm_text_count >= 2:
            category = "dm_verity"
            strong_delay = primary_delay in (100_000, 200_000, 3_200_000)
            confidence = confidence_map.get(
                "dm_verity_full" if strong_delay else "dm_verity", 0.88
            )
        elif dm_text_count == 1 and primary_delay in (100_000, 200_000, 3_200_000):
            category = "dm_verity"
            confidence = confidence_map.get("dm_verity_weak", 0.65)
        else:
            notes.append(
                f"跳过 payload +0x{func_start:x} 的函数：引用警告字符串但未匹配警告分发器/Red/dm-verity 结构"
            )
            continue

        if confidence < min_confidence:
            notes.append(
                f"跳过 payload +0x{func_start:x} 的函数：置信度 {confidence:.2f} 低于阈值 {min_confidence:.2f}"
            )
            continue
        offset = base + func_start
        if offset < 0 or offset + len(PATCH_AARCH64) > len(image.data):
            notes.append(f"函数入口 0x{offset:x} 超出文件范围，已跳过")
            continue
        is_patched = func_start in patched_start_set
        evidence = _build_aarch64_evidence(
            image,
            func_start,
            group_refs,
            features,
            primary_delay,
            delay_count,
            callers.get(func_start, 0),
            next_start=next_start,
        )
        if is_patched:
            evidence.append(
                "候选入口已是补丁字节 e0 03 1f 2a c0 03 5f d6（mov w0,wzr; ret），"
                "该警告函数可能已经打过补丁"
            )
        finding = LkFinding(
            category=category,
            arch=AARCH64_ARCH,
            kind="heuristic",
            offset=offset,
            function_start=offset,
            before=image.data[offset : offset + len(PATCH_AARCH64)],
            after=PATCH_AARCH64,
            confidence=round(confidence, 2),
            evidence=evidence,
            selected=False if is_patched else confidence >= min_selected,
        )
        if is_patched:
            patched_candidates.append(finding)
        else:
            findings.append(finding)
    return findings, refs, patched_candidates


# ----------------------------------------------------------------------
# ARM32 扫描
# ----------------------------------------------------------------------
def _scan_arm32(
    image: LkImage,
    *,
    rules: Sequence[dict[str, Any]],
    notes: list[str],
) -> list[LkFinding]:
    payload = image.payload
    base = image.payload_offset
    findings: list[LkFinding] = []
    seen_offsets: dict[int, LkFinding] = {}
    for rule in rules:
        pattern = bytes.fromhex(rule["pattern"])
        if not pattern:
            continue
        positions: list[int] = []
        index = payload.find(pattern)
        while index >= 0:
            positions.append(index)
            index = payload.find(pattern, index + 1)
        description = rule.get("description") or ""
        label = f"{rule['category']}" + (f"（{description}）" if description else "")
        if not positions:
            notes.append(f"未命中：{label} 特征码 {pattern[:6].hex()}…")
            continue
        unique = len(positions) == 1
        if not unique:
            notes.append(
                f"{label} 特征码命中 {len(positions)} 处（不唯一，需人工确认具体补丁点）"
            )
        for position in positions:
            offset = base + position
            if offset + len(PATCH_ARM32) > len(image.data):
                continue
            existing = seen_offsets.get(offset)
            confidence = 0.98 if unique else 0.5
            if existing is not None:
                if confidence > existing.confidence:
                    seen_offsets[offset] = existing  # 保留更高置信度
                continue
            evidence = [
                f"精确特征码命中：{pattern.hex()}（payload +0x{position:x}）",
                f"类别：{rule['category']}；引擎：{rule.get('arch', ARM32_ARCH)} 字节匹配",
            ]
            if description:
                evidence.append(f"说明：{description}")
            if rule.get("source"):
                evidence.append(f"来源：{rule['source']}")
            if not unique:
                evidence.append(
                    f"注意：同一特征码在镜像中共命中 {len(positions)} 处，需人工确认后才可应用"
                )
            finding = LkFinding(
                category=rule["category"],
                arch=ARM32_ARCH,
                kind="signature",
                offset=offset,
                function_start=offset,
                before=image.data[offset : offset + len(PATCH_ARM32)],
                after=PATCH_ARM32,
                confidence=confidence,
                evidence=evidence,
                selected=unique,
            )
            seen_offsets[offset] = finding
            findings.append(finding)
    findings.sort(key=lambda item: item.offset)
    return findings


# ----------------------------------------------------------------------
# 已打补丁检测
# ----------------------------------------------------------------------
def _find_all(data: bytes, needle: bytes, *, align: int = 1, limit: int = 64) -> list[int]:
    results: list[int] = []
    index = data.find(needle)
    while index >= 0 and len(results) < limit:
        if align <= 1 or index % align == 0:
            results.append(index)
        index = data.find(needle, index + 1)
    return results


def _detect_already_patched_arm32(image: LkImage) -> bool:
    """ARM32：特征码剩余部分紧跟在补丁字节之后 -> 该函数已被重写为立即返回。"""
    payload = image.payload
    for rule in DEFAULT_RULES["arm32_rules"]:
        needle = bytes.fromhex(rule["pattern"])
        patch = bytes.fromhex(rule["patch"])
        if len(needle) <= 4:
            continue
        remainder = needle[4:]
        index = payload.find(remainder)
        while index >= 0:
            if index >= 4 and payload[index - 4 : index] == patch:
                return True
            index = payload.find(remainder, index + 1)
    return False


def _aarch64_patch_byte_hint(image: LkImage) -> str | None:
    """定位不到候选函数时的低置信提示：全 payload 里存在多少补丁字节。

    注意：该提示**不能**判定镜像已打补丁（AArch64 代码中 mov w0,wzr; ret 很常见），
    只用于向用户解释为何没有候选。
    """
    locations = _find_all(image.payload, PATCH_AARCH64, align=4, limit=32)
    if not locations:
        return None
    return (
        f"payload 中存在 {len(locations)} 处 mov w0,wzr; ret 字节（AArch64 常见自然函数结尾，"
        "不能据此判定已打补丁）"
    )


# ----------------------------------------------------------------------
# 扫描入口
# ----------------------------------------------------------------------
def scan(
    image: LkImage,
    *,
    rules: Iterable[dict[str, Any]] | None = None,
) -> LkScanReport:
    """扫描 LK 镜像，返回补丁候选与安全信息。"""
    if not isinstance(image, LkImage):
        raise TypeError(f"image 必须是 LkImage，收到 {type(image).__name__}")
    arch = detect_arch(image)
    ref_texts, display_texts = _rule_strings()
    occurrences = _warning_occurrences(image.payload, display_texts or ref_texts)
    warnings = _warning_strings_from_occurrences(image, occurrences)
    guard_params = _collect_guard_params(image)
    notes: list[str] = []
    findings: list[LkFinding] = []
    patched_candidates: list[LkFinding] = []
    refs: list[_StringRef] = []

    if arch == AARCH64_ARCH:
        findings, refs, patched_candidates = _scan_aarch64(image, notes=notes)
    elif arch == ARM32_ARCH:
        findings = _scan_arm32(image, rules=_rules_for_scan(rules), notes=notes)
    else:
        notes.append(
            "无法识别 LK 架构（既不像 AArch64，也不像 ARM32 Thumb）；"
            "本工具不会修改未知架构的镜像（fail-safe）"
        )

    findings.sort(key=lambda item: (item.offset, item.category))
    already_patched = False
    if arch == AARCH64_ARCH:
        if patched_candidates and not findings:
            # ① refs 推导出的候选入口全部已是补丁字节 -> 已打补丁（保留候选供诊断）
            already_patched = True
            findings = patched_candidates
            notes.append(
                "警告函数入口处已是补丁字节（mov w0,wzr; ret）：该镜像似乎已经打过 LK 警告补丁，"
                "无需重复处理（候选偏移与证据保留供诊断）"
            )
        elif patched_candidates and findings:
            # 部分候选已补丁，仅对剩余正常入口提供补丁
            notes.append(
                f"{len(patched_candidates)} 个候选入口已是补丁字节（已跳过），"
                f"其余 {len(findings)} 个候选正常"
            )
        elif not findings:
            hint = _aarch64_patch_byte_hint(image)
            if hint:
                notes.append(hint + "；fail-safe：不提供自动补丁")
    elif arch == ARM32_ARCH and not findings:
        if _detect_already_patched_arm32(image):
            already_patched = True
            notes.append(
                "特征码剩余字节紧跟补丁字节（movs r0,#0; bx lr）：该镜像似乎已经打过 LK 警告补丁，"
                "无需重复处理"
            )

    if arch in (AARCH64_ARCH, ARM32_ARCH) and not warnings:
        notes.append("未在镜像中找到已知警告文本（可能已被清除，或厂商使用了不同的提示文本）")
    if image.is_container and image.ext_magic is not None and image.ext_magic != LK_EXT_MAGIC:
        notes.append(
            f"头部扩展 magic=0x{image.ext_magic:08X} 不是常见的新版 LK 值 0x{LK_EXT_MAGIC:08X}，"
            "已按通用 MTK 分区镜像处理"
        )
    notes.extend(_ab_slot_notes(image))

    if already_patched:
        verdict = "already-patched"
    elif findings:
        verdict = f"supported-{arch}"
    else:
        verdict = "unsupported"
    return LkScanReport(
        image=image,
        arch=arch,
        findings=findings,
        warnings=warnings,
        guard_params=guard_params,
        already_patched=already_patched,
        verdict=verdict,
        notes=notes,
    )


def _ab_slot_notes(image: LkImage) -> list[str]:
    if image.path is None:
        return []
    siblings = find_sibling_slots(image.path)
    if not siblings:
        return []
    names = ", ".join(path.name for path in siblings)
    return [
        f"检测到同目录另有 A/B 槽位镜像（{names}）：若设备为 A/B 分区，请两个槽位都刷写，"
        "只刷一个可能仍从另一槽位启动并显示警告。"
    ]


# ----------------------------------------------------------------------
# 应用补丁
# ----------------------------------------------------------------------
def _expected_change_ranges(report: LkScanReport, *, categories: Sequence[str] | None = None) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for finding in report.findings:
        if not finding.selected:
            continue
        if categories is not None and finding.category not in categories:
            continue
        ranges.append((finding.offset, finding.offset + len(finding.after)))
    return ranges


def _warning_ranges(report: LkScanReport) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for warning in report.warnings:
        length = len(warning.text.encode("utf-8", errors="replace"))
        if length > 0:
            ranges.append((warning.offset, warning.offset + length))
    return ranges


def apply_patches(
    image: LkImage,
    report: LkScanReport,
    *,
    categories: Iterable[str] | None = None,
    clear_warning_text: bool = False,
    text_ranges: Sequence[tuple[int, int]] | None = None,
) -> tuple[bytes, list[str]]:
    """应用补丁，返回 ``(新文件字节, 变更日志)``。

    安全规则：只改写扫描命中的补丁区间（及可选的警告文本区间）；若会改变
    ``androidboot.*`` / verifiedbootstate / veritymode / atm 等内核参数的出现次数，
    或与扫描时的 before 字节不一致，则抛 :class:`LkPatchError` 拒绝写入。
    """
    if not isinstance(image, LkImage):
        raise TypeError(f"image 必须是 LkImage，收到 {type(image).__name__}")
    if not isinstance(report, LkScanReport):
        raise TypeError(f"report 必须是 LkScanReport，收到 {type(report).__name__}")
    allowed: tuple[str, ...] | None = None
    if categories is not None:
        allowed = tuple(str(item) for item in categories)
        unknown = [item for item in allowed if item not in CATEGORIES]
        if unknown:
            raise LkPatchError(
                f"未知补丁类别：{', '.join(unknown)}（可选 {', '.join(CATEGORIES)}）",
                hint="请使用 orange_state / red_state / dm_verity / warning_text。",
            )

    data = bytearray(image.data)
    changes: list[str] = []
    patch_ranges: list[tuple[int, int]] = []

    for finding in report.findings:
        if not finding.selected:
            continue
        if allowed is not None and finding.category not in allowed:
            continue
        start = finding.offset
        end = start + len(finding.after)
        if start < image.payload_offset or end > len(image.data):
            raise LkPatchError(
                f"补丁位置越界：0x{start:x}..0x{end:x}（文件长度 {len(image.data)}）",
                hint="扫描结果与文件不匹配，请重新扫描后再应用。",
            )
        current = bytes(data[start:end])
        if current == finding.after:
            changes.append(f"跳过 {finding.category} @0x{start:x}：已经是补丁状态")
            continue
        if current != finding.before:
            raise LkPatchError(
                f"补丁点 0x{start:x} 的当前字节 {current.hex()} 与扫描结果 {finding.before.hex()} 不一致，"
                "文件可能已被修改或已打过其它补丁",
                hint="请重新扫描文件，确认候选后再应用（fail-safe）。",
                context={"offset": start},
            )
        data[start:end] = finding.after
        patch_ranges.append((start, end))
        changes.append(
            f"{finding.category} @0x{start:x}（payload +0x{start - image.payload_offset:x}）："
            f"{finding.before.hex()} -> {finding.after.hex()}"
        )

    cleared_ranges: list[tuple[int, int]] = []
    if clear_warning_text:
        if text_ranges is not None:
            ranges = [(int(start), int(end)) for start, end in text_ranges]
        else:
            ranges = _warning_ranges(report)
        if not ranges:
            changes.append("未找到可清空的警告文本区间（已跳过）")
        for start, end in sorted(set(ranges)):
            if start < image.payload_offset or end > len(image.data) or end <= start:
                raise LkPatchError(
                    f"警告文本区间越界：0x{start:x}..0x{end:x}",
                    hint="请检查报告中的警告文本偏移。",
                )
            if any(not (end <= patch_start or start >= patch_end) for patch_start, patch_end in patch_ranges):
                raise LkPatchError(
                    f"警告文本区间 0x{start:x}..0x{end:x} 与补丁区间重叠，已拒绝",
                    hint="请分开执行代码补丁与文本清空。",
                )
            current = bytes(data[start:end])
            if not current.strip(b"\x00"):
                continue
            data[start:end] = b"\x00" * (end - start)
            cleared_ranges.append((start, end))
            changes.append(f"warning_text @0x{start:x}：清空 {end - start} 字节警告文本")

    new_bytes = bytes(data)

    if image.is_container and image.header is not None and new_bytes[:HEADER_SIZE] != image.header:
        raise LkPatchError(
            "补丁意外改动了前 0x200 字节 MTK 头，已拒绝",
            hint="这是内部安全检查；请报告该问题。",
        )
    before_guard = _guard_signature(image.payload)
    after_guard = _guard_signature(new_bytes[image.payload_offset : image.payload_offset + len(image.payload)])
    if before_guard != after_guard:
        detail = "、".join(
            f"{name}: {before_guard.get(name, 0)}->{after_guard.get(name, 0)}"
            for name in sorted(set(before_guard) | set(after_guard))
            if before_guard.get(name, 0) != after_guard.get(name, 0)
        )
        raise LkPatchError(
            f"补丁会改变内核启动参数（androidboot.* / verifiedbootstate / veritymode / atm）出现次数：{detail}，已拒绝",
            hint="这些参数被安全规则保护；请取消“清空文本”或换用其它补丁类别。",
            context={"guard_before": before_guard, "guard_after": after_guard},
        )
    if not patch_ranges and not cleared_ranges:
        return new_bytes, []
    return new_bytes, changes


# ----------------------------------------------------------------------
# 校验
# ----------------------------------------------------------------------
def _diff_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    common = min(len(before), len(after))
    start: int | None = None
    for index in range(common):
        if before[index] != after[index]:
            if start is None:
                start = index
        elif start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, common))
    if len(before) != len(after):
        ranges.append((common, max(len(before), len(after))))
    return ranges


def _range_inside_any(item: tuple[int, int], ranges: Sequence[tuple[int, int]]) -> bool:
    start, end = item
    return any(start >= range_start and end <= range_end for range_start, range_end in ranges)


def _mov_w0_zero(arch: str, mnemonic: str, op_str: str) -> bool:
    if arch == AARCH64_ARCH:
        return mnemonic == "mov" and "w0" in op_str and ("wzr" in op_str or "#0" in op_str)
    return mnemonic in ("movs", "mov") and "r0" in op_str and "#0" in op_str


def _ret_instruction(arch: str, mnemonic: str, op_str: str) -> bool:
    if arch == AARCH64_ARCH:
        return mnemonic == "ret"
    return mnemonic == "bx" and "lr" in op_str


def _patch_instruction_check(arch: str, lines: Sequence[str]) -> bool:
    """反汇编行里是否包含 ``mov w0,#0 / movs r0,#0`` 与 ``ret / bx lr``。"""
    mov_ok = False
    ret_ok = False
    for line in lines:
        if ":" not in line:
            continue
        body = line.split(":", 1)[1].strip()
        parts = body.split(None, 1)
        if not parts:
            continue
        mnemonic = parts[0]
        op_str = parts[1] if len(parts) > 1 else ""
        if _mov_w0_zero(arch, mnemonic, op_str):
            mov_ok = True
        if _ret_instruction(arch, mnemonic, op_str):
            ret_ok = True
    return mov_ok and ret_ok


def verify_patch(image: LkImage, new_data: bytes | bytearray | memoryview, report: LkScanReport) -> dict[str, Any]:
    """校验补丁结果，返回规格 §5 要求的字典。"""
    if not isinstance(image, LkImage):
        raise TypeError(f"image 必须是 LkImage，收到 {type(image).__name__}")
    if not isinstance(report, LkScanReport):
        raise TypeError(f"report 必须是 LkScanReport，收到 {type(report).__name__}")
    new_bytes = bytes(new_data)
    size_same = len(new_bytes) == len(image.data)
    header_same = True
    if image.is_container and image.header is not None:
        header_same = size_same and new_bytes[:HEADER_SIZE] == image.header
    new_payload = new_bytes[image.payload_offset : image.payload_offset + len(image.payload)] if size_same else b""
    guard_counts_same = size_same and _guard_signature(image.payload) == _guard_signature(new_payload)

    changed_ranges = _diff_ranges(image.data, new_bytes) if size_same else []
    expected_ranges = _expected_change_ranges(report) + _warning_ranges(report)
    unexpected_changes = [
        (start, end) for start, end in changed_ranges if not _range_inside_any((start, end), expected_ranges)
    ]

    capstone = _try_capstone()
    disasm_after: list[str] = []
    instruction_checks: list[bool] = []
    applied_count = 0
    for finding in report.findings:
        if not finding.selected:
            continue
        start = finding.offset
        end = start + len(finding.after)
        if end > len(new_bytes):  # pragma: no cover - 尺寸变化时已判 False
            instruction_checks.append(False)
            continue
        current = bytes(new_bytes[start:end])
        if current == finding.before:
            # 该候选未被应用（例如 CLI 用 --categories 只打了部分类别）：跳过校验
            continue
        applied_count += 1
        lines = _disasm_lines(new_bytes[start:end], report.arch, address=start, limit=4)
        if capstone is None or not lines:
            # 缺少 capstone 时退化为字节级校验
            instruction_checks.append(current == finding.after)
            continue
        disasm_after.extend(lines)
        instruction_checks.append(current == finding.after and _patch_instruction_check(report.arch, lines))

    warnings_remaining = len(_warning_occurrences(new_payload, _rule_strings()[1]))
    patch_ok = bool(
        size_same
        and header_same
        and guard_counts_same
        and not unexpected_changes
        and all(instruction_checks)
    )
    result: dict[str, Any] = {
        "size_same": size_same,
        "header_same": header_same,
        "guard_counts_same": guard_counts_same,
        "changed_ranges": changed_ranges,
        "disasm_after": disasm_after,
        "residual_warnings": warnings_remaining,
        "patch_ok": patch_ok,
        # 附加信息（GUI/CLI 展示用）
        "arch": report.arch,
        "patch_count": len(instruction_checks),
        "applied_count": applied_count,
        "instruction_checks": instruction_checks,
        "expected_ranges": expected_ranges,
        "unexpected_changes": unexpected_changes,
        "capstone_available": capstone is not None,
        "sha256_before": image.sha256,
        "sha256_after": _sha256(new_bytes),
    }
    return result


# ----------------------------------------------------------------------
# 预览辅助（CLI/GUI 展示补丁前后反汇编）
# ----------------------------------------------------------------------
def disassemble_patch_points(
    image: LkImage,
    report: LkScanReport,
    new_data: bytes | None = None,
) -> list[str]:
    """反汇编各候选补丁点（默认用原始数据，传入 new_data 可看补丁后结果）。"""
    data = image.data if new_data is None else bytes(new_data)
    lines: list[str] = []
    for finding in report.findings:
        if not finding.selected:
            continue
        start = finding.offset
        end = start + len(finding.after)
        if end > len(data):
            continue
        lines.extend(_disasm_lines(data[start:end], report.arch, address=start, limit=4))
    return lines


# ----------------------------------------------------------------------
# 备份 / 还原 / A/B 槽位
# ----------------------------------------------------------------------
def backup_path(path: str | Path) -> Path:
    """原始镜像备份路径：``<name>_original_backup<ext>``。"""
    source = Path(path)
    suffix = source.suffix or ".img"
    return source.with_name(f"{source.stem}_original_backup{suffix}")


def patched_path(path: str | Path) -> Path:
    """补丁输出路径：``<name>_patched<ext>``。"""
    source = Path(path)
    suffix = source.suffix or ".img"
    return source.with_name(f"{source.stem}_patched{suffix}")


def save_backup(image: LkImage, backup_dir: str | Path | None = None) -> Path:
    """保存原始镜像备份，返回备份路径。"""
    if not isinstance(image, LkImage):
        raise TypeError(f"image 必须是 LkImage，收到 {type(image).__name__}")
    directory = Path(backup_dir) if backup_dir is not None else None
    if image.path is not None:
        target = backup_path(image.path)
        if directory is not None:
            target = directory / target.name
    else:
        base_name = f"lk_{image.sha256[:8]}_original_backup.bin"
        target = (directory if directory is not None else Path.cwd()) / base_name
    if target.exists():
        # 已存在同名备份时追加时间戳，避免覆盖唯一的一份原始数据
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = target.with_name(f"{target.stem}_{stamp}{target.suffix}")
    _atomic_write(target, image.data)
    return target


def restore_from_backup(backup: str | Path, target_path: str | Path) -> Path:
    """把备份文件原子还原到目标路径，返回目标路径。"""
    source = Path(backup)
    if not source.is_file():
        raise LkPatchError(
            f"备份文件不存在：{source}",
            hint="请确认备份路径（默认 <name>_original_backup.img）。",
            context={"path": str(source)},
        )
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise LkPatchError(
            f"无法读取备份文件：{source}（{exc}）",
            context={"path": str(source)},
        ) from exc
    if not data:
        raise LkPatchError(f"备份文件为空：{source}", hint="该备份可能已损坏。")
    target = Path(target_path)
    _atomic_write(target, data)
    return target


def find_sibling_slots(path: str | Path) -> list[Path]:
    """查找同目录下的 A/B 槽位 LK 镜像（如 lk2.img / lk_b.bin）。"""
    source = Path(path)
    directory = source.parent
    suffixes = {source.suffix or ".img", ".img", ".bin"}
    candidates: list[Path] = []
    for suffix in sorted(suffixes):
        candidates.extend(
            [
                directory / f"lk2{suffix}",
                directory / f"lk_b{suffix}",
                directory / f"lk-b{suffix}",
                directory / f"lk_B{suffix}",
            ]
        )
    stem = source.stem
    counterpart: str | None = None
    if re.match(r"^lk[_-]?[aA]$", stem):
        counterpart = "lk_b"
    elif re.match(r"^lk[_-]?[bB]$", stem):
        counterpart = "lk_a"
    if counterpart is not None:
        for suffix in sorted(suffixes):
            candidates.append(directory / f"{counterpart}{suffix}")
    result: list[Path] = []
    for candidate in candidates:
        if candidate == source:
            continue
        if candidate.is_file() and candidate not in result:
            result.append(candidate)
    return result
