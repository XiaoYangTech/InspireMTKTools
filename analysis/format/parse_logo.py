#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parse_logo.py -- MTK logo.bin reference parser / extractor / repacker
=======================================================================

Throw-away analysis aid for the logobuilder rebuild project (NOT the app).

Supported containers (as found in LogoBuilder v1.6 samples):
  * LOGO container  -- 512-byte MTK header + block table @0x200 + zlib blocks
                       (logo.bin, 51 blocks)
  * raw/opaque      -- 512-byte MTK header + non-table payload (lk.bin: raw
                       ARM LK bootloader).  Preserved losslessly.

Usage
-----
  python parse_logo.py <input.bin> <outdir>                 # extract
  python parse_logo.py <input.bin> <outdir> --repack OUT.bin [--recompress]
  python parse_logo.py <input.bin> <outdir> --table          # inventory table
Options
  --sizes PATH     sizes.txt used to map decompressed buffer length -> (w,h,bpp)
                   (default: next to input, next to this script, else built-in)
  --recompress     re-deflate images instead of reusing original zlib streams
  --force          overwrite existing output files
  -q/--quiet       less console output

Dependencies: Python 3.8+ standard library + Pillow (PIL).
No other packages; the reference compressor is located opportunistically
(see find_standard_zlib()); the built-in `zlib` module is ALWAYS sufficient to
decode and to build valid containers - it just may not reproduce the exact
DEFLATE byte stream of the original tool (zlib-ng vs zlib implementations).
"""

import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
import shutil
import struct
import sys
import zlib

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    sys.stderr.write("Pillow is required: pip install Pillow\n")
    raise

MAGIC = 0x58881688
HEADER_SIZE = 512
TOC_OFFSET = 0x200
CUSTOM_HEADER_BYTES = 0x28          # bytes 0x00..0x27 carry meaning, 0x28..0x1FF = 0xFF filler
DEFAULT_TAG = b"LOGO\x00\x00\x00\x00"
MANIFEST_NAME = "manifest.json"

# sizes.txt shipped with LogoBuilder v1.6 (49 lines, "WxHxBPP", BPP defaults to 2).
# The app reads this file at startup and builds Dictionary<buf_len, (w,h,bpp)>
# where buf_len = w*h*bpp.  It is an EXTERNAL table: dimensions are NOT stored
# inside logo.bin.  Built-in copy is only a fallback for self-contained use.
BUILTIN_SIZES = """2560x800x2
1080x1920x2
800x1280x2
720x1280x2
600x1024x2
540x960x2
480x853x2
480x854x2
480x856x2
480x800x2
320x480x2
240x320x2
304x52x2
304x1x2
218x51x2
169x28x2
169x1x2
163x29x2
163x1x2
138x20x2
138x2x2
135x24x2
135x1x2
108x121x2
102x1x2
84x121x2
63x105x2
57x64x2
48x54x2
45x139x2
45x64x2
38x54x2
36x51x2
32x105x2
32x36x2
30x27x2
27x36x2
15x27x2
480x854x4
38x54x4
48x54x4
135x24x4
135x1x4
720x1280x4
45x64x4
57x64x4
163x29x4
163x1x4
540x960x4
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def sha256(data):
    return hashlib.sha256(data).hexdigest()


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def p32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def read_sizes_file(path):
    """Parse sizes.txt -> dict {w*h*bpp: (w, h, bpp)}, last entry wins."""
    table = {}
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("x")
            w = int(parts[0])
            h = int(parts[1])
            bpp = int(parts[2]) if len(parts) > 2 else 2
            table[w * h * bpp] = (w, h, bpp)
            rows.append((w, h, bpp))
    return table, rows


def load_sizes(input_path, explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    d_input = os.path.dirname(os.path.abspath(input_path))
    d_script = os.path.dirname(os.path.abspath(__file__))
    candidates += [os.path.join(d_input, "sizes.txt"),
                   os.path.join(d_script, "sizes.txt")]
    for c in candidates:
        if c and os.path.isfile(c):
            table, rows = read_sizes_file(c)
            return table, rows, c
    table = {}
    rows = []
    for line in BUILTIN_SIZES.strip().splitlines():
        w, h, bpp = (int(x) for x in line.split("x"))
        table[w * h * bpp] = (w, h, bpp)
        rows.append((w, h, bpp))
    return table, rows, "<builtin>"


def looks_like_32bit(buf):
    """Reference app heuristic (behaviourally verified against LogoBuilder):
    len % 4 == 0 and every 4th byte (alpha slot of B,G,R,A) is 0xFF."""
    n = len(buf)
    return n % 4 == 0 and buf[3::4] == b"\xff" * (n // 4)


# --------------------------------------------------------------------------- #
# zlib compressor selection
# --------------------------------------------------------------------------- #
class Compressor(object):
    def __init__(self, name, func):
        self.name = name
        self.func = func

    def __call__(self, raw):
        return self.func(raw)


def _ctypes_zlib_compressor(path, name):
    try:
        lib = ctypes.CDLL(path)
    except OSError:
        return None
    try:
        lib.zlibVersion.restype = ctypes.c_char_p
        version = lib.zlibVersion().decode("ascii", "replace")
    except Exception:
        version = "?"
    lib.compress2.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_ulong),
                              ctypes.c_char_p, ctypes.c_ulong, ctypes.c_int]
    lib.compress2.restype = ctypes.c_int

    def compress(raw):
        outlen = ctypes.c_ulong(len(raw) + len(raw) // 1000 + 64)
        out = ctypes.create_string_buffer(outlen.value)
        rc = lib.compress2(out, ctypes.byref(outlen), raw, len(raw), 9)
        if rc != 0:
            raise RuntimeError("compress2 failed rc=%d" % rc)
        return out.raw[:outlen.value]

    comp = Compressor("%s (zlib %s)" % (name, version), compress)
    if comp(b"probe" * 100) != zlib.compress(b"probe" * 100, 9):
        # different implementation: still valid zlib, just note the name
        pass
    return comp


def find_standard_zlib():
    """Prefer a real C zlib (reproduces this sample byte-for-byte) over the
    interpreter's built-in zlib, which on some builds is zlib-ng."""
    cands = []
    env = os.environ.get("LOGO_ZLIB_DLL")
    if env:
        cands.append(env)
    for d in (os.path.join(sys.base_prefix, "DLLs"), os.path.join(sys.prefix, "DLLs")):
        cands += [os.path.join(d, "zlib1.dll"), os.path.join(d, "libz.dll")]
    cands += ["zlib1.dll", "libz.so.1", "libz.so", "libz.dylib"]
    for c in cands:
        if not c:
            continue
        path = c if os.path.isabs(c) or os.sep in c else None
        if path is None:
            found = ctypes.util.find_library(c.split(".")[0].replace("libz1", "z"))
            if not found:
                continue
            path = found
        if not os.path.exists(path) and not os.path.isabs(path):
            continue
        comp = _ctypes_zlib_compressor(path, "C zlib via ctypes")
        if comp is not None:
            return comp
    return None


def get_compressor(verbose=True):
    std = find_standard_zlib()
    if std is not None:
        if verbose:
            print("compressor: %s" % std.name)
        return std
    if verbose:
        print("compressor: Python stdlib zlib %s (may differ from original DEFLATE bytes)"
              % zlib.ZLIB_RUNTIME_VERSION)
    return Compressor("stdlib zlib %s" % zlib.ZLIB_RUNTIME_VERSION,
                      lambda raw: zlib.compress(raw, 9))


# --------------------------------------------------------------------------- #
# image <-> raw framebuffer (exact MTK/LogoBuilder spelling)
# --------------------------------------------------------------------------- #
_5BIT = bytes(i >> 3 for i in range(256))            # 8 -> 5 bits (truncate)
_6BIT = bytes(i >> 2 for i in range(256))            # 8 -> 6 bits (truncate)
_G_LOW = bytes(((i >> 2) & 0x07) << 5 for i in range(256))   # low 3 of green, placed at bits 5..7
_G_HIGH = bytes(i >> 5 for i in range(256))                   # high 3 of green -> bits 0..2


def decode_rgb565(raw, w, h):
    """RGB565 little-endian -> RGBA bytes.  Expansion matches the app:
    R=(r5<<3), G=(g6<<2), B=(b5<<3)  (i.e. (v & 0xF800)>>8, (v & 0x07E0)>>3,
    (v & 0x001F)<<3), alpha = 255."""
    n = w * h
    if len(raw) < 2 * n:
        raise ValueError("buffer too small for %dx%d RGB565" % (w, h))
    v = int.from_bytes(raw[:2 * n], "little")
    mask_r = int.from_bytes(b"\x00\xf8" * n, "little")
    mask_g = int.from_bytes(b"\xe0\x07" * n, "little")
    mask_b = int.from_bytes(b"\x1f\x00" * n, "little")
    rb = (((v & mask_r) >> 8) << 3).to_bytes(2 * n, "little")
    gb = (((v & mask_g) >> 3) << 2).to_bytes(2 * n, "little")
    bb = (((v & mask_b) << 3)).to_bytes(2 * n, "little")
    rgba = bytearray(4 * n)
    rgba[0::4] = rb[0::2]
    rgba[1::4] = gb[0::2]
    rgba[2::4] = bb[0::2]
    rgba[3::4] = b"\xff" * n
    return bytes(rgba)


def encode_rgb565(rgba, w, h):
    """RGBA bytes -> RGB565 little-endian, truncation R>>3, G>>2, B>>3."""
    n = w * h
    R = rgba[0::4]
    G = rgba[1::4]
    B = rgba[2::4]
    low = (int.from_bytes(G.translate(_G_LOW), "little")
           + int.from_bytes(B.translate(_5BIT), "little")).to_bytes(n, "little")
    high = (int.from_bytes(R.translate(_5BIT), "little")
            + int.from_bytes(G.translate(_G_HIGH), "little")).to_bytes(n, "little")
    out = bytearray(2 * n)
    out[0::2] = low
    out[1::2] = high
    return bytes(out)


def decode_bgra(raw, w, h):
    """32bpp little-endian B,G,R,A -> RGBA bytes (alpha preserved)."""
    n = w * h
    if len(raw) < 4 * n:
        raise ValueError("buffer too small for %dx%d BGRA" % (w, h))
    raw = raw[:4 * n]
    rgba = bytearray(4 * n)
    rgba[0::4] = raw[2::4]   # R
    rgba[1::4] = raw[1::4]   # G
    rgba[2::4] = raw[0::4]   # B
    rgba[3::4] = raw[3::4]   # A
    return bytes(rgba)


def encode_bgra(rgba, w, h):
    """RGBA bytes -> little-endian B,G,R,A (exact inverse of decode_bgra)."""
    n = w * h
    out = bytearray(4 * n)
    out[0::4] = rgba[2::4]
    out[1::4] = rgba[1::4]
    out[2::4] = rgba[0::4]
    out[3::4] = rgba[3::4]
    return bytes(out)


def raw_to_image(raw, w, h, bpp):
    if bpp == 4:
        return Image.frombytes("RGBA", (w, h), decode_bgra(raw, w, h))
    if bpp == 2:
        return Image.frombytes("RGBA", (w, h), decode_rgb565(raw, w, h))
    raise ValueError("unsupported bpp %r" % (bpp,))
    return None


def image_to_raw(img, w, h, bpp):
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    if img.size != (w, h):
        raise ValueError("image size %r != declared %r" % (img.size, (w, h)))
    rgba = img.tobytes()
    if bpp == 4:
        return encode_bgra(rgba, w, h)
    if bpp == 2:
        return encode_rgb565(rgba, w, h)
    raise ValueError("unsupported bpp %r" % (bpp,))


# --------------------------------------------------------------------------- #
# container parse
# --------------------------------------------------------------------------- #
class Container(object):
    def __init__(self):
        self.kind = None          # "logo" | "raw"
        self.header = b""         # 512 raw bytes
        self.magic = 0
        self.size_field = 0
        self.tag = b""
        self.toc_count = 0
        self.toc_total = 0
        self.offsets = []
        self.blocks = []          # list of dicts
        self.warnings = []
        self.data = b""


def parse_container(data, sizes_table, sizes_rows):
    c = Container()
    c.data = data
    if len(data) < HEADER_SIZE:
        raise ValueError("file shorter than 512-byte MTK header")
    c.header = data[:HEADER_SIZE]
    c.magic = u32(data, 0)
    c.size_field = u32(data, 4)
    c.tag = data[8:16]

    ok_toc = False
    if len(data) >= TOC_OFFSET + 8:
        count = u32(data, TOC_OFFSET)
        total = u32(data, TOC_OFFSET + 4)
        table_end = 8 + 4 * count
        if 1 <= count <= (len(data) - TOC_OFFSET) // 4 and total == len(data) - HEADER_SIZE \
                and table_end + HEADER_SIZE <= len(data):
            offs = list(struct.unpack_from("<%dI" % count, data, TOC_OFFSET + 8))
            if offs[0] == table_end and all(offs[i] < offs[i + 1] for i in range(count - 1)) \
                    and offs[-1] < total:
                ok_toc = True
                c.kind = "logo"
                c.toc_count = count
                c.toc_total = total
                c.offsets = offs

    if not ok_toc:
        c.kind = "raw"
        c.warnings.append(
            "no valid LOGO block table at 0x200 (count/total mismatch) -> treating as "
            "raw MTK payload container (header + opaque payload)")
        payload = data[HEADER_SIZE:]
        if u32(c.header, 4) != len(payload):
            c.warnings.append("header size field %d != payload length %d"
                              % (u32(c.header, 4), len(payload)))
        c.blocks = [dict(index=0, type="raw_payload", file_offset=HEADER_SIZE,
                         stored_len=len(payload), stored_sha256=sha256(payload),
                         raw_len=None, raw_sha256=None, raw=None)]
        return c

    if c.magic != MAGIC:
        c.warnings.append("unexpected magic 0x%08X (expected 0x58881688)" % c.magic)
    if c.tag.rstrip(b"\x00") != b"LOGO":
        c.warnings.append("tag is %r, not b'LOGO'" % c.tag)
    if u32(c.header, 4) != c.toc_total:
        c.warnings.append("header size field %d != TOC total %d"
                          % (u32(c.header, 4), c.toc_total))

    for i in range(c.toc_count):
        rel = c.offsets[i]
        abs_off = HEADER_SIZE + rel
        stored_len = (c.toc_total - rel) if i == c.toc_count - 1 \
            else (c.offsets[i + 1] - rel)
        blob = data[abs_off:abs_off + stored_len]
        blk = dict(index=i, type="unknown", rel_offset=rel, file_offset=abs_off,
                   stored_len=stored_len, stored_sha256=sha256(blob),
                   zlib_header=blob[:2].hex() if blob else "", raw_len=None,
                   raw_sha256=None, width=None, height=None, bpp=None, raw=None)
        raw = None
        try:
            dz = zlib.decompressobj()
            raw = dz.decompress(blob)
            raw += dz.flush()
            if not dz.eof or dz.unused_data:
                raise ValueError("not a single complete zlib stream")
        except Exception:
            raw = None
        if raw is None:
            blk["type"] = "binary"
            blk["raw"] = blob
            blk["raw_len"] = len(blob)
            blk["raw_sha256"] = sha256(blob)
            c.blocks.append(blk)
            continue
        key = len(raw)
        if key in sizes_table:
            w, h, bpp = sizes_table[key]
            blk.update(type="image", raw=raw, raw_len=key, raw_sha256=sha256(raw),
                       width=w, height=h, bpp=bpp, sizes_key=key)
        else:
            # reference app: unknown buffer length is preserved as binary block
            blk["type"] = "binary"
            blk["raw"] = raw
            blk["raw_len"] = key
            blk["raw_sha256"] = sha256(raw)
            c.warnings.append("block %d: decompressed %d bytes has no sizes.txt entry "
                              "-> kept as binary block" % (i, key))
        c.blocks.append(blk)
    return c


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #
def ensure_dir(path, force):
    if os.path.isdir(path):
        if force:
            shutil.rmtree(path)
        else:
            return
    os.makedirs(path)


def extract(c, outdir, source_path, sizes_path, force=False, verbose=True):
    ensure_dir(outdir, force)
    sd = os.path.join(outdir, "streams")
    os.makedirs(sd, exist_ok=True)
    write_bytes(os.path.join(outdir, "header.bin"), c.header)
    manifest = dict(
        format="mtk-logo-analysis",
        version=1,
        source=dict(path=os.path.abspath(source_path), size=len(c.data),
                    sha256=sha256(c.data)),
        container=dict(kind=c.kind, header_size=HEADER_SIZE),
        header=dict(magic="0x%08X" % c.magic, magic_expected="0x%08X" % MAGIC,
                    size_field=c.size_field, tag=c.tag.hex(),
                    tag_ascii=c.tag[:c.tag.find(b"\x00")].decode("latin-1")
                    if b"\x00" in c.tag else c.tag.decode("latin-1"),
                    bytes_0x10_0x27_hex=c.header[0x10:0x28].hex(),
                    padding_0x28_0x200_all_ff=c.header[0x28:HEADER_SIZE]
                    == b"\xff" * (HEADER_SIZE - 0x28)),
        sizes_file=sizes_path,
        warnings=c.warnings,
        blocks=[],
    )
    if c.kind == "logo":
        manifest["container"].update(dict(
            toc_offset=TOC_OFFSET, toc_count=c.toc_count, toc_total=c.toc_total,
            table_end_rel=8 + 4 * c.toc_count,
            data_start_rel=8 + 4 * c.toc_count,
            data_start_file_offset=HEADER_SIZE + 8 + 4 * c.toc_count,
            offsets=c.offsets))
    for blk in c.blocks:
        i = blk["index"]
        rec = dict(index=i, type=blk["type"], file_offset=blk["file_offset"],
                   rel_offset=blk.get("rel_offset"), stored_len=blk["stored_len"],
                   stored_sha256=blk["stored_sha256"], zlib_header=blk.get("zlib_header", ""),
                   raw_len=blk.get("raw_len"), raw_sha256=blk.get("raw_sha256"),
                   width=blk.get("width"), height=blk.get("height"),
                   bpp=blk.get("bpp"))
        if blk["type"] == "image":
            img = raw_to_image(blk["raw"], blk["width"], blk["height"], blk["bpp"])
            fn = "%02d_%dx%dx%d.png" % (i, blk["width"], blk["height"], blk["bpp"])
            path = os.path.join(outdir, fn)
            img.save(path)
            rec["file"] = fn
            rec["png_sha256"] = sha256_file(path)
        elif blk["type"] == "binary":
            fn = "block%02d.dat" % i
            write_bytes(os.path.join(outdir, fn), blk["raw"])
            rec["file"] = fn
        else:  # raw_payload
            fn = "payload.bin"
            write_bytes(os.path.join(outdir, fn), c.data[HEADER_SIZE:])
            rec["file"] = fn
            rec["payload_sha256"] = sha256_file(os.path.join(outdir, fn))
        # original compressed stream (for byte-identical repack)
        if blk["type"] in ("image", "binary") and c.kind == "logo":
            write_bytes(os.path.join(sd, "%02d.z" % i),
                        c.data[blk["file_offset"]:blk["file_offset"] + blk["stored_len"]])
        manifest["blocks"].append(rec)
    write_bytes(os.path.join(outdir, MANIFEST_NAME),
                (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    if verbose:
        print("extracted %d block(s) -> %s  [kind=%s]" % (len(c.blocks), outdir, c.kind))
        for w in c.warnings:
            print("  WARNING: " + w)
    return manifest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


# --------------------------------------------------------------------------- #
# repack
# --------------------------------------------------------------------------- #
def repack(outdir, out_path, recompress=False, verbose=True):
    mpath = os.path.join(outdir, MANIFEST_NAME)
    if not os.path.isfile(mpath):
        raise SystemExit("no %s in %s (run extraction first)" % (MANIFEST_NAME, outdir))
    man = json.load(open(mpath, "r", encoding="utf-8"))
    header = bytearray(open(os.path.join(outdir, "header.bin"), "rb").read())
    if len(header) != HEADER_SIZE:
        raise SystemExit("header.bin is not 512 bytes")
    comp = get_compressor(verbose) if recompress else None
    stats = dict(reused=0, recompressed=0, binary=0)
    streams = []

    if man["container"]["kind"] == "raw":
        payload = open(os.path.join(outdir, "payload.bin"), "rb").read()
        struct.pack_into("<I", header, 4, len(payload))
        write_bytes(out_path, bytes(header) + payload)
        if verbose:
            print("repacked raw container -> %s (%d bytes)" % (out_path, HEADER_SIZE + len(payload)))
        return stats

    for blk in man["blocks"]:
        i = blk["index"]
        if blk["type"] == "image":
            img = Image.open(os.path.join(outdir, blk.get("file") or
                                          "%02d_%dx%dx%d.png" % (i, blk["width"], blk["height"], blk["bpp"])))
            raw = image_to_raw(img, blk["width"], blk["height"], blk["bpp"])
            stream_path = os.path.join(outdir, "streams", "%02d.z" % i)
            reuse = (not recompress and os.path.isfile(stream_path)
                     and sha256(raw) == blk["raw_sha256"])
            if reuse:
                streams.append(open(stream_path, "rb").read())
                stats["reused"] += 1
                if sha256(streams[-1]) != blk["stored_sha256"]:
                    raise SystemExit("stored stream %d does not match manifest" % i)
            else:
                streams.append(comp(raw))
                stats["recompressed"] += 1
            if sha256(raw) != blk["raw_sha256"]:
                raise SystemExit("image %d pixels changed; cannot guarantee device-identical "
                                 "framebuffer" % i)
        elif blk["type"] == "binary":
            stream_path = os.path.join(outdir, "streams", "%02d.z" % i)
            if os.path.isfile(stream_path):
                streams.append(open(stream_path, "rb").read())
            else:
                raise SystemExit("missing stored compressed stream for binary block %d" % i)
            stats["binary"] += 1
        else:
            raise SystemExit("unexpected block type %r" % blk["type"])

    count = len(streams)
    table_size = 8 + 4 * count
    total = table_size + sum(len(s) for s in streams)
    struct.pack_into("<I", header, 4, total)      # header.size = payload length
    out = bytearray()
    out += header
    out += p32(count) + p32(total)
    pos = table_size
    for s in streams:
        out += p32(pos)
        pos += len(s)
    for s in streams:
        out += s
    write_bytes(out_path, bytes(out))
    if verbose:
        print("repacked %d block(s) -> %s (%d bytes; reused=%d recompressed=%d binary=%d)"
              % (count, out_path, len(out), stats["reused"], stats["recompressed"],
                 stats["binary"]))
    return stats


# --------------------------------------------------------------------------- #
# comparison / reporting
# --------------------------------------------------------------------------- #
def compare_files(a, b):
    if a == b:
        return True, []
    diffs = []
    n = min(len(a), len(b))
    i = 0
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            diffs.append((i, j - i))
            i = j
        else:
            i += 1
    if len(a) != len(b):
        diffs.append((n, abs(len(a) - len(b))))
    return False, diffs


def describe_diffs(src, out, man):
    """Attribute raw byte differences to header / TOC / block streams."""
    _, diffs = compare_files(src, out)
    if not diffs:
        return "byte-identical"
    labels = []
    toc_end = TOC_OFFSET + 8 + 4 * man["container"].get("toc_count", 0)
    for off, ln in diffs[:20]:
        if off < HEADER_SIZE:
            what = "512-byte header"
        elif off < toc_end:
            what = "block table"
        else:
            idx = None
            for b in man["blocks"]:
                s = b["file_offset"]
                e = s + b["stored_len"]
                if off < e or (idx is None and off >= s):
                    if s <= off < s + b["stored_len"] or off >= s:
                        idx = b["index"]
                if s <= off < e:
                    idx = b["index"]
                    break
            what = "block %d zlib stream" % idx if idx is not None else "tail/other"
        labels.append("0x%X..0x%X (%d bytes) in %s" % (off, off + ln, ln, what))
    return "differences: " + "; ".join(labels)


def print_table(c, sizes_table):
    """sizes.txt line for each block ("" if n/a)."""
    print("idx  file_off  rel_off  stored  zlib  raw_len   WxHxBPP     type")
    for blk in c.blocks:
        line = "-"
        if blk["type"] == "image":
            line = "%dx%dx%d" % (blk["width"], blk["height"], blk["bpp"])
        print("%3d  0x%06X  0x%06X  %7d  %-4s  %8s   %-11s %s"
              % (blk["index"], blk["file_offset"], blk.get("rel_offset") or 0,
                 blk["stored_len"], blk.get("zlib_header", ""),
                 blk.get("raw_len"), line, blk["type"]))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="MTK logo.bin parser / extractor / repacker (analysis aid)")
    ap.add_argument("input", help="logo.bin / lk.bin (MTK partition dump)")
    ap.add_argument("outdir", help="directory for PNGs + manifest + original streams")
    ap.add_argument("--repack", metavar="OUT.bin", help="repack extracted data to OUT.bin")
    ap.add_argument("--recompress", action="store_true",
                    help="re-deflate images instead of reusing original zlib streams")
    ap.add_argument("--sizes", metavar="PATH", help="sizes.txt to use")
    ap.add_argument("--table", action="store_true", help="print block inventory table")
    ap.add_argument("--force", action="store_true", help="recreate outdir")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    data = open(args.input, "rb").read()
    sizes_table, sizes_rows, sizes_path = load_sizes(args.input, args.sizes)
    c = parse_container(data, sizes_table, sizes_rows)
    if not args.quiet:
        print("input : %s (%d bytes sha256=%s)" % (args.input, len(data), sha256(data)))
        print("header: magic=0x%08X size=%d tag=%r" % (c.magic, c.size_field, c.tag))
    manifest = extract(c, args.outdir, args.input, sizes_path, args.force, not args.quiet)
    if args.table:
        print_table(c, sizes_table)
    rc = 0
    if args.repack:
        repack(args.outdir, args.repack, args.recompress, not args.quiet)
        out = open(args.repack, "rb").read()
        same, _ = compare_files(data, out)
        if not args.quiet:
            print("repack compared to input: %s"
                  % ("BYTE-IDENTICAL (sha256=%s)" % sha256(out) if same
                     else "NOT identical -- " + describe_diffs(data, out, manifest)))
        rc = 0 if same else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
