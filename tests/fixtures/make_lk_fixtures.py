#!/usr/bin/env python3
"""Deterministic LK-patch test-corpus generator (QA scope, independent of ``logobuilder``).

Produces synthetic MediaTek LK partition images / bare LK payloads that exercise the
LK warning-patch feature (see ``docs/spec_lkpatch.md``) without shipping any
third-party firmware:

``arm32/``
    * ``lk_arm32_needles.img``       512-B MTK header + all 7 built-in ARM32 Thumb-2
                                     signature needles + warning strings + guard params
                                     + zero padding to a 4 MiB partition
    * ``lk_arm32_no_signature.img``  same payload without any needle (fail-safe case)
    * ``lk_arm32_already_patched.img`` needles already replaced by ``00207047``
    * ``lk_arm32_bad_magic.img``     wrong magic + valid needles
    * ``lk_arm32_truncated.img``     header claims more payload than the file holds

``aarch64/``
    * ``lk_aarch64_warnings.img``    synthetic AArch64 LK built from *real* instruction
                                     encodings: warning dispatcher (Orange/Red state,
                                     cmp w8,#1/#3/#2 chain, ``movk 0x4c/0x4b40`` = 5 s)
                                     and dm-verity warning function (2x "dm-verity"
                                     refs, ``movk 1/0x86a0`` = 100,000 µs)
    * ``lk_aarch64_dmverity_only.img`` only the dm-verity function
    * ``lk_aarch64_no_warnings.img`` valid AArch64 code without warning strings
    * ``lk_aarch64_bad_magic.img``   wrong header magic
    * ``lk_aarch64_raw_payload.bin`` bare payload (no 512-byte header) -> raw mode

``negative/``
    * ``all_zero_4mb.bin``           4 MiB of 0x00
    * ``lk_bad_magic_zero.img``      header-sized zero image (magic mismatch)
    * ``not_lk_random.bin``          deterministic random blob

A manifest (``manifest.json``) records every file's sha256 and the **expected
findings** (category + absolute file offset + patch bytes) so tests assert exact
offsets instead of "some finding exists".

AArch64 encoding is implemented from scratch (no capstone dependency) and the
prologue/constant encoders are self-checked against the real sample when present
(see ``selftest_encoders``).

Usage::

    python tests/fixtures/make_lk_fixtures.py            # generate / refresh
    python tests/fixtures/make_lk_fixtures.py --verify   # manifest hash check
    python tests/fixtures/make_lk_fixtures.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from pathlib import Path

HEADER_SIZE = 0x200
MTK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689
PARTITION_SIZE = 4 * 1024 * 1024
GENERATOR_VERSION = "1"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_OUT = HERE / "generated_lk"
REAL_SAMPLE = REPO_ROOT / "analysis" / "lk" / "samples" / "lk_a.bin"

#: ``00207047`` = ``movs r0, #0; bx lr`` (Thumb) — the universal ARM32 patch.
ARM32_PATCH = bytes.fromhex("00207047")
#: ``e0 03 1f 2a c0 03 5f d6`` = ``mov w0, wzr; ret`` (AArch64 little-endian).
AARCH64_PATCH = bytes.fromhex("e0031f2ac0035fd6")

#: The 7 built-in ARM32 signature needles (facts from public community research,
#: see docs/spec_lkpatch.md §3.4) with their categories.
ARM32_NEEDLES: list[dict] = [
    {"category": "orange_state", "hex": "08b50a4b7b441b681b68022b"},
    {"category": "orange_state", "hex": "08b50e4b7b441b681b68022b"},
    {"category": "orange_state", "hex": "08b50a4b7b441b681b68012b"},
    {"category": "orange_state", "hex": "08b50e4b7b441b681b68012b"},
    {"category": "red_state", "hex": "f0b5002489b0"},
    {"category": "dm_verity", "hex": "30b583b002ab0022"},
    {"category": "dm_verity", "hex": "022800d0704738b53224e0f7"},
]

#: Real warning texts verified by the Lead against the live device strings
#: (docs/spec_lkpatch.md §3). Orange path and Red path have distinct text blocks.
WARNING_STRINGS = [
    # orange path (state 2)
    b"Orange State\n\n",
    b"OS Not Being Verified Or Custom OS. Dismiss After 5 Seconds.\n",
    # red path (state 1 / 3)
    b"Red State\n\n",
    b"Device Cannot Boot. Enter Repair Mode.",
    b"Dismiss After 5 Seconds.\n",
    # dm-verity path
    b"dm-verity corruption\n\n",
    b"Your device is corrupt.\n",
    b"It can't be trusted and may not work properly.\n",
    b"Device will continue to bootup in %ds\n",
    b"tran_dm_fix=1",
    b"Device occur dm vertity corruption",
]

#: Payload offsets of the warning strings inside a synthetic AArch64 payload.
AARCH64_STRING_LAYOUT: dict[bytes, int] = {
    WARNING_STRINGS[0]: 0x2000,
    WARNING_STRINGS[1]: 0x2020,
    WARNING_STRINGS[2]: 0x2080,
    WARNING_STRINGS[3]: 0x20A0,
    WARNING_STRINGS[4]: 0x20E0,
    WARNING_STRINGS[5]: 0x2120,
    WARNING_STRINGS[6]: 0x2160,
    WARNING_STRINGS[7]: 0x21A0,
    WARNING_STRINGS[8]: 0x2200,
    WARNING_STRINGS[9]: 0x2240,
    WARNING_STRINGS[10]: 0x2260,
}

#: Kernel boot parameters that must never change (guarded by the patcher).
GUARD_PARAMS = [
    b"androidboot.verifiedbootstate=orange",
    b"androidboot.veritymode=enforcing",
    b"androidboot.atm=disabled",
]


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------
def prng_stream(seed: str, length: int) -> bytes:
    out = bytearray()
    counter = 0
    seed_bytes = seed.encode("utf-8")
    while len(out) < length:
        out += hashlib.sha256(seed_bytes + counter.to_bytes(4, "little")).digest()
        counter += 1
    return bytes(out[:length])


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# MTK partition header (layout verified on the real lk_a.bin sample)
# ---------------------------------------------------------------------------
def build_lk_header(
    payload_len: int,
    *,
    name: str = "lk",
    magic: int = MTK_MAGIC,
    ext_magic: int = LK_EXT_MAGIC,
    size_field: int | None = None,
) -> bytes:
    header = bytearray(HEADER_SIZE)
    struct.pack_into("<I", header, 0x00, magic)
    struct.pack_into("<I", header, 0x04, payload_len if size_field is None else size_field)
    encoded = name.encode("ascii", "replace")[:12]
    header[0x08 : 0x08 + len(encoded)] = encoded
    header[0x14:0x28] = b"\x00" * 0x14
    header[0x28:0x30] = b"\xff" * 8
    struct.pack_into("<I", header, 0x30, ext_magic)
    struct.pack_into("<I", header, 0x34, 0x200)
    struct.pack_into("<I", header, 0x38, 1)
    return bytes(header)


def build_lk_image(
    payload: bytes,
    *,
    partition_size: int = PARTITION_SIZE,
    name: str = "lk",
    magic: int = MTK_MAGIC,
    ext_magic: int = LK_EXT_MAGIC,
    size_field: int | None = None,
) -> bytes:
    if partition_size < HEADER_SIZE + len(payload):
        raise ValueError("partition_size too small for header+payload")
    return build_lk_header(
        len(payload), name=name, magic=magic, ext_magic=ext_magic, size_field=size_field
    ) + payload + b"\x00" * (partition_size - HEADER_SIZE - len(payload))


# ---------------------------------------------------------------------------
# AArch64 instruction encoders (little-endian), validated against real sample
# ---------------------------------------------------------------------------
def _u32(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def stp_x29_x30_pre(imm: int) -> bytes:
    """``stp x29, x30, [sp, #-imm]!`` (imm multiple of 8)."""
    assert imm > 0 and imm % 8 == 0
    imm7 = (-imm // 8) & 0x7F
    return _u32(0xA9800000 | (imm7 << 15) | (30 << 10) | (31 << 5) | 29)


def ldp_x29_x30_post(imm: int = 0x20) -> bytes:
    """``ldp x29, x30, [sp], #imm``."""
    imm7 = (imm // 8) & 0x7F
    return _u32(0xA8C00000 | (imm7 << 15) | (30 << 10) | (31 << 5) | 29)


def str_x_reg_sp(rt: int, off: int) -> bytes:
    """``str x<rt>, [sp, #off]``."""
    return _u32(0xF9000000 | ((off // 8) << 10) | (31 << 5) | rt)


def ldr_x_reg(rt: int, rn: int, off: int = 0) -> bytes:
    """``ldr x<rt>, [x<rn>, #off]``."""
    return _u32(0xF9400000 | ((off // 8) << 10) | (rn << 5) | rt)


def ldr_w_reg(rt: int, rn: int, off: int = 0) -> bytes:
    """``ldr w<rt>, [x<rn>, #off]``."""
    return _u32(0xB9400000 | ((off // 4) << 10) | (rn << 5) | rt)


def str_wzr(rt: int, rn: int, off: int = 0) -> bytes:
    """``str w<rt>, [x<rn>, #off]`` (use rt=31 for wzr)."""
    return _u32(0xB9000000 | ((off // 4) << 10) | (rn << 5) | rt)


def mov_x_sp(rd: int = 29) -> bytes:
    """``mov x<rd>, sp``."""
    return _u32(0x910003E0 | rd)


def mov_w_reg(rd: int, rm: int) -> bytes:
    """``mov w<rd>, w<rm>``."""
    return _u32(0x2A0003E0 | (rm << 16) | rd)


def add_imm_x(rd: int, rn: int, imm: int) -> bytes:
    """``add x<rd>, x<rn>, #imm`` (imm 0..4095)."""
    assert 0 <= imm <= 0xFFF
    return _u32(0x91000000 | (imm << 10) | (rn << 5) | rd)


def cmp_imm_w(rn: int, imm: int) -> bytes:
    """``cmp w<rn>, #imm``."""
    return _u32(0x7100001F | ((imm & 0xFFF) << 10) | (rn << 5))


def adrp(rd: int, pc: int, target: int) -> bytes:
    """``adrp x<rd>, #target`` (``pc`` = address of this instruction, mapped 1:1)."""
    delta = ((target & ~0xFFF) - (pc & ~0xFFF)) >> 12
    delta &= 0x1FFFFF  # 21-bit two's complement
    immlo = delta & 0x3
    immhi = (delta >> 2) & 0x7FFFF
    return _u32(0x90000000 | (immlo << 29) | (immhi << 5) | rd)


def b_cond(cond: int, pc: int, target: int) -> bytes:
    """Conditional branch (``cond``: 0=EQ, 1=NE...)."""
    assert (target - pc) % 4 == 0
    imm = ((target - pc) // 4) & 0x7FFFF
    return _u32(0x54000000 | (imm << 5) | cond)


def bl(pc: int, target: int) -> bytes:
    assert (target - pc) % 4 == 0
    imm = ((target - pc) // 4) & 0x3FFFFFF
    return _u32(0x94000000 | imm)


def b(pc: int, target: int) -> bytes:
    """Unconditional ``b`` branch."""
    assert (target - pc) % 4 == 0
    imm = ((target - pc) // 4) & 0x3FFFFFF
    return _u32(0x14000000 | imm)


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


# ---------------------------------------------------------------------------
# Synthetic AArch64 LK payload builder
# ---------------------------------------------------------------------------
def build_aarch64_payload(*, include_dispatcher: bool = True,
                          include_dmverity: bool = True,
                          include_warning_strings: bool = True,
                          size: int = 0x4000) -> tuple[bytes, dict]:
    """Assemble a synthetic AArch64 LK payload and return ``(bytes, layout)``.

    Layout (payload-relative offsets, payload mapped at address 0):

    * ``0x1000`` boot-state warning dispatcher. State mapping is the verified one:
      **state 2 -> Orange path, state 1/3 -> Red path**; each path prints its real
      warning text block and then waits 5,000,000 µs (5 s).
    * ``0x1800`` dm-verity warning function (6 real dm-verity strings referenced,
      ``0x186A0`` = 100,000 µs constants; the real device loops 30x = 3.0 s).
    * ``0x2000`` warning strings (real texts, see ``WARNING_STRINGS``)
    * ``0x2400`` guard params (``androidboot.*``)
    * ``0x2800`` boot-state global (u32 = 1)
    * ``0x3000`` dummy print helper; ``0x3010`` dummy delay helper
    """
    buf = bytearray(b"\x00" * size)
    layout: dict = {"functions": {}, "strings": {}, "guard_params": {}}

    # --- string region -------------------------------------------------
    if include_warning_strings:
        for text, offset in AARCH64_STRING_LAYOUT.items():
            layout["strings"][text.decode("utf-8", "replace")] = offset
            blob = text + b"\x00"
            assert offset + len(blob) <= 0x2400, f"string {text!r} overflows string region"
            buf[offset : offset + len(blob)] = blob

    guard_offsets: dict[str, int] = {}
    guard_cursor = 0x2400
    for param in GUARD_PARAMS:
        guard_offsets[param.decode()] = guard_cursor
        blob = param + b"\x00"
        buf[guard_cursor : guard_cursor + len(blob)] = blob
        guard_cursor += ((len(blob) + 15) // 16) * 16
    layout["guard_params"] = guard_offsets

    struct.pack_into("<I", buf, 0x2800, 1)  # boot-state global = 1 (Red path)

    def emit_list(start: int, chunks: list[bytes]) -> None:
        offset = start
        for chunk in chunks:
            assert len(chunk) == 4, chunk
            buf[offset : offset + 4] = chunk
            offset += 4

    # --- warning dispatcher (state 2 -> Orange, state 1/3 -> Red) ------
    if include_dispatcher:
        asm_start = 0x1000
        ins: list[bytes] = []
        labels: dict[str, int] = {}
        pc = asm_start

        def put(chunk: bytes) -> None:
            nonlocal pc
            ins.append(chunk)
            pc += 4

        def mark(name: str) -> None:
            nonlocal pc
            labels[name] = pc

        put(stp_x29_x30_pre(0x20))
        put(str_x_reg_sp(19, 0x10))
        put(mov_x_sp(29))
        # load global boot state (payload 0x2800)
        put(adrp(10, asm_start + 12, 0x2800))
        put(add_imm_x(10, 10, 0x2800 & 0xFFF))
        put(ldr_w_reg(8, 10, 0))
        put(cmp_imm_w(8, 2))
        put(b_cond(0, asm_start + 28, 0))   # b.eq orange (patched below)
        put(cmp_imm_w(8, 1))
        put(b_cond(0, asm_start + 36, 0))   # b.eq red
        put(cmp_imm_w(8, 3))
        put(b_cond(0, asm_start + 44, 0))   # b.eq red
        put(b(asm_start + 48, 0))           # b skip (patched below)
        put(nop())
        # ---- orange path: 0x1038 ----
        mark("orange")
        for text in (WARNING_STRINGS[0], WARNING_STRINGS[1]):
            ins_offset = asm_start + 4 * len(ins)
            put(adrp(0, ins_offset, AARCH64_STRING_LAYOUT[text]))
            put(add_imm_x(0, 0, AARCH64_STRING_LAYOUT[text] & 0xFFF))
            put(bl(asm_start + 4 * len(ins), 0x3000))
        put(movz_w(0, 0x4B40))
        put(movk_w(0, 0x4C, 16))  # w0 = 0x4C4B40 = 5,000,000 us = 5 s
        put(bl(asm_start + 4 * len(ins), 0x3010))
        # ---- red path: 0x105C ----
        mark("red")
        for text in (WARNING_STRINGS[2], WARNING_STRINGS[3], WARNING_STRINGS[4]):
            ins_offset = asm_start + 4 * len(ins)
            put(adrp(0, ins_offset, AARCH64_STRING_LAYOUT[text]))
            put(add_imm_x(0, 0, AARCH64_STRING_LAYOUT[text] & 0xFFF))
            put(bl(asm_start + 4 * len(ins), 0x3000))
        put(movz_w(0, 0x4B40))
        put(movk_w(0, 0x4C, 16))  # 5,000,000 us = 5 s
        put(bl(asm_start + 4 * len(ins), 0x3010))
        # ---- common return: ----
        mark("skip")
        put(mov_w0_wzr())
        put(ldp_x29_x30_post(0x20))
        put(ret())

        ins[7] = b_cond(0, asm_start + 28, labels["orange"])
        ins[9] = b_cond(0, asm_start + 36, labels["red"])
        ins[11] = b_cond(0, asm_start + 44, labels["red"])
        ins[12] = b(asm_start + 48, labels["skip"])
        emit_list(asm_start, ins)
        layout["functions"]["orange_state"] = asm_start
        layout["functions"]["orange_state_orange_path"] = labels["orange"]
        layout["functions"]["orange_state_red_path"] = labels["red"]
        layout["functions"]["orange_state_end"] = asm_start + 4 * len(ins)

    # --- dm-verity warning function (3.0 s = 30 x 100,000 us) ----------
    if include_dmverity:
        asm_start = 0x1800
        ins = []
        pc = asm_start
        labels = {}

        def put2(chunk: bytes) -> None:
            nonlocal pc
            ins.append(chunk)
            pc += 4

        def mark2(name: str) -> None:
            labels[name] = pc

        put2(stp_x29_x30_pre(0x20))
        put2(mov_w_reg(19, 0))
        put2(add_imm_x(0, 29, 0x1C))
        put2(str_wzr(31, 29, 0x1C))
        put2(bl(asm_start + 16, 0x3000))
        put2(cmp_imm_w(19, 2))
        put2(b_cond(0, asm_start + 24, 0))   # b.eq warn (patched below)
        put2(ldr_w_reg(8, 29, 0x1C))
        put2(cmp_imm_w(8, 1))
        put2(b_cond(1, asm_start + 36, 0))   # b.ne skip (patched below)
        put2(nop())
        mark2("warn")
        for text in (
            WARNING_STRINGS[5], WARNING_STRINGS[6], WARNING_STRINGS[7],
            WARNING_STRINGS[8], WARNING_STRINGS[9], WARNING_STRINGS[10],
        ):
            ins_offset = asm_start + 4 * len(ins)
            put2(adrp(0, ins_offset, AARCH64_STRING_LAYOUT[text]))
            put2(add_imm_x(0, 0, AARCH64_STRING_LAYOUT[text] & 0xFFF))
            put2(bl(asm_start + 4 * len(ins), 0x3000))
        # real device: 30 iterations x 100,000 us = 3.0 s
        put2(movz_w(9, 30))  # loop count = 30
        for _ in range(2):
            put2(movz_w(0, 0x86A0))
            put2(movk_w(0, 1, 16))  # w0 = 0x186A0 = 100,000 us
            put2(bl(asm_start + 4 * len(ins), 0x3010))
        put2(nop())
        mark2("skip")
        put2(ldr_x_reg(19, 31, 0x10))
        put2(ldp_x29_x30_post(0x20))
        put2(ret())

        warn_pc = asm_start + 6 * 4
        ins[6] = b_cond(0, warn_pc, labels["warn"])
        ins[9] = b_cond(1, asm_start + 36, labels["skip"])
        emit_list(asm_start, ins)
        layout["functions"]["dm_verity"] = asm_start
        layout["functions"]["dm_verity_end"] = asm_start + 4 * len(ins)

    # --- dummy helpers -------------------------------------------------
    for helper in (0x3000, 0x3010):
        emit_list(helper, [stp_x29_x30_pre(0x10), nop(), ldp_x29_x30_post(0x10), ret()])

    return bytes(buf), layout


def build_arm32_payload(*, include_needles: bool = True,
                        needles_patched: bool = False,
                        size: int = 0x8000) -> tuple[bytes, dict]:
    buf = bytearray(b"\x00\xbf" * (size // 2))  # Thumb NOP filler
    layout: dict = {"needle_offsets": [], "strings": {}, "guard_params": {}}

    # realistic Thumb prologue bytes at the payload start
    buf[0:8] = bytes.fromhex("2de9f04f0fb5") + b"\x00\xbf"

    needle_cursor = 0x1000
    for entry in ARM32_NEEDLES:
        needle = bytes.fromhex(entry["hex"])
        if include_needles:
            assert needle_cursor + len(needle) <= 0x2000
            if needles_patched:
                blob = ARM32_PATCH + b"\x00\xbf" * ((len(needle) - 4 + 1) // 2)
                blob = blob[: len(needle)]
            else:
                blob = needle
            buf[needle_cursor : needle_cursor + len(blob)] = blob
        layout["needle_offsets"].append(
            {"category": entry["category"], "offset": needle_cursor,
             "needle": entry["hex"], "patched": needles_patched}
        )
        needle_cursor += 0x100

    string_cursor = 0x2000
    for text in WARNING_STRINGS:
        layout["strings"][text.decode()] = string_cursor
        blob = text + b"\x00"
        buf[string_cursor : string_cursor + len(blob)] = blob
        string_cursor += ((len(blob) + 15) // 16) * 16

    guard_cursor = 0x2400
    for param in GUARD_PARAMS:
        layout["guard_params"][param.decode()] = guard_cursor
        blob = param + b"\x00"
        buf[guard_cursor : guard_cursor + len(blob)] = blob
        guard_cursor += ((len(blob) + 15) // 16) * 16

    return bytes(buf), layout


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
class Corpus:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.entries: dict[str, dict] = {}

    def write(self, rel_path: str, data: bytes, *, kind: str,
              expected: dict | None = None, note: str = "") -> Path:
        path = self.out / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.entries[rel_path] = {
            "kind": kind,
            "bytes": len(data),
            "sha256": sha256(data),
            "expected": expected or {},
            "note": note,
        }
        return path


def _arm32_expected(layout: dict, *, payload_offset: int, patched: bool) -> dict:
    findings = []
    for item in layout["needle_offsets"]:
        file_offset = payload_offset + item["offset"]
        findings.append(
            {
                "category": item["category"],
                "offset": file_offset,
                "payload_offset": item["offset"],
                "before_prefix": (ARM32_PATCH if patched else bytes.fromhex(item["needle"])[:4]).hex(),
                "after": ARM32_PATCH.hex(),
            }
        )
    return {
        "arch": "arm32-thumb",
        "payload_offset": payload_offset,
        "patch_bytes": ARM32_PATCH.hex(),
        "findings": findings,
        "warning_strings": {
            text: payload_offset + offset for text, offset in layout["strings"].items()
        },
        "guard_params": {
            text: payload_offset + offset for text, offset in layout["guard_params"].items()
        },
    }


def build_all(out_dir: Path = DEFAULT_OUT) -> dict:
    out = Path(out_dir)
    if out.exists():
        has_manifest = (out / "manifest.json").is_file()
        empty = not any(out.iterdir())
        if not (has_manifest or empty):
            raise SystemExit(f"refusing to overwrite non-fixture directory: {out}")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(out)

    # ---- ARM32: all 7 needles ----------------------------------------
    payload, layout = build_arm32_payload()
    image = build_lk_image(payload)
    corpus.write(
        "arm32/lk_arm32_needles.img",
        image,
        kind="valid-arm32",
        expected=_arm32_expected(layout, payload_offset=HEADER_SIZE, patched=False),
        note="512B MTK header + all 7 built-in ARM32 Thumb-2 needles + warning strings"
             " + guard params + zero pad to 4 MiB",
    )

    # ---- ARM32: no signature needle ----------------------------------
    payload, layout = build_arm32_payload(include_needles=False)
    corpus.write(
        "arm32/lk_arm32_no_signature.img",
        build_lk_image(payload),
        kind="unsupported-arm32",
        expected={
            "arch": "arm32-thumb",
            "verdict_in": ["unsupported"],
            "findings": [],
            "note": "warning strings present but no known needle -> fail-safe, no write",
        },
        note="no signature needle: scanner must fail safe and write nothing",
    )

    # ---- ARM32: already patched --------------------------------------
    payload, layout = build_arm32_payload(needles_patched=True)
    corpus.write(
        "arm32/lk_arm32_already_patched.img",
        build_lk_image(payload),
        kind="already-patched",
        expected={
            "arch": "arm32-thumb",
            "verdict_in": ["already-patched", "unsupported"],
            "findings": [],
            "note": "needles replaced by 00207047; nothing left to patch",
        },
        note="already patched image (no needle left)",
    )

    # ---- ARM32: bad magic --------------------------------------------
    payload, layout = build_arm32_payload()
    corpus.write(
        "arm32/lk_arm32_bad_magic.img",
        build_lk_image(payload, magic=0x12345678),
        kind="invalid",
        expected={
            "error": True,
            "reason_contains_any": ["magic", "MTK", "不支持", "不是"],
            "no_output": True,
        },
        note="magic != 0x58881688 (payload holds valid needles, header must be rejected)",
    )

    # ---- ARM32: truncated payload ------------------------------------
    payload, layout = build_arm32_payload()
    truncated = build_lk_image(payload, size_field=len(payload) + 0x1000)
    corpus.write(
        "arm32/lk_arm32_truncated.img",
        truncated[: HEADER_SIZE + 0x100],
        kind="invalid",
        expected={
            "error": True,
            "reason_contains_any": ["截断", "size", "长度", "不一致", "不支持"],
            "no_output": True,
        },
        note="header size field claims more payload than the file actually has",
    )

    # ---- AArch64: dispatcher + dm-verity -----------------------------
    payload, layout = build_aarch64_payload()
    image = build_lk_image(payload)
    functions = layout["functions"]
    expected = {
        "arch": "aarch64",
        "payload_offset": HEADER_SIZE,
        "patch_bytes": AARCH64_PATCH.hex(),
        "findings": [
            {
                "category": "orange_state",
                "offset": HEADER_SIZE + functions["orange_state"],
                "payload_offset": functions["orange_state"],
                "before_prefix": stp_x29_x30_pre(0x20).hex(),
                "after": AARCH64_PATCH.hex(),
            },
            {
                "category": "dm_verity",
                "offset": HEADER_SIZE + functions["dm_verity"],
                "payload_offset": functions["dm_verity"],
                "before_prefix": stp_x29_x30_pre(0x20).hex(),
                "after": AARCH64_PATCH.hex(),
            },
        ],
        "warning_strings": {
            text: HEADER_SIZE + offset for text, offset in layout["strings"].items()
        },
        "guard_params": {
            text: HEADER_SIZE + offset for text, offset in layout["guard_params"].items()
        },
        "note": "synthetic but instruction-accurate: stp prologue + adrp/add string refs"
                " + cmp 1/3/2 chain + 0x4C4B40 (5 s) / 0x186A0 (100 ms) constants",
    }
    corpus.write(
        "aarch64/lk_aarch64_warnings.img",
        image,
        kind="valid-aarch64",
        expected=expected,
        note="synthetic AArch64 LK: warning dispatcher + dm-verity function",
    )

    # ---- AArch64: dm-verity only -------------------------------------
    payload, layout = build_aarch64_payload(include_dispatcher=False)
    functions = layout["functions"]
    corpus.write(
        "aarch64/lk_aarch64_dmverity_only.img",
        build_lk_image(payload),
        kind="valid-aarch64",
        expected={
            "arch": "aarch64",
            "payload_offset": HEADER_SIZE,
            "patch_bytes": AARCH64_PATCH.hex(),
            "findings": [
                {
                    "category": "dm_verity",
                    "offset": HEADER_SIZE + functions["dm_verity"],
                    "payload_offset": functions["dm_verity"],
                    "before_prefix": stp_x29_x30_pre(0x20).hex(),
                    "after": AARCH64_PATCH.hex(),
                }
            ],
        },
        note="only dm-verity function present",
    )

    # ---- AArch64: valid code, no warning strings ---------------------
    payload, layout = build_aarch64_payload(
        include_warning_strings=False,
        include_dispatcher=False,
        include_dmverity=False,
    )
    corpus.write(
        "aarch64/lk_aarch64_no_warnings.img",
        build_lk_image(payload),
        kind="unsupported-aarch64",
        expected={"arch": "aarch64", "verdict_in": ["unsupported"], "findings": [], "no_output": True},
        note="valid-looking AArch64 payload without warning strings -> fail-safe",
    )

    # ---- AArch64: bad magic ------------------------------------------
    payload, layout = build_aarch64_payload()
    corpus.write(
        "aarch64/lk_aarch64_bad_magic.img",
        build_lk_image(payload, magic=0xDEADBEEF),
        kind="invalid",
        expected={
            "error": True,
            "reason_contains_any": ["magic", "MTK", "不支持", "不是"],
            "no_output": True,
        },
        note="wrong magic",
    )

    # ---- AArch64: bare payload (no header) ---------------------------
    payload, layout = build_aarch64_payload()
    functions = layout["functions"]
    corpus.write(
        "aarch64/lk_aarch64_raw_payload.bin",
        payload,
        kind="raw-aarch64",
        expected={
            "arch": "aarch64",
            "is_container": False,
            "payload_offset": 0,
            "patch_bytes": AARCH64_PATCH.hex(),
            "findings": [
                {"category": "orange_state", "offset": functions["orange_state"],
                 "before_prefix": stp_x29_x30_pre(0x20).hex(), "after": AARCH64_PATCH.hex()},
                {"category": "dm_verity", "offset": functions["dm_verity"],
                 "before_prefix": stp_x29_x30_pre(0x20).hex(), "after": AARCH64_PATCH.hex()},
            ],
        },
        note="bare LK payload (no 512-byte MTK header) -> raw mode, offsets are payload offsets",
    )

    # ---- negative blobs ----------------------------------------------
    corpus.write(
        "negative/all_zero_4mb.bin",
        b"\x00" * PARTITION_SIZE,
        kind="invalid",
        expected={"error": True, "reason_contains_any": ["magic", "不支持", "不是", "空"], "no_output": True},
        note="4 MiB of zeros",
    )
    corpus.write(
        "negative/not_lk_random.bin",
        prng_stream("not-lk-random", 0x4000),
        kind="invalid",
        expected={"error": True, "reason_contains_any": ["magic", "不支持", "不是"], "no_output": True},
        note="deterministic random blob (no MTK header)",
    )

    # ---- manifest ----------------------------------------------------
    manifest = {
        "generator": "tests/fixtures/make_lk_fixtures.py",
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": sha256(Path(__file__).read_bytes()),
        "constants": {
            "header_size": HEADER_SIZE,
            "mtk_magic": f"0x{MTK_MAGIC:08X}",
            "lk_ext_magic": f"0x{LK_EXT_MAGIC:08X}",
            "partition_size": PARTITION_SIZE,
            "arm32_patch": ARM32_PATCH.hex(),
            "aarch64_patch": AARCH64_PATCH.hex(),
            "arm32_needles": ARM32_NEEDLES,
            "warning_strings": [text.decode() for text in WARNING_STRINGS],
            "guard_params": [param.decode() for param in GUARD_PARAMS],
        },
        "fixtures": corpus.entries,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------------------
# Encoder self-check (against the real AArch64 sample when it is present)
# ---------------------------------------------------------------------------
def selftest_encoders() -> list[str]:
    problems: list[str] = []
    # Known encodings from docs/spec_lkpatch.md / real sample.
    checks = [
        ("stp x29,x30,[sp,#-0x20]!", stp_x29_x30_pre(0x20), "fd7bbea9"),
        ("str x19,[sp,#0x10]", str_x_reg_sp(19, 0x10), "f30b00f9"),
        ("mov x29,sp", mov_x_sp(29), "fd030091"),
        ("ldp x29,x30,[sp],#0x20", ldp_x29_x30_post(0x20), "fd7bc2a8"),
        ("ret", ret(), "c0035fd6"),
        ("mov w0,wzr", mov_w0_wzr(), "e0031f2a"),
        ("cmp w8,#1", cmp_imm_w(8, 1), "1f050071"),
        ("cmp w8,#3", cmp_imm_w(8, 3), "1f0d0071"),
        ("mov w0,#0x4b40", movz_w(0, 0x4B40), "00688952"),
        ("movk w0,#0x4c,lsl#16", movk_w(0, 0x4C, 16), "8009a072"),
        ("mov w0,#0x86a0", movz_w(0, 0x86A0), "00d49052"),
        ("movk w0,#1,lsl#16", movk_w(0, 1, 16), "2000a072"),
        ("mov w19,w0", mov_w_reg(19, 0), "f303002a"),
        ("add x0,x29,#0x1c", add_imm_x(0, 29, 0x1C), "a0730091"),
        ("str wzr,[x29,#0x1c]", str_wzr(31, 29, 0x1C), "bf1f00b9"),
        ("ldr w8,[x29,#0x1c]", ldr_w_reg(8, 29, 0x1C), "a81f40b9"),
    ]
    for name, encoded, expected_hex in checks:
        if encoded.hex() != expected_hex:
            problems.append(f"{name}: got {encoded.hex()} expected {expected_hex}")

    if REAL_SAMPLE.is_file():
        data = REAL_SAMPLE.read_bytes()
        if len(data) >= HEADER_SIZE + 0x4D750:
            prologue = data[HEADER_SIZE + 0x4D728 : HEADER_SIZE + 0x4D728 + 12]
            expected = stp_x29_x30_pre(0x20) + str_x_reg_sp(19, 0x10) + mov_x_sp(29)
            if prologue != expected:
                problems.append(
                    f"real sample prologue mismatch: {prologue.hex()} != {expected.hex()}"
                )
    return problems


# ---------------------------------------------------------------------------
# ensure / verify / CLI
# ---------------------------------------------------------------------------
def _manifest_current(out: Path) -> bool:
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if manifest.get("generator_version") != GENERATOR_VERSION:
        return False
    if manifest.get("generator_sha256") != sha256(Path(__file__).read_bytes()):
        return False
    for rel_path, entry in manifest.get("fixtures", {}).items():
        path = out / rel_path
        if not path.is_file():
            return False
        if sha256(path.read_bytes()) != entry["sha256"]:
            return False
    return True


def ensure(out: Path = DEFAULT_OUT, *, quiet: bool = True) -> dict:
    out = Path(out)
    if not _manifest_current(out):
        manifest = build_all(out)
        if not quiet:
            print(f"generated {len(manifest['fixtures'])} LK fixtures in {out}")
        return manifest
    return json.loads((out / "manifest.json").read_text(encoding="utf-8"))


def load_manifest(out: Path = DEFAULT_OUT) -> dict:
    return json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic LK-patch fixtures.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    problems = selftest_encoders()
    if problems:
        print("ENCODER SELFTEST FAILED:", file=sys.stderr)
        for problem in problems:
            print(" -", problem, file=sys.stderr)
        return 1
    print(f"encoder selftest: OK (real sample present: {REAL_SAMPLE.is_file()})")

    out = args.out
    if args.list:
        manifest = ensure(out, quiet=True)
        for rel_path, entry in manifest["fixtures"].items():
            print(f"{entry.get('kind', '?'):20s} {rel_path}")
        return 0
    if args.verify:
        if _manifest_current(out):
            print(f"OK: LK fixture corpus is current ({out})")
            return 0
        print(f"STALE/MISSING: {out}", file=sys.stderr)
        return 1
    if args.force or not _manifest_current(out):
        manifest = build_all(out)
        print(f"OK: generated {len(manifest['fixtures'])} LK fixtures in {out}")
        return 0
    print(f"OK: LK fixture corpus already current ({out}); use --force to rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
