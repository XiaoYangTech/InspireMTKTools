#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate / run the generic MTK LK warning-patch detector.

Pure Python + capstone.  Loads `analysis/lk/rules_lk.json`, parses an MTK `lk.img`
/ `lk.bin` (512-byte MTK header + payload) or a raw LK payload, then:

  * ARM32-Thumb samples: runs every `kind=signature` rule (exact or masked needle
    search) and validates the patch bytes.
  * AArch64 samples: runs the generic heuristic detector — ADRP+ADD string
    references -> enclosing function prologue (backwards scan) -> classification
    by warning-string set + state cmp chain + delay MOVZ/MOVK constants.  No
    fixed offsets are used.

For every detected patch site it prints:
  detected payload/file offset, evidence (string refs / cmp chain / delay count /
  callers), patch-safety checks (branches or data refs into the 8 overwritten
  bytes), and the before/after disassembly.

Usage:
  python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin
  python analysis/lk/validate_detector.py --all
  python analysis/lk/validate_detector.py analysis/lk/samples/lk_a.bin --dump
  python analysis/lk/validate_detector.py --all --shift 0x1000
  python analysis/lk/validate_detector.py --synthetic-arm32

Exit code: 0 iff every expected finding was found (and all patch-safety checks
passed), 1 otherwise (fail-safe).  No file is ever written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_ARCH_ARM64, CS_MODE_ARM, CS_MODE_LITTLE_ENDIAN, CS_MODE_THUMB, Cs
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_MEM

HERE = Path(__file__).resolve().parent
RULES_PATH = HERE / "rules_lk.json"

MTK_MAGIC = 0x58881688
MTK_EXT_MAGIC = 0x58891689
MTK_HEADER_SIZE = 0x200

A64_RET = bytes.fromhex("c0035fd6")
THUMB_BX_LR = bytes.fromhex("7047")
ARM_BX_LR = bytes.fromhex("1eff2fe1")

# Per-sample expected findings (payload offsets).  Only used to score PASS/FAIL
# for the real samples; the detector itself never uses these.
EXPECTED = {
    "lk_a.bin": {"orange_state": 0x4D728, "dm_verity": 0x4CA58},
    "lk_b.bin": {"orange_state": 0x4D728, "dm_verity": 0x4CA58},
    "lk_a.unlocked.bin": {"orange_state": 0x4D728, "dm_verity": 0x4CA58},
}

TAG = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]", "INFO": "[INFO]"}


def load_rules() -> dict:
    with open(RULES_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


class Image:
    """One LK image: raw file bytes + parsed payload.

    Verified layout (lk_a/lk_b/lk_a.unlocked):
      0x000  u32 magic = 0x58881688
      0x004  u32 size  = payload length (0xD47D0 = 870352)
      0x008  char[32] name = "lk"
      0x030  u32 ext magic = 0x58891689
      0x200  payload[size]
      payload end = 0x200 + size = 0xD49D0; the next sub-image header
      ("cert1") starts exactly there -> `size` is a payload length, not an end offset.
    The 4 MB file is a packed MTK lk partition holding several 0x200-header
    sub-images (lk, cert1, cert2, bl2_ext, aee, lk_main_dtb) 16-byte aligned;
    it is NOT "payload + zero padding".
    """

    def __init__(self, data: bytes, name: str = "<memory>"):
        self.name = name
        self.data = data
        self.file_size = len(data)
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.container = False
        self.payload = data
        self.payload_off = 0
        self.header = None
        self.declared_size = len(data)
        self.mtk_name = ""
        self.ext_magic = 0
        self.following_images: list[tuple[int, int, str]] = []
        if len(data) >= MTK_HEADER_SIZE:
            magic, size = struct.unpack_from("<II", data, 0)
            if magic == MTK_MAGIC and MTK_HEADER_SIZE + size <= len(data):
                self.container = True
                self.payload = data[MTK_HEADER_SIZE:MTK_HEADER_SIZE + size]
                self.payload_off = MTK_HEADER_SIZE
                self.header = data[:MTK_HEADER_SIZE]
                self.declared_size = size
                self.mtk_name = data[8:8 + 32].split(b"\x00", 1)[0].decode("latin-1")
                self.ext_magic = struct.unpack_from("<I", data, 0x30)[0]
                # enumerate the packed sub-images that follow this one
                off = MTK_HEADER_SIZE + size
                while off + 12 <= len(data) and len(self.following_images) < 16:
                    mg, sz = struct.unpack_from("<II", data, off)
                    if mg != MTK_MAGIC:
                        break
                    nm = data[off + 8:off + 40].split(b"\x00", 1)[0].decode("latin-1")
                    self.following_images.append((off, sz, nm))
                    end = off + MTK_HEADER_SIZE + sz
                    off = (end + 0xF) & ~0xF  # sub-images are 16-byte aligned
        self.payload_size = len(self.payload)

    # helpers ---------------------------------------------------------------
    def p2f(self, p: int) -> int:
        return p + self.payload_off

    def describe(self) -> list[str]:
        out = [
            f"file size        : {self.file_size} ({self.file_size:#x})",
            f"sha256(file)     : {self.sha256}",
            f"MTK container    : {'yes' if self.container else 'no (raw payload)'}"
            + (f"  name={self.mtk_name!r} size(payload len)={self.declared_size}"
               f" ({self.declared_size:#x}) ext_magic={self.ext_magic:#010x}" if self.container else ""),
            f"payload          : offset {self.payload_off:#x}, size {self.payload_size} ({self.payload_size:#x})",
        ]
        if self.container and self.following_images:
            out.append("packed sub-images: " + ", ".join(
                f"{nm}@{off:#x}(size {sz})" for off, sz, nm in self.following_images[:6])
                + (f" ... ({len(self.following_images)} total)" if len(self.following_images) > 6 else ""))
        return out


def parse_image(path: Path) -> Image:
    return Image(path.read_bytes(), name=path.name)


# ---------------------------------------------------------------------------
# generic helpers
# ---------------------------------------------------------------------------
def find_all(hay: bytes, needle: bytes, limit: int = 64) -> list[int]:
    out, start = [], 0
    while len(out) < limit:
        i = hay.find(needle, start)
        if i < 0:
            break
        out.append(i)
        start = i + 1
    return out


def find_masked(hay: bytes, needle: bytes, mask: bytes | None, limit: int = 64) -> list[int]:
    """Byte search; mask byte 0xff = must match, 0x00 = wildcard (None = exact)."""
    if mask is None:
        return find_all(hay, needle, limit)
    if len(mask) != len(needle):
        raise ValueError("needle_mask length must equal needle length")
    fixed = [(i, b) for i, b in enumerate(needle) if mask[i]]
    first_i, first_b = fixed[0]
    out, start = [], 0
    n = len(hay) - len(needle)
    while len(out) < limit:
        i = hay.find(bytes([first_b]), start)
        if i < 0 or i > n:
            break
        base = i - first_i
        if base >= 0 and all(hay[base + j] == b for j, b in fixed):
            out.append(base)
        start = i + 1
    return out


def cstring(payload: bytes, off: int, maxlen: int = 120) -> str | None:
    if not (0 <= off < len(payload)):
        return None
    end = payload.find(b"\x00", off, min(len(payload), off + maxlen))
    if end < 0:
        return None
    raw = payload[off:end]
    if not raw:
        return ""
    if any(b < 9 or (13 < b < 32) or b > 126 for b in raw):
        return None
    return raw.decode("latin-1")


def detect_arch(payload: bytes) -> tuple[str, dict]:
    """AArch64 vs ARM32-Thumb via RET/BX-LR counts + a capstone prologue sample."""
    counts = {
        "aarch64_ret": payload.count(A64_RET),
        "thumb_bx_lr": payload.count(THUMB_BX_LR),
        "arm_bx_lr": payload.count(ARM_BX_LR),
    }
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    prologues = 0
    sample_end = min(len(payload), 0x20000)  # first 128 KiB is enough for arch vote
    for off in range(0, sample_end - 4, 4):
        got = list(md.disasm(payload[off:off + 4], off))
        if not got:
            continue
        ins = got[0]
        if ins.mnemonic == "stp" and ins.op_str.startswith("x29, x30, [sp"):
            prologues += 1
    counts["stp_x29_x30"] = prologues
    if counts["aarch64_ret"] > 0 and counts["aarch64_ret"] >= counts["thumb_bx_lr"] and prologues >= 8:
        return "aarch64", counts
    if counts["thumb_bx_lr"] > 0 or counts["arm_bx_lr"] > 0:
        return "arm32-thumb", counts
    return "unknown", counts


# ---------------------------------------------------------------------------
# ARM32-Thumb signature rules
# ---------------------------------------------------------------------------
def validate_arm32(img: Image, rules: dict) -> dict:
    """Run every signature rule whose arch is arm32-thumb."""
    payload = img.payload
    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    results = []
    for rule in rules["rules"]:
        if rule["arch"] != "arm32-thumb":
            continue
        needle = bytes.fromhex(rule["needle"])
        mask = bytes.fromhex(rule["needle_mask"]) if rule.get("needle_mask") else None
        hits = find_masked(payload, needle, mask)
        patch = bytes.fromhex(rule["patch"])
        poff = int(rule.get("patch_offset", 0))
        # Exact needles must be unique (fail-safe).  Masked "generic" needles
        # intentionally cover several literal-pool variants, so 1..2 hits is OK;
        # real images normally contain exactly one of them.
        if mask is None:
            expected_range = (1, 1)
        else:
            expected_range = (1, 2)
        n = len(hits)
        entry = {
            "rule": rule["id"],
            "category": rule["category"],
            "kind": rule["kind"],
            "confidence": rule["confidence"],
            "hits": hits,
            "expected_hits": expected_range,
            "offsets_payload": [h + poff for h in hits],
            "offsets_file": [img.p2f(h + poff) for h in hits],
            "status": "PASS" if expected_range[0] <= n <= expected_range[1] else "FAIL",
            "before": None,
            "after": None,
            "disasm_before": [],
            "disasm_after": [],
        }
        if hits:
            start = hits[0] + poff
            before = payload[start:start + len(patch)]
            entry["before"] = before
            entry["after"] = patch
            for ins in md.disasm(before, img.p2f(start)):
                entry["disasm_before"].append(f"{ins.mnemonic} {ins.op_str}")
            # patch into a copy for the after-disassembly (never written to disk)
            tmp = bytearray(payload)
            tmp[start:start + len(patch)] = patch
            for ins in md.disasm(bytes(tmp[start:start + max(len(patch) + 2, 6)]), img.p2f(start)):
                entry["disasm_after"].append(f"{ins.mnemonic} {ins.op_str}")
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# AArch64 heuristic detector (no fixed offsets)
# ---------------------------------------------------------------------------
class A64Detector:
    def __init__(self, img: Image, params: dict):
        self.img = img
        self.P = img.payload
        self.N = len(self.P)
        self.params = params
        self.patch_bytes = bytes.fromhex(params.get("patch", "e0031f2ac0035fd6"))
        self.patched_entries: set[int] = set()
        self.md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        self.md.detail = True
        self._cache: dict[int, object] = {}

    # instruction access -----------------------------------------------------
    def dec(self, off: int):
        c = self._cache
        if off in c:
            return c[off]
        ins = None
        if 0 <= off and off + 4 <= self.N and off % 4 == 0:
            got = list(self.md.disasm(self.P[off:off + 4], off))
            if got:
                ins = got[0]
        c[off] = ins
        return ins

    # string references ------------------------------------------------------
    def collect_refs(self) -> list[dict]:
        """Every ADRP+ADD(reg,reg,#imm) whose computed target starts a C string."""
        refs = []
        for off in range(0, self.N - 4, 4):
            ins = self.dec(off)
            if ins is None or ins.mnemonic != "adrp":
                continue
            if len(ins.operands) < 2 or ins.operands[1].type != ARM64_OP_IMM:
                continue
            page = ins.operands[1].imm
            reg = ins.operands[0].reg
            for k in range(1, int(self.params.get("ref_window_instructions", 3)) + 1):
                n2 = self.dec(off + 4 * k)
                if n2 is None:
                    break
                if n2.mnemonic == "add" and len(n2.operands) >= 3:
                    try:
                        if n2.operands[1].reg != reg or n2.operands[2].type != ARM64_OP_IMM:
                            continue
                    except Exception:
                        continue
                    tgt = page + n2.operands[2].imm
                    text = cstring(self.P, tgt)
                    if text:
                        refs.append({"ref_off": off, "target": tgt, "text": text})
                    break
        return refs

    # function boundaries ----------------------------------------------------
    def func_start(self, off: int) -> int | None:
        window = int(self.params.get("max_backtrack", 1280))
        for cand in range(off, max(0, off - window) - 1, -4):
            # an already-patched function entry is a legitimate boundary too
            if cand in self.patched_entries:
                return cand
            ins = self.dec(cand)
            if ins is None:
                continue
            s = ins.op_str
            if ins.mnemonic == "stp" and s.startswith("x29, x30, [sp,"):
                return cand
            if ins.mnemonic == "sub" and s.startswith("sp, sp,"):
                return cand
        return None

    def func_end(self, start: int) -> int:
        prev = None
        for off in range(start, min(self.N - 4, start + 0x800), 4):
            ins = self.dec(off)
            if ins is None:
                prev = None
                continue
            if ins.mnemonic == "ret" and prev is not None and prev.mnemonic == "ldp" \
                    and prev.op_str.startswith("x29, x30, [sp],"):
                return off + 4
            prev = ins
        for off in range(start, min(self.N - 4, start + 0x800), 4):
            ins = self.dec(off)
            if ins is not None and ins.mnemonic == "ret":
                return off + 4
        return min(self.N, start + 0x400)

    # evidence helpers -------------------------------------------------------
    def cmp_chain_hits(self, start: int, end: int) -> list[int]:
        values = set(self.params["state_cmp_chain"]["imm_values"])
        hits = []
        for off in range(start, end, 4):
            ins = self.dec(off)
            if ins is None or ins.mnemonic != "cmp" or len(ins.operands) < 2:
                continue
            op = ins.operands[1]
            if op.type != ARM64_OP_IMM:
                continue
            if op.imm in values:
                hits.append(op.imm)
        return hits

    def count_bytes(self, start: int, end: int, pattern: bytes) -> int:
        n, i = 0, start
        while True:
            j = self.P.find(pattern, i, end)
            if j < 0:
                return n
            n += 1
            i = j + 1

    def callers(self, entry: int) -> list[int]:
        out = []
        for off in range(0, self.N - 4, 4):
            ins = self.dec(off)
            if ins is None or ins.mnemonic not in ("bl", "b", "cbz", "cbnz", "tbz", "tbnz"):
                continue
            for op in ins.operands:
                if op.type == ARM64_OP_IMM:
                    if op.imm == entry:
                        out.append(off)
                    break
        return out

    def _tagged_base(self) -> int | None:
        """Infer the LK link base used by `0xffff0000_<u32 link-addr>` string pointers.

        LK stores some constant strings (e.g. the boot-state-name table at
        payload 0xacc90) as 64-bit words 0xffff0000 + (link_base + payload_off).
        The base is recovered as the page-aligned value that puts almost every
        such word inside [0, payload_size).
        """
        lows = []
        for i in range(0, self.N - 8, 4):
            hi = struct.unpack_from("<I", self.P, i + 4)[0]
            if hi == 0xFFFF0000:
                lows.append(struct.unpack_from("<I", self.P, i)[0])
        if len(lows) < 8:
            return None
        # Some 0xffff0000 high words are sentinel/`0xffff0000`-tagged values that
        # are not link pointers, so score candidate bases by how many 64-bit words
        # they place inside the payload and take the best absolute count.
        best = None
        for base in sorted({low & ~0xFFF for low in lows} | {low for low in lows if low % 0x1000 == 0}):
            if not (0x40000000 <= base <= 0x60000000):
                continue
            inside = sum(1 for low in lows if 0 <= low - base < self.N)
            if best is None or inside > best[0]:
                best = (inside, base)
        if best is None or best[0] < 16:
            return None
        return best[1]

    def safety(self, entry: int) -> dict:
        """Check for anything else that references the 8 bytes we overwrite.

        A `bl entry` caller is expected and harmless; we flag only references
        that land *inside* [entry, entry+8) or that treat the entry as data.
        """
        lo, hi = entry, entry + 8
        branches = []
        data_refs = []
        for off in range(0, self.N - 4, 4):
            ins = self.dec(off)
            if ins is None:
                continue
            if ins.mnemonic in ("b", "bl", "cbz", "cbnz", "tbz", "tbnz", "adr"):
                for op in ins.operands:
                    if op.type == ARM64_OP_IMM:
                        if entry < op.imm < hi:
                            branches.append((off, op.imm, ins.mnemonic))
                        break
            if ins.mnemonic == "adrp" and len(ins.operands) >= 2 and ins.operands[1].type == ARM64_OP_IMM:
                page = ins.operands[1].imm
                reg = ins.operands[0].reg
                for k in (1, 2, 3):
                    n2 = self.dec(off + 4 * k)
                    if n2 is None:
                        break
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
                            data_refs.append((off, addr, n2.mnemonic))
                        break
            if ins.mnemonic == "ldr" and len(ins.operands) >= 2 and ins.operands[1].type == ARM64_OP_MEM:
                mem = ins.operands[1].mem
                if mem.base == 0 and lo <= mem.disp < hi:  # literal pool load
                    data_refs.append((off, mem.disp, "ldr-literal"))
            if ins.mnemonic == "adr" and len(ins.operands) >= 2:
                try:
                    if lo <= ins.operands[1].imm < hi:
                        data_refs.append((off, ins.operands[1].imm, "adr"))
                except Exception:
                    pass
        # absolute pointer words: plain u32/u64, or LK 0xffff0000-tagged link pointer
        base = self._tagged_base()
        ptr_refs = []
        for i in range(0, self.N - 4, 4):
            u32 = struct.unpack_from("<I", self.P, i)[0]
            if u32 == entry:
                ptr_refs.append((i, "u32"))
            if i + 8 <= self.N:
                u64 = struct.unpack_from("<Q", self.P, i)[0]
                if u64 == entry:
                    ptr_refs.append((i, "u64"))
                if base is not None and u64 >> 32 == 0xFFFF0000 and (u64 & 0xFFFFFFFF) == base + entry:
                    ptr_refs.append((i, f"tagged(link={u64 & 0xFFFFFFFF:#x}, base={base:#x})"))
        return {"branches": branches, "data_refs": data_refs, "pointer_refs": ptr_refs,
                "tagged_base": base}

    # main detection ---------------------------------------------------------
    def detect(self) -> list[dict]:
        refs = self.collect_refs()
        wstrings = self.params["warning_strings"]
        disp_keys = wstrings["orange_state"] + wstrings["red_state"]
        dm_keys = wstrings["dm_verity"]

        # Pre-compute entries that already carry the 8-byte patch.  Only accept a
        # patch-byte match as an entry if >=2 warning-string refs sit in the next
        # 0x400 bytes (prevents random data matches from hijacking backtracking).
        all_keys = disp_keys + dm_keys
        warn_ref_sites = [r["ref_off"] for r in refs if any(k in r["text"] for k in all_keys)]
        self.patched_entries = set()
        start = 0
        while True:
            pos = self.P.find(self.patch_bytes, start)
            if pos < 0:
                break
            if pos % 4 == 0:
                n = sum(1 for a in warn_ref_sites if pos < a < pos + 0x400)
                if n >= 2:
                    self.patched_entries.add(pos)
            start = pos + 1

        groups: dict[int, list[dict]] = {}
        for r in refs:
            fs = self.func_start(r["ref_off"])
            if fs is None:
                continue
            g = groups.setdefault(fs, [])
            g.append(r)

        findings = []
        for fs in sorted(groups):
            grefs = groups[fs]
            texts = [r["text"] for r in grefs]
            disp_hit_keys = sorted({k for k in disp_keys for t in texts if k in t})
            dm_hit_keys = sorted({k for k in dm_keys for t in texts if k in t})
            if not disp_hit_keys and not dm_hit_keys:
                continue
            fe = self.func_end(fs)
            cmp_hits = self.cmp_chain_hits(fs, fe)
            delay_specs = self.params.get("delay_constants", {})
            delay5 = 0
            delay100 = 0
            for name, spec in delay_specs.items():
                if isinstance(spec, dict) and spec.get("bytes"):
                    pat = bytes.fromhex(spec["bytes"])
                    if "5s" in name or name == "dispatcher_5s":
                        delay5 = self.count_bytes(fs, fe, pat)
                    else:
                        delay100 = max(delay100, self.count_bytes(fs, fe, pat))
            core = [k for k in ("Orange State", "Red State") if k in disp_hit_keys]
            category = None
            confidence = 0.0
            if core and (len(disp_hit_keys) >= 2 or len(cmp_hits) >= 2 or delay5 >= 1):
                category = "orange_state"
                strong = len(disp_hit_keys) >= 2 and len(cmp_hits) >= 2 and delay5 >= 1
                confidence = 0.95 if strong else 0.85
            elif len(dm_hit_keys) >= 2 or (len(dm_hit_keys) >= 1 and delay100 >= 10):
                if not core:
                    category = "dm_verity"
                    confidence = 0.95 if len(dm_hit_keys) >= 2 and delay100 >= 10 else 0.85
            if category is None:
                continue
            entry = fs
            before = self.P[entry:entry + 8]
            patch = bytes.fromhex(self.params["patch"])
            tmp = bytearray(self.P)
            tmp[entry:entry + len(patch)] = patch
            after = bytes(tmp[entry:entry + 8])
            md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
            dis_before = [f"{i.mnemonic} {i.op_str}" for i in md.disasm(before, self.img.p2f(entry))]
            dis_after = [f"{i.mnemonic} {i.op_str}" for i in md.disasm(after, self.img.p2f(entry))]
            guard_pats = self.params.get("guard_do_not_touch", [])
            guard_before = {g: self.P.count(g.encode()) for g in guard_pats}
            guard_after = {g: bytes(tmp).count(g.encode()) for g in guard_pats}
            findings.append({
                "category": category,
                "func_start": entry,
                "func_end": fe,
                "func_start_file": self.img.p2f(entry),
                "confidence": confidence,
                "warning_keys": sorted(set(disp_hit_keys + dm_hit_keys)),
                "string_refs": [{"ref": r["ref_off"], "target": r["target"], "text": r["text"]}
                                for r in sorted(grefs, key=lambda x: x["ref_off"])],
                "cmp_hits": cmp_hits,
                "delay5": delay5,
                "delay100": delay100,
                "callers": self.callers(entry),
                "safety": self.safety(entry),
                "before": before,
                "after": after,
                "disasm_before": dis_before,
                "disasm_after": dis_after,
                "guard_before": guard_before,
                "guard_after": guard_after,
                "guard_ok": guard_before == guard_after,
                "already_patched": self.P[entry:entry + len(patch)] == patch,
                "function_disasm": None,
            })
        return findings

    def _dump(self, lo: int, hi: int) -> list[str]:
        out = []
        for off in range(lo, hi, 4):
            ins = self.dec(off)
            out.append(f"  {off:06x}: {ins.mnemonic:<8} {ins.op_str}" if ins
                       else f"  {off:06x}: <data {self.P[off:off+4].hex()}>")
        return out


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def fmt_hex(b: bytes) -> str:
    return " ".join(f"{x:02x}" for x in b)


def report_arm32(results: list[dict]) -> int:
    npass = 0
    print("--- ARM32-Thumb signature rules ---")
    for r in results:
        ok = r["status"] == "PASS"
        npass += ok
        print(f"{TAG['PASS'] if ok else TAG['FAIL']} {r['rule']:<42s} "
              f"cat={r['category']:<12s} hits={len(r['hits'])}"
              f" (expected {r['expected_hits'][0]}-{r['expected_hits'][1]}) conf={r['confidence']}")
        if r["hits"]:
            for p, f in zip(r["offsets_payload"], r["offsets_file"]):
                print(f"        payload 0x{p:06x}  file 0x{f:06x}")
            if ok:
                print(f"        before: {fmt_hex(r['before'])}  ->  {' ; '.join(r['disasm_before'])}")
                print(f"        after : {fmt_hex(r['after'])}  ->  {' ; '.join(r['disasm_after'])}")
        else:
            print("        needle not found")
    print(f"  ARM32 summary: {npass}/{len(results)} rules PASS")
    return npass


def report_a64(img: Image, findings: list[dict], params: dict, rules: dict, verbose: bool) -> int:
    print("\n--- AArch64 heuristic rules ---")
    npass = 0
    by_cat = {f["category"]: f for f in findings}
    for rule in rules["rules"]:
        if rule["arch"] != "aarch64":
            continue
        cat = rule["category"]
        f = by_cat.get(cat)
        ok = f is not None
        npass += ok
        print(f"\n{TAG['PASS'] if ok else TAG['FAIL']} {rule['id']:<42s} cat={cat:<12s}")
        if not ok:
            print("        no function matched this category")
            continue
        print(f"        function : payload 0x{f['func_start']:06x}..0x{f['func_end']:06x}  "
              f"(file 0x{f['func_start_file']:06x}..0x{img.p2f(f['func_end']):06x})")
        print(f"        confidence: {f['confidence']:.2f}  warning keys: {', '.join(f['warning_keys'])}")
        for sr in f["string_refs"]:
            print(f"        ref payload+0x{sr['ref']:06x} -> string 0x{sr['target']:06x} {sr['text'][:64]!r}")
        print(f"        state cmp chain: {f['cmp_hits']}   delay(5,000,000us) occurrences: {f['delay5']}"
              f"   delay(100,000us) occurrences: {f['delay100']}")
        print(f"        callers (bl/b): {[hex(c) for c in f['callers']] or 'none'}")
        sfy = f["safety"]
        print(f"        safety: branches into [entry,entry+8): {[(hex(a), hex(b), m) for a, b, m in sfy['branches']] or 'none'}")
        print(f"                data refs into [entry,entry+8): {[(hex(a), hex(b), m) for a, b, m in sfy['data_refs']] or 'none'}")
        print(f"                pointer words to entry        : {[(hex(a), m) for a, m in sfy['pointer_refs']] or 'none'}"
              f"   (inferred tagged link base: {hex(sfy['tagged_base']) if sfy.get('tagged_base') else 'n/a'})")
        print(f"        guard strings (androidboot.*/verifiedbootstate/veritymode/atm/...) before/after patch: "
              f"{'unchanged' if f['guard_ok'] else 'CHANGED!'}")
        print(f"        before : {fmt_hex(f['before'])}  ->  {' ; '.join(f['disasm_before'])}")
        print(f"        after  : {fmt_hex(f['after'])}  ->  {' ; '.join(f['disasm_after'])}")
        print(f"        patch  : file offset 0x{f['func_start_file']:06x}, 8 bytes, file size unchanged")
        if f["already_patched"]:
            print("        note   : entry already equals the patch bytes (already patched image)")
        if verbose:
            print("        function disassembly:")
            for line in f.get("function_disasm") or []:
                print("        " + line)
    return npass


def safety_ok(findings: list[dict]) -> bool:
    for f in findings:
        s = f["safety"]
        if s["branches"] or s["data_refs"] or s["pointer_refs"]:
            return False
        if not f.get("guard_ok", True):
            return False
    return True


def run_sample(path: Path, verbose: bool = False, arch_override: str | None = None) -> int:
    rules = load_rules()
    img = parse_image(path)
    print(f"\n=== sample: {path.name} ===")
    for line in img.describe():
        print("  " + line)
    arch, counts = detect_arch(img.payload) if arch_override is None else (arch_override, {})
    print(f"  arch detected: {arch}  {counts}")

    expected = EXPECTED.get(path.name)
    failures = 0
    if arch == "arm32-thumb":
        results = validate_arm32(img, rules)
        npass = report_arm32(results)
        failures += (len(results) - npass)
        if expected:
            print(f"\n{TAG['FAIL']} expected AArch64 sample name {path.name} but arch detected {arch}")
            failures += 1
        else:
            print("\n--- expected offsets ---")
            print("  (no expected map for this sample; acceptance = failed rules == 0)")
            print(f"RESULT: {'PASS' if failures == 0 else 'FAIL'} ({npass}/{len(results)} rules PASS)")
        return 0 if failures == 0 else 1

    if arch != "aarch64":
        print(f"{TAG['FAIL']} unknown architecture; fail-safe, no patch offered")
        return 1

    det = A64Detector(img, rules["aarch64_heuristic"])
    findings = det.detect()
    if verbose:
        for f in findings:
            f["function_disasm"] = det._dump(f["func_start"], f["func_end"])
    npass_a64 = report_a64(img, findings, rules["aarch64_heuristic"], rules, verbose)

    print("\n--- ARM32-Thumb signature rules ---")
    print(f"{TAG['SKIP']} all 13 ARM32 signature rules (arch mismatch: sample is aarch64)")

    print("\n--- expected offsets ---")
    if expected:
        for cat, off in expected.items():
            match = [f for f in findings if f["category"] == cat and f["func_start"] == off]
            if match:
                print(f"{TAG['PASS']} {cat:<12s} payload 0x{off:06x} (file 0x{img.p2f(off):06x})")
            else:
                got = [f"0x{f['func_start']:06x}" for f in findings if f["category"] == cat]
                print(f"{TAG['FAIL']} {cat:<12s} payload 0x{off:06x} NOT found (got {got or 'none'})")
                failures += 1
        if not safety_ok(findings):
            print(f"{TAG['FAIL']} patch-safety check failed (there is another reference into the overwritten bytes)")
            failures += 1
        if findings and all(f["already_patched"] for f in findings):
            print(f"{TAG['INFO']} image already carries the patch at every detected site (already-patched)")
        ok = failures == 0
        print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(findings)} finding(s), "
              f"{len(expected)} expected offset(s), safety {'OK' if safety_ok(findings) else 'FAIL'})")
        return 0 if ok else 1

    # unknown sample: generic acceptance = at least one high-confidence finding, safe
    ok = bool(findings) and safety_ok(findings) and max(f["confidence"] for f in findings) >= 0.8
    print(f"  (no expected map for this sample; acceptance = >=1 high-confidence safe finding)")
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({len(findings)} finding(s), "
          f"safety {'OK' if safety_ok(findings) else 'FAIL'})")
    return 0 if ok else 1


def shift_test(path: Path, shift: int) -> int:
    """Offset-invariance test: prepend `shift` zero bytes (page-aligned) to the payload.

    Both code and strings move by the same amount, so every PC-relative
    ADRP+ADD reference still has the same delta — exactly what happens when a
    linker places the same code at a different offset.  The detector must find
    the same functions at (expected + shift) with no fixed-offset assumptions.
    """
    if shift % 0x1000:
        print(f"{TAG['FAIL']} --shift must be a multiple of 0x1000 (page aligned), got {shift:#x}")
        return 1
    img = parse_image(path)
    # Map the payload at a new origin: code and strings both move by `shift`, so
    # all PC-relative ADRP+ADD deltas are unchanged (different link placement).
    shifted = Image(b"\x00" * 16)
    shifted.payload = b"\x00" * shift + img.payload
    shifted.payload_size = len(shifted.payload)
    shifted.payload_off = 0
    rules = load_rules()
    arch, counts = detect_arch(shifted.payload)
    if arch != "aarch64":
        print(f"{TAG['FAIL']} {path.name}: shifted variant detected as {arch}")
        return 1
    det = A64Detector(shifted, rules["aarch64_heuristic"])
    findings = det.detect()
    expected = EXPECTED.get(path.name)
    print(f"\n=== offset-invariance test: {path.name} payload shifted by {shift:#x} ===")
    if not expected:
        ok = bool(findings)
        print(f"  generic shifted acceptance: {'PASS' if ok else 'FAIL'} ({len(findings)} findings)")
        return 0 if ok else 1
    failures = 0
    for cat, off in expected.items():
        want = off + shift
        hit = [f for f in findings if f["category"] == cat and f["func_start"] == want]
        print(f"  {TAG['PASS'] if hit else TAG['FAIL']} {cat:<12s} expected payload 0x{want:06x}"
              f" (orig 0x{off:06x}+0x{shift:x}) got "
              f"{[hex(f['func_start']) for f in findings if f['category'] == cat] or 'none'}")
        if not hit:
            failures += 1
    if not safety_ok(findings):
        print(f"  {TAG['FAIL']} safety check failed on shifted variant")
        failures += 1
    print(f"  shifted RESULT: {'PASS' if failures == 0 else 'FAIL'}")
    return 0 if failures == 0 else 1


def synthetic_arm32() -> int:
    """Build an in-memory synthetic ARM32-Thumb LK container embedding every needle.

    Used to validate the ARM32 rule DB (the real samples are AArch64 only).
    Each canonical needle is embedded once, page/padding separated.
    """
    rules = load_rules()
    exact = [r for r in rules["rules"]
             if r["arch"] == "arm32-thumb" and r["kind"] == "signature"
             and "pushpop" not in r["id"] and not r.get("needle_mask")]
    payload = bytearray(b"\x00" * 0x1000)  # data/strings area
    payload += b"Orange State\x00Red State\x00Your device will boot in 5 seconds\x00" + b"\x00" * 0x100
    payload += b"androidboot.verifiedbootstate=orange\x00androidboot.veritymode=green\x00" + b"\x00" * 0x100
    for rule in exact:
        payload += b"\x00" * 64
        payload += bytes.fromhex(rule["needle"]) + b"\x00" * 4
    payload += b"\x00" * (0x800 - (len(payload) % 0x800))
    # MTK header: `size` field is the PAYLOAD length (verified against real samples)
    header = bytearray(MTK_HEADER_SIZE)
    struct.pack_into("<II", header, 0, MTK_MAGIC, len(payload))
    header[8:8 + 3] = b"lk\x00"
    struct.pack_into("<I", header, 0x30, MTK_EXT_MAGIC)
    data = bytes(header) + bytes(payload)
    img = Image(data, name="<synthetic-arm32>")
    print("\n=== synthetic ARM32-Thumb LK container ===")
    for line in img.describe():
        print("  " + line)
    arch, counts = detect_arch(img.payload)
    print(f"  arch detected: {arch}  {counts}")
    results = validate_arm32(img, rules)
    npass = report_arm32(results)
    ok = npass == len(results) and arch == "arm32-thumb"
    print(f"RESULT: {'PASS' if ok else 'FAIL'} ({npass}/{len(results)} ARM32 rules PASS)")
    return 0 if ok else 1


def simulate_patch(path: Path) -> int:
    """Apply the AArch64 patch in memory, then rescan: must report already_patched
    at the expected entries, keep file size/header/payload-outside-lk unchanged,
    and disassemble the patched entries as `mov w0, wzr ; ret`."""
    rules = load_rules()
    img = parse_image(path)
    if not img.container:
        print(f"{TAG['FAIL']} {path.name}: not an MTK container, cannot simulate")
        return 1
    expected = EXPECTED.get(path.name)
    if not expected:
        print(f"{TAG['SKIP']} {path.name}: no expected map, nothing to simulate")
        return 0
    patch = bytes.fromhex(rules["patch"]["aarch64"])
    data = bytearray(img.data)
    for cat, poff in expected.items():
        foff = img.p2f(poff)
        data[foff:foff + 8] = patch
    changed_outside = sum(1 for i, (a, b) in enumerate(zip(data, img.data))
                          if a != b and not (img.payload_off <= i < img.payload_off + img.payload_size))
    patched = Image(bytes(data), name=path.name + "(patched)")
    det = A64Detector(patched, rules["aarch64_heuristic"])
    findings = det.detect()
    by_cat = {}
    for f in findings:
        by_cat.setdefault(f["category"], []).append(f)
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    print(f"\n=== simulate patch (in memory, no file written): {path.name} ===")
    failures = 0
    for cat, poff in expected.items():
        got = by_cat.get(cat, [])
        hit = [f for f in got if f["func_start"] == poff and f["already_patched"]]
        if hit:
            print(f"  {TAG['PASS']} {cat:<12s} payload 0x{poff:06x}: re-detected as ALREADY PATCHED")
            print(f"         disasm: {' ; '.join(f'{i.mnemonic} {i.op_str}' for i in md.disasm(bytes(data[img.p2f(poff):img.p2f(poff)+8]), img.p2f(poff)))}")
        else:
            print(f"  {TAG['FAIL']} {cat:<12s} payload 0x{poff:06x}: not re-detected as already-patched "
                  f"(got {[(hex(f['func_start']), f['already_patched']) for f in got] or 'none'})")
            failures += 1
    print(f"  file size unchanged: {len(data) == len(img.data)}   header(0x200) unchanged: "
          f"{bytes(data[:0x200]) == img.data[:0x200]}   changed bytes outside lk payload: {changed_outside}")
    if len(data) != len(img.data) or bytes(data[:0x200]) != img.data[:0x200] or changed_outside:
        failures += 1
    print(f"  simulate RESULT: {'PASS' if failures == 0 else 'FAIL'}")
    return 0 if failures == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Validate the generic MTK LK warning-patch detector")
    ap.add_argument("image", nargs="?", help="lk.img / lk.bin (or raw payload)")
    ap.add_argument("--all", action="store_true", help="run every sample found in analysis/lk/samples")
    ap.add_argument("--shift", type=lambda s: int(s, 0), default=None,
                    help="offset-invariance test: shift payload by N bytes (page-aligned)")
    ap.add_argument("--synthetic-arm32", action="store_true", help="self-test all ARM32 signature rules")
    ap.add_argument("--simulate-patch", action="store_true",
                    help="apply the patch in memory at the expected entries and verify already-patched rescan")
    ap.add_argument("--dump", action="store_true", help="print full function disassembly for AArch64 findings")
    ap.add_argument("--arch", choices=["aarch64", "arm32-thumb"], help="override architecture detection")
    args = ap.parse_args(argv)

    if args.synthetic_arm32:
        return synthetic_arm32()

    rc = 0
    if args.all or args.image is None:
        samples = sorted((HERE / "samples").glob("*.bin"))
        if not samples:
            print(f"no samples found in {HERE / 'samples'}")
            return 1
        for p in samples:
            rc |= run_sample(p, verbose=args.dump, arch_override=args.arch)
        if args.simulate_patch:
            for p in samples:
                rc |= simulate_patch(p)
        if args.shift is not None:
            for p in samples:
                rc |= shift_test(p, args.shift)
        print(f"\nOVERALL: {'PASS' if rc == 0 else 'FAIL'} ({len(samples)} sample(s))")
        return rc

    path = Path(args.image)
    if not path.is_file():
        print(f"not a file: {path}")
        return 1
    rc |= run_sample(path, verbose=args.dump, arch_override=args.arch)
    if args.simulate_patch:
        rc |= simulate_patch(path)
    if args.shift is not None:
        rc |= shift_test(path, args.shift)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
