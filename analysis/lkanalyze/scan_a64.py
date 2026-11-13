# -*- coding: utf-8 -*-
"""Robust AArch64 scanner for MTK LK payload: find ADRP+ADD refs to warning strings.

Payload is mapped at address 0, so a reference to string at payload offset `off`
shows up as `adrp_target + add_imm == off` (LK linked at a page-aligned base).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

WARN_KEYS = [
    b"Orange State",
    b"Red State",
    b"Your device has been unlocked",
    b"Your device has failed verification",
    b"dm-verity",
    b"dm_verity",
    b"Your device will boot in 5 seconds",
    b"Your device will reboot in 5 seconds",
    b"5 seconds",
    b"corrupt",
]

IMG = Path(sys.argv[1] if len(sys.argv) > 1 else "analysis/external/lk_samples/lk_a.bin")
data = IMG.read_bytes()
magic, size = struct.unpack_from("<II", data, 0)
payload = data[0x200:0x200 + size]
print(f"image={IMG.name} file={len(data)} magic=0x{magic:08x} size={size}")

offsets: dict[bytes, int] = {}
for key in WARN_KEYS:
    idx = payload.find(key)
    if idx >= 0:
        offsets[key] = idx
print("strings:", {k.decode(): hex(v) for k, v in offsets.items()})

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True

N = len(payload)
insn_cache: dict[int, object] = {}


def decode_at(off: int):
    if off in insn_cache:
        return insn_cache[off]
    if off + 4 > N or off % 4:
        insn_cache[off] = None
        return None
    got = None
    for ins in md.disasm(payload[off:off + 4], off):
        got = ins
        break
    insn_cache[off] = got
    return got


adrp_refs: list[tuple[int, int, int]] = []  # (adrp_off, adrp_target, reg)
for off in range(0, N - 4, 4):
    ins = decode_at(off)
    if ins is None or ins.mnemonic != "adrp":
        continue
    if len(ins.operands) < 2:
        continue
    reg = ins.operands[0].reg
    target = ins.operands[1].imm
    adrp_refs.append((off, target, reg))

print(f"adrp instructions: {len(adrp_refs)}")

refs: list[tuple[int, int, int, int]] = []  # (adrp_off, target, str_off, delta)
for off, page, reg in adrp_refs:
    for nxt in (off + 4, off + 8, off + 12):
        ins = decode_at(nxt)
        if ins is None:
            continue
        if ins.mnemonic == "add" and len(ins.operands) >= 3:
            try:
                rd = ins.operands[0].reg
                rn = ins.operands[1].reg
                imm = ins.operands[2].imm
            except Exception:
                continue
            if rn == reg:
                target = page + imm
                for key, soff in offsets.items():
                    if 0 <= target - soff <= 64:
                        refs.append((off, target, soff, target - soff))
                        break

print(f"string refs found: {len(refs)}")
by_key: dict[int, list[int]] = {}
for adrp_off, target, soff, delta in refs:
    by_key.setdefault(soff, []).append(adrp_off)

for key, soff in offsets.items():
    hits = by_key.get(soff, [])
    print(f"\n{key.decode()!r} payload+0x{soff:x}: {len(hits)} ref(s)")
    for a in sorted(hits)[:12]:
        print(f"   adrp @payload+0x{a:x}  (file 0x{a + 0x200:x})")

# Disassemble around each reference to inspect surrounding function
def show_around(center: int, before: int = 0x60, after: int = 0x80) -> None:
    start = max(0, center - before)
    end = min(N, center + after)
    print(f"--- context around payload+0x{center:x} ---")
    for off in range(start, end, 4):
        ins = decode_at(off)
        if ins is None:
            continue
        mark = " <== ref" if off == center else ""
        print(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}{mark}")


first = sorted({a for a in by_key.get(offsets.get(b"Orange State", -1), [])})
if first:
    show_around(first[0])
else:
    # fallback: any ref
    if refs:
        show_around(sorted(r[0] for r in refs)[0])
