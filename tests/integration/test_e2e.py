#!/usr/bin/env python3
"""End-to-end acceptance tests for the LogoBuilder rebuild.

Written against ``docs/spec_api.md`` (the GUI/core contract) and the real shipped
samples.  The module fails *soft* while ``logobuilder.core`` is still being
written: :func:`pytest.importorskip` turns the whole file into skips instead of
collection errors.

Coverage map (acceptance deliverables)
--------------------------------------
* real-sample oracle            -> independent parse + exact block histogram
* byte-identical roundtrip      -> ``logo.bin`` / ``lk.bin`` SHA-256 before/after
* extract -> edit -> pack       -> solid-red replacement + untouched blocks
* update.zip                    -> entries, /dev/logo script, template binary
* project lifecycle             -> new/save/reopen/validate/repair/legacy import
* CLI                           -> info/extract/pack/zip/sizes + errors/exit codes
* size inference                -> candidate ranking includes the true size
* pixel oracle                  -> RGB565 bijection + BGRA byte order
* packaging hygiene             -> core has no tkinter/ttkbootstrap imports

Run::

    python -m pytest tests/integration -q
    python -m pytest tests -q
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from collections import Counter
from pathlib import Path

import pytest

# Fail soft until core-dev lands src/logobuilder/core/__init__.py.
pytest.importorskip("logobuilder.core")

from PIL import Image  # noqa: E402

import make_fixtures  # noqa: E402  (tests/fixtures is on sys.path via conftest)
import oracle_sample  # noqa: E402  (independent struct+zlib oracle)

from logobuilder import core  # noqa: E402
from logobuilder.core import (  # noqa: E402
    BinaryBlock,
    CorruptContainer,
    ExportError,
    ImageBlock,
    LogoBuilderError,
    LogoContainer,
    LogoProject,
    LogoSize,
    NotLogoFile,
    SizeDB,
    decode_pixels,
    image_to_pixels,
    inspect_update_zip,
    is_mtk_logo,
    make_update_zip,
    pixels_to_image,
    read_boot_logo,
    rgb565_to_rgb888,
    rgb888_to_rgb565,
    sniff_format,
    write_boot_logo,
)

ROOT = Path(__file__).resolve().parents[2]
REAL_LOGO = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "logo.bin"
REAL_LK = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "lk.bin"
TEMPLATE_ZIP = ROOT / "src" / "logobuilder" / "assets" / "update_template.zip"

REAL_LOGO_SHA256 = oracle_sample.EXPECTED_LOGO_SHA256
REAL_LK_SHA256 = oracle_sample.EXPECTED_LK_SHA256
EXPECTED_HISTOGRAM = oracle_sample.EXPECTED_LOGO_HISTOGRAM

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
ANDROID_META = "META-INF/com/google/android/"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256(Path(path).read_bytes())


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    """Session-scoped fixture corpus (generated on demand, cached on disk)."""
    make_fixtures.ensure()
    return make_fixtures.DEFAULT_OUT


def fixture_bytes(fixture_root: Path, rel_path: str) -> bytes:
    return (fixture_root / rel_path).read_bytes()


def container_histogram(container: LogoContainer) -> dict[str, int]:
    hist: Counter[str] = Counter()
    for block in container.blocks:
        if isinstance(block, ImageBlock):
            hist[str(block.size)] += 1
        else:
            hist["binary"] += 1
    return dict(hist)


def error_issues(project: LogoProject) -> list[str]:
    """Return only error-level validation issues (warnings are legitimate states)."""
    return [str(issue) for issue in project.validate() if issue.level == "error"]


def assert_readable_chinese_error(exc: BaseException) -> None:
    message = getattr(exc, "message", None) or str(exc)
    assert message, "error message must not be empty"
    assert CJK_RE.search(message), f"error message should be Chinese/readable: {message!r}"


def run_cli(*args: object, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    """Run ``python -m logobuilder.core.cli`` as a real subprocess."""
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, "-m", "logobuilder.core.cli", *[str(arg) for arg in args]]
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=env,
    )


def copy_legacy_project(fixture_root: Path, destination: Path) -> Path:
    source = fixture_root / "project" / "legacy_project"
    target = destination / "legacy_project"
    shutil.copytree(source, target)
    return target


# ===========================================================================
# 1. Independent oracle for the shipped samples (no logobuilder involved)
# ===========================================================================
def test_oracle_real_logo_bin_independent() -> None:
    result = oracle_sample.check_logo_bin(REAL_LOGO)
    failed = [check for check in result["checks"] if not check["ok"]]
    assert not failed, f"independent oracle failures: {failed}"
    assert result["block_count"] == 51
    assert result["histogram"] == EXPECTED_HISTOGRAM


def test_oracle_real_lk_bin_is_raw_payload() -> None:
    result = oracle_sample.check_lk_bin(REAL_LK)
    failed = [check for check in result["checks"] if not check["ok"]]
    assert not failed, f"independent oracle failures: {failed}"
    assert result["block_table_attempt"]["valid"] is False


def test_oracle_repack_is_byte_identical() -> None:
    data = REAL_LOGO.read_bytes()
    parsed = oracle_sample.parse_logo_bin(data)
    rebuilt = oracle_sample.repack_from_parsed(data, parsed)
    assert sha256(rebuilt) == sha256(data) == REAL_LOGO_SHA256


# ===========================================================================
# 2. Real-sample core acceptance: parse + byte-identical roundtrip
# ===========================================================================
def test_real_logo_parse_matches_oracle() -> None:
    data = REAL_LOGO.read_bytes()
    oracle = oracle_sample.parse_logo_bin(data)
    container = LogoContainer.load(REAL_LOGO)

    assert container.mode == "block-table"
    assert container.tag == "LOGO"
    assert container.count == 51
    assert container.data_len == oracle["header"]["data_len"] == 2_005_959
    assert container.header[:4] == data[:4]
    assert container.header[8:] == data[8:512]
    assert container_histogram(container) == EXPECTED_HISTOGRAM

    # every image block: pixels == zlib decompress(raw) and no pending blocks
    assert container.pending_image_blocks == []
    for block in container.blocks:
        assert isinstance(block, ImageBlock), f"block {block.index} should be an image block"
        raw = block.raw_compressed
        assert raw is not None
        assert block.pixels == zlib.decompress(raw)


def test_real_logo_roundtrip_byte_identical() -> None:
    original = REAL_LOGO.read_bytes()
    container = LogoContainer.load(REAL_LOGO)
    rebuilt = container.to_bytes()
    assert len(rebuilt) == len(original)
    assert sha256(rebuilt) == sha256(original) == REAL_LOGO_SHA256


def test_real_logo_save_roundtrip(tmp_path: Path) -> None:
    container = LogoContainer.load(REAL_LOGO)
    target = container.save(tmp_path / "resaved_logo.bin")
    assert sha256_file(target) == REAL_LOGO_SHA256


def test_real_lk_raw_mode_lossless() -> None:
    original = REAL_LK.read_bytes()
    container = LogoContainer.load(REAL_LK)
    assert container.mode == "raw"
    assert container.tag == "LK"
    assert container.raw_payload == original[512:]
    assert sha256(container.to_bytes()) == sha256(original) == REAL_LK_SHA256
    # a raw payload must NOT be forced through the zlib block table
    assert container.pending_image_blocks == []


def test_real_lk_via_bytes_parse() -> None:
    original = REAL_LK.read_bytes()
    container = LogoContainer.parse(original)
    assert container.mode == "raw"
    assert container.to_bytes() == original


def test_sniff_format_real_samples() -> None:
    logo = REAL_LOGO.read_bytes()
    lk = REAL_LK.read_bytes()
    assert is_mtk_logo(logo) and is_mtk_logo(lk)
    assert not is_mtk_logo(b"not a logo")
    assert sniff_format(logo).startswith("mtk-logo")
    assert sniff_format(lk).startswith("mtk-logo")
    assert sniff_format(logo) != sniff_format(lk)
    assert not sniff_format(b"plain text").startswith("mtk-logo")
    assert sniff_format(b"") not in ("mtk-logo",)


def test_pad_to_partition_size() -> None:
    original = REAL_LOGO.read_bytes()
    container = LogoContainer.load(REAL_LOGO)
    target = len(original) + 4096
    padded = container.to_bytes(pad_to_size=target)
    assert len(padded) == target
    assert padded[: len(original)] == original
    assert set(padded[len(original):]) == {0}
    with pytest.raises(ExportError):
        container.to_bytes(pad_to_size=len(original) - 1)


# ===========================================================================
# 3. Synthetic fixture matrix
# ===========================================================================
ROUNDTRIP_CASES = [
    "valid/logo_bpp4_540x960.bin",
    "valid/logo_bpp2_1080x1920.bin",
    "valid/logo_mixed.bin",
    "valid/logo_tag_lk.bin",
    "valid/logo_pad_ff.bin",
    "valid/logo_pad_zero.bin",
    "valid/logo_pad_pattern.bin",
]


@pytest.mark.parametrize("rel_path", ROUNDTRIP_CASES)
def test_valid_synthetic_roundtrip_byte_identical(fixture_root: Path, rel_path: str) -> None:
    data = fixture_bytes(fixture_root, rel_path)
    container = LogoContainer.parse(data)
    rebuilt = container.to_bytes()
    assert sha256(rebuilt) == sha256(data), f"{rel_path} is not byte-identical after roundtrip"
    assert rebuilt[:4] == data[:4]
    assert rebuilt[8:512] == data[8:512]


@pytest.mark.parametrize(
    "rel_path",
    ["valid/logo_pad_ff.bin", "valid/logo_pad_zero.bin", "valid/logo_pad_pattern.bin"],
)
def test_header_padding_variants_preserved(fixture_root: Path, rel_path: str) -> None:
    data = fixture_bytes(fixture_root, rel_path)
    container = LogoContainer.parse(data)
    assert container.count == 1
    assert container.to_bytes() == data  # padding bytes 16..511 kept verbatim


def test_synthetic_bpp2_and_bpp4_metadata(fixture_root: Path) -> None:
    bpp4 = LogoContainer.parse(fixture_bytes(fixture_root, "valid/logo_bpp4_540x960.bin"))
    assert bpp4.count == 1
    assert isinstance(bpp4.blocks[0], ImageBlock)
    assert bpp4.blocks[0].size == LogoSize(540, 960, 4)
    assert len(bpp4.blocks[0].pixels) == 540 * 960 * 4

    bpp2 = LogoContainer.parse(fixture_bytes(fixture_root, "valid/logo_bpp2_1080x1920.bin"))
    assert bpp2.count == 1
    assert isinstance(bpp2.blocks[0], ImageBlock)
    assert bpp2.blocks[0].size == LogoSize(1080, 1920, 2)
    assert len(bpp2.blocks[0].pixels) == 1080 * 1920 * 2


def test_mixed_image_and_binary_blocks(fixture_root: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/logo_mixed.bin")
    container = LogoContainer.parse(data)
    assert container.count == 4
    kinds = [type(block).__name__ for block in container.blocks]
    assert kinds == ["ImageBlock", "BinaryBlock", "ImageBlock", "BinaryBlock"]
    assert isinstance(container.blocks[0], ImageBlock)
    assert container.blocks[0].size == LogoSize(540, 960, 4)
    assert container.blocks[1].decompressed_ok is True       # zlib-binary, unknown size
    assert container.blocks[3].decompressed_ok is False      # raw binary, not zlib
    assert container.blocks[3].raw_compressed is not None
    assert 1 in container.pending_image_blocks
    assert 3 not in container.pending_image_blocks
    assert container.to_bytes() == data


def test_tag_lk_with_valid_block_table(fixture_root: Path) -> None:
    container = LogoContainer.parse(fixture_bytes(fixture_root, "valid/logo_tag_lk.bin"))
    assert container.tag == "LK"
    assert container.mode == "block-table"
    assert container.count == 1
    assert isinstance(container.blocks[0], ImageBlock)


def test_empty_container_allowed(fixture_root: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/empty_container.bin")
    container = LogoContainer.parse(data)
    assert container.count == 0
    assert container.blocks == []
    assert container.to_bytes() == data


def test_trailing_bytes_are_ignored(fixture_root: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/logo_trailing_bytes.bin")
    container = LogoContainer.parse(data)
    assert container.count == 1
    assert len(container.trailing) == 20
    # trailing bytes are not part of the container payload and are dropped on write
    assert container.to_bytes() == data[: len(data) - 20]


def test_unknown_size_block_pending_and_assign(fixture_root: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/logo_unknown_size.bin")
    container = LogoContainer.parse(data)
    assert container.count == 2
    assert isinstance(container.blocks[0], ImageBlock)
    assert isinstance(container.blocks[1], BinaryBlock)
    assert container.blocks[1].decompressed_ok is True
    assert container.pending_image_blocks == [1]
    assert len(container.blocks[1].data) == 100_000

    # a size whose `key` matches the decompressed length converts it to an image block
    image_block = container.assign_size(1, LogoSize(400, 125, 2))
    assert isinstance(image_block, ImageBlock)
    assert container.pending_image_blocks == []
    assert container.to_bytes() == data  # raw zlib bytes reused -> lossless

    with pytest.raises(LogoBuilderError):
        container.assign_size(0, LogoSize(400, 125, 2))


def test_zlib_corrupt_block_degrades_to_binary(fixture_root: Path) -> None:
    data = fixture_bytes(fixture_root, "corrupt/block_zlib_corrupt.bin")
    container = LogoContainer.parse(data)
    assert container.count == 3
    assert isinstance(container.blocks[1], BinaryBlock)
    assert container.blocks[1].decompressed_ok is False
    assert container.blocks[1].raw_compressed is not None
    assert sha256(container.to_bytes()) == sha256(data)


CORRUPT_CASES = [
    ("corrupt/truncated_header.bin", ("CorruptContainer", "NotLogoFile")),
    ("corrupt/bad_magic.bin", ("NotLogoFile",)),
    ("corrupt/datalen_too_large.bin", ("CorruptContainer",)),
    ("corrupt/offset0_wrong.bin", ("CorruptContainer",)),
    ("corrupt/offsets_nonmonotonic.bin", ("CorruptContainer",)),
    ("corrupt/offsets_beyond_datalen.bin", ("CorruptContainer",)),
    ("corrupt/huge_count.bin", ("CorruptContainer",)),
    ("corrupt/not_logo_random.bin", ("NotLogoFile",)),
]


@pytest.mark.parametrize("rel_path,expected_classes", CORRUPT_CASES)
def test_corrupt_fixtures_raise_readable_errors(
    fixture_root: Path, rel_path: str, expected_classes: tuple[str, ...]
) -> None:
    data = fixture_bytes(fixture_root, rel_path)
    with pytest.raises(LogoBuilderError) as excinfo:
        LogoContainer.parse(data)
    assert isinstance(excinfo.value, tuple(getattr(core, name) for name in expected_classes)), (
        f"{rel_path}: got {type(excinfo.value).__name__}, "
        f"expected one of {expected_classes}"
    )
    assert_readable_chinese_error(excinfo.value)


@pytest.mark.parametrize(
    "rel_path",
    [
        "corrupt/datalen_echo_mismatch.bin",
        "corrupt/count_zero_datalen_mismatch.bin",
    ],
)
def test_inconsistent_toc_datalen_is_lenient_with_warnings(
    fixture_root: Path, rel_path: str
) -> None:
    """Lead-approved lenient behavior (docs/spec_api.md): recover + warning, no error.

    A dataLen echo mismatch must not abort parsing, but the problem must be
    diagnosable: ``container.warnings`` records expected/actual values and the CLI
    prints the warning to stderr while still exiting 0.  ``to_bytes()`` must always
    rewrite a self-consistent container.
    """
    data = fixture_bytes(fixture_root, rel_path)
    container = LogoContainer.parse(data)  # must not raise
    warnings = " ".join(container.warnings)
    assert container.warnings, f"{rel_path}: expected at least one warning"
    assert "dataLen" in warnings
    assert "头部" in warnings and "块表" in warnings
    assert re.search(r"\d+", warnings), "warning should contain expected/actual byte counts"

    rewritten = container.to_bytes()
    reparsed = LogoContainer.parse(rewritten)
    # the dataLen warning must be gone after the self-consistent rewrite; a
    # genuinely corrupt zlib block (present in one fixture) may still warn.
    assert not any("dataLen" in warning and "不一致" in warning for warning in reparsed.warnings)
    assert reparsed.to_bytes() == rewritten, "rewrite must be idempotent/self-consistent"

    result = run_cli("info", fixture_root / rel_path)
    assert result.returncode == 0, result.stderr
    assert "警告" in result.stderr, f"warning should go to stderr: {result.stderr!r}"
    assert "dataLen" in result.stderr


# ===========================================================================
# 4. Pixel-layer oracle (independent math, no trusting the same code path)
# ===========================================================================
def test_rgb565_bijection_all_values() -> None:
    """Any decode output must re-encode to the *same* 16-bit value."""
    for value in range(0x10000):
        r, g, b = rgb565_to_rgb888(value)
        assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255
        assert rgb888_to_rgb565(r, g, b) == value, f"RGB565 roundtrip broken for 0x{value:04X}"


def test_rgb565_primary_colors() -> None:
    red = image_to_pixels(Image.new("RGBA", (2, 2), (255, 0, 0, 255)), LogoSize(2, 2, 2))
    assert struct.unpack("<4H", red) == (0xF800,) * 4
    green = image_to_pixels(Image.new("RGBA", (2, 2), (0, 255, 0, 255)), LogoSize(2, 2, 2))
    assert struct.unpack("<4H", green) == (0x07E0,) * 4
    blue = image_to_pixels(Image.new("RGBA", (2, 2), (0, 0, 255, 255)), LogoSize(2, 2, 2))
    assert struct.unpack("<4H", blue) == (0x001F,) * 4


def test_bpp4_bgra_byte_order() -> None:
    image = Image.new("RGBA", (2, 2), (10, 20, 30, 40))
    pixels = image_to_pixels(image, LogoSize(2, 2, 4))
    assert pixels == bytes([30, 20, 10, 40]) * 4  # stored B,G,R,A


def test_decode_pixels_bpp4_is_identity() -> None:
    data = os.urandom(64)
    assert decode_pixels(data, LogoSize(4, 4, 4)) == data


def test_real_logo_pixels_decode_independently() -> None:
    """Independent check: block 0 pixels are the raw zlib payload (BGRA layout)."""
    data = REAL_LOGO.read_bytes()
    parsed = oracle_sample.parse_logo_bin(data)
    first = parsed["blocks"][0]
    raw = parsed["table"]["payload"][
        first["offset"] : first["offset"] + first["compressed_len"]
    ]
    expected_pixels = zlib.decompress(raw)

    container = LogoContainer.load(REAL_LOGO)
    assert container.blocks[0].pixels == expected_pixels
    # BGRA -> RGBA image conversion verified on the first two pixels
    image = container.blocks[0].to_image()
    for x in range(2):
        b, g, r, a = expected_pixels[x * 4 : x * 4 + 4]
        assert image.getpixel((x, 0)) == (r, g, b, a)


def test_decode_pixels_bpp2_vs_image() -> None:
    encoded = b"".join(struct.pack("<H", v) for v in (0x0000, 0xFFFF, 0xF800, 0x001F))
    size = LogoSize(2, 2, 2)
    pixels = decode_pixels(encoded, size)  # expanded to compact BGRA
    assert len(pixels) == 4 * 4
    # original C# Color16ToInt linear scaling: channel = bits * (256 >> bit_count)
    assert pixels[0:4] == bytes([0, 0, 0, 255])            # 0x0000 -> black
    assert pixels[4:8] == bytes([248, 252, 248, 255])      # 0xFFFF -> (248,252,248)
    b, g, r, a = pixels[8:12]                              # 0xF800 -> red (BGRA order)
    assert (r, g, b, a) == (248, 0, 0, 255)
    assert pixels[12:16] == bytes([248, 0, 0, 255])        # 0x001F -> blue
    image = pixels_to_image(encoded, size)
    assert image.size == (2, 2)
    assert image.getpixel((0, 0)) == (0, 0, 0, 255)
    assert image.getpixel((1, 0)) == (248, 252, 248, 255)


def test_image_to_pixels_stretch_fit() -> None:
    image = Image.new("RGBA", (10, 10), (1, 2, 3, 255))
    pixels = image_to_pixels(image, LogoSize(540, 960, 4))
    assert len(pixels) == 540 * 960 * 4
    assert pixels[:4] == bytes([3, 2, 1, 255])  # B,G,R,A little-endian


# ===========================================================================
# 5. Size inference
# ===========================================================================
def test_size_db_default_exact_match() -> None:
    database = SizeDB.default()
    exact = database.exact(540 * 960 * 4)
    assert exact == [LogoSize(540, 960, 4)]
    assert LogoSize(540, 960, 4) in database


def test_size_candidates_ranking_contains_true_size() -> None:
    # Synthetic ambiguity: two entries share the same key (540*960*4 == 960*540*4).
    database = SizeDB([LogoSize(540, 960, 4), LogoSize(960, 540, 4)])
    assert len(database.exact(540 * 960 * 4)) == 2
    candidates = database.candidates(540 * 960 * 4, aspect_hint=540 / 960)
    sizes = [candidate.size for candidate in candidates]
    assert LogoSize(540, 960, 4) in sizes
    assert candidates[0].size == LogoSize(540, 960, 4)
    assert candidates[0].score >= 0.9


def test_size_candidates_unknown_length_derives_true_size() -> None:
    # 400x125x2 == 100_000 bytes, absent from the shipped sizes table.
    database = SizeDB.default()
    assert database.exact(100_000) == []
    candidates = database.candidates(100_000, aspect_hint=400 / 125)
    sizes = [candidate.size for candidate in candidates]
    assert LogoSize(400, 125, 2) in sizes, f"true size missing from candidates: {sizes[:10]}"


def test_guess_sizes_for_blocks() -> None:
    guessed = core.guess_sizes_for_blocks([540 * 960 * 4, 540 * 960 * 4, 1080 * 1920 * 2])
    assert len(guessed) == 3
    assert guessed[0][0].size == LogoSize(540, 960, 4)
    assert any(candidate.size == LogoSize(1080, 1920, 2) for candidate in guessed[2])


# ===========================================================================
# 6. Project lifecycle
# ===========================================================================
def test_project_new_save_reopen_validate_clean(fixture_root: Path, tmp_path: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/logo_bpp4_540x960.bin")
    source = LogoContainer.parse(data)
    project_dir = tmp_path / "proj"
    project = LogoProject.new(project_dir, source)

    assert (project_dir / "header.bin").is_file()
    assert (project_dir / "header.bin").stat().st_size == 512
    assert (project_dir / "project.json").is_file()
    assert (project_dir / "img1.png").is_file()
    assert project.count == 1
    assert project.validate() == []

    project.save()
    reopened = LogoProject.open(project_dir)
    assert reopened.validate() == []
    assert reopened.count == 1
    assert isinstance(reopened.blocks[0].size, LogoSize)
    assert reopened.blocks[0].size == LogoSize(540, 960, 4)
    # untouched project packs back byte-identically (raw zlib reused)
    assert reopened.to_container().to_bytes() == data


def test_project_real_logo_extract_pack_byte_identical(tmp_path: Path) -> None:
    original = REAL_LOGO.read_bytes()
    container = LogoContainer.load(REAL_LOGO)
    project = LogoProject.new(tmp_path / "logo_proj", container)
    packed = project.to_container().to_bytes()
    assert sha256(packed) == sha256(original) == REAL_LOGO_SHA256


def test_project_replace_image_solid_red(tmp_path: Path) -> None:
    original = REAL_LOGO.read_bytes()
    container = LogoContainer.load(REAL_LOGO)
    project = LogoProject.new(tmp_path / "logo_proj", container)

    red = Image.new("RGBA", (540, 960), (255, 0, 0, 255))
    project.replace_image(0, red)
    project.save()
    packed = project.to_container().to_bytes()

    modified = LogoContainer.parse(packed)
    assert modified.count == 51
    assert isinstance(modified.blocks[0], ImageBlock)
    assert modified.blocks[0].size == LogoSize(540, 960, 4)
    expected_pixels = image_to_pixels(red, LogoSize(540, 960, 4))
    assert modified.blocks[0].pixels == expected_pixels
    # decoded image is solid red
    image = modified.blocks[0].to_image()
    assert image.getpixel((0, 0)) == (255, 0, 0, 255)
    assert image.getpixel((539, 959)) == (255, 0, 0, 255)

    # header magic/dataLen/offsets stay self-consistent; all other blocks kept verbatim
    assert modified.header[:4] == original[:4]
    assert modified.data_len == len(packed) - 512
    for index in range(1, 51):
        assert modified.blocks[index].raw_compressed == container.blocks[index].raw_compressed, (
            f"block {index} compressed bytes changed although only block 0 was edited"
        )
    assert sha256(packed) != sha256(original)


def test_project_set_block_size_unknown_block(fixture_root: Path, tmp_path: Path) -> None:
    data = fixture_bytes(fixture_root, "valid/logo_unknown_size.bin")
    container = LogoContainer.parse(data)
    project = LogoProject.new(tmp_path / "unknown_proj", container)
    assert project.pending == [1]

    project.set_block_size(1, LogoSize(400, 125, 2))
    assert project.pending == []
    # NOTE: current implementation leaves the stale binary file behind, which
    # validate() reports as an (unreferenced-file) warning; errors must be empty.
    assert error_issues(project) == []
    built = project.to_container()
    assert isinstance(built.blocks[1], ImageBlock)
    assert built.blocks[1].size == LogoSize(400, 125, 2)
    packed = built.to_bytes()
    # 100_000 bytes is absent from the sizes table -> re-parse needs the explicit size
    rebuilt = LogoContainer.parse(packed, block_sizes=[None, LogoSize(400, 125, 2)])
    assert isinstance(rebuilt.blocks[1], ImageBlock)
    assert rebuilt.blocks[1].size == LogoSize(400, 125, 2)


def test_legacy_project_import_equivalent_blocks(fixture_root: Path, tmp_path: Path) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    project = LogoProject.import_legacy(project_dir)
    assert project.count == 2
    assert [block.kind for block in project.blocks] == ["image", "binary"]
    assert project.blocks[0].size == LogoSize(540, 960, 4)
    assert project.blocks[1].binary_path is not None
    assert (project_dir / project.blocks[1].binary_path).is_file()

    source = LogoContainer.parse(fixture_bytes(fixture_root, "project/legacy_source_container.bin"))
    assert source.count == project.count == 2
    assert source.blocks[0].size == project.blocks[0].size
    assert len(source.blocks[1].data) == 2048
    assert len((project_dir / project.blocks[1].binary_path).read_bytes()) == 2048


def test_legacy_project_open_auto_detects(fixture_root: Path, tmp_path: Path) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    project = LogoProject.open(project_dir)  # auto-converts .project
    assert project.count == 2
    project.save()
    assert project.validate() == []
    assert (project_dir / "project.json").is_file()


def test_legacy_project_import_with_source_is_lossless(
    fixture_root: Path, tmp_path: Path
) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    source_bytes = fixture_bytes(fixture_root, "project/legacy_source_container.bin")
    source = LogoContainer.parse(source_bytes)
    project = LogoProject.import_legacy(project_dir, source_container=source)
    packed = project.to_container().to_bytes()
    assert sha256(packed) == sha256(source_bytes)


def test_project_repair_missing_image(fixture_root: Path, tmp_path: Path) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    source = LogoContainer.parse(fixture_bytes(fixture_root, "project/legacy_source_container.bin"))
    project = LogoProject.import_legacy(project_dir, source_container=source)
    project.save()
    image_path = project_dir / project.blocks[0].image_path
    image_path.unlink()

    issues = project.validate()
    assert any("图片缺失" in str(issue) for issue in issues), [str(i) for i in issues]
    repair_messages = project.repair(repair=True)
    assert any("已修复" in message for message in repair_messages), repair_messages
    assert image_path.is_file()
    assert error_issues(project) == []


def test_project_repair_wrong_image_size(fixture_root: Path, tmp_path: Path) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    source = LogoContainer.parse(fixture_bytes(fixture_root, "project/legacy_source_container.bin"))
    project = LogoProject.import_legacy(project_dir, source_container=source)
    project.save()
    image_path = project_dir / project.blocks[0].image_path
    Image.new("RGBA", (100, 100), (0, 255, 0, 255)).save(image_path, "PNG")

    issues = project.validate()
    assert any("不一致" in str(issue) for issue in issues), [str(i) for i in issues]
    project.repair(repair=True)
    assert error_issues(project) == []
    with Image.open(image_path) as handle:
        assert handle.size == (540, 960)


def test_project_repair_missing_binary(fixture_root: Path, tmp_path: Path) -> None:
    project_dir = copy_legacy_project(fixture_root, tmp_path)
    source = LogoContainer.parse(fixture_bytes(fixture_root, "project/legacy_source_container.bin"))
    project = LogoProject.import_legacy(project_dir, source_container=source)
    project.save()
    binary_path = project_dir / project.blocks[1].binary_path
    binary_path.unlink()

    issues = project.validate()
    assert any("二进制数据缺失" in str(issue) for issue in issues), [str(i) for i in issues]
    project.repair(repair=True)
    assert binary_path.is_file()
    assert error_issues(project) == []


# ===========================================================================
# 7. update.zip flashable package
# ===========================================================================
def _template_binary_sha256() -> str:
    with zipfile.ZipFile(TEMPLATE_ZIP, "r") as archive:
        return sha256(archive.read(ANDROID_META + "update-binary"))


def test_make_update_zip_contents(tmp_path: Path) -> None:
    logo_bytes = LogoContainer.load(REAL_LOGO).to_bytes()
    out = tmp_path / "update.zip"
    produced = make_update_zip(logo_bytes, out)
    assert produced == out and out.is_file()

    with zipfile.ZipFile(out, "r") as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert "logo.bin" in names
        assert ANDROID_META + "update-binary" in names
        assert ANDROID_META + "updater-script" in names
        assert "META-INF/MANIFEST.MF" in names
        assert archive.read("logo.bin") == logo_bytes
        script = archive.read(ANDROID_META + "updater-script").decode("utf-8")
        assert 'package_extract_file("logo.bin", "/dev/logo")' in script
        assert sha256(archive.read(ANDROID_META + "update-binary")) == _template_binary_sha256()

    info = inspect_update_zip(out)
    assert "logo.bin" in info["files"]
    assert "/dev/logo" in info["script"]
    assert info["binary_sha256"] == _template_binary_sha256()


def test_make_update_zip_is_deterministic(tmp_path: Path) -> None:
    logo_bytes = LogoContainer.load(REAL_LOGO).to_bytes()
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    make_update_zip(logo_bytes, first)
    make_update_zip(logo_bytes, second)
    assert sha256_file(first) == sha256_file(second)


def test_make_update_zip_custom_script_and_binary(tmp_path: Path) -> None:
    logo_bytes = LogoContainer.load(REAL_LOGO).to_bytes()
    custom_script = 'ui_print("QA custom");\npackage_extract_file("logo.bin", "/dev/logo");\n'
    custom_binary = tmp_path / "update-binary"
    custom_binary.write_bytes(b"#!/sbin/sh\n# qa custom update-binary\n")
    out = tmp_path / "custom.zip"
    make_update_zip(
        logo_bytes, out, logo_filename="logo.img", updater_script=custom_script,
        update_binary=custom_binary,
    )
    with zipfile.ZipFile(out, "r") as archive:
        assert archive.read(ANDROID_META + "updater-script").decode() == custom_script
        assert archive.read(ANDROID_META + "update-binary") == custom_binary.read_bytes()
        assert archive.read("logo.img") == logo_bytes
        assert "logo.bin" not in archive.namelist()


# ===========================================================================
# 8. CLI (subprocess, real exit codes / stderr)
# ===========================================================================
def test_cli_info_real_logo() -> None:
    result = run_cli("info", REAL_LOGO)
    assert result.returncode == 0, result.stderr
    assert "51" in result.stdout
    assert "540x960x4" in result.stdout
    assert "LOGO" in result.stdout


def test_cli_info_json() -> None:
    result = run_cli("info", REAL_LOGO, "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 51
    assert payload["tag"] == "LOGO"


def test_cli_info_lk_raw_mode() -> None:
    result = run_cli("info", REAL_LK)
    assert result.returncode == 0, result.stderr
    assert "LK" in result.stdout
    assert "raw" in result.stdout.lower()


def test_cli_extract_pack_roundtrip(tmp_path: Path) -> None:
    project_dir = tmp_path / "cli_proj"
    extract = run_cli("extract", REAL_LOGO, project_dir)
    assert extract.returncode == 0, extract.stderr
    assert (project_dir / "project.json").is_file()
    assert (project_dir / "header.bin").is_file()
    assert len(list(project_dir.glob("img*.png"))) == 51

    packed = tmp_path / "packed.bin"
    pack = run_cli("pack", project_dir, packed)
    assert pack.returncode == 0, pack.stderr
    assert sha256_file(packed) == REAL_LOGO_SHA256


def test_cli_zip(tmp_path: Path) -> None:
    out = tmp_path / "update.zip"
    result = run_cli("zip", REAL_LOGO, out)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(out, "r") as archive:
        assert "logo.bin" in archive.namelist()
        assert ANDROID_META + "updater-script" in archive.namelist()


def test_cli_sizes() -> None:
    result = run_cli("sizes")
    assert result.returncode == 0, result.stderr
    assert "540x960x4" in result.stdout


@pytest.mark.parametrize(
    "rel_path",
    [
        "corrupt/bad_magic.bin",
        "corrupt/datalen_too_large.bin",
        "corrupt/huge_count.bin",
        "corrupt/not_logo_random.bin",
        "corrupt/truncated_header.bin",
    ],
)
def test_cli_corrupt_inputs_fail_with_chinese_error(fixture_root: Path, rel_path: str) -> None:
    result = run_cli("info", fixture_root / rel_path)
    assert result.returncode == 1, (result.returncode, result.stdout, result.stderr)
    assert CJK_RE.search(result.stderr), f"stderr should be a readable Chinese error: {result.stderr!r}"


def test_cli_missing_arguments_exit_code_2() -> None:
    result = run_cli("info")
    assert result.returncode == 2


# ===========================================================================
# 9. boot_logo (bare RGB565 second screen)
# ===========================================================================
def test_boot_logo_fixture_roundtrip_exact(fixture_root: Path, tmp_path: Path) -> None:
    raw = fixture_bytes(fixture_root, "raw/boot_logo_240x320.bin")
    image, size = read_boot_logo(fixture_root / "raw/boot_logo_240x320.bin")
    assert size == LogoSize(240, 320, 2)
    assert image.size == (240, 320)
    out = tmp_path / "boot_logo.out"
    written_size = write_boot_logo(image, out)
    assert written_size == size
    assert out.read_bytes() == raw, "RGB565 encode(decode(x)) must be lossless"


def test_boot_logo_from_image(tmp_path: Path) -> None:
    image = Image.new("RGBA", (240, 320), (255, 0, 0, 255))
    out = tmp_path / "boot_logo"
    size = write_boot_logo(image, out)
    assert size == LogoSize(240, 320, 2)
    assert out.stat().st_size == 240 * 320 * 2
    decoded, decoded_size = read_boot_logo(out)
    assert decoded_size == size
    r, g, b, _a = decoded.getpixel((0, 0))
    assert r >= 240 and g <= 8 and b <= 8
    with pytest.raises(ValueError):
        write_boot_logo(image, tmp_path / "bad", LogoSize(240, 320, 4))


def test_boot_logo_bad_size_raises(tmp_path: Path) -> None:
    path = tmp_path / "garbage"
    path.write_bytes(b"\x00" * 13)
    with pytest.raises(LogoBuilderError):
        read_boot_logo(path, LogoSize(240, 320, 2))


# ===========================================================================
# 10. Packaging / architecture hygiene
# ===========================================================================
def test_version_is_3_0_0() -> None:
    """v3.0.0 product rename (亿方联发科工具箱 / InspireMTKTools)."""
    import logobuilder  # noqa: PLC0415

    assert logobuilder.__version__ == "3.0.0"


def test_core_has_no_gui_imports() -> None:
    """Contract: the core library must not import tkinter/ttkbootstrap."""
    core_dir = ROOT / "src" / "logobuilder" / "core"
    forbidden = {"tkinter", "ttkbootstrap", "tk"}
    offenders: list[str] = []
    for path in sorted(core_dir.glob("*.py")):
        source = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", source, re.MULTILINE):
            module = match.group(1).split(".")[0]
            if module in forbidden:
                offenders.append(f"{path.name}:{match.group(0).strip()}")
    assert not offenders, f"core imports GUI modules: {offenders}"
