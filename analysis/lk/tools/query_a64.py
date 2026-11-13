# -*- coding: utf-8 -*-
"""Targeted queries for LK AArch64 analysis: strings, callers, delay counts, refs."""
from __future__ import annotations

import struct
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True


def load(name="lk_a.bin"):
    raw = (SAMPLES / name).read_bytes()
    size = struct.unpack_from("<I", raw, 4)[0]
    return raw[0x200:0x200 + size]


P = load()
N = len(P)
cache = {}


def dec(off):
    if off in cache:
        return cache[off]
    v = None
    if 0 <= off and off + 4 <= N and off % 4 == 0:
        g = list(md.disasm(P[off:off + 4], off))
        if g:
            v = g[0]
    cache[off] = v
    return v


def show(lo, hi, title=""):
    print(f"\n--- {title} [0x{lo:x},0x{hi:x}) ---")
    for off in range(lo, hi, 4):
        ins = dec(off)
        print(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}" if ins else f"  {off:06x}: <data {P[off:off+4].hex()}>")


def cstr(off, maxlen=160):
    e = P.find(b"\x00", off)
    if e < 0:
        e = off + maxlen
    return P[off:e]


print("## raw string block 0xac6a0..0xac7a0")
o = 0xac6a0
while o < 0xac7a0:
    e = P.find(b"\x00", o)
    if e < 0 or e >= 0xac7a0:
        break
    if e > o:
        print(f"  0x{o:06x} len={e-o:3d} {P[o:e]!r}")
    o = e + 1

print("\n## raw string block 0xacb40..0xacc70 (verifiedbootstate + warnings)")
o = 0xacb40
while o < 0xacc70:
    e = P.find(b"\x00", o)
    if e < 0 or e >= 0xacc70:
        break
    if e > o:
        print(f"  0x{o:06x} len={e-o:3d} {P[o:e]!r}")
    o = e + 1

print("\n## raw string block 0xb2890..0xb2960 ('will boot in' area)")
o = 0xb2890
while o < 0xb2960:
    e = P.find(b"\x00", o)
    if e < 0 or e >= 0xb2960:
        break
    if e > o:
        print(f"  0x{o:06x} len={e-o:3d} {P[o:e]!r}")
    o = e + 1

print("\n## caller region: boot state dispatcher callers")
show(0x4d1b0, 0x4d2a0, "callers of dispatcher/dmverity (0x4d21c,0x4d22c,0x4d254)")
# function start before 0x4d21c
for cand in range(0x4d21c, 0x4ce00, -4):
    ins = dec(cand)
    if ins and ins.mnemonic == "stp" and ins.op_str.startswith("x29, x30, [sp,"):
        print(f"\n  caller function start = 0x{cand:06x}")
        show(cand, cand + 0x40, "caller function head")
        break

print("\n## delay call counts")
for lo, hi, name in ((0x4d728, 0x4d7ec, "dispatcher"), (0x4ca58, 0x4cc68, "dmverity")):
    calls = []
    for off in range(lo, hi, 4):
        ins = dec(off)
        if ins and ins.mnemonic == "bl" and ins.operands[0].type == ARM64_OP_IMM:
            calls.append((off, ins.operands[0].imm))
    from collections import Counter
    c = Counter(t for _, t in calls)
    print(f"  {name} 0x{lo:x}..0x{hi:x}: {len(calls)} bl; targets {dict((hex(k), v) for k, v in c.items())}")
    if name == "dmverity":
        delay = [o for o, t in calls if t == 0x5F8B0]
        print(f"    delay(0x5f8b0) calls: {len(delay)} sites: {[hex(x) for x in delay]}")

print("\n## string refs to key warning strings (wide adrp window)")
KEYS = {
    "Orange State": 0x0ACBDB,
    "Red State": 0x0ACC28,
    "orange_unlocked_msg @0xacbea": 0x0ACBEA,
    "red_msg_a @0xacc0e": 0x0ACC0E,
    "red_msg_b @0xacc34": 0x0ACC34,
    "dm-verity corruption": 0x0AC6A3,
    "dm_verity_state fails": 0x0AC765,
    "dm_verity_status a": 0x0AC786,
    "dm_verity_status b": 0x0AC79D,
    "dm-verity status.": 0x0AC8A5,
    "will boot in 5 seconds": 0x0B28B6,
    "5 seconds (after)": 0x0B2927,
}
for name, target in KEYS.items():
    page_of_target = target & ~0xFFF
    hits = []
    for off in range(0, N - 4, 4):
        ins = dec(off)
        if ins is None or ins.mnemonic != "adrp":
            continue
        ops = ins.operands
        if len(ops) < 2 or ops[1].type != ARM64_OP_IMM:
            continue
        page = ops[1].imm
        reg = ops[0].reg
        if (page & ~0xFFF) != page_of_target and page != page_of_target:
            # capstone gives absolute page; accept exact page only
            continue
        # follow up to 8 insns for add reg,reg,#imm / ldr
        for k in range(1, 9):
            n2 = dec(off + 4 * k)
            if n2 is None:
                break
            if n2.mnemonic in ("add", "ldr") and len(n2.operands) >= 2:
                try:
                    rn = n2.operands[1].reg
                except Exception:
                    rn = None
                if rn == reg:
                    addr = page
                    if n2.mnemonic == "add":
                        for op in n2.operands[2:]:
                            if op.type == ARM64_OP_IMM:
                                addr += op.imm
                    else:
                        for op in n2.operands[2:]:
                            if op.type == ARM64_OP_IMM:
                                addr += op.imm
                    if addr == target:
                        hits.append(off)
                    break
            if n2.mnemonic in ("bl", "b", "ret", "br", "blr"):
                break
    print(f"  {name:34s} -> {[hex(h) for h in hits]}")
