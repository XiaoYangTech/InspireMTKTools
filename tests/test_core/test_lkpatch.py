"""LK 引导程序修补（lkpatch）单元 / 集成测试。

覆盖 docs/spec_lkpatch.md §4/§5/§7：

* 合成 ARM32 LK 分区镜像：7 条内置特征码全部命中、补丁字节精确、头不变、备份/还原；
* 合成 AArch64 LK（真实指令编码）：警告分发器 + dm-verity 函数识别与补丁；
* 真实样本 ``analysis/lk/samples/lk_a.bin`` / ``lk_b.bin``（存在则测，否则 skip）：
  exactly 2 findings @0x4D928/0x4CC58，补丁后反汇编为 ``mov w0,wzr; ret``，原文件不被修改；
* QA 合成 fixture（``tests/fixtures/generated_lk``）：按 manifest 精确断言；
* 负样本 fail-safe、内核参数保护、自定义规则、CLI 四个子命令与退出码。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import struct
from pathlib import Path

import pytest
from PIL import Image

from logobuilder.core import lkpatch
from logobuilder.core.cli import main

ROOT = Path(__file__).resolve().parents[2]
REAL_LK_DIR = ROOT / "analysis" / "lk" / "samples"
REAL_LK_A = REAL_LK_DIR / "lk_a.bin"
REAL_LK_B = REAL_LK_DIR / "lk_b.bin"
REAL_LK_A_SHA = "bb2f2e6a6f7b7603dee3de08dc09e29dd74da49bd4cb98f20e8be648ba66f873"
REAL_LK_B_SHA = "f4ecd3eed32f6f8ce112115af11b054de14bf01a8d6cab7d12b31bd9051aa6e4"
QA_FIXTURES = ROOT / "tests" / "fixtures" / "generated_lk"
ARM32_PATCH = bytes.fromhex("00207047")
AARCH64_PATCH = bytes.fromhex("e0031f2ac0035fd6")
HEADER_SIZE = 0x200
MTK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689

requires_real_lk = pytest.mark.skipif(
    not (REAL_LK_A.is_file() and REAL_LK_B.is_file()),
    reason="缺少真实 LK 样本 analysis/lk/samples/lk_a.bin / lk_b.bin",
)
requires_qa_fixtures = pytest.mark.skipif(
    not (QA_FIXTURES / "manifest.json").is_file(),
    reason="缺少 QA LK fixture 语料 tests/fixtures/generated_lk/manifest.json",
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------
# 合成镜像构造工具（独立于被测实现）
# ----------------------------------------------------------------------
def build_lk_header(
    payload_len: int,
    *,
    name: str = "lk",
    magic: int = MTK_MAGIC,
    ext_magic: int = LK_EXT_MAGIC,
) -> bytes:
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<II", header, 0, magic, payload_len)
    encoded = name.encode("ascii")[:12]
    header[8 : 8 + len(encoded)] = encoded
    struct.pack_into("<I", header, 0x30, ext_magic)
    header[0x28:] = b"\xff" * (HEADER_SIZE - 0x28)
    return bytes(header)


def build_lk_image(payload: bytes, *, partition_size: int | None = None, **header_kwargs) -> bytes:
    header = build_lk_header(len(payload), **header_kwargs)
    data = header + payload
    if partition_size is not None and partition_size > len(data):
        data += b"\x00" * (partition_size - len(data))
    return data


# --- ARM64 指令编码 ---------------------------------------------------
def _u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def stp_x29_x30_pre(imm: int) -> bytes:
    imm7 = (-imm // 8) & 0x7F
    return _u32(0xA9800000 | (imm7 << 15) | (30 << 10) | (31 << 5) | 29)


def ldp_x29_x30_post(imm: int = 0x20) -> bytes:
    imm7 = (imm // 8) & 0x7F
    return _u32(0xA8C00000 | (imm7 << 15) | (30 << 10) | (31 << 5) | 29)


def mov_x_sp(rd: int = 29) -> bytes:
    return _u32(0x910003E0 | rd)


def adrp(rd: int, pc: int, target: int) -> bytes:
    delta = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    delta &= 0x1FFFFF
    return _u32(0x90000000 | ((delta & 0x3) << 29) | (((delta >> 2) & 0x7FFFF) << 5) | rd)


def add_imm_x(rd: int, rn: int, imm: int) -> bytes:
    return _u32(0x91000000 | ((imm & 0xFFF) << 10) | (rn << 5) | rd)


def cmp_imm_w(rn: int, imm: int) -> bytes:
    return _u32(0x7100001F | ((imm & 0xFFF) << 10) | (rn << 5))


def b_cond(cond: int, pc: int, target: int) -> bytes:
    imm = ((target - pc) // 4) & 0x7FFFF
    return _u32(0x54000000 | (imm << 5) | cond)


def bl(pc: int, target: int) -> bytes:
    imm = ((target - pc) // 4) & 0x3FFFFFF
    return _u32(0x94000000 | imm)


def nop() -> bytes:
    return _u32(0xD503201F)


def ret() -> bytes:
    return _u32(0xD65F03C0)


def mov_w0_wzr() -> bytes:
    return _u32(0x2A1F03E0)


def movz_w(rd: int, imm16: int, shift: int = 0) -> bytes:
    return _u32(0x52800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd)


def movk_w(rd: int, imm16: int, shift: int = 0) -> bytes:
    return _u32(0x72800000 | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd)


def ldr_w_reg(rt: int, rn: int, off: int = 0) -> bytes:
    return _u32(0xB9400000 | ((off // 4) << 10) | (rn << 5) | rt)


def str_wzr(rt: int, rn: int, off: int = 0) -> bytes:
    return _u32(0xB9000000 | ((off // 4) << 10) | (rn << 5) | rt)


def build_aarch64_payload(
    *,
    include_dispatcher: bool = True,
    include_dmverity: bool = True,
    include_warning_strings: bool = True,
    size: int = 0x4000,
) -> tuple[bytes, dict]:
    """构造与真实样本同构的合成 AArch64 LK payload。

    布局（payload 相对偏移）：
      * 0x1000 警告分发器（Orange/Red 引用、cmp #1/#3/#2、5,000,000µs）
      * 0x1800 dm-verity 警告函数（2 个 dm-verity 引用、100,000µs × 2）
      * 0x2000 警告字符串
      * 0x2400 内核启动参数（受保护）
      * 0x2800 boot state 全局变量
      * 0x3000 / 0x3010 打印/延时 dummy 辅助函数
    """
    buf = bytearray(b"\x00" * size)
    layout: dict[str, dict[str, int]] = {"functions": {}, "strings": {}}
    string_offsets = {
        b"Orange State": 0x2000,
        b"Red State": 0x2020,
        b"Your device has been unlocked": 0x2040,
        b"Your device has failed verification": 0x2080,
        b"dm-verity": 0x20C0,
        b"dm_verity": 0x20E0,
    }
    if include_warning_strings:
        for text, offset in string_offsets.items():
            buf[offset : offset + len(text) + 1] = text + b"\x00"
            layout["strings"][text.decode()] = offset
    guard_blobs = [
        b"androidboot.verifiedbootstate=orange",
        b"androidboot.veritymode=enforcing",
        b"androidboot.atm=disabled",
    ]
    guard_cursor = 0x2400
    for blob in guard_blobs:
        buf[guard_cursor : guard_cursor + len(blob) + 1] = blob + b"\x00"
        guard_cursor += len(blob) + 1
    struct.pack_into("<I", buf, 0x2800, 2)  # state = 2 (Orange 路径)

    def emit(start: int, chunks: list[bytes]) -> None:
        offset = start
        for chunk in chunks:
            buf[offset : offset + 4] = chunk
            offset += 4

    if include_dispatcher:
        start = 0x1000
        ins: list[bytes] = []
        labels: dict[str, int] = {}
        pc = start

        def put(chunk: bytes) -> None:
            nonlocal pc
            ins.append(chunk)
            pc += 4

        def mark(name: str) -> None:
            nonlocal pc
            labels[name] = pc

        put(stp_x29_x30_pre(0x20))
        put(str_wzr(31, 31, 0))  # str wzr,[sp]
        put(mov_x_sp())
        put(adrp(10, start + 12, string_offsets[b"Orange State"]))
        put(add_imm_x(10, 10, string_offsets[b"Orange State"] & 0xFFF))
        put(adrp(10, start + 20, 0x2800))
        put(add_imm_x(10, 10, 0x2800 & 0xFFF))
        put(ldr_w_reg(8, 10, 0))
        put(cmp_imm_w(8, 1))
        put(b_cond(0, start + 36, 0))
        put(cmp_imm_w(8, 3))
        put(b_cond(0, start + 44, 0))
        put(cmp_imm_w(8, 2))
        put(b_cond(1, start + 52, 0))
        mark("warn")
        for text in (b"Red State", b"Orange State"):
            ins_offset = start + 4 * len(ins)
            put(adrp(0, ins_offset, string_offsets[text]))
            put(add_imm_x(0, 0, string_offsets[text] & 0xFFF))
            put(bl(start + 4 * len(ins), 0x3000))
        put(movz_w(0, 0x4B40))
        put(movk_w(0, 0x4C, 16))
        put(bl(start + 4 * len(ins), 0x3010))
        mark("skip")
        put(mov_w0_wzr())
        put(ldp_x29_x30_post(0x20))
        put(ret())
        warn_pc = start + 9 * 4
        ins[9] = b_cond(0, warn_pc, labels["warn"])
        ins[11] = b_cond(0, warn_pc + 8, labels["warn"])
        ins[13] = b_cond(1, warn_pc + 16, labels["skip"])
        emit(start, ins)
        layout["functions"]["orange_state"] = start

    if include_dmverity:
        start = 0x1800
        ins = []
        labels = {}
        pc = start

        def put2(chunk: bytes) -> None:
            nonlocal pc
            ins.append(chunk)
            pc += 4

        def mark2(name: str) -> None:
            nonlocal pc
            labels[name] = pc

        put2(stp_x29_x30_pre(0x20))
        put2(ldr_w_reg(8, 31, 0))
        put2(cmp_imm_w(8, 1))
        put2(b_cond(0, start + 16, 0))
        put2(nop())
        mark2("warn")
        for text in (b"dm-verity", b"dm_verity"):
            ins_offset = start + 4 * len(ins)
            put2(adrp(0, ins_offset, string_offsets[text]))
            put2(add_imm_x(0, 0, string_offsets[text] & 0xFFF))
            put2(bl(start + 4 * len(ins), 0x3000))
        for _ in range(2):
            put2(movz_w(0, 0x86A0))
            put2(movk_w(0, 1, 16))
            put2(bl(start + 4 * len(ins), 0x3010))
        put2(nop())
        mark2("skip")
        put2(ldp_x29_x30_post(0x20))
        put2(ret())
        ins[3] = b_cond(0, start + 12, labels["warn"])
        emit(start, ins)
        layout["functions"]["dm_verity"] = start

    for helper in (0x3000, 0x3010):
        emit(helper, [stp_x29_x30_pre(0x10), nop(), ldp_x29_x30_post(0x10), ret()])
    return bytes(buf), layout


def build_arm32_payload() -> tuple[bytes, dict]:
    """构造含 7 条内置 Thumb-2 特征码 + 警告字符串 + 内核参数的合成 payload。"""
    needles = list(lkpatch.DEFAULT_RULES["arm32_rules"])
    size = 0x8000
    buf = bytearray(b"\x00\xbf" * (size // 2))
    buf[0:6] = bytes.fromhex("2de9f04f0fb5")  # 真实 Thumb-2 push 入口
    offsets: list[int] = []
    cursor = 0x1000
    for rule in needles:
        needle = bytes.fromhex(rule["pattern"])
        buf[cursor : cursor + len(needle)] = needle
        offsets.append(cursor)
        cursor += 0x100
    strings = {
        b"Orange State": 0x3000,
        b"Red State": 0x3020,
        b"Your device has been unlocked": 0x3040,
        b"Your device has failed verification": 0x3080,
        b"dm-verity": 0x30C0,
        b"dm_verity": 0x30E0,
    }
    for text, offset in strings.items():
        buf[offset : offset + len(text) + 1] = text + b"\x00"
    for index, blob in enumerate(
        [
            b"androidboot.verifiedbootstate=orange",
            b"androidboot.veritymode=enforcing",
            b"androidboot.atm=disabled",
        ]
    ):
        offset = 0x3200 + index * 0x40
        buf[offset : offset + len(blob) + 1] = blob + b"\x00"
    return bytes(buf), {"needle_offsets": offsets, "strings": strings}


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def synthetic_arm32_image_bytes() -> bytes:
    payload, layout = build_arm32_payload()
    image = build_lk_image(payload, partition_size=len(payload) + HEADER_SIZE)
    # 附带 layout 供断言（通过 dict 属性不方便，直接用模块级缓存）
    return image


@pytest.fixture()
def synthetic_arm32_image(synthetic_arm32_image_bytes: bytes) -> lkpatch.LkImage:
    return lkpatch.parse_lk_image(synthetic_arm32_image_bytes)


@pytest.fixture(scope="module")
def synthetic_aarch64_image_bytes() -> bytes:
    payload, _layout = build_aarch64_payload()
    return build_lk_image(payload, partition_size=len(payload) + HEADER_SIZE)


@pytest.fixture()
def synthetic_aarch64_image(synthetic_aarch64_image_bytes: bytes) -> lkpatch.LkImage:
    return lkpatch.parse_lk_image(synthetic_aarch64_image_bytes)


@pytest.fixture(scope="module")
def real_lk_a_bytes() -> bytes:
    return REAL_LK_A.read_bytes()


@pytest.fixture(scope="module")
def real_lk_a_scan(real_lk_a_bytes):
    image = lkpatch.parse_lk_image(real_lk_a_bytes, REAL_LK_A)
    return image, lkpatch.scan(image)


# ----------------------------------------------------------------------
# 合成 ARM32
# ----------------------------------------------------------------------
def test_arm32_all_rules_found(synthetic_arm32_image, synthetic_arm32_image_bytes):
    report = lkpatch.scan(synthetic_arm32_image)
    assert report.arch == "arm32-thumb"
    assert report.verdict == "supported-arm32-thumb"
    assert report.already_patched is False
    expected = [(rule["category"], 0x200 + offset) for rule, offset in zip(
        lkpatch.DEFAULT_RULES["arm32_rules"], build_arm32_payload()[1]["needle_offsets"]
    )]
    assert [(finding.category, finding.offset) for finding in report.findings] == expected
    assert all(finding.after == ARM32_PATCH for finding in report.findings)
    assert all(finding.selected for finding in report.findings)
    assert all(finding.confidence >= 0.9 for finding in report.findings)


def test_arm32_apply_and_verify(synthetic_arm32_image):
    report = lkpatch.scan(synthetic_arm32_image)
    new_bytes, changes = lkpatch.apply_patches(synthetic_arm32_image, report)
    assert len(new_bytes) == len(synthetic_arm32_image.data)
    assert new_bytes[:HEADER_SIZE] == synthetic_arm32_image.header
    assert len(changes) == 7
    for finding in report.findings:
        assert new_bytes[finding.offset : finding.offset + 4] == ARM32_PATCH
    verification = lkpatch.verify_patch(synthetic_arm32_image, new_bytes, report)
    assert verification["patch_ok"] is True
    assert verification["size_same"] and verification["header_same"] and verification["guard_counts_same"]
    assert len(verification["changed_ranges"]) == 7
    disasm = " ".join(verification["disasm_after"])
    assert "movs r0, #0" in disasm and "bx lr" in disasm


def test_arm32_dry_run_does_not_write(synthetic_arm32_image, tmp_path, capsys):
    source = tmp_path / "lk_arm32.img"
    source.write_bytes(synthetic_arm32_image.data)
    backup = lkpatch.backup_path(source)
    output = lkpatch.patched_path(source)
    code = main(["lk-patch", str(source), "--dry-run"])
    captured = capsys.readouterr()
    assert code == 0
    assert "dry-run" in captured.out
    assert "补丁后反汇编" in captured.out
    assert not output.exists()
    assert not backup.exists()
    assert source.read_bytes() == synthetic_arm32_image.data


def test_arm32_backup_and_restore(synthetic_arm32_image, tmp_path, capsys):
    source = tmp_path / "lk_arm32.img"
    source.write_bytes(synthetic_arm32_image.data)
    code = main(["lk-patch", str(source)])
    capsys.readouterr()
    assert code == 0
    backup = lkpatch.backup_path(source)
    patched = lkpatch.patched_path(source)
    assert backup.is_file() and patched.is_file()
    assert sha256(backup.read_bytes()) == sha256(synthetic_arm32_image.data)
    assert patched.read_bytes() != synthetic_arm32_image.data
    # 校验 CLI
    assert main(["lk-verify", str(source), str(patched)]) == 0
    # 还原
    assert main(["lk-restore", str(backup), "-o", str(source)]) == 0
    assert sha256(source.read_bytes()) == sha256(synthetic_arm32_image.data)


# ----------------------------------------------------------------------
# 合成 AArch64
# ----------------------------------------------------------------------
def test_aarch64_detector_and_patch(synthetic_aarch64_image):
    report = lkpatch.scan(synthetic_aarch64_image)
    assert report.arch == "aarch64"
    assert report.verdict == "supported-aarch64"
    assert report.already_patched is False
    by_category = {finding.category: finding for finding in report.findings}
    assert set(by_category) == {"orange_state", "dm_verity"}
    assert by_category["orange_state"].offset == 0x200 + 0x1000
    assert by_category["dm_verity"].offset == 0x200 + 0x1800
    assert by_category["orange_state"].confidence >= 0.9
    assert by_category["dm_verity"].confidence >= 0.9
    evidence = "\n".join(by_category["orange_state"].evidence)
    assert "延时常量" in evidence and "5,000,000" in evidence
    assert "状态映射" in evidence
    dm_evidence = "\n".join(by_category["dm_verity"].evidence)
    assert "100,000" in dm_evidence

    new_bytes, changes = lkpatch.apply_patches(synthetic_aarch64_image, report)
    assert len(changes) == 2
    for finding in report.findings:
        assert new_bytes[finding.offset : finding.offset + 8] == AARCH64_PATCH
    verification = lkpatch.verify_patch(synthetic_aarch64_image, new_bytes, report)
    assert verification["patch_ok"] is True
    disasm = " ".join(verification["disasm_after"])
    assert "mov w0, wzr" in disasm and "ret" in disasm
    assert new_bytes[:HEADER_SIZE] == synthetic_aarch64_image.header


def test_aarch64_clear_text(synthetic_aarch64_image):
    report = lkpatch.scan(synthetic_aarch64_image)
    assert len(report.warnings) >= 4
    new_bytes, changes = lkpatch.apply_patches(
        synthetic_aarch64_image, report, clear_warning_text=True
    )
    verification = lkpatch.verify_patch(synthetic_aarch64_image, new_bytes, report)
    assert verification["patch_ok"] is True
    assert verification["residual_warnings"] == 0
    assert any(change.startswith("warning_text") for change in changes)
    # 内核启动参数一字未动
    for guard in (b"androidboot.verifiedbootstate=orange", b"androidboot.veritymode=enforcing", b"androidboot.atm=disabled"):
        assert new_bytes.count(guard) == synthetic_aarch64_image.data.count(guard)


def test_aarch64_already_patched(synthetic_aarch64_image, synthetic_aarch64_image_bytes):
    report = lkpatch.scan(synthetic_aarch64_image)
    patched_bytes, _changes = lkpatch.apply_patches(synthetic_aarch64_image, report)
    patched_image = lkpatch.parse_lk_image(patched_bytes)
    rescanned = lkpatch.scan(patched_image)
    assert rescanned.verdict == "already-patched"
    assert rescanned.already_patched is True
    assert len(rescanned.findings) == 2  # 保留候选偏移供诊断
    assert any("补丁字节" in note for note in rescanned.notes)
    # apply_patches 对已补丁候选不提供可写补丁
    assert all(not finding.selected for finding in rescanned.findings)
    new_bytes, changes = lkpatch.apply_patches(patched_image, rescanned)
    assert new_bytes == patched_bytes
    assert changes == []


def test_aarch64_raw_payload(synthetic_aarch64_image):
    payload = synthetic_aarch64_image.payload
    raw_image = lkpatch.parse_lk_image(payload)
    assert raw_image.is_container is False
    assert raw_image.payload_offset == 0
    assert lkpatch.detect_arch(raw_image) == "aarch64"
    report = lkpatch.scan(raw_image)
    assert report.verdict == "supported-aarch64"
    assert [finding.offset for finding in report.findings] == [0x1000, 0x1800]


# ----------------------------------------------------------------------
# 负样本 / 安全规则
# ----------------------------------------------------------------------
def test_negative_all_zero_and_random():
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.parse_lk_image(b"\x00" * (4 * 1024 * 1024))
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.parse_lk_image(bytes(range(256)) * 64)
    # bad magic 但明显是 MTK 头结构（name/ext magic） -> 拒绝
    header = bytearray(build_lk_header(16, magic=0x12345678))
    with pytest.raises(lkpatch.LkPatchError) as excinfo:
        lkpatch.parse_lk_image(bytes(header) + b"\x00" * 16)
    assert "magic" in excinfo.value.message or "MTK" in excinfo.value.message


def test_negative_truncated_header():
    header = bytearray(build_lk_header(0x10000))
    with pytest.raises(lkpatch.LkPatchError) as excinfo:
        lkpatch.parse_lk_image(bytes(header) + b"\x00" * 64)
    assert "截断" in excinfo.value.message or "size" in excinfo.value.message


def test_negative_no_warning_strings():
    payload = bytearray(b"\x00" * 0x4000)
    payload[0x1000:0x1010] = stp_x29_x30_pre(0x20) + nop() + ldp_x29_x30_post(0x20) + ret()
    image = lkpatch.parse_lk_image(build_lk_image(bytes(payload)))
    report = lkpatch.scan(image)
    assert report.arch == "aarch64"
    assert report.verdict == "unsupported"
    assert report.findings == []
    new_bytes, changes = lkpatch.apply_patches(image, report)
    assert changes == []
    assert new_bytes == image.data


def test_guard_param_protection(synthetic_aarch64_image):
    report = lkpatch.scan(synthetic_aarch64_image)
    guard_offset = synthetic_aarch64_image.payload.find(b"androidboot.verifiedbootstate=orange")
    assert guard_offset >= 0
    payload_offset = synthetic_aarch64_image.payload_offset
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.apply_patches(
            synthetic_aarch64_image,
            report,
            clear_warning_text=True,
            text_ranges=[(payload_offset + guard_offset, payload_offset + guard_offset + 16)],
        )


def test_apply_refuses_tampered_before_bytes(synthetic_aarch64_image):
    report = lkpatch.scan(synthetic_aarch64_image)
    tampered = bytearray(synthetic_aarch64_image.data)
    first = report.findings[0]
    tampered[first.offset] ^= 0xFF
    tampered_image = lkpatch.parse_lk_image(tampered)
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.apply_patches(tampered_image, report)


def test_verify_detects_unexpected_change(synthetic_aarch64_image):
    report = lkpatch.scan(synthetic_aarch64_image)
    new_bytes, _ = lkpatch.apply_patches(synthetic_aarch64_image, report)
    tampered = bytearray(new_bytes)
    tampered[0x1000] ^= 0xFF  # 头部之外的随机位置
    verification = lkpatch.verify_patch(synthetic_aarch64_image, tampered, report)
    assert verification["patch_ok"] is False
    assert verification["unexpected_changes"]


# ----------------------------------------------------------------------
# 规则 / 备份 / A-B 提示
# ----------------------------------------------------------------------
def test_builtin_rules_shape():
    rules = lkpatch.DEFAULT_ARM32_RULES
    assert len(rules) == 7
    categories = [rule["category"] for rule in rules]
    assert categories.count("orange_state") == 4
    assert categories.count("red_state") == 1
    assert categories.count("dm_verity") == 2
    for rule in rules:
        assert len(bytes.fromhex(rule["pattern"])) >= 4
        assert bytes.fromhex(rule["patch"]) == ARM32_PATCH


def test_rules_asset_exists_and_loads():
    path = lkpatch.LK_PATCH_RULES_FILE
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == "logobuilder-lk-patch-rules"
    assert len(payload["arm32"]["rules"]) == 7
    assert payload["aarch64"]["patch"] == AARCH64_PATCH.hex()


def test_custom_rules_load_and_scan(tmp_path):
    pattern = "deadbeef0badf00d"
    custom = tmp_path / "rules.json"
    custom.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "category": "orange_state",
                        "pattern": pattern,
                        "patch": "00207047",
                        "description": "测试自定义特征码",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    rules = lkpatch.load_custom_rules(custom)
    assert rules[0]["pattern"] == pattern
    # 构造一个包含自定义特征码的 ARM32 镜像
    payload, _ = build_arm32_payload()
    buf = bytearray(payload)
    needle = bytes.fromhex(pattern)
    buf[0x5000 : 0x5000 + len(needle)] = needle
    image = lkpatch.parse_lk_image(build_lk_image(bytes(buf)))
    report = lkpatch.scan(image, rules=rules)
    assert any(finding.category == "orange_state" and finding.offset == 0x200 + 0x5000 for finding in report.findings)


def test_custom_rules_errors(tmp_path):
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.load_custom_rules(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{oops", encoding="utf-8")
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.load_custom_rules(broken)
    bad_hex = tmp_path / "bad.json"
    bad_hex.write_text(json.dumps({"rules": [{"category": "dm_verity", "pattern": "zz"}]}), encoding="utf-8")
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.load_custom_rules(bad_hex)
    bad_category = tmp_path / "badcat.json"
    bad_category.write_text(
        json.dumps({"rules": [{"category": "nope", "pattern": "08b5", "patch": "00207047"}]}),
        encoding="utf-8",
    )
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.load_custom_rules(bad_category)


def test_backup_paths_and_restore(tmp_path, synthetic_arm32_image):
    source = tmp_path / "lk.img"
    source.write_bytes(synthetic_arm32_image.data)
    assert lkpatch.backup_path(source).name == "lk_original_backup.img"
    assert lkpatch.patched_path(source).name == "lk_patched.img"
    backup = lkpatch.save_backup(synthetic_arm32_image)
    assert backup.is_file() and backup.read_bytes() == synthetic_arm32_image.data
    target = tmp_path / "restored.img"
    restored = lkpatch.restore_from_backup(backup, target)
    assert restored == target and target.read_bytes() == synthetic_arm32_image.data
    with pytest.raises(lkpatch.LkPatchError):
        lkpatch.restore_from_backup(tmp_path / "no_backup.img", target)


def test_ab_slot_note(tmp_path, synthetic_aarch64_image_bytes):
    source = tmp_path / "lk_a.bin"
    source.write_bytes(synthetic_aarch64_image_bytes)
    (tmp_path / "lk_b.bin").write_bytes(synthetic_aarch64_image_bytes)
    image = lkpatch.parse_lk_image(synthetic_aarch64_image_bytes, source)
    siblings = lkpatch.find_sibling_slots(source)
    assert [path.name for path in siblings] == ["lk_b.bin"]
    report = lkpatch.scan(image)
    assert any("A/B" in note for note in report.notes)


# ----------------------------------------------------------------------
# QA 合成 fixture 语料（逐项对照 manifest）
# ----------------------------------------------------------------------
@requires_qa_fixtures
def test_qa_fixtures_match_manifest():
    manifest = json.loads((QA_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for relative, info in manifest["fixtures"].items():
        path = QA_FIXTURES / relative
        expected = info.get("expected", {})
        try:
            image = lkpatch.parse_lk_image(path.read_bytes(), path)
            report = lkpatch.scan(image)
        except lkpatch.LkPatchError:
            if not expected.get("error"):
                failures.append(f"{relative}: 非预期解析错误")
            continue
        if "arch" in expected and report.arch != expected["arch"]:
            failures.append(f"{relative}: arch {report.arch} != {expected['arch']}")
        if "verdict_in" in expected and report.verdict not in expected["verdict_in"]:
            failures.append(f"{relative}: verdict {report.verdict} not in {expected['verdict_in']}")
        if "findings" in expected:
            got = [(finding.category, finding.offset) for finding in report.findings]
            want = [(item["category"], item["offset"]) for item in expected["findings"]]
            if got != want:
                failures.append(f"{relative}: findings {got} != {want}")
    assert not failures, "\n".join(failures)


# ----------------------------------------------------------------------
# 真实 AArch64 样本
# ----------------------------------------------------------------------
@requires_real_lk
def test_real_lk_a_findings(real_lk_a_bytes):
    assert sha256(real_lk_a_bytes) == REAL_LK_A_SHA
    image = lkpatch.parse_lk_image(real_lk_a_bytes, REAL_LK_A)
    assert image.is_container and image.name == "lk" and image.ext_magic == LK_EXT_MAGIC
    report = lkpatch.scan(image)
    assert report.arch == "aarch64"
    assert report.verdict == "supported-aarch64"
    assert report.already_patched is False
    by_category = {finding.category: finding for finding in report.findings}
    assert len(report.findings) == 2
    assert set(by_category) == {"orange_state", "dm_verity"}
    assert by_category["orange_state"].offset == 0x4D928
    assert by_category["dm_verity"].offset == 0x4CC58
    assert by_category["orange_state"].confidence >= 0.9
    assert by_category["dm_verity"].confidence >= 0.9
    orange_evidence = "\n".join(by_category["orange_state"].evidence)
    assert "5,000,000" in orange_evidence and "Orange State" in orange_evidence and "Red State" in orange_evidence
    dm_evidence = "\n".join(by_category["dm_verity"].evidence)
    assert "30 × 100,000" in dm_evidence and "3.0 秒" in dm_evidence
    # 原样本未被修改
    assert sha256(REAL_LK_A.read_bytes()) == REAL_LK_A_SHA


@requires_real_lk
def test_real_lk_a_patch_verify_disasm(real_lk_a_bytes):
    image = lkpatch.parse_lk_image(real_lk_a_bytes, REAL_LK_A)
    report = lkpatch.scan(image)
    new_bytes, changes = lkpatch.apply_patches(image, report)
    verification = lkpatch.verify_patch(image, new_bytes, report)
    assert verification["patch_ok"] is True
    assert verification["size_same"] and verification["header_same"] and verification["guard_counts_same"]
    assert verification["unexpected_changes"] == []
    assert [(start, end) for start, end in verification["changed_ranges"]] == [
        (0x4CC58, 0x4CC60),
        (0x4D928, 0x4D930),
    ]
    disasm = " ".join(verification["disasm_after"])
    assert "mov w0, wzr" in disasm and "ret" in disasm
    # 补丁后入口字节精确
    assert new_bytes[0x4CC58 : 0x4CC60] == AARCH64_PATCH
    assert new_bytes[0x4D928 : 0x4D930] == AARCH64_PATCH
    # 输入 bytes 未被修改
    assert sha256(real_lk_a_bytes) == REAL_LK_A_SHA


@requires_real_lk
def test_real_lk_b_same_findings(real_lk_a_scan):
    real_lk_b_bytes = REAL_LK_B.read_bytes()
    assert sha256(real_lk_b_bytes) == REAL_LK_B_SHA
    image = lkpatch.parse_lk_image(real_lk_b_bytes, REAL_LK_B)
    report = lkpatch.scan(image)
    assert report.verdict == "supported-aarch64"
    assert [(finding.category, finding.offset) for finding in report.findings] == [
        ("dm_verity", 0x4CC58),
        ("orange_state", 0x4D928),
    ]
    # A/B 槽位互相提示
    assert any("A/B" in note for note in report.notes)


@requires_real_lk
def test_real_lk_cli_full_flow(tmp_path, capsys):
    source = tmp_path / "lk_a.bin"
    source.write_bytes(REAL_LK_A.read_bytes())
    # 1) lk-info --json
    code = main(["lk-info", str(source), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["arch"] == "aarch64"
    assert [(item["category"], item["offset"]) for item in payload["findings"]] == [
        ("dm_verity", 0x4CC58),
        ("orange_state", 0x4D928),
    ]
    # 2) lk-patch --dry-run：不写文件
    code = main(["lk-patch", str(source), "--dry-run", "--clear-text"])
    captured = capsys.readouterr()
    assert code == 0
    assert "dry-run" in captured.out and "补丁前反汇编" in captured.out
    assert not lkpatch.patched_path(source).exists()
    assert not lkpatch.backup_path(source).exists()
    # 3) lk-patch：生成 _patched + 备份
    code = main(["lk-patch", str(source)])
    capsys.readouterr()
    assert code == 0
    patched = lkpatch.patched_path(source)
    backup = lkpatch.backup_path(source)
    assert patched.is_file() and backup.is_file()
    assert sha256(backup.read_bytes()) == REAL_LK_A_SHA
    patched_data = patched.read_bytes()
    assert sha256(source.read_bytes()) == REAL_LK_A_SHA
    assert patched_data[0x4CC58 : 0x4CC60] == AARCH64_PATCH
    assert patched_data[0x4D928 : 0x4D930] == AARCH64_PATCH
    # 4) lk-verify
    code = main(["lk-verify", str(source), str(patched)])
    captured = capsys.readouterr()
    assert code == 0
    assert "PASS" in captured.out
    # 5) 已补丁镜像再扫描 -> already-patched，lk-patch 拒绝
    capsys.readouterr()
    code = main(["lk-info", str(patched)])
    capsys.readouterr()
    assert code == 0
    code = main(["lk-patch", str(patched)])
    captured = capsys.readouterr()
    assert code == 1
    assert "已经打过" in captured.out + captured.err
    # 6) lk-restore
    code = main(["lk-restore", str(backup), "-o", str(source)])
    capsys.readouterr()
    assert code == 0
    assert sha256(source.read_bytes()) == REAL_LK_A_SHA


# ----------------------------------------------------------------------
# CLI 负样本 & 退出码
# ----------------------------------------------------------------------
def test_cli_lk_info_unsupported_returns_nonzero(tmp_path, capsys):
    payload = bytearray(b"\x00" * 0x4000)
    payload[0x1000:0x1010] = stp_x29_x30_pre(0x20) + nop() + ldp_x29_x30_post(0x20) + ret()
    source = tmp_path / "no_warnings.img"
    source.write_bytes(build_lk_image(bytes(payload)))
    code = main(["lk-info", str(source)])
    captured = capsys.readouterr()
    assert code == 1
    assert "不支持" in captured.err or "fail-safe" in captured.err
    code = main(["lk-info", str(tmp_path / "missing.img")])
    captured = capsys.readouterr()
    assert code == 1 and "错误" in captured.err
    code = main(["lk-patch", str(tmp_path / "missing.img")])
    assert code == 1


def test_cli_lk_patch_unsupported_writes_nothing(tmp_path):
    payload = bytearray(b"\x00" * 0x4000)
    payload[0x1000:0x1010] = stp_x29_x30_pre(0x20) + nop() + ldp_x29_x30_post(0x20) + ret()
    source = tmp_path / "no_warnings.img"
    source.write_bytes(build_lk_image(bytes(payload)))
    assert main(["lk-patch", str(source)]) == 1
    assert not lkpatch.patched_path(source).exists()
    assert not lkpatch.backup_path(source).exists()


def test_cli_categories_and_bad_category(synthetic_aarch64_image_bytes, tmp_path):
    source = tmp_path / "lk_synth.bin"
    source.write_bytes(synthetic_aarch64_image_bytes)
    code = main(["lk-patch", str(source), "--categories", "dm_verity", "-o", str(tmp_path / "dm_only.bin")])
    assert code == 0
    result = (tmp_path / "dm_only.bin").read_bytes()
    assert result[0x200 + 0x1800 : 0x200 + 0x1808] == AARCH64_PATCH
    assert result[0x200 + 0x1000 : 0x200 + 0x1008] == synthetic_aarch64_image_bytes[0x200 + 0x1000 : 0x200 + 0x1008]
    # 非法类别 -> argparse 参数错误（退出码 2）
    assert main(["lk-patch", str(source), "--categories", "bad_category"]) == 2


def test_cli_clear_text_output(synthetic_aarch64_image_bytes, tmp_path, capsys):
    source = tmp_path / "lk_synth.bin"
    source.write_bytes(synthetic_aarch64_image_bytes)
    output = tmp_path / "lk_clear.bin"
    code = main(["lk-patch", str(source), "--clear-text", "-o", str(output)])
    capsys.readouterr()
    assert code == 0
    assert output.is_file()
    original_image = lkpatch.parse_lk_image(source.read_bytes(), source)
    report = lkpatch.scan(original_image)
    verification = lkpatch.verify_patch(original_image, output.read_bytes(), report)
    assert verification["patch_ok"] is True
    assert verification["residual_warnings"] == 0
    assert output.read_bytes()[:HEADER_SIZE] == synthetic_aarch64_image_bytes[:HEADER_SIZE]
    # 清空的只是警告文本，androidboot.* 计数不变
    for guard in (
        b"androidboot.verifiedbootstate=orange",
        b"androidboot.veritymode=enforcing",
        b"androidboot.atm=disabled",
    ):
        assert output.read_bytes().count(guard) == synthetic_aarch64_image_bytes.count(guard)


# ----------------------------------------------------------------------
# 依赖 / capstone 可选
# ----------------------------------------------------------------------
def test_capstone_missing_degrades_gracefully(monkeypatch, synthetic_aarch64_image):
    monkeypatch.setattr(lkpatch, "_CAPSTONE_MODULE", None)
    report = lkpatch.scan(synthetic_aarch64_image)
    assert report.verdict == "unsupported"
    assert report.findings == []
    assert any("capstone" in note.lower() for note in report.notes)
    # ARM32 精确特征码不依赖 capstone
    payload, _ = build_arm32_payload()
    arm_image = lkpatch.parse_lk_image(build_lk_image(payload))
    arm_report = lkpatch.scan(arm_image)
    assert arm_report.verdict == "supported-arm32-thumb"


def test_core_imports_without_capstone_syntax():
    source = (ROOT / "src" / "logobuilder" / "core" / "lkpatch.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_imports = {
        alias.name.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "capstone" not in top_imports, "capstone 必须延迟导入（核心库在未安装时也要能 import）"


def test_api_contract_exports():
    import logobuilder.core as core

    for name in (
        "LkImage",
        "LkFinding",
        "LkWarningString",
        "LkScanReport",
        "LkPatchError",
        "parse_lk_image",
        "detect_arch",
        "apply_patches",
        "verify_patch",
        "save_backup",
        "restore_from_backup",
        "backup_path",
        "patched_path",
        "load_custom_rules",
    ):
        assert hasattr(core, name), f"logobuilder.core 缺少 LK API：{name}"
    for name in (
        "parse_lk_image",
        "detect_arch",
        "apply_patches",
        "verify_patch",
        "save_backup",
        "restore_from_backup",
        "patched_path",
        "backup_path",
    ):
        assert hasattr(core.lkpatch, name)
