#!/usr/bin/env python3
"""Independent oracle for MediaTek LOGO partition images (``logo.bin`` / ``lk.bin``).

This module is the QA *ground truth*: it parses the shipped real samples with its
own ``struct`` + ``zlib`` code and **never imports** ``logobuilder``.  The
rebuild's core library is expected to agree with every fact asserted here.

Facts asserted for the shipped ``logo.bin`` (from ``docs/spec_original.md`` and
``analysis/format/spec_bin.md``):

* 512-byte header, magic ``0x58881688``, tag ``LOGO``, dataLen@4 = 2,005,959,
  ``filesize - 512 == dataLen`` (no trailing bytes).
* payload @0x200: ``count=51``, second ``dataLen`` echo == header value,
  ``offsets[0] == 8 + 4*count == 212``, offsets strictly increasing,
  last block ends exactly at ``dataLen``.
* every block is a valid zlib stream (level-9 header ``78 DA``); decompressed
  lengths map 1:1 to the documented resolution histogram below, all bpp=4.
* re-serialising the container from scratch (header bytes + raw compressed
  blocks preserved verbatim) reproduces the original file **byte-identically**
  (same SHA-256).

Facts asserted for the shipped ``lk.bin``:

* it shares the 512-byte header (magic + tag ``LK`` + dataLen@4 = 294,132 =
  ``filesize - 512``) but its payload is *not* a block container: the block
  table is self-inconsistent (count/offsets nonsense) and there is no zlib
  stream at any of the declared offsets.  A correct implementation must treat
  it as an opaque raw container and reproduce it losslessly.

Usage::

    python tests/integration/oracle_sample.py                 # human summary
    python tests/integration/oracle_sample.py --json out.json # + machine JSON
    python tests/integration/oracle_sample.py --quiet         # exit code only

Exit code 0 iff every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

HEADER_SIZE = 512
MAGIC = 0x58881688
MAX_REASONABLE_BLOCK_COUNT = 0x10000  # sanity guard, no real logo.bin is huge

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOGO = REPO_ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "logo.bin"
DEFAULT_LK = REPO_ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "lk.bin"

# Documented expected histogram: "WxHxD" -> block count.  Total = 51.
EXPECTED_LOGO_HISTOGRAM = {
    "540x960x4": 27,
    "720x1280x4": 2,
    "38x54x4": 10,
    "135x24x4": 10,
    "48x54x4": 1,
    "135x1x4": 1,
}
EXPECTED_LOGO_COUNT = 51
EXPECTED_LOGO_DATA_LEN = 2_005_959
EXPECTED_LOGO_FILE_SIZE = 2_006_471
# SHA-256 of the pristine shipped sample; byte-identical roundtrip must hit it.
EXPECTED_LOGO_SHA256 = "c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c"
EXPECTED_LK_SHA256 = "5bc6c099663502f5e71ea111206751fd5d264339b7cbee1a184cc7b49fcece55"
EXPECTED_LK_DATA_LEN = 294_132
EXPECTED_LK_FILE_SIZE = 294_644


class OracleError(Exception):
    """A structural invariant was violated (oracle-side, independent of rebuild)."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# sizes.txt lookup (used only to *describe* decompressed lengths; the oracle
# asserts the hard-coded histogram above, not a table lookup).
# ---------------------------------------------------------------------------
def load_sizes_table(path: Path) -> dict[int, list[str]]:
    """Return ``{width*height*bpp: ["540x960x4", ...]}`` from a sizes.txt file."""
    table: dict[int, list[str]] = {}
    if not path.is_file():
        return table
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = raw_line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            width, height, depth = text.lower().split("x")
            w, h, d = int(width), int(height), int(depth)
        except ValueError:
            continue
        table.setdefault(w * h * d, []).append(text)
    return table


# ---------------------------------------------------------------------------
# Container parsing (independent implementation)
# ---------------------------------------------------------------------------
def read_header(data: bytes) -> dict:
    if len(data) < HEADER_SIZE:
        raise OracleError(f"file too short for 512-byte header: {len(data)}")
    magic = struct.unpack_from("<I", data, 0)[0]
    data_len = struct.unpack_from("<I", data, 4)[0]
    tag = data[8:16].split(b"\x00", 1)[0].decode("latin-1")
    return {
        "magic": magic,
        "magic_hex": f"0x{magic:08X}",
        "data_len": data_len,
        "tag": tag,
        "header_sha256": sha256(data[:HEADER_SIZE]),
        "header_8_512_sha256": sha256(data[8:HEADER_SIZE]),
    }


def try_block_table(data: bytes) -> dict:
    """Attempt a strict LOGO block-table parse of the payload.

    Returns a dict describing the attempt; ``valid=False`` with a ``reason`` when
    the payload is not a self-consistent block container (raw/non-LOGO payload).
    """
    size = len(data)
    result: dict = {"valid": False, "reason": None, "blocks": [], "payload": None}
    try:
        header = read_header(data)
    except OracleError as exc:
        result["reason"] = str(exc)
        return result
    data_len = header["data_len"]
    if header["magic"] != MAGIC:
        result["reason"] = f"magic {header['magic_hex']} != 0x{MAGIC:08X}"
        return result
    if data_len > size - HEADER_SIZE:
        result["reason"] = (
            f"dataLen {data_len} > filesize-512 {size - HEADER_SIZE}"
        )
        return result
    if data_len < 8:
        result["reason"] = f"dataLen {data_len} < 8 (no room for block table)"
        return result
    payload = data[HEADER_SIZE:HEADER_SIZE + data_len]
    count, data_len_echo = struct.unpack_from("<II", payload, 0)
    result["count"] = count
    result["data_len_echo"] = data_len_echo
    if count == 0:
        # docs/spec_api.md explicitly allows an empty container.
        if data_len_echo != data_len or data_len != 8:
            result["reason"] = (
                f"empty container inconsistent: dataLen={data_len} echo={data_len_echo}"
            )
            return result
        result.update(valid=True, empty=True, blocks=[], offsets=[], payload=payload)
        return result
    if count > MAX_REASONABLE_BLOCK_COUNT:
        result["reason"] = f"count {count} exceeds sanity limit"
        return result
    if 8 + 4 * count > data_len:
        result["reason"] = (
            f"count {count} needs {8 + 4 * count} B of table > dataLen {data_len}"
        )
        return result
    if data_len_echo != data_len:
        result["reason"] = (
            f"payload dataLen echo {data_len_echo} != header dataLen {data_len}"
        )
        return result
    offsets = list(struct.unpack_from(f"<{count}I", payload, 8))
    result["offsets"] = offsets
    if offsets[0] != 8 + 4 * count:
        result["reason"] = (
            f"offsets[0]={offsets[0]} != 8+4*count={8 + 4 * count}"
        )
        return result
    for index, off in enumerate(offsets):
        if off < 8 + 4 * count or off >= data_len:
            result["reason"] = f"offset[{index}]={off} out of payload range"
            return result
        if index and off <= offsets[index - 1]:
            result["reason"] = (
                f"offsets not strictly increasing at [{index}]: "
                f"{offsets[index - 1]} -> {off}"
            )
            return result
    lengths = [
        (offsets[i + 1] if i + 1 < count else data_len) - offsets[i]
        for i in range(count)
    ]
    blocks = []
    for index, (off, length) in enumerate(zip(offsets, lengths)):
        raw = payload[off:off + length]
        entry = {
            "index": index,
            "offset": off,
            "compressed_len": length,
            "raw_sha256": sha256(raw),
            "zlib_header": raw[:2].hex() if len(raw) >= 2 else "",
        }
        try:
            decompressed = zlib.decompress(raw)
        except zlib.error as exc:  # pragma: no cover - depends on input bytes
            entry["decompressed_len"] = None
            entry["decompress_error"] = str(exc)
        else:
            entry["decompressed_len"] = len(decompressed)
            entry["decompressed_sha256"] = sha256(decompressed)
        blocks.append(entry)
    result.update(
        valid=True,
        blocks=blocks,
        lengths=lengths,
        payload=payload,
        offsets_valid=True,
    )
    return result


def parse_logo_bin(data: bytes) -> dict:
    """Full strict parse used by the oracle; raises OracleError on violation."""
    header = read_header(data)
    table = try_block_table(data)
    if not table["valid"]:
        raise OracleError(f"not a valid MTK LOGO block container: {table['reason']}")
    if table.get("empty"):
        raise OracleError("strict sample parser does not accept count==0 containers")
    blocks = table["blocks"]
    data_len = header["data_len"]
    count = table["count"]
    problems: list[str] = []

    if header["magic"] != MAGIC:
        problems.append(f"magic {header['magic_hex']} != 0x{MAGIC:08X}")
    if data_len != 8 + 4 * count + sum(b["compressed_len"] for b in blocks):
        problems.append("dataLen != 8 + 4*count + sum(compressed_len)")
    if table["offsets"][-1] + blocks[-1]["compressed_len"] != data_len:
        problems.append("last block does not end exactly at dataLen")
    if len(data) != HEADER_SIZE + data_len:
        # Not fatal in the format (trailing bytes are ignored), but the shipped
        # samples must have no trailing bytes for a byte-identical roundtrip.
        problems.append(
            f"filesize {len(data)} != 512+dataLen {HEADER_SIZE + data_len}"
        )
    for block in blocks:
        if block.get("decompressed_len") is None:
            problems.append(f"block[{block['index']}] is not valid zlib")
    if problems:
        raise OracleError("; ".join(problems))
    return {
        "header": header,
        "table": table,
        "blocks": blocks,
        "sha256": sha256(data),
        "file_size": len(data),
    }


def repack_from_parsed(data: bytes, parsed: dict) -> bytes:
    """Rebuild the file from scratch, reusing header + raw compressed blocks.

    This is the byte-identical invariant the rebuild's ``LogoContainer.to_bytes``
    must satisfy for an *unmodified* container.
    """
    header = data[:HEADER_SIZE]
    count = parsed["table"]["count"]
    data_len = parsed["header"]["data_len"]
    offsets: list[int] = []
    cursor = 8 + 4 * count
    payload_body = bytearray()
    for block in parsed["blocks"]:
        raw = parsed["table"]["payload"][
            block["offset"]:block["offset"] + block["compressed_len"]
        ]
        offsets.append(cursor)
        payload_body.extend(raw)
        cursor += len(raw)
    head = struct.pack("<II", len(offsets), data_len)
    out = (
        struct.pack("<I", MAGIC)
        + struct.pack("<I", data_len)
        + header[8:HEADER_SIZE]
        + head
        + struct.pack(f"<{len(offsets)}I", *offsets)
        + bytes(payload_body)
    )
    if len(out) != len(data):
        raise OracleError(f"repack length {len(out)} != original {len(data)}")
    return out


def opaque_repack(data: bytes) -> bytes:
    """Lossless raw-container roundtrip: header magic/dataLen rewritten, rest kept."""
    header = data[:HEADER_SIZE]
    data_len = struct.unpack_from("<I", data, 4)[0]
    return (
        struct.pack("<I", MAGIC)
        + struct.pack("<I", data_len)
        + header[8:]
        + data[HEADER_SIZE:]
    )


def histogram(parsed: dict, sizes_path: Path | None = None) -> dict[str, int]:
    """Map decompressed block lengths to ``WxHxD`` using on-disk sizes table."""
    table = load_sizes_table(sizes_path or (REPO_ROOT / "src/logobuilder/assets/sizes.txt"))
    result: dict[str, int] = {}
    for block in parsed["blocks"]:
        length = block["decompressed_len"]
        names = table.get(length)
        name = names[0] if names else f"unknown({length})"
        result[name] = result.get(name, 0) + 1
    return result


# ---------------------------------------------------------------------------
# Checks for the two shipped samples
# ---------------------------------------------------------------------------
def check_logo_bin(path: Path = DEFAULT_LOGO) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not path.is_file():
        check("sample-present", False, f"missing {path}")
        return {"sample": str(path), "checks": checks, "passed": False}
    data = path.read_bytes()
    check("sample-present", True, str(path))
    check("file-size", len(data) == EXPECTED_LOGO_FILE_SIZE,
          {"expected": EXPECTED_LOGO_FILE_SIZE, "actual": len(data)})

    parsed = None
    try:
        parsed = parse_logo_bin(data)
    except OracleError as exc:
        check("strict-block-container-parse", False, str(exc))
    else:
        check("strict-block-container-parse", True, "valid")

    if parsed is None:
        return {
            "sample": str(path),
            "sha256": sha256(data),
            "checks": checks,
            "passed": all(c["ok"] for c in checks),
        }

    header = parsed["header"]
    table = parsed["table"]
    check("magic", header["magic"] == MAGIC, header["magic_hex"])
    check("tag", header["tag"] == "LOGO", header["tag"])
    check("dataLen@4", header["data_len"] == EXPECTED_LOGO_DATA_LEN,
          {"expected": EXPECTED_LOGO_DATA_LEN, "actual": header["data_len"]})
    check("dataLen==filesize-512", header["data_len"] == len(data) - HEADER_SIZE,
          {"data_len": header["data_len"], "filesize": len(data)})
    check("blockCount", table["count"] == EXPECTED_LOGO_COUNT,
          {"expected": EXPECTED_LOGO_COUNT, "actual": table["count"]})
    check("dataLen-echo", table["data_len_echo"] == header["data_len"],
          {"header": header["data_len"], "payload": table["data_len_echo"]})
    check("offsets[0]==8+4*count",
          table["offsets"][0] == 8 + 4 * table["count"],
          {"offsets0": table["offsets"][0],
           "expected": 8 + 4 * table["count"]})
    check("offsets-strictly-increasing",
          all(b > a for a, b in zip(table["offsets"], table["offsets"][1:])),
          "ok")
    check("payload-fully-covered",
          table["offsets"][-1] + table["blocks"][-1]["compressed_len"] == header["data_len"],
          {"last_end": table["offsets"][-1] + table["blocks"][-1]["compressed_len"],
           "data_len": header["data_len"]})
    check("all-blocks-zlib",
          all(b.get("decompressed_len") is not None for b in table["blocks"]),
          {"ok": sum(1 for b in table["blocks"] if b.get("decompressed_len") is not None),
           "total": len(table["blocks"])})
    zlib_headers = sorted({b["zlib_header"] for b in table["blocks"]})
    check("zlib-level9-header(78da)", zlib_headers == ["78da"], zlib_headers)
    check("sha256-pristine", parsed["sha256"] == EXPECTED_LOGO_SHA256,
          {"expected": EXPECTED_LOGO_SHA256, "actual": parsed["sha256"]})

    hist = histogram(parsed)
    check("block-size-histogram", hist == EXPECTED_LOGO_HISTOGRAM,
          {"expected": EXPECTED_LOGO_HISTOGRAM, "actual": hist})
    check("all-blocks-bpp4", all(name.endswith("x4") for name in hist),
          sorted(hist))

    try:
        rebuilt = repack_from_parsed(data, parsed)
    except OracleError as exc:
        check("repack-byte-identical", False, str(exc))
    else:
        check("repack-byte-identical", rebuilt == data,
              {"sha256_original": parsed["sha256"],
               "sha256_rebuilt": sha256(rebuilt)})
        check("repack-sha256", sha256(rebuilt) == EXPECTED_LOGO_SHA256, sha256(rebuilt))

    return {
        "sample": str(path),
        "sha256": parsed["sha256"],
        "file_size": len(data),
        "header": header,
        "block_count": table["count"],
        "data_len": header["data_len"],
        "histogram": hist,
        "blocks": table["blocks"],
        "checks": checks,
        "passed": all(c["ok"] for c in checks),
    }


def check_lk_bin(path: Path = DEFAULT_LK) -> dict:
    checks: list[dict] = []

    def check(name: str, ok: bool, detail) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not path.is_file():
        check("sample-present", False, f"missing {path}")
        return {"sample": str(path), "checks": checks, "passed": False}
    data = path.read_bytes()
    check("sample-present", True, str(path))
    check("file-size", len(data) == EXPECTED_LK_FILE_SIZE,
          {"expected": EXPECTED_LK_FILE_SIZE, "actual": len(data)})
    try:
        header = read_header(data)
    except OracleError as exc:
        check("header-parse", False, str(exc))
        return {"sample": str(path), "checks": checks, "passed": False}
    check("header-parse", True, "ok")
    check("magic", header["magic"] == MAGIC, header["magic_hex"])
    check("tag", header["tag"] == "LK", header["tag"])
    check("dataLen@4==filesize-512",
          header["data_len"] == len(data) - HEADER_SIZE,
          {"data_len": header["data_len"], "filesize": len(data)})
    check("sha256-pristine", sha256(data) == EXPECTED_LK_SHA256,
          {"expected": EXPECTED_LK_SHA256, "actual": sha256(data)})

    table = try_block_table(data)
    check("not-a-block-container", not table["valid"],
          table.get("reason"))
    # Independent evidence: no zlib stream begins at the offset pattern a block
    # table would declare.  Payload starts with ARM branch instructions.
    payload = data[HEADER_SIZE:]
    check("payload-not-zlib-at-0", not payload.startswith(b"\x78\xda"), payload[:8].hex())
    first_word = struct.unpack_from("<I", payload, 0)[0]
    check("payload-first-word-arm-branch", (first_word & 0x0F000000) == 0x0A000000,
          f"0x{first_word:08X}")
    try:
        rebuilt = opaque_repack(data)
    except OracleError as exc:
        check("opaque-repack-byte-identical", False, str(exc))
    else:
        check("opaque-repack-byte-identical", rebuilt == data,
              {"sha256": sha256(rebuilt)})
    return {
        "sample": str(path),
        "sha256": sha256(data),
        "file_size": len(data),
        "header": header,
        "block_table_attempt": {
            "valid": table["valid"],
            "reason": table.get("reason"),
            "count": table.get("count"),
        },
        "checks": checks,
        "passed": all(c["ok"] for c in checks),
    }


def run_oracle(logo_path: Path = DEFAULT_LOGO, lk_path: Path = DEFAULT_LK) -> dict:
    logo = check_logo_bin(logo_path)
    lk = check_lk_bin(lk_path)
    return {
        "oracle": "tests/integration/oracle_sample.py (independent struct+zlib)",
        "python": sys.version.split()[0],
        "logo_bin": logo,
        "lk_bin": lk,
        "passed": logo["passed"] and lk["passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logo", type=Path, default=DEFAULT_LOGO)
    parser.add_argument("--lk", type=Path, default=DEFAULT_LK)
    parser.add_argument("--json", type=Path, default=None,
                        help="write machine-readable result JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = run_oracle(args.logo, args.lk)
    except Exception as exc:  # oracle must never crash with a traceback-only exit
        print(f"ORACLE ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    if not args.quiet:
        print("=" * 72)
        print("INDEPENDENT ORACLE — real MTK LOGO samples")
        print("=" * 72)
        for key in ("logo_bin", "lk_bin"):
            sample = result[key]
            print(f"\n[{key}] {sample['sample']}")
            print(f"  sha256 = {sample.get('sha256')}")
            if "histogram" in sample:
                print(f"  count={sample['block_count']} dataLen={sample['data_len']}")
                print(f"  histogram={sample['histogram']}")
            for check in sample["checks"]:
                mark = "PASS" if check["ok"] else "FAIL"
                detail = check["detail"]
                if not isinstance(detail, str):
                    detail = json.dumps(detail, ensure_ascii=False)
                print(f"  [{mark}] {check['name']}: {detail}")
    print("\nORACLE RESULT:", "PASS" if result["passed"] else "FAIL")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
