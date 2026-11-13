#!/usr/bin/env python3
"""Deterministic test-corpus generator for the LogoBuilder rebuild.

This generator is intentionally **independent of ``logobuilder``**: it writes
MTK LOGO containers with its own ``struct``/``zlib`` code so the fixtures can be
used as an external oracle for the rebuild's parser.  It is also usable before
``src/logobuilder/core`` exists.

Corpus produced (all files are byte-reproducible):

``valid/*``
    * ``logo_bpp4_540x960.bin``      one BGRA8888 block, tag ``LOGO``
    * ``logo_bpp2_1080x1920.bin``    one RGB565 block, tag ``LOGO``
    * ``logo_mixed.bin``             image + zlib-binary + image + raw-binary
    * ``logo_tag_lk.bin``            tag ``LK``, valid block table
    * ``logo_pad_ff.bin`` / ``logo_pad_zero.bin`` / ``logo_pad_pattern.bin``
                                     header padding variants (same block data)
    * ``logo_trailing_bytes.bin``    4 stray bytes after ``512+dataLen``
    * ``empty_container.bin``        count == 0, dataLen == 8 (allowed empty)
    * ``logo_unknown_size.bin``      known 540x960x4 block + unknown-length block

``corrupt/*``
    * ``truncated_header.bin``       only 300 bytes (no full 512-byte header)
    * ``bad_magic.bin``              magic != 0x58881688
    * ``datalen_too_large.bin``      header dataLen > filesize-512
    * ``datalen_echo_mismatch.bin``  payload dataLen != header dataLen
    * ``offset0_wrong.bin``          offsets[0] != 8+4*count
    * ``offsets_nonmonotonic.bin``   offsets[2] < offsets[1]
    * ``offsets_beyond_datalen.bin`` offsets[-1] == dataLen
    * ``huge_count.bin``             count = 0x10000000 (OOM guard)
    * ``count_zero_datalen_mismatch.bin``  count==0 but dataLen==100
    * ``block_zlib_corrupt.bin``     middle block is not a zlib stream
    * ``not_logo_random.bin``        4096 deterministic random bytes

``raw/*``
    * ``lk_like_raw.bin``            valid 512-byte header + raw ARM-like payload
    * ``boot_logo_240x320.bin``      bare RGB565 pixels, no header

``project/legacy_project/``
    * original-exe style project: ``header.bin`` + ``.project`` + ``img1.png``
      + ``block2.dat`` (+ ``sizes.txt``, ``ignored.txt``, custom ``updater-script``)
    * ``legacy_source_container.bin`` the container it was extracted from

Usage::

    python tests/fixtures/make_fixtures.py                 # generate/refresh
    python tests/fixtures/make_fixtures.py --list          # list fixture names
    python tests/fixtures/make_fixtures.py --verify        # verify manifest only
    python tests/fixtures/make_fixtures.py --out DIR       # custom output dir

``tests/integration/test_e2e.py`` calls :func:`ensure` so ``pytest tests`` works
on a clean checkout without a separate generation step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path

HEADER_SIZE = 512
MAGIC = 0x58881688
GENERATOR_VERSION = "1"

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DEFAULT_OUT = HERE / "generated"
ASSET_SIZES = REPO_ROOT / "src" / "logobuilder" / "assets" / "sizes.txt"

# The real samples pad the header exactly like this: bytes 16..39 zero,
# bytes 40..511 0xFF (verified against the shipped logo.bin / lk.bin).
PAD_ORIGINAL = b"\x00" * 24 + b"\xff" * (HEADER_SIZE - 16 - 24)
PAD_FF = b"\xff" * (HEADER_SIZE - 16)
PAD_ZERO = b"\x00" * (HEADER_SIZE - 16)
PAD_PATTERN = bytes((i * 37 + 11) & 0xFF for i in range(HEADER_SIZE - 16))


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------
def prng_stream(seed: str, length: int) -> bytes:
    """Deterministic, dependency-free pseudo-random byte stream."""
    out = bytearray()
    counter = 0
    seed_bytes = seed.encode("utf-8")
    while len(out) < length:
        out += hashlib.sha256(seed_bytes + counter.to_bytes(4, "little")).digest()
        counter += 1
    return bytes(out[:length])


def bpp_pixels(width: int, height: int, bpp: int, seed: str) -> bytes:
    """Deterministic pixel bytes of exactly ``width*height*bpp`` bytes.

    The same row is repeated, which keeps the generated fixture compressible
    and the generator fast while still exercising every byte.
    """
    row = prng_stream(seed + ":row", width * bpp)
    return row * height


def z9(data: bytes) -> bytes:
    return zlib.compress(data, 9)


# ---------------------------------------------------------------------------
# Container writer (independent implementation of docs/spec_original.md §1.1)
# ---------------------------------------------------------------------------
def build_header(tag: str, data_len: int, pad: bytes = PAD_ORIGINAL) -> bytes:
    tag_bytes = tag.encode("ascii")[:8].ljust(8, b"\x00")
    assert len(pad) == HEADER_SIZE - 16
    return struct.pack("<II", MAGIC, data_len) + tag_bytes + pad


def build_container(
    blocks: list[bytes],
    *,
    tag: str = "LOGO",
    pad: bytes = PAD_ORIGINAL,
) -> bytes:
    """Serialise a spec-conforming container from already-compressed blocks."""
    count = len(blocks)
    data_len = 8 + 4 * count + sum(len(b) for b in blocks)
    offsets: list[int] = []
    cursor = 8 + 4 * count
    for block in blocks:
        offsets.append(cursor)
        cursor += len(block)
    payload = struct.pack("<II", count, data_len)
    payload += struct.pack(f"<{count}I", *offsets)
    payload += b"".join(blocks)
    assert len(payload) == data_len
    return build_header(tag, data_len, pad) + payload


def build_raw_container(payload: bytes, *, tag: str, data_len: int | None = None,
                        pad: bytes = PAD_ORIGINAL) -> bytes:
    """Header + arbitrary (non-block-table) payload, as shipped ``lk.bin``."""
    return build_header(tag, len(payload) if data_len is None else data_len, pad) + payload


def build_custom(
    *,
    tag: str = "LOGO",
    payload: bytes,
    header_data_len: int,
    pad: bytes = PAD_ORIGINAL,
) -> bytes:
    """Header with an explicit dataLen (used to craft corrupt fixtures)."""
    return build_header(tag, header_data_len, pad) + payload


# ---------------------------------------------------------------------------
# sizes.txt (for choosing a length that is *not* in the table)
# ---------------------------------------------------------------------------
def load_size_keys(path: Path = ASSET_SIZES) -> set[int]:
    keys: set[int] = set()
    if not path.is_file():
        return keys
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            w, h, d = (int(part) for part in text.lower().split("x"))
        except ValueError:
            continue
        keys.add(w * h * d)
    return keys


def unknown_length(start: int = 100_000) -> int:
    keys = load_size_keys()
    length = start
    while length in keys:
        length += 1
    return length


# ---------------------------------------------------------------------------
# Corpus definition
# ---------------------------------------------------------------------------
class Corpus:
    def __init__(self, out_dir: Path) -> None:
        self.out = out_dir
        self.entries: dict[str, dict] = {}

    def write(self, rel_path: str, data: bytes, *, kind: str,
              expected: dict | None = None, note: str = "") -> Path:
        path = self.out / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.entries[rel_path] = {
            "kind": kind,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "expected": expected or {},
            "note": note,
        }
        return path

    def record_dir(self, rel_path: str, *, kind: str, expected: dict | None = None,
                   note: str = "") -> Path:
        directory = self.out / rel_path
        files = {}
        for file in sorted(directory.rglob("*")):
            if file.is_file():
                files[str(file.relative_to(directory)).replace("\\", "/")] = {
                    "bytes": file.stat().st_size,
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
        self.entries[rel_path] = {
            "kind": kind,
            "files": files,
            "expected": expected or {},
            "note": note,
        }
        return directory


def _pixels_histogram(blocks: list[tuple[int, int, int]]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for width, height, bpp in blocks:
        name = f"{width}x{height}x{bpp}"
        hist[name] = hist.get(name, 0) + 1
    return hist


def build_all(out_dir: Path = DEFAULT_OUT) -> dict:
    out = Path(out_dir)
    if out.exists():
        has_manifest = (out / "manifest.json").is_file()
        empty = not any(out.iterdir())
        if not (has_manifest or empty):
            raise SystemExit(
                f"refusing to overwrite non-fixture directory: {out} "
                "(remove it or pass a different --out)"
            )
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(out)

    # ---- valid single-block containers -------------------------------
    bpp4_w, bpp4_h = 540, 960
    bpp4_raw = bpp_pixels(bpp4_w, bpp4_h, 4, "bpp4-540x960")
    bpp4_compressed = z9(bpp4_raw)
    single_bpp4 = build_container([bpp4_compressed])
    corpus.write(
        "valid/logo_bpp4_540x960.bin",
        single_bpp4,
        kind="valid",
        expected={
            "sniff": "mtk-logo",
            "parse": "ok",
            "tag": "LOGO",
            "count": 1,
            "sizes": ["540x960x4"],
            "decompressed_lengths": [bpp4_w * bpp4_h * 4],
        },
        note="single BGRA8888 block; canonical valid fixture",
    )

    bpp2_w, bpp2_h = 1080, 1920
    bpp2_raw = bpp_pixels(bpp2_w, bpp2_h, 2, "bpp2-1080x1920")
    single_bpp2 = build_container([z9(bpp2_raw)])
    corpus.write(
        "valid/logo_bpp2_1080x1920.bin",
        single_bpp2,
        kind="valid",
        expected={
            "sniff": "mtk-logo",
            "parse": "ok",
            "tag": "LOGO",
            "count": 1,
            "sizes": ["1080x1920x2"],
            "decompressed_lengths": [bpp2_w * bpp2_h * 2],
        },
        note="single RGB565 block (bpp=2)",
    )

    # ---- mixed image + binary blocks ----------------------------------
    bin1 = prng_stream("binary-block-zlib", 1234)          # zlib-ok, unknown size
    bin2 = prng_stream("binary-block-raw", 777)            # not zlib at all
    mixed = build_container([bpp4_compressed, z9(bin1), z9(bpp_pixels(135, 24, 4, "bpp4-135x24")), bin2])
    corpus.write(
        "valid/logo_mixed.bin",
        mixed,
        kind="valid",
        expected={
            "sniff": "mtk-logo",
            "parse": "ok",
            "tag": "LOGO",
            "count": 4,
            "sizes": ["540x960x4", None, "135x24x4", None],
            "binary_blocks": [1, 3],
            "decompressed_ok": [True, True, True, False],
            "pending_image_blocks": [1],
        },
        note="image + zlib binary + image + non-zlib binary",
    )

    # ---- tag LK but valid block table ---------------------------------
    corpus.write(
        "valid/logo_tag_lk.bin",
        build_container([bpp4_compressed], tag="LK"),
        kind="valid",
        expected={
            "sniff": "mtk-logo",
            "parse": "ok",
            "tag": "LK",
            "count": 1,
            "sizes": ["540x960x4"],
        },
        note="valid block table with non-LOGO tag must still parse as blocks",
    )

    # ---- header padding variants --------------------------------------
    for name, pad, note in (
        ("logo_pad_ff.bin", PAD_FF, "header bytes 16..511 all 0xFF"),
        ("logo_pad_zero.bin", PAD_ZERO, "header bytes 16..511 all 0x00"),
        ("logo_pad_pattern.bin", PAD_PATTERN, "header bytes 16..511 deterministic pattern"),
    ):
        data = build_container([bpp4_compressed], pad=pad)
        corpus.write(
            f"valid/{name}",
            data,
            kind="valid",
            expected={
                "parse": "ok",
                "tag": "LOGO",
                "count": 1,
                "header_preserved": True,
                "header_tail_sha256": hashlib.sha256(pad).hexdigest(),
            },
            note=note,
        )

    # ---- trailing bytes after dataLen (must be ignored, not an error) --
    corpus.write(
        "valid/logo_trailing_bytes.bin",
        single_bpp4 + b"\xde\xad\xbe\xef" * 5,
        kind="valid",
        expected={
            "parse": "ok",
            "count": 1,
            "trailing_bytes": 20,
            "data_len": struct.unpack_from("<I", single_bpp4, 4)[0],
        },
        note="stray bytes after 512+dataLen are ignored",
    )

    # ---- empty container (spec_api allows count == 0) -----------------
    empty = build_custom(
        payload=struct.pack("<II", 0, 8), header_data_len=8
    )
    corpus.write(
        "valid/empty_container.bin",
        empty,
        kind="valid",
        expected={"parse": "ok", "count": 0, "tag": "LOGO", "data_len": 8},
        note="count==0 empty container is allowed by docs/spec_api.md",
    )

    # ---- unknown-size image block -------------------------------------
    unknown_len = unknown_length()
    unknown_raw = prng_stream("unknown-size-block", unknown_len)
    unknown_compressed = z9(unknown_raw)
    unknown_container = build_container([bpp4_compressed, unknown_compressed])
    corpus.write(
        "valid/logo_unknown_size.bin",
        unknown_container,
        kind="valid",
        expected={
            "parse": "ok",
            "count": 2,
            "sizes": ["540x960x4", None],
            "decompressed_lengths": [bpp4_w * bpp4_h * 4, unknown_len],
            "pending_image_blocks": [1],
            "unknown_decompressed_len": unknown_len,
        },
        note="block 1 decompresses to a length absent from assets/sizes.txt",
    )

    # ---- corrupt fixtures ---------------------------------------------
    def corrupt(name: str, data: bytes, expected: dict, note: str) -> None:
        corpus.write(f"corrupt/{name}", data, kind="corrupt",
                     expected=expected, note=note)

    corrupt(
        "truncated_header.bin",
        single_bpp4[:300],
        {"parse": "error", "error_classes": ["NotLogoFile", "CorruptContainer"]},
        "file shorter than the 512-byte header",
    )
    bad_magic = bytearray(single_bpp4)
    bad_magic[0:4] = struct.pack("<I", 0x12345678)
    corrupt(
        "bad_magic.bin",
        bytes(bad_magic),
        {"parse": "error", "error_classes": ["NotLogoFile"]},
        "magic != 0x58881688",
    )
    corrupt(
        "datalen_too_large.bin",
        build_custom(
            payload=single_bpp4[HEADER_SIZE:],
            header_data_len=len(single_bpp4),  # > filesize-512
        ),
        {"parse": "error", "error_classes": ["CorruptContainer"]},
        "header dataLen > filesize-512",
    )

    # payload-level corruption uses raw manual assembly
    def payload_with_table(
        count: int,
        offsets: list[int],
        blocks: bytes,
        *,
        echo: int | None = None,
        header_data_len: int | None = None,
    ) -> tuple[bytes, int]:
        data_len = 8 + 4 * count + len(blocks)
        payload = struct.pack("<II", count, data_len if echo is None else echo)
        payload += struct.pack(f"<{count}I", *offsets)
        payload += blocks
        return payload, (data_len if header_data_len is None else header_data_len)

    block_a = z9(b"AAAA" * 10)
    block_b = z9(b"BBBB" * 10)
    block_c = z9(b"CCCC" * 10)

    # datalen echo mismatch: header dataLen correct, in-range table echo 8 bytes shorter
    blocks = block_a + block_b + block_c
    data_len = 8 + 12 + len(blocks)
    payload = struct.pack("<II", 3, data_len - 8)
    payload += struct.pack("<3I", 20, 20 + len(block_a), 20 + len(block_a) + len(block_b))
    payload += blocks
    corrupt(
        "datalen_echo_mismatch.bin",
        build_custom(payload=payload, header_data_len=data_len),
        {
            "parse": "ok-lenient",
            "warnings_contain": ["dataLen", "头部", "块表"],
            "rewrite_self_consistent": True,
        },
        "payload dataLen echo differs from header dataLen (in-range value truncates the last block); "
        "per Lead decision/spec_api update the parser records a warning and recovers",
    )

    # offsets[0] != 8+4*count (count=3 -> 20; use 16)
    payload = struct.pack("<II", 3, data_len)
    payload += struct.pack("<3I", 16, 16 + len(block_a), 16 + len(block_a) + len(block_b))
    payload += blocks
    corrupt(
        "offset0_wrong.bin",
        build_custom(payload=payload, header_data_len=data_len),
        {"parse": "error", "error_classes": ["CorruptContainer"]},
        "offsets[0] must be 8+4*count (=20)",
    )

    # offsets non-monotonic: 20, 24, 22 (all >= table end, all < dataLen)
    payload = struct.pack("<II", 3, data_len)
    payload += struct.pack("<3I", 20, 24, 22)
    payload += blocks
    corrupt(
        "offsets_nonmonotonic.bin",
        build_custom(payload=payload, header_data_len=data_len),
        {"parse": "error", "error_classes": ["CorruptContainer"]},
        "offsets[2] < offsets[1]",
    )

    # offsets[-1] == dataLen (must be strictly inside)
    payload = struct.pack("<II", 3, data_len)
    payload += struct.pack("<3I", 20, 20 + len(block_a), data_len)
    payload += blocks
    corrupt(
        "offsets_beyond_datalen.bin",
        build_custom(payload=payload, header_data_len=data_len),
        {"parse": "error", "error_classes": ["CorruptContainer"]},
        "last offset is not strictly inside the payload",
    )

    # huge count: 0x10000000 blocks would need > dataLen bytes of table
    huge_count = 0x10000000
    payload = struct.pack("<II", huge_count, 64) + b"\x00" * 56
    corrupt(
        "huge_count.bin",
        build_custom(payload=payload, header_data_len=64),
        {"parse": "error", "error_classes": ["CorruptContainer"]},
        "huge block count must be rejected before allocating (OOM guard)",
    )

    # count==0 but dataLen says 100 -> echo mismatch
    payload = struct.pack("<II", 0, 8) + b"\x00" * 92
    corrupt(
        "count_zero_datalen_mismatch.bin",
        build_custom(payload=payload, header_data_len=100),
        {
            "parse": "ok-lenient",
            "warnings_contain": ["dataLen", "头部", "块表"],
            "rewrite_self_consistent": True,
            "ignored_trailing_bytes": 92,
        },
        "count==0 with inconsistent header dataLen; per Lead decision the parser records a "
        "warning, ignores the trailing bytes and writes a self-consistent container",
    )

    # one block is not zlib: parse must degrade to BinaryBlock, not fail
    b0 = z9(b"GOOD" * 20)
    b1 = bytearray(z9(b"GOOD" * 20))
    for i in range(8, min(len(b1) - 4, 24)):
        b1[i] ^= 0xFF
    b2 = z9(b"GOOD" * 20)
    corrupted_blocks = b0 + bytes(b1) + b2
    dl = 8 + 12 + len(corrupted_blocks)
    payload = struct.pack("<II", 3, dl)
    payload += struct.pack("<3I", 20, 20 + len(b0), 20 + len(b0) + len(b1))
    payload += corrupted_blocks
    corrupt(
        "block_zlib_corrupt.bin",
        build_custom(payload=payload, header_data_len=dl),
        {
            "parse": "ok",
            "count": 3,
            "decompressed_ok": [True, False, True],
            "binary_blocks": [1],
            "roundtrip": "byte-identical",
        },
        "middle block zlib stream is corrupt: must round-trip losslessly as binary",
    )

    corrupt(
        "not_logo_random.bin",
        prng_stream("random-blob", 4096),
        {"parse": "error", "error_classes": ["NotLogoFile"], "sniff": "raw"},
        "no MTK header at all",
    )

    # ---- raw / non-container fixtures ---------------------------------
    lk_payload = b"\x07\x00\x00\xea\xf2\x52\x00\xea\xf8\x52\x00\xea\xfe\x52\x00\xea"
    lk_payload += prng_stream("lk-raw", 8192)
    corpus.write(
        "raw/lk_like_raw.bin",
        build_raw_container(lk_payload, tag="LK"),
        kind="raw",
        expected={
            "sniff": "mtk-logo",
            "parse": "raw-mode",
            "tag": "LK",
            "data_len": len(lk_payload),
            "opaque_roundtrip": "byte-identical",
        },
        note="valid 512-byte header but payload is not a block table (like real lk.bin)",
    )
    boot_w, boot_h = 240, 320
    boot_raw = bpp_pixels(boot_w, boot_h, 2, "boot-logo-240x320")
    corpus.write(
        "raw/boot_logo_240x320.bin",
        boot_raw,
        kind="raw",
        expected={"format": "boot_logo", "width": boot_w, "height": boot_h,
                  "bpp": 2, "bytes": boot_w * boot_h * 2},
        note="bare RGB565 boot_logo pixels, no header",
    )

    # ---- legacy (original exe) project directory ----------------------
    legacy_img_raw = bpp_pixels(bpp4_w, bpp4_h, 4, "legacy-img1")
    legacy_bin = prng_stream("legacy-block2", 2048)
    legacy_source = build_container([z9(legacy_img_raw), z9(legacy_bin)])
    corpus.write(
        "project/legacy_source_container.bin",
        legacy_source,
        kind="valid",
        expected={
            "parse": "ok",
            "count": 2,
            "sizes": ["540x960x4", None],
            "binary_blocks": [1],
        },
        note="container that project/legacy_project/ was extracted from",
    )

    legacy_dir = out / "project" / "legacy_project"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "header.bin").write_bytes(legacy_source[:HEADER_SIZE])
    (legacy_dir / ".project").write_bytes(
        ("image:img1.png 540 960 4\n"
         "binary:block2.dat\n").encode("ascii")
    )
    (legacy_dir / "block2.dat").write_bytes(legacy_bin)
    (legacy_dir / "sizes.txt").write_bytes(
        b"540x960x4\n540x960x2\n720x1280x4\n"
    )
    (legacy_dir / "ignored.txt").write_bytes(b"")
    (legacy_dir / "updater-script").write_bytes(
        b'ui_print("qa custom updater-script");\n'
        b'package_extract_file("logo.bin", "/dev/logo");\n'
    )
    _write_legacy_png(legacy_dir / "img1.png", legacy_img_raw, bpp4_w, bpp4_h)
    corpus.record_dir(
        "project/legacy_project",
        kind="legacy-project",
        expected={
            "header": "header.bin",
            "project_file": ".project",
            "blocks": [
                {"kind": "image", "file": "img1.png", "size": "540x960x4"},
                {"kind": "binary", "file": "block2.dat", "bytes": len(legacy_bin)},
            ],
            "custom_updater_script": "updater-script",
        },
        note="original LogoBuilder v1.6 project layout (header.bin + .project + imgN.png/blockN.dat)",
    )

    # ---- manifest -----------------------------------------------------
    manifest = {
        "generator": "tests/fixtures/make_fixtures.py",
        "generator_version": GENERATOR_VERSION,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version.split()[0],
        "constants": {
            "header_size": HEADER_SIZE,
            "magic": f"0x{MAGIC:08X}",
            "pad_original": {
                "bytes_16_39": "0x00",
                "bytes_40_511": "0xFF",
            },
        },
        "fixtures": corpus.entries,
    }
    manifest_path = out / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _write_legacy_png(path: Path, bgra: bytes, width: int, height: int) -> None:
    """Write a PNG whose pixels map *exactly* back to ``bgra`` (no re-encode loss)."""
    try:
        from PIL import Image  # noqa: PLC0415 - optional dependency at generation time
    except ImportError as exc:  # pragma: no cover - Pillow is a project dependency
        raise SystemExit(f"Pillow is required to generate the legacy project fixture: {exc}")
    # RGBA storage expects R,G,B,A; source pixel bytes are B,G,R,A.
    rgba = bytearray(len(bgra))
    rgba[0::4] = bgra[2::4]
    rgba[1::4] = bgra[1::4]
    rgba[2::4] = bgra[0::4]
    rgba[3::4] = bgra[3::4]
    Image.frombytes("RGBA", (width, height), bytes(rgba)).save(path, "PNG")


# ---------------------------------------------------------------------------
# ensure / verify / CLI
# ---------------------------------------------------------------------------
def manifest_is_current(out_dir: Path = DEFAULT_OUT) -> bool:
    manifest_path = Path(out_dir) / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if manifest.get("generator_version") != GENERATOR_VERSION:
        return False
    if manifest.get("generator_sha256") != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        return False
    for rel_path, entry in manifest.get("fixtures", {}).items():
        path = Path(out_dir) / rel_path
        if "sha256" in entry:
            if not path.is_file():
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                return False
        elif "files" in entry:
            if not path.is_dir():
                return False
            for rel_file, file_entry in entry["files"].items():
                file_path = path / rel_file
                if not file_path.is_file():
                    return False
                if hashlib.sha256(file_path.read_bytes()).hexdigest() != file_entry["sha256"]:
                    return False
    return True


def ensure(out_dir: Path = DEFAULT_OUT, *, quiet: bool = True) -> dict:
    """Generate the corpus if missing/stale and return the manifest."""
    out = Path(out_dir)
    if manifest_is_current(out):
        return json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    manifest = build_all(out)
    if not quiet:
        print(f"generated {len(manifest['fixtures'])} fixtures in {out}")
    return manifest


def load_manifest(out_dir: Path = DEFAULT_OUT) -> dict:
    return json.loads((Path(out_dir) / "manifest.json").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the LogoBuilder QA fixture corpus.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--list", action="store_true", help="list fixture names and exit")
    parser.add_argument("--verify", action="store_true", help="verify manifest against files only")
    parser.add_argument("--force", action="store_true", help="rebuild even if current")
    args = parser.parse_args(argv)

    out = args.out
    if args.list:
        manifest = ensure(out, quiet=True)
        for rel_path, entry in manifest["fixtures"].items():
            print(f"{entry.get('kind', '?'):16s} {rel_path}")
        return 0
    if args.verify:
        if manifest_is_current(out):
            print(f"OK: fixture corpus is current ({out})")
            return 0
        print(f"STALE/MISSING: {out}", file=sys.stderr)
        return 1
    if args.force or not manifest_is_current(out):
        manifest = build_all(out)
        print(f"OK: generated {len(manifest['fixtures'])} fixtures in {out}")
        return 0
    print(f"OK: fixture corpus already current ({out}); use --force to rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
