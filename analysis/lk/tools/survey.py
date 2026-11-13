# -*- coding: utf-8 -*-
"""Survey MTK LK samples: header, warning strings, payload diffs between samples.

Usage: python analysis/lk/tools/survey.py
Writes nothing; prints an evidence dump used for analysis/lk/lk_a64_analysis.md.
"""
from __future__ import annotations

import hashlib
import struct
from pathlib import Path

SAMPLES = Path("analysis/lk/samples")

# Every warning/state-related token the task asks about, plus guards that must NOT change.
WARN_KEYS = [
    b"Orange State",
    b"Red State",
    b"orange",
    b"Orange",
    b"red state",
    b"Your device has been unlocked",
    b"Your device has failed verification",
    b"dm-verity",
    b"dm_verity",
    b"dm-verity error",
    b"failed verification",
    b"will boot in",
    b"will reboot in",
    b"5 seconds",
    b"corrupt",
    b"verified boot",
    b"boot state",
    b"Boot state",
    b"boot_state",
]
GUARD_KEYS = [
    b"androidboot.verifiedbootstate",
    b"androidboot.veritymode",
    b"androidboot.atm",
    b"androidboot.",
    b"verifiedbootstate",
    b"veritymode",
    b"avb",
    b"AVB",
    b"vbmeta",
    b"lk",
]


def parse(path: Path):
    data = path.read_bytes()
    magic, size = struct.unpack_from("<II", data, 0)
    name = data[8:20].split(b"\x00", 1)[0].decode("latin-1")
    ext = struct.unpack_from("<I", data, 0x30)[0]
    hdr = data[:0x200] if magic == 0x58881688 and 0x200 + size <= len(data) else None
    payload = data[0x200:0x200 + size] if hdr else data
    return {
        "path": path,
        "data": data,
        "magic": magic,
        "size": size,
        "name": name,
        "ext": ext,
        "payload": payload,
        "payload_off": 0x200 if hdr else 0,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def find_all(hay: bytes, needle: bytes, limit: int = 12) -> list[int]:
    out, start = [], 0
    while len(out) < limit:
        i = hay.find(needle, start)
        if i < 0:
            break
        out.append(i)
        start = i + 1
    return out


def diff_ranges(a: bytes, b: bytes, min_len: int = 1, max_show: int = 40):
    ranges = []
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            # extend over equal bytes <= 3 to merge nearly-contiguous diffs
            while j + 4 <= n and a[j:j + 4] == b[j:j + 4]:
                k = j
                while k < n and a[k] == b[k]:
                    k += 1
                if k - j <= 4:
                    j = k
                else:
                    break
            ranges.append((i, j))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        ranges.append((n, max(len(a), len(b))))
    return ranges


def main() -> int:
    files = sorted(SAMPLES.glob("*.bin"))
    infos = {p.name: parse(p) for p in files}
    for name, inf in infos.items():
        print(f"\n=== {name}: file={len(inf['data'])} sha256={inf['sha256']}")
        print(f"    magic=0x{inf['magic']:08x} size={inf['size']} ({inf['size']}, 0x{inf['size']:x}) name={inf['name']!r} ext=0x{inf['ext']:08x} payload_off=0x{inf['payload_off']:x} payload_len={len(inf['payload'])}")

    base = infos.get("lk_a.bin")
    if base is None:
        print("no lk_a.bin")
        return 1
    pay = base["payload"]
    print("\n=== warning strings in lk_a payload (payload offsets; file = +0x200) ===")
    for key in WARN_KEYS:
        hits = find_all(pay, key)
        if hits:
            for h in hits:
                print(f"  {key.decode('latin-1')!r:42s} payload 0x{h:06x}  file 0x{h + 0x200:06x}  ctx={pay[max(0,h-8):h+len(key)+8]!r}")
    print("\n=== guard strings (must NOT be touched) in lk_a payload ===")
    for key in GUARD_KEYS:
        hits = find_all(pay, key, limit=6)
        if hits:
            print(f"  {key.decode('latin-1')!r:38s} {[hex(h) for h in hits]}")

    for other in ("lk_b.bin", "lk_a.unlocked.bin"):
        if other not in infos:
            continue
        o = infos[other]
        print(f"\n=== diff lk_a.bin payload vs {other} payload ===")
        rs = diff_ranges(pay, o["payload"])
        print(f"  total {len(rs)} changed range(s); sizes: a={len(pay)} b={len(o['payload'])}")
        for (s, e) in rs[:60]:
            print(f"  payload 0x{s:06x}..0x{e:06x} (len {e - s})  file 0x{s + 0x200:06x}")
            print(f"    A: {pay[s:e].hex()[:120]}")
            print(f"    B: {o['payload'][s:e].hex()[:120]}")
        # whole-file header compare
        print(f"  header identical (0x200): {base['data'][:0x200] == o['data'][:0x200]}")
        print(f"  tail identical: {base['data'][0x200 + base['size']:] == o['data'][0x200 + o['size']:]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
