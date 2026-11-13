"""测试桩：模拟 ``logobuilder.core.lkpatch`` 的公共 API。

**仅在 `tests/` 内使用**：当真实核心库尚未提供 lkpatch 时（并行开发阶段），
UI 测试用它验证面板交互；真实核心就绪后测试会优先使用真实实现。
桩只实现 UI 需要的语义（ARM32 特征码 + 少量 AArch64 假标记），
不做真正的 AArch64 反汇编；真实样本相关测试一律通过 ``importorskip``。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

MTK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689
HEADER_SIZE = 0x200

#: 与 docs/spec_lkpatch.md §3.4 一致的 ARM32 特征码
DEFAULT_ARM32_RULES: list[dict[str, Any]] = [
    {"id": "orange-a10p-0", "category": "orange_state", "needle": "08b50a4b7b441b681b68022b"},
    {"id": "orange-a10p-1", "category": "orange_state", "needle": "08b50e4b7b441b681b68022b"},
    {"id": "orange-legacy-0", "category": "orange_state", "needle": "08b50a4b7b441b681b68012b"},
    {"id": "orange-legacy-1", "category": "orange_state", "needle": "08b50e4b7b441b681b68012b"},
    {"id": "red-state", "category": "red_state", "needle": "f0b5002489b0"},
    {"id": "dm-verity-0", "category": "dm_verity", "needle": "30b583b002ab0022"},
    {"id": "dm-verity-1", "category": "dm_verity", "needle": "022800d0704738b53224e0f7"},
]

PATCH_ARM32 = bytes.fromhex("00207047")
PATCH_AARCH64 = bytes.fromhex("e0031f2ac0035fd6")

_WARNING_KEYS = (
    b"Orange State",
    b"Red State",
    b"dm-verity",
    b"dm_verity",
    b"Your device has been unlocked",
    b"Your device has failed verification",
    b"5 seconds",
)
_GUARD_KEYS = (
    b"androidboot.verifiedbootstate",
    b"androidboot.veritymode",
    b"androidboot.atm",
)


@dataclass
class LkImage:
    path: Path | None
    data: bytes
    header: bytes | None
    payload: bytes
    payload_offset: int
    is_container: bool
    sha256: str
    name: str = ""


@dataclass
class LkFinding:
    category: str
    arch: str
    kind: str
    offset: int
    function_start: int | None
    before: bytes
    after: bytes
    confidence: float
    evidence: list[str] = field(default_factory=list)
    selected: bool = True


@dataclass
class LkWarningString:
    offset: int
    text: str


@dataclass
class LkScanReport:
    image: LkImage
    arch: str
    findings: list[LkFinding]
    warnings: list[LkWarningString]
    guard_params: dict[str, list[str]]
    already_patched: bool
    verdict: str
    notes: list[str] = field(default_factory=list)


def parse_lk_image(data: bytes, path: Path | None = None) -> LkImage:
    blob = bytes(data)
    sha = hashlib.sha256(blob).hexdigest()
    if len(blob) >= 4 and int.from_bytes(blob[0:4], "little") == MTK_MAGIC:
        if len(blob) < HEADER_SIZE:
            raise ValueError(f"文件被截断：只有 {len(blob)} 字节，MTK LK 头需要 512 字节")
        header = blob[:HEADER_SIZE]
        size = int.from_bytes(header[4:8], "little")
        if size <= 0 or HEADER_SIZE + size > len(blob):
            size = max(0, len(blob) - HEADER_SIZE)
        payload = blob[HEADER_SIZE : HEADER_SIZE + size]
        name = header[8:20].split(b"\x00", 1)[0].decode("latin-1", errors="replace")
        return LkImage(path, blob, header, payload, HEADER_SIZE, True, sha, name)
    return LkImage(path, blob, None, blob, 0, False, sha, "")


def detect_arch(image: LkImage) -> str:
    payload = image.payload
    for rule in DEFAULT_ARM32_RULES:
        if bytes.fromhex(rule["needle"]) in payload:
            return "arm32-thumb"
    if payload[:2] in (b"\x08\xb5", b"\xf0\xb5"):
        return "arm32-thumb"
    if b"FAKEOA" in payload or b"FAKEDM" in payload:
        return "aarch64"
    return "unknown"


def _guard_params(image: LkImage) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    payload = image.payload
    for key in _GUARD_KEYS:
        name = key.decode("ascii")
        offsets: list[str] = []
        start = 0
        while True:
            index = payload.find(key, start)
            if index < 0:
                break
            offsets.append(f"0x{image.payload_offset + index:08X}")
            start = index + 1
        if offsets:
            result[name] = offsets
    return result


def _warnings(image: LkImage) -> list[LkWarningString]:
    result: list[LkWarningString] = []
    payload = image.payload
    for key in _WARNING_KEYS:
        start = 0
        while True:
            index = payload.find(key, start)
            if index < 0:
                break
            result.append(
                LkWarningString(image.payload_offset + index, key.decode("ascii", errors="replace"))
            )
            start = index + 1
    result.sort(key=lambda item: item.offset)
    return result


def scan(image: LkImage) -> LkScanReport:
    arch = detect_arch(image)
    findings: list[LkFinding] = []
    payload = image.payload
    for rule in DEFAULT_ARM32_RULES:
        needle = bytes.fromhex(rule["needle"])
        start = 0
        matches: list[int] = []
        while True:
            index = payload.find(needle, start)
            if index < 0:
                break
            matches.append(index)
            start = index + 1
        for index in matches:
            offset = image.payload_offset + index
            findings.append(
                LkFinding(
                    category=rule["category"],
                    arch="arm32-thumb",
                    kind="signature",
                    offset=offset,
                    function_start=offset,
                    before=needle[:4],
                    after=PATCH_ARM32,
                    confidence=1.0,
                    evidence=[
                        f"ARM32 特征码命中（{rule['id']}）",
                        f"{needle[:8].hex()} -> 00207047",
                    ],
                )
            )
    # 假 AArch64 标记（仅测试桩支持）
    for marker, category in ((b"FAKEOA", "orange_state"), (b"FAKEDM", "dm_verity")):
        index = payload.find(marker)
        if index >= 0:
            offset = image.payload_offset + index
            findings.append(
                LkFinding(
                    category=category,
                    arch="aarch64",
                    kind="heuristic",
                    offset=offset,
                    function_start=offset,
                    before=payload[index : index + 8],
                    after=PATCH_AARCH64,
                    confidence=0.8,
                    evidence=["AArch64 假标记（测试桩）", "mov w0, wzr; ret"],
                )
            )
    patched = not findings and PATCH_ARM32 in payload
    if findings and arch == "arm32-thumb":
        verdict = "supported-arm32"
    elif findings and arch == "aarch64":
        verdict = "supported-aarch64"
    elif patched:
        verdict = "already-patched"
    else:
        verdict = "unsupported"
    notes = []
    if not findings and not patched:
        notes.append("未找到可自动修补的特征（fail-safe）")
    guard = _guard_params(image)
    return LkScanReport(
        image=image,
        arch=arch,
        findings=findings,
        warnings=_warnings(image),
        guard_params=guard,
        already_patched=patched,
        verdict=verdict,
        notes=notes,
    )


def apply_patches(
    image: LkImage,
    report: LkScanReport,
    *,
    categories: Iterable[str] | None = None,
    clear_warning_text: bool = False,
    text_ranges: list[tuple[int, int]] | None = None,
) -> tuple[bytes, list[str]]:
    selected = set(categories) if categories is not None else None
    data = bytearray(image.data)
    changes: list[str] = []
    for finding in report.findings:
        if selected is not None and finding.category not in selected:
            continue
        start = finding.offset
        data[start : start + len(finding.after)] = finding.after
        changes.append(
            f"{finding.category} @ 0x{finding.offset:08X}: {finding.before.hex()} -> {finding.after.hex()}"
        )
    if clear_warning_text:
        for warning in report.warnings:
            start = warning.offset
            raw = warning.text.encode("latin-1", errors="ignore")
            for index in range(len(raw)):
                if start + index < len(data):
                    data[start + index] = 0
            changes.append(f"warning_text @ 0x{warning.offset:08X}: cleared")
    return bytes(data), changes


def _changed_ranges(old: bytes, new: bytes) -> list[str]:
    ranges: list[str] = []
    start: int | None = None
    for index in range(min(len(old), len(new))):
        if old[index] != new[index]:
            if start is None:
                start = index
        else:
            if start is not None:
                ranges.append(f"0x{start:08X}-0x{index:08X}")
                start = None
    if start is not None:
        ranges.append(f"0x{start:08X}-0x{min(len(old), len(new)):08X}")
    return ranges


def verify_patch(image: LkImage, new_data: bytes, report: LkScanReport) -> dict[str, Any]:
    blob = bytes(new_data)
    header_same = (
        image.header is None
        or blob[:HEADER_SIZE] == image.header[:HEADER_SIZE]
    )
    old_guard = _guard_params(image)
    new_image = parse_lk_image(blob, image.path)
    new_guard = _guard_params(new_image)
    guard_same = {key: len(values) for key, values in old_guard.items()} == {
        key: len(values) for key, values in new_guard.items()
    }
    warnings_after = _warnings(new_image)
    residual = [warning.text for warning in warnings_after]
    arch = report.arch
    disasm = "mov w0, #0 ; ret" if arch == "aarch64" else "movs r0, #0 ; bx lr"
    changed = _changed_ranges(image.data, blob)
    patch_ok = (
        len(blob) == len(image.data)
        and header_same
        and guard_same
        and bool(changed)
        and all(len(finding.after) > 0 for finding in report.findings)
    )
    return {
        "size_same": len(blob) == len(image.data),
        "header_same": header_same,
        "guard_counts_same": guard_same,
        "changed_ranges": changed,
        "disasm_after": disasm,
        "residual_warnings": residual,
        "patch_ok": patch_ok,
    }


def backup_path(path: str | Path) -> Path:
    p = Path(path)
    suffix = p.suffix or ".img"
    return p.with_name(p.stem + "_original_backup" + suffix)


def patched_path(path: str | Path) -> Path:
    p = Path(path)
    suffix = p.suffix or ".img"
    return p.with_name(p.stem + "_patched" + suffix)


def save_backup(image: LkImage, backup_dir: str | Path | None = None) -> Path:
    source = image.path
    name = backup_path(source).name if source is not None else "lk_original_backup.img"
    directory = Path(backup_dir) if backup_dir is not None else (Path(source).parent if source else Path.cwd())
    target = directory / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(image.data)
    return target


def restore_from_backup(backup_path_value: str | Path, target_path: str | Path) -> Path:
    data = Path(backup_path_value).read_bytes()
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def load_custom_rules(path: str | Path) -> list[dict[str, Any]]:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(payload.get("rules", payload)) if isinstance(payload, dict) else list(payload)
