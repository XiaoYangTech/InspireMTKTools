# -*- coding: utf-8 -*-
"""Inspect candidate dispatcher functions around warning-string refs in AArch64 LK payload."""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM

IMG = Path(sys.argv[1])
data = IMG.read_bytes()
size = struct.unpack_from("<I", data, 4)[0]
payload = data[0x200:0x200 + size]
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True

N = len(payload)
cache: dict[int, object] = {}


def dec(off: int):
    if off in cache:
        return cache[off]
    v = None
    if 0 <= off and off + 4 <= N and off % 4 == 0:
        for ins in md.disasm(payload[off:off + 4], off):
            v = ins
            break
    cache[off] = v
    return v


def show(center: int, before: int = 0x40, after: int = 0x60, title: str = "") -> None:
    start = max(0, center - before)
    end = min(N, center + after)
    print(f"\n===== {title} around payload+0x{center:x} =====")
    for off in range(start, end, 4):
        ins = dec(off)
        if ins is None:
            print(f"  {off:06x}: <data>")
            continue
        mark = "  <== ref" if off == center else ""
        print(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}{mark}")


# function start detection: scan backwards for stp x29,x30,[sp,#-N]! or sub sp,sp,#N
def find_func_start(off: int, window: int = 0x400) -> int | None:
    start = max(0, off - window)
    for cand in range(off, start, -4):
        ins = dec(cand)
        if ins is None:
            continue
        if ins.mnemonic == "stp" and "x29" in ins.op_str and "sp," in ins.op_str:
            return cand
        if ins.mnemonic == "sub" and ins.op_str.startswith("sp, sp"):
            return cand
    return None


targets = {
    "Orange State ref": 0x4d770,
    "Red State ref 0x4cf94": 0x4cf94,
    "Red State ref 0x4d7a0": 0x4d7a0,
    "dm-verity ref 0x4caa4": 0x4caa4,
    "dm_verity ref 0x4cce0": 0x4cce0,
    "dm_verity ref 0x4cd88": 0x4cd88,
    "5 seconds ref 0x6edc": 0x6edc,
    "5 seconds ref 0xab40": 0xab40,
}
for name, off in targets.items():
    fs = find_func_start(off)
    print(f"\n{name}: func_start={hex(fs) if fs else None}  size_hint={hex(off - fs) if fs else '?'}")
    show(off, 0x40, 0x60, name)
    if fs is not None:
        print(f"  --- function head @{fs:#x} ---")
        for o in range(fs, min(fs + 0x20, N), 4):
            ins = dec(o)
            if ins:
                print(f"    {o:06x}: {ins.mnemonic:<8} {ins.op_str}")
