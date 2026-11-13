# -*- coding: utf-8 -*-
"""Deep AArch64 analysis of MTK LK samples (independent re-derivation).

Usage:
  python analysis/lk/tools/analyze_a64.py [sample] [--dump-functions]

Prints (and writes analysis/lk/_evidence_a64.txt):
  * ADRP+ADD string-reference map for all warning/state strings
  * full disassembly of the candidate functions and their callers
  * BL xrefs to candidate function entries
  * patch-safety findings (incoming branches/data refs into overwritten bytes)
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_REG, ARM64_OP_MEM

ROOT = Path(__file__).resolve().parents[1]  # analysis/lk
SAMPLES = ROOT / "samples"
OUT = ROOT / "_evidence_a64.txt"

# strings of interest (payload bytes); grouped by role for classification
INTEREST = {
    "orange_state": [b"Orange State"],
    "red_state": [b"Red State"],
    "unlocked_warning": [b"Your device has been unlocked", b"Your device is unlocked"],
    "verity_warning": [b"Your device has failed verification", b"failed verification"],
    "dmverity": [b"dm-verity", b"dm_verity"],
    "boot_in": [b"will boot in", b"will reboot in"],
    "five_sec": [b"5 seconds"],
    "corrupt": [b"corrupt"],
    "guard": [b"androidboot.verifiedbootstate", b"androidboot.veritymode", b"androidboot.atm", b"androidboot."],
}

md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
md.detail = True


class Payload:
    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.file_len = len(raw)
        self.raw = raw
        magic, size = struct.unpack_from("<II", raw, 0)
        self.magic, self.size = magic, size
        self.container = magic == 0x58881688 and 0x200 + size <= len(raw)
        self.base = 0x200 if self.container else 0
        self.payload = raw[self.base:self.base + size] if self.container else raw
        self.n = len(self.payload)
        self._cache: dict[int, object] = {}

    def dec(self, off: int):
        c = self._cache
        if off in c:
            return c[off]
        ins = None
        if 0 <= off and off + 4 <= self.n and off % 4 == 0:
            got = list(md.disasm(self.payload[off:off + 4], off))
            if got:
                ins = got[0]
        c[off] = ins
        return ins

    def f2p(self, f: int) -> int:
        return f - self.base

    def p2f(self, p: int) -> int:
        return p + self.base


def find_all(hay: bytes, needle: bytes, limit: int = 64) -> list[int]:
    out, start = [], 0
    while len(out) < limit:
        i = hay.find(needle, start)
        if i < 0:
            break
        out.append(i)
        start = i + 1
    return out


def string_entries(img: Payload) -> list[tuple[int, int, str, str]]:
    """(payload_off, end, role, text) for every interesting string occurrence."""
    ents = []
    for role, keys in INTEREST.items():
        for key in keys:
            for off in find_all(img.payload, key):
                # extend to NUL for readable text
                end = img.payload.find(b"\x00", off)
                if end < 0 or end - off > 200:
                    end = off + len(key)
                text = img.payload[off:end].decode("latin-1", "replace")
                ents.append((off, end, role, text))
    return ents


def adrp_add_refs(img: Payload, targets: list[int], window: int = 96) -> list[tuple[int, int, int]]:
    """All (ref_off, target, delta) where ADRP+ADD references one of `targets`.

    Capstone's ADRP operand imm is the absolute page address; payload is mapped at 0.
    """
    tset = sorted(set(targets))
    out = []
    for off in range(0, img.n - 4, 4):
        ins = img.dec(off)
        if ins is None or ins.mnemonic != "adrp":
            continue
        if len(ins.operands) < 2 or ins.operands[1].type != ARM64_OP_IMM:
            continue
        page = ins.operands[1].imm
        reg = ins.operands[0].reg
        for nxt in (off + 4, off + 8, off + 12):
            n2 = img.dec(nxt)
            if n2 is None:
                continue
            if n2.mnemonic == "add" and len(n2.operands) >= 3:
                try:
                    rn = n2.operands[1].reg
                    imm = n2.operands[2].imm
                except Exception:
                    continue
                if rn != reg:
                    continue
                tgt = page + imm
                for t in tset:
                    if t <= tgt <= t + window:
                        out.append((off, tgt, tgt - t))
                        break
    return out


def func_start(img: Payload, ref_off: int, window: int = 0x500) -> int | None:
    lo = max(0, ref_off - window)
    for cand in range(ref_off, lo - 1, -4):
        ins = img.dec(cand)
        if ins is None:
            continue
        ops = ins.op_str
        if ins.mnemonic == "stp" and ops.startswith("x29, x30, [sp,") and "!" in ops:
            return cand
        if ins.mnemonic == "sub" and ops.startswith("sp, sp,"):
            return cand
        if ins.mnemonic == "stp" and ops.startswith("x29, x30, [sp,"):
            return cand
    return None


def disasm_lines(img: Payload, lo: int, hi: int, marks: dict[int, str] | None = None) -> list[str]:
    marks = marks or {}
    lines = []
    for off in range(lo, hi, 4):
        ins = img.dec(off)
        if ins is None:
            lines.append(f"  {off:06x}: <undecodable .word {img.payload[off:off+4].hex()}>")
            continue
        mark = ("  ; " + marks[off]) if off in marks else ""
        lines.append(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}{mark}")
    return lines


def bl_xrefs(img: Payload, targets: list[int]) -> dict[int, list[int]]:
    hit: dict[int, list[int]] = {t: [] for t in targets}
    for off in range(0, img.n - 4, 4):
        ins = img.dec(off)
        if ins is None or ins.mnemonic not in ("bl", "b", "cbz", "cbnz", "tbz", "tbnz", "adr"):
            continue
        for op in ins.operands:
            if op.type == ARM64_OP_IMM:
                if op.imm in hit:
                    hit[op.imm].append(off)
                break
    return hit


def data_refs_to(img: Payload, lo: int, hi: int) -> list[tuple[int, str]]:
    """Any computed ADRP(+ADD/LDR) address or literal-pool load landing in [lo,hi)."""
    out = []
    for off in range(0, img.n - 4, 4):
        ins = img.dec(off)
        if ins is None:
            continue
        if ins.mnemonic == "adrp" and len(ins.operands) >= 2:
            page = ins.operands[1].imm
            reg = ins.operands[0].reg
            for nxt in (off + 4, off + 8, off + 12):
                n2 = img.dec(nxt)
                if n2 is None:
                    continue
                if n2.mnemonic in ("add", "ldr", "str") and len(n2.operands) >= 2:
                    try:
                        rn = n2.operands[1].reg
                    except Exception:
                        continue
                    if rn != reg:
                        continue
                    addr = page
                    for op in n2.operands[2:]:
                        if op.type == ARM64_OP_IMM:
                            addr += op.imm
                        elif op.type == ARM64_OP_MEM and op.mem.base == reg:
                            addr += op.mem.disp
                    if lo <= addr < hi:
                        out.append((off, f"{n2.mnemonic} -> 0x{addr:x}"))
        if ins.mnemonic == "ldr" and len(ins.operands) >= 2 and ins.operands[1].type == ARM64_OP_MEM:
            mem = ins.operands[1].mem
            if mem.base == 0 and lo <= mem.disp < hi:  # literal load [pc, #imm]
                out.append((off, f"ldr literal -> 0x{mem.disp:x}"))
        if ins.mnemonic == "adr" and len(ins.operands) >= 2:
            try:
                if lo <= ins.operands[1].imm < hi:
                    out.append((off, f"adr -> 0x{ins.operands[1].imm:x}"))
            except Exception:
                pass
    return out


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "lk_a.bin"
    img = Payload(SAMPLES / which) if not Path(which).is_absolute() else Payload(Path(which))
    lines: list[str] = []

    def p(s: str = "") -> None:
        lines.append(s)
        print(s)

    p(f"# sample={img.path.name} file={img.file_len} sha256(payload) container={img.container}")
    p(f"# magic=0x{img.magic:08x} size={img.size} base=0x{img.base:x} payload_len={img.n}")

    # sanity: ADRP semantics check on known dispatcher instr
    for probe in (0x4D734, 0x4D750, 0x4CA90):
        ins = img.dec(probe)
        if ins:
            p(f"# probe {probe:06x}: {ins.mnemonic} {ins.op_str} bytes={img.payload[probe:probe+4].hex()}")

    # --- string table ---
    ents = string_entries(img)
    # dedupe overlapping
    ents = sorted({(o, e, r, t) for o, e, r, t in ents})
    p("\n## interesting strings (payload offsets)")
    for o, e, r, t in ents:
        p(f"  [{r:18s}] payload 0x{o:06x} file 0x{img.p2f(o):06x} len={e-o:3d} {t[:90]!r}")

    # --- refs ---
    targets = [o for o, *_ in ents]
    refs = adrp_add_refs(img, targets)
    p(f"\n## adrp+add refs: {len(refs)}")
    by_func: dict[int | None, list[tuple[int, int, str]]] = {}
    tmap = {}
    for o, e, r, t in ents:
        tmap.setdefault(o, []).append((r, t))
    for ref_off, tgt, delta in refs:
        role, text = tmap.get(tgt - delta, [("?", "")])[0]
        fs = func_start(img, ref_off)
        by_func.setdefault(fs, []).append((ref_off, tgt, f"{role}:{text[:40]!r} delta={delta}"))
    for fs in sorted(by_func, key=lambda x: (x is None, x or 0)):
        p(f"\n### function start {('0x%06x' % fs) if fs is not None else None} refs={len(by_func[fs])}")
        for ref_off, tgt, note in sorted(by_func[fs]):
            p(f"  ref@payload 0x{ref_off:06x} (file 0x{img.p2f(ref_off):06x}) -> 0x{tgt:06x} {note}")

    # --- candidate function full disassembly ---
    cands = {
        "boot_state_dispatcher (payload 0x4d728)": 0x4D728,
        "dm_verity_warning (payload 0x4ca58)": 0x4CA58,
    }
    for name, fs in cands.items():
        p(f"\n## FULL FUNCTION {name}")
        # find end heuristically: next stp x29,x30 prologue after 0x20, or +0x200 cap
        end = fs + 0x120
        for off in range(fs + 0x18, fs + 0x400, 4):
            ins = img.dec(off)
            if ins and ins.mnemonic == "stp" and ins.op_str.startswith("x29, x30, [sp,"):
                end = off
                break
            if ins and ins.mnemonic == "ret":
                end = off + 4
        p(f"  range payload 0x{fs:06x}..0x{end:06x} (file 0x{img.p2f(fs):06x}..0x{img.p2f(end):06x})")
        for ln in disasm_lines(img, fs, end):
            p(ln)
        p("  callers (bl/b to entry):")
        for off in bl_xrefs(img, [fs])[fs]:
            p(f"    payload 0x{off:06x} (file 0x{img.p2f(off):06x})")
        p("  incoming branch/data refs into [start,start+8):")
        drefs = data_refs_to(img, fs, fs + 8)
        p(f"    data refs: {[(hex(a), w) for a, w in drefs]}")
        for off in range(0, img.n - 4, 4):
            ins = img.dec(off)
            if ins is None or ins.mnemonic not in ("b", "bl", "cbz", "cbnz", "tbz", "tbnz"):
                continue
            imm = None
            for op in ins.operands:
                if op.type == ARM64_OP_IMM:
                    imm = op.imm
                    break
            if imm is not None and fs <= imm < fs + 8 and off != fs:
                p(f"    branch from 0x{off:06x} ({ins.mnemonic}) -> 0x{imm:06x}")

    # --- global warning classification ---
    p("\n## classification of all warning-ish functions")
    for fs in sorted(by_func, key=lambda x: (x is None, x or 0)):
        if fs is None:
            continue
        entries = by_func[fs]
        roles = sorted({e[2].split(":")[0] for e in entries})
        p(f"  func 0x{fs:06x} roles={roles} nrefs={len(entries)}")
        for ref_off, tgt, note in sorted(entries):
            p(f"      @0x{ref_off:06x} {note}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[written] {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
