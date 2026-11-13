# -*- coding: utf-8 -*-
"""Classify every function that references interesting strings in the LK payload.

Builds an image-wide map: function start -> referenced strings (via ADRP+ADD),
then prints all functions whose strings mention warnings / boot state / dm-verity,
and separately all functions referencing androidboot.* guard strings.
Also dumps neighbouring helper functions and searches for absolute pointer words.
"""
from __future__ import annotations

import struct
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"
md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True

WARN_WORDS = (
    b"state", b"State", b"unlocked", b"verif", b"corrupt", b"dm-ver",
    b"dm_ver", b"boot in", b"5 seconds", b"trusted", b"Repair", b"POWER key",
    b"shutdown", b"warn", b"WARN",
)
GUARD_PREFIXES = (b"androidboot.", b"verifiedbootstate", b"veritymode", b"vbmeta", b"avb")


def load(name="lk_a.bin"):
    raw = (SAMPLES / name).read_bytes()
    size = struct.unpack_from("<I", raw, 4)[0]
    return raw, raw[0x200:0x200 + size]


class Img:
    def __init__(self, payload: bytes):
        self.P = payload
        self.N = len(payload)
        self.cache: dict[int, object] = {}

    def dec(self, off):
        c = self.cache
        if off in c:
            return c[off]
        v = None
        if 0 <= off and off + 4 <= self.N and off % 4 == 0:
            g = list(md.disasm(self.P[off:off + 4], off))
            if g:
                v = g[0]
        c[off] = v
        return v

    def cstr(self, off, maxlen=140):
        if not (0 <= off < self.N):
            return None
        e = self.P.find(b"\x00", off, min(self.N, off + maxlen))
        if e < 0:
            return None
        raw = self.P[off:e]
        if not raw:
            return ""
        if any(b < 9 or (13 < b < 32) or b > 126 for b in raw):
            return None  # not a printable C string
        return raw.decode("latin-1")


def collect_refs(img: Img):
    """(ref_off, target) for every ADRP+ADD with target inside payload."""
    refs = []
    for off in range(0, img.N - 4, 4):
        ins = img.dec(off)
        if ins is None or ins.mnemonic != "adrp":
            continue
        if len(ins.operands) < 2 or ins.operands[1].type != ARM64_OP_IMM:
            continue
        page = ins.operands[1].imm
        reg = ins.operands[0].reg
        for k in (1, 2, 3):
            n2 = img.dec(off + 4 * k)
            if n2 is None:
                break
            if n2.mnemonic == "add" and len(n2.operands) >= 3:
                try:
                    if n2.operands[1].reg != reg or n2.operands[2].type != ARM64_OP_IMM:
                        continue
                except Exception:
                    continue
                tgt = page + n2.operands[2].imm
                if 0 <= tgt < img.N:
                    refs.append((off, tgt))
                break
    return refs


def func_start(img: Img, off: int) -> int | None:
    for cand in range(off, max(0, off - 0x600), -4):
        ins = img.dec(cand)
        if ins is None:
            continue
        s = ins.op_str
        if ins.mnemonic == "stp" and s.startswith("x29, x30, [sp,"):
            return cand
        if ins.mnemonic == "sub" and s.startswith("sp, sp,"):
            return cand
    return None


def main():
    raw, payload = load()
    img = Img(payload)
    refs = collect_refs(img)
    print(f"total adrp+add refs into payload: {len(refs)}")
    byfunc: dict[int | None, list[tuple[int, int, str]]] = {}
    for ref_off, tgt in refs:
        s = img.cstr(tgt)
        if s is None:
            continue
        fs = func_start(img, ref_off)
        byfunc.setdefault(fs, []).append((ref_off, tgt, s))

    print("\n### functions referencing warning-related strings")
    for fs in sorted(f for f in byfunc if f is not None):
        entries = byfunc[fs]
        joined = b"\n".join(e[2].encode("latin-1") for e in entries)
        if not any(w in joined for w in WARN_WORDS):
            continue
        print(f"\nfunc 0x{fs:06x} (file 0x{fs+0x200:06x})")
        for ref_off, tgt, s in sorted(entries):
            print(f"   ref 0x{ref_off:06x} -> str 0x{tgt:06x} {s[:100]!r}")
        # show head instructions
        print("   head:", end=" ")
        for k in range(0, 4):
            ins = img.dec(fs + 4 * k)
            if ins:
                print(f"{ins.mnemonic} {ins.op_str} |", end=" ")
        print()

    print("\n### functions referencing guard strings (androidboot.* / vbmeta / avb)")
    for fs in sorted(f for f in byfunc if f is not None):
        entries = byfunc[fs]
        guarded = [e for e in entries if e[2].encode("latin-1", "ignore").startswith(GUARD_PREFIXES)
                   or "verifiedbootstate" in e[2] or "veritymode" in e[2] or "vbmeta" in e[2]]
        if not guarded:
            continue
        print(f"  func 0x{fs:06x}: {len(guarded)} guard-string refs e.g. {guarded[0][2][:70]!r}")

    print("\n### functions with NO function start found (refs before any prologue)")
    for fs in sorted((f for f in byfunc if f is None), key=lambda x: 0):
        for ref_off, tgt, s in sorted(byfunc[None]):
            print(f"   ref 0x{ref_off:06x} -> 0x{tgt:06x} {s[:80]!r}")

    print("\n### neighbour helper functions (payload 0x4d690..0x4d730)")
    for off in range(0x4D690, 0x4D730, 4):
        ins = img.dec(off)
        print(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}" if ins else f"  {off:06x}: <data>")

    print("\n### absolute pointer words equal to candidate function offsets")
    targets = [0x4D728, 0x4CA58, 0x4D71C, 0x4D6E8, 0x4D6EC, 0x4D710, 0x4CC74]
    for t in targets:
        le4 = struct.pack("<I", t)
        le8 = struct.pack("<Q", t)
        hits4 = [i for i in range(0, img.N - 4, 4) if img.P[i:i + 4] == le4]
        hits8 = [i for i in range(0, img.N - 8, 4) if img.P[i:i + 8] == le8]
        print(f"  ptr 0x{t:06x}: u32 hits={[hex(h) for h in hits4[:8]]} u64 hits={[hex(h) for h in hits8[:8]]}")

    print("\n### warning-ish strings with their reference sites (all functions, wide scan)")
    for needle in (b"Orange State", b"Red State", b"dm-verity corruption", b"Your device is corrupt",
                   b"It can't be trusted", b"Device will continue to bootup", b"tran_dm_fix",
                   b"dm vertity corruption", b"verifiedbootstate", b"will boot in 5 seconds",
                   b"continue boot with POWER key", b"shutdown after 5 seconds", b"dm-verity status.",
                   b"set_dm_verity_state", b"get_dm_verity_status", b"clear_dm_verity_status"):
        start = 0
        while True:
            idx = img.P.find(needle, start)
            if idx < 0:
                break
            # normalize to string start
            s0 = idx
            while s0 > 0 and img.P[s0 - 1] != 0:
                s0 -= 1
            hits = [(hex(r), hex(t)) for r, t in refs if t == s0]
            print(f"  {needle.decode('latin-1')!r:42s} str@0x{s0:06x} refs={hits}")
            start = idx + 1


if __name__ == "__main__":
    main()
