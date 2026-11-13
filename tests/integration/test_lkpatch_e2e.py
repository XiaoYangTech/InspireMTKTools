#!/usr/bin/env python3
"""End-to-end acceptance tests for the **LK warning patch** feature
(``docs/spec_lkpatch.md``).

The whole module skips (instead of erroring) while
``src/logobuilder/core/lkpatch.py`` is still being written::

    pytest.importorskip("logobuilder.core.lkpatch")

Coverage map (spec §4/§5/§7):

* fixture-independent invariants of the synthetic corpus (exact expected offsets);
* ``parse_lk_image`` / ``detect_arch`` / ``scan`` on ARM32 + AArch64 + raw payloads;
* ARM32: all 7 signature needles found at exact offsets, patch ``00207047``;
* AArch64: synthetic function probes (dispatcher + dm-verity) patched with
  ``e0 03 1f 2a c0 03 5f d6``;
* ``apply_patches`` guard rails: header bytes unchanged, ``androidboot.*`` counts
  unchanged, only expected ranges differ; ``verify_patch`` reports ``patch_ok``;
* tamper/guard test: a patch that would modify a kernel boot parameter is rejected;
* backup / patched path helpers + ``save_backup`` / ``restore_from_backup``;
* CLI ``lk-info`` / ``lk-patch`` (dry-run, default, -o) / ``lk-verify`` /
  ``lk-restore`` exit codes, outputs, Chinese fail-safe errors and "no output file";
* real AArch64 sample ``analysis/lk/samples/lk_a.bin`` (skip when absent):
  findings at file offsets ``0x4D928`` (orange) and ``0x4CC58`` (dm_verity),
  patch + verify, original sample untouched;
* LOGO regression: the existing real ``logo.bin`` roundtrip is still byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("logobuilder.core.lkpatch")

import make_lk_fixtures  # noqa: E402  (tests/fixtures on sys.path via conftest)

from logobuilder.core import LogoBuilderError, LogoContainer  # noqa: E402

#: Absolute path of the LK-patch helpers; the importorskip guarantees presence.
from logobuilder.core import lkpatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
REAL_LOGO = ROOT / "LogoBuilder_jb51" / "LogoBuilder_v1.6中文版" / "logo.bin"
REAL_LOGO_SHA = "c321a9f107d1b2839bc073f42396e83593deb3f69fd3b34f6a85ae079206655c"
REAL_LK_DIR = ROOT / "analysis" / "lk" / "samples"
REAL_LK = REAL_LK_DIR / "lk_a.bin"
REAL_LK_SHA = "bb2f2e6a6f7b7603dee3de08dc09e29dd74da49bd4cb98f20e8be648ba66f873"
REAL_LK_B = REAL_LK_DIR / "lk_b.bin"
REAL_LK_B_SHA = "f4ecd3eed32f6f8ce112115af11b054de14bf01a8d6cab7d12b31bd9051aa6e4"
#: real warning texts (Lead-verified against live device strings)
ORANGE_TEXT = b"Orange State\n\n"
RED_TEXT = b"Red State\n\n"
DMV_TEXT = b"dm-verity corruption\n\n"

HEADER_SIZE = 0x200
ARM32_PATCH = bytes.fromhex("00207047")
AARCH64_PATCH = bytes.fromhex("e0031f2ac0035fd6")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: Guard params from the fixture generator; their byte-count must never change.
GUARD_PARAMS = list(make_lk_fixtures.GUARD_PARAMS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256(Path(path).read_bytes())


def count_bytes(haystack: bytes, needle: bytes) -> int:
    return haystack.count(needle)


def diff_ranges(old: bytes, new: bytes) -> list[tuple[int, int]]:
    """Return coalesced ``(start, end)`` byte ranges where old != new."""
    assert len(old) == len(new)
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, (a, b) in enumerate(zip(old, new)):
        if a != b:
            if start is None:
                start = index
        elif start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(old)))
    return ranges


def assert_changes_only_in_spans(
    old: bytes, new: bytes, spans: list[tuple[int, int]]
) -> None:
    """Every changed byte must lie inside one of the expected patch spans."""
    changed = diff_ranges(old, new)
    for start, end in changed:
        assert any(start >= span[0] and end <= span[1] for span in spans), (
            f"unexpected changed range {(start, end)} outside patch spans {spans}"
        )
    for span_start, span_end in spans:
        assert any(start < span_end and end > span_start for start, end in changed), (
            f"expected patch span {(span_start, span_end)} was not modified"
        )


def assert_chinese_error(exc: BaseException) -> None:
    message = getattr(exc, "message", None) or str(exc)
    assert message and CJK_RE.search(message), f"expected readable Chinese error, got {message!r}"


@pytest.fixture(scope="session")
def corpus() -> dict:
    make_lk_fixtures.ensure()
    return json.loads((make_lk_fixtures.DEFAULT_OUT / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def lk_root() -> Path:
    make_lk_fixtures.ensure()
    return make_lk_fixtures.DEFAULT_OUT


def fixture_path(root: Path, rel: str) -> Path:
    return root / rel


def expected_for(corpus: dict, rel: str) -> dict:
    return corpus["fixtures"][rel]["expected"]


# ===========================================================================
# 0. Fixture corpus invariants (works even before the core is complete)
# ===========================================================================
def test_fixture_manifest_expected_offsets(corpus: dict) -> None:
    arm = expected_for(corpus, "arm32/lk_arm32_needles.img")
    assert arm["payload_offset"] == HEADER_SIZE
    assert len(arm["findings"]) == 7
    categories = [finding["category"] for finding in arm["findings"]]
    assert categories.count("orange_state") == 4
    assert categories.count("red_state") == 1
    assert categories.count("dm_verity") == 2
    assert arm["patch_bytes"] == ARM32_PATCH.hex()

    a64 = expected_for(corpus, "aarch64/lk_aarch64_warnings.img")
    by_category = {finding["category"]: finding for finding in a64["findings"]}
    assert set(by_category) == {"orange_state", "dm_verity"}
    assert by_category["orange_state"]["offset"] == 0x1200  # 0x200 + payload 0x1000
    assert by_category["dm_verity"]["offset"] == 0x1A00      # 0x200 + payload 0x1800
    assert a64["patch_bytes"] == AARCH64_PATCH.hex()


def test_fixture_files_are_well_formed(lk_root: Path) -> None:
    arm = (lk_root / "arm32/lk_arm32_needles.img").read_bytes()
    magic, size = struct.unpack_from("<II", arm, 0)
    assert magic == 0x58881688
    assert size == 0x8000  # synthetic ARM32 payload length
    assert arm[8:20].rstrip(b"\x00") == b"lk"
    assert struct.unpack_from("<I", arm, 0x30)[0] == 0x58891689
    assert len(arm) == 4 * 1024 * 1024
    assert set(arm[HEADER_SIZE + size :]) == {0}  # zero partition padding

    a64 = (lk_root / "aarch64/lk_aarch64_warnings.img").read_bytes()
    assert struct.unpack_from("<I", a64, 0)[0] == 0x58881688
    assert a64[0x1200 : 0x1200 + 4] == bytes.fromhex("fd7bbea9")  # stp prologue
    assert a64[0x1A00 : 0x1A00 + 4] == bytes.fromhex("fd7bbea9")
    # real-encoding constants are present somewhere in the payload
    assert bytes.fromhex("006889528009a072") in a64  # 5,000,000 us
    assert bytes.fromhex("00d490522000a072") in a64  # 100,000 us


# ===========================================================================
# 1. parse / detect_arch / scan
# ===========================================================================
def test_parse_container_and_raw(lk_root: Path) -> None:
    container_path = fixture_path(lk_root, "arm32/lk_arm32_needles.img")
    image = lkpatch.parse_lk_image(container_path.read_bytes(), path=container_path)
    assert image.is_container is True
    assert image.payload_offset == HEADER_SIZE
    assert image.header is not None and len(image.header) == HEADER_SIZE
    assert image.name.rstrip("\x00") == "lk"
    assert image.sha256 == sha256_file(container_path)

    raw_path = fixture_path(lk_root, "aarch64/lk_aarch64_raw_payload.bin")
    raw = lkpatch.parse_lk_image(raw_path.read_bytes(), path=raw_path)
    assert raw.is_container is False
    assert raw.payload_offset == 0
    assert raw.header is None
    assert raw.payload == raw_path.read_bytes()


def test_detect_arch(lk_root: Path) -> None:
    arm = lkpatch.parse_lk_image(fixture_path(lk_root, "arm32/lk_arm32_needles.img").read_bytes())
    a64 = lkpatch.parse_lk_image(fixture_path(lk_root, "aarch64/lk_aarch64_warnings.img").read_bytes())
    raw = lkpatch.parse_lk_image(fixture_path(lk_root, "aarch64/lk_aarch64_raw_payload.bin").read_bytes())
    assert lkpatch.detect_arch(arm) == "arm32-thumb"
    assert lkpatch.detect_arch(a64) == "aarch64"
    assert lkpatch.detect_arch(raw) == "aarch64"


def test_scan_arm32_finds_all_seven_needles(lk_root: Path, corpus: dict) -> None:
    path = fixture_path(lk_root, "arm32/lk_arm32_needles.img")
    image = lkpatch.parse_lk_image(path.read_bytes(), path=path)
    report = lkpatch.scan(image)
    expected = expected_for(corpus, "arm32/lk_arm32_needles.img")

    assert report.arch == "arm32-thumb"
    assert report.verdict.startswith("supported-arm32"), report.verdict
    assert report.already_patched is False
    assert len(report.findings) == 7
    findings = sorted(report.findings, key=lambda item: item.offset)
    for finding, exp in zip(findings, sorted(expected["findings"], key=lambda item: item["offset"])):
        assert finding.offset == exp["offset"], f"{finding.category} offset"
        assert finding.category == exp["category"]
        assert finding.before[:4] == bytes.fromhex(exp["before_prefix"])
        assert finding.after == ARM32_PATCH
        assert 0.0 <= finding.confidence <= 1.0
    # warning strings (real device texts) and guard params are reported
    warning_texts = {warning.text for warning in report.warnings}
    assert any("Orange State" in text for text in warning_texts), warning_texts
    assert any("Red State" in text for text in warning_texts), warning_texts
    assert any("dm-verity corruption" in text for text in warning_texts), warning_texts
    assert report.guard_params, "guard params must be reported"
    assert any("verifiedbootstate" in key for key in report.guard_params)


def test_scan_aarch64_synthetic(lk_root: Path, corpus: dict) -> None:
    path = fixture_path(lk_root, "aarch64/lk_aarch64_warnings.img")
    image = lkpatch.parse_lk_image(path.read_bytes(), path=path)
    report = lkpatch.scan(image)
    expected = expected_for(corpus, "aarch64/lk_aarch64_warnings.img")

    assert report.arch == "aarch64"
    assert report.verdict.startswith("supported-aarch64"), report.verdict
    by_category = {finding.category: finding for finding in report.findings}
    for category, exp in ((item["category"], item) for item in expected["findings"]):
        assert category in by_category, f"missing {category} finding: {[f.category for f in report.findings]}"
        finding = by_category[category]
        assert finding.offset == exp["offset"], f"{category} offset {finding.offset:x} != {exp['offset']:x}"
        assert finding.after == AARCH64_PATCH
        assert finding.evidence, "heuristic findings must carry evidence"
        assert finding.confidence > 0


def test_scan_aarch64_dmverity_only(lk_root: Path) -> None:
    image = lkpatch.parse_lk_image(
        fixture_path(lk_root, "aarch64/lk_aarch64_dmverity_only.img").read_bytes()
    )
    report = lkpatch.scan(image)
    assert [finding.category for finding in report.findings] == ["dm_verity"]
    assert report.findings[0].offset == 0x1A00


def test_scan_aarch64_raw_payload_offsets(lk_root: Path) -> None:
    image = lkpatch.parse_lk_image(
        fixture_path(lk_root, "aarch64/lk_aarch64_raw_payload.bin").read_bytes()
    )
    report = lkpatch.scan(image)
    offsets = sorted(finding.offset for finding in report.findings)
    assert offsets == [0x1000, 0x1800]  # raw payload: no 0x200 shift


def test_scan_unsupported_samples(lk_root: Path) -> None:
    no_signature = lkpatch.parse_lk_image(
        fixture_path(lk_root, "arm32/lk_arm32_no_signature.img").read_bytes()
    )
    report = lkpatch.scan(no_signature)
    assert report.findings == []
    assert report.verdict == "unsupported"

    no_warnings = lkpatch.parse_lk_image(
        fixture_path(lk_root, "aarch64/lk_aarch64_no_warnings.img").read_bytes()
    )
    assert lkpatch.scan(no_warnings).findings == []


def test_scan_already_patched(lk_root: Path) -> None:
    image = lkpatch.parse_lk_image(
        fixture_path(lk_root, "arm32/lk_arm32_already_patched.img").read_bytes()
    )
    report = lkpatch.scan(image)
    assert report.findings == [] or all(not finding.selected for finding in report.findings)
    assert report.verdict in ("already-patched", "unsupported")
    if report.verdict == "already-patched":
        assert report.already_patched is True


# ===========================================================================
# 2. apply_patches / verify_patch (ARM32)
# ===========================================================================
def test_apply_patches_arm32_guard_rails(lk_root: Path, corpus: dict) -> None:
    path = fixture_path(lk_root, "arm32/lk_arm32_needles.img")
    original = path.read_bytes()
    image = lkpatch.parse_lk_image(original, path=path)
    report = lkpatch.scan(image)
    expected = expected_for(corpus, "arm32/lk_arm32_needles.img")

    new_data, changes = lkpatch.apply_patches(image, report)
    assert isinstance(new_data, (bytes, bytearray))
    new_data = bytes(new_data)
    assert len(new_data) == len(original)
    assert new_data[:HEADER_SIZE] == original[:HEADER_SIZE], "LK header must stay byte-identical"
    for finding in expected["findings"]:
        off = finding["offset"]
        assert new_data[off : off + 4] == ARM32_PATCH, f"patch not applied at {off:#x}"
    assert changes, "apply_patches must return a change log"
    changed = diff_ranges(original, new_data)
    expected_offsets = {finding["offset"] for finding in expected["findings"]}
    first_bytes = {start for start, end in changed if end - start == 4}
    assert first_bytes == expected_offsets, f"unexpected changed ranges: {changed}"
    for param in GUARD_PARAMS:
        assert count_bytes(original, param) == count_bytes(new_data, param), (
            f"kernel boot parameter changed: {param!r}"
        )

    verification = lkpatch.verify_patch(image, new_data, report)
    assert verification["size_same"] is True
    assert verification["header_same"] is True
    assert verification["guard_counts_same"] is True
    assert verification["patch_ok"] is True


def test_apply_patches_aarch64_guard_rails(lk_root: Path, corpus: dict) -> None:
    path = fixture_path(lk_root, "aarch64/lk_aarch64_warnings.img")
    original = path.read_bytes()
    image = lkpatch.parse_lk_image(original, path=path)
    report = lkpatch.scan(image)
    expected = expected_for(corpus, "aarch64/lk_aarch64_warnings.img")

    new_data, changes = lkpatch.apply_patches(image, report)
    new_data = bytes(new_data)
    assert len(new_data) == len(original)
    assert new_data[:HEADER_SIZE] == original[:HEADER_SIZE]
    for finding in expected["findings"]:
        off = finding["offset"]
        assert new_data[off : off + 8] == AARCH64_PATCH, f"patch not applied at {off:#x}"
    spans = [(finding["offset"], finding["offset"] + 8) for finding in expected["findings"]]
    assert_changes_only_in_spans(original, new_data, spans)
    for param in GUARD_PARAMS:
        assert count_bytes(original, param) == count_bytes(new_data, param)

    verification = lkpatch.verify_patch(image, new_data, report)
    assert verification["size_same"] is True
    assert verification["header_same"] is True
    assert verification["guard_counts_same"] is True
    assert verification["patch_ok"] is True
    disasm = " ".join(str(item) for item in verification.get("disasm_after", []))
    if disasm:
        assert "mov" in disasm.lower() and "ret" in disasm.lower()


def test_apply_patches_category_filter(lk_root: Path) -> None:
    path = fixture_path(lk_root, "aarch64/lk_aarch64_warnings.img")
    original = path.read_bytes()
    image = lkpatch.parse_lk_image(original, path=path)
    report = lkpatch.scan(image)
    new_data, _changes = lkpatch.apply_patches(image, report, categories=["dm_verity"])
    new_data = bytes(new_data)
    assert new_data[0x1A00 : 0x1A00 + 8] == AARCH64_PATCH        # dm-verity patched
    assert new_data[0x1200 : 0x1200 + 8] == original[0x1200 : 0x1200 + 8]  # dispatcher untouched


def test_apply_patches_clear_warning_text_keeps_guards(lk_root: Path) -> None:
    path = fixture_path(lk_root, "arm32/lk_arm32_needles.img")
    original = path.read_bytes()
    image = lkpatch.parse_lk_image(original, path=path)
    report = lkpatch.scan(image)
    new_data, _changes = lkpatch.apply_patches(image, report, clear_warning_text=True)
    new_data = bytes(new_data)
    assert len(new_data) == len(original)
    # warning strings cleared (0x00 filled), kernel boot params untouched
    for text in (ORANGE_TEXT, RED_TEXT, DMV_TEXT):
        assert count_bytes(new_data, text) == 0, f"warning text not cleared: {text!r}"
    for param in GUARD_PARAMS:
        assert count_bytes(original, param) == count_bytes(new_data, param)
    verification = lkpatch.verify_patch(image, new_data, report)
    assert verification["guard_counts_same"] is True
    assert verification["patch_ok"] is True


def test_tampered_patch_touching_guard_param_is_rejected(lk_root: Path) -> None:
    path = fixture_path(lk_root, "arm32/lk_arm32_needles.img")
    original = path.read_bytes()
    image = lkpatch.parse_lk_image(original, path=path)
    report = lkpatch.scan(image)

    # spec §5.4 guard rule is occurrence-count based: removing/duplicating a
    # guard token must be detected by verify_patch and rejected.
    param = GUARD_PARAMS[0]
    param_offset = original.find(param)
    assert param_offset >= 0
    tampered = bytearray(original)
    # erase the "=orange" suffix so the token no longer counts as a boot param
    token = b"androidboot.verifiedbootstate"
    tampered[param_offset : param_offset + len(token)] = b"X" * len(token)
    verification = lkpatch.verify_patch(image, bytes(tampered), report)
    assert verification["guard_counts_same"] is False
    assert verification["patch_ok"] is False

    # apply_patches must refuse text ranges that overlap a guarded boot parameter
    guarded_range = (param_offset, param_offset + len(param))
    try:
        forged_data, forged_changes = lkpatch.apply_patches(
            image, report, clear_warning_text=True, text_ranges=[guarded_range]
        )
    except LogoBuilderError as exc:
        assert_chinese_error(exc)
    else:
        assert bytes(forged_data) == original, "apply_patches silently changed a guarded parameter"
        assert not forged_changes


# ===========================================================================
# 3. Backup / restore / path helpers
# ===========================================================================
def test_backup_and_restore_helpers(lk_root: Path, tmp_path: Path) -> None:
    path = tmp_path / "lk_arm32_needles.img"
    original = fixture_path(lk_root, "arm32/lk_arm32_needles.img").read_bytes()
    path.write_bytes(original)
    image = lkpatch.parse_lk_image(original, path=path)

    backup = lkpatch.save_backup(image, backup_dir=tmp_path)
    backup = Path(backup)
    assert backup.is_file()
    assert sha256_file(backup) == sha256(original)
    assert "_original_backup" in backup.name

    assert "_patched" in lkpatch.patched_path(path).name
    assert "_original_backup" in lkpatch.backup_path(path).name

    patched_bytes = bytes(lkpatch.apply_patches(image, lkpatch.scan(image))[0])
    target = tmp_path / "restored.img"
    target.write_bytes(patched_bytes)
    restored = lkpatch.restore_from_backup(backup, target)
    restored = Path(restored) if restored is not None else target
    assert sha256_file(Path(restored)) == sha256(original)


# ===========================================================================
# 4. CLI end-to-end
# ===========================================================================
def run_cli(*args: object, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT / "src") + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, "-m", "logobuilder.core.cli", *[str(arg) for arg in args]]
    return subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, env=env,
    )


def prepare(tmp_path: Path, lk_root: Path, rel: str) -> Path:
    target = tmp_path / Path(rel).name
    target.write_bytes(fixture_path(lk_root, rel).read_bytes())
    return target


def test_cli_lk_info(lk_root: Path, tmp_path: Path) -> None:
    src = prepare(tmp_path, lk_root, "arm32/lk_arm32_needles.img")
    result = run_cli("lk-info", src)
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "arm32" in combined.lower() or "7" in combined
    assert "supported" in combined.lower() or "可补丁" in combined or "支持" in combined


def test_cli_lk_patch_dry_run_does_not_write(lk_root: Path, tmp_path: Path) -> None:
    src = prepare(tmp_path, lk_root, "arm32/lk_arm32_needles.img")
    before = sha256_file(src)
    result = run_cli("lk-patch", src, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert sha256_file(src) == before, "--dry-run must not modify the input"
    assert not lkpatch.patched_path(src).exists(), "--dry-run must not write the patched file"
    assert not lkpatch.backup_path(src).exists()


def test_cli_lk_patch_writes_backup_and_patched(lk_root: Path, tmp_path: Path, corpus: dict) -> None:
    rel = "arm32/lk_arm32_needles.img"
    src = prepare(tmp_path, lk_root, rel)
    original = src.read_bytes()
    result = run_cli("lk-patch", src)
    assert result.returncode == 0, result.stderr

    patched = lkpatch.patched_path(src)
    backup = lkpatch.backup_path(src)
    assert patched.is_file(), f"patched file not written: {patched}"
    assert backup.is_file(), f"backup not written: {backup}"
    assert sha256_file(backup) == sha256(original), "backup must be the original bytes"
    assert sha256_file(src) == sha256(original), "input file must not be modified"

    patched_data = patched.read_bytes()
    assert patched_data[:HEADER_SIZE] == original[:HEADER_SIZE]
    for finding in expected_for(corpus, rel)["findings"]:
        off = finding["offset"]
        assert patched_data[off : off + 4] == ARM32_PATCH
    for param in GUARD_PARAMS:
        assert count_bytes(original, param) == count_bytes(patched_data, param)


def test_cli_lk_patch_output_option(lk_root: Path, tmp_path: Path) -> None:
    src = prepare(tmp_path, lk_root, "aarch64/lk_aarch64_warnings.img")
    out = tmp_path / "custom_out.img"
    result = run_cli("lk-patch", src, "-o", out)
    assert result.returncode == 0, result.stderr
    assert out.is_file()
    data = out.read_bytes()
    assert data[0x1200 : 0x1200 + 8] == AARCH64_PATCH
    assert data[0x1A00 : 0x1A00 + 8] == AARCH64_PATCH


def test_cli_lk_verify_and_restore(lk_root: Path, tmp_path: Path) -> None:
    src = prepare(tmp_path, lk_root, "arm32/lk_arm32_needles.img")
    original = src.read_bytes()
    assert run_cli("lk-patch", src).returncode == 0
    patched = lkpatch.patched_path(src)
    backup = lkpatch.backup_path(src)

    verify = run_cli("lk-verify", src, patched)
    assert verify.returncode == 0, verify.stderr

    target = tmp_path / "restored.img"
    restore = run_cli("lk-restore", backup, "-o", target)
    assert restore.returncode == 0, restore.stderr
    assert target.is_file()
    assert sha256_file(target) == sha256(original)


@pytest.mark.parametrize(
    "rel",
    [
        "arm32/lk_arm32_no_signature.img",
        "arm32/lk_arm32_bad_magic.img",
        "arm32/lk_arm32_truncated.img",
        "negative/all_zero_4mb.bin",
        "negative/not_lk_random.bin",
    ],
)
def test_cli_fail_safe_unsupported_inputs(lk_root: Path, tmp_path: Path, rel: str) -> None:
    src = prepare(tmp_path, lk_root, rel)
    original_sha = sha256_file(src)
    result = run_cli("lk-patch", src)
    assert result.returncode != 0, f"{rel}: unsupported input must fail, got exit 0"
    combined = result.stdout + result.stderr
    assert CJK_RE.search(combined), f"{rel}: expected readable Chinese reason, got {combined!r}"
    assert sha256_file(src) == original_sha
    assert not lkpatch.patched_path(src).exists(), f"{rel}: no output file may be produced"


def test_cli_missing_arguments_exit_code_2() -> None:
    result = run_cli("lk-info")
    assert result.returncode == 2


# ===========================================================================
# 5. Real AArch64 sample (skipped when the local sample is absent)
# ===========================================================================
REAL_SAMPLES = [
    pytest.param(REAL_LK, REAL_LK_SHA, id="lk_a"),
    pytest.param(REAL_LK_B, REAL_LK_B_SHA, id="lk_b"),
]


@pytest.mark.parametrize("sample_path,expected_sha", REAL_SAMPLES)
def test_real_aarch64_sample_scan_and_patch(
    sample_path: Path, expected_sha: str, tmp_path: Path
) -> None:
    if not sample_path.is_file():
        pytest.skip(f"local AArch64 sample {sample_path.name} absent")
    original = sample_path.read_bytes()
    assert sha256(original) == expected_sha
    assert len(original) == 4 * 1024 * 1024

    image = lkpatch.parse_lk_image(original, path=sample_path)
    assert image.is_container is True
    assert image.payload_offset == HEADER_SIZE
    report = lkpatch.scan(image)
    assert report.arch == "aarch64"
    assert report.verdict.startswith("supported-aarch64"), report.verdict

    by_category = {finding.category: finding for finding in report.findings}
    assert "orange_state" in by_category, [finding.category for finding in report.findings]
    assert "dm_verity" in by_category, [finding.category for finding in report.findings]
    assert by_category["orange_state"].offset == 0x4D928
    assert by_category["dm_verity"].offset == 0x4CC58

    new_data, _changes = lkpatch.apply_patches(image, report)
    new_data = bytes(new_data)
    assert len(new_data) == len(original)
    assert new_data[:HEADER_SIZE] == original[:HEADER_SIZE], "header must stay byte-identical"
    assert new_data[0x4D928 : 0x4D928 + 8] == AARCH64_PATCH
    assert new_data[0x4CC58 : 0x4CC58 + 8] == AARCH64_PATCH
    assert_changes_only_in_spans(original, new_data, [(0x4D928, 0x4D930), (0x4CC58, 0x4CC60)])

    verification = lkpatch.verify_patch(image, new_data, report)
    assert verification["size_same"] is True
    assert verification["header_same"] is True
    assert verification["guard_counts_same"] is True
    assert verification["patch_ok"] is True

    # the shipped sample file on disk must never be modified by QA
    assert sha256_file(sample_path) == expected_sha


@pytest.mark.parametrize("sample_path,expected_sha", REAL_SAMPLES)
def test_real_aarch64_sample_cli(sample_path: Path, expected_sha: str, tmp_path: Path) -> None:
    if not sample_path.is_file():
        pytest.skip(f"local AArch64 sample {sample_path.name} absent")
    out = tmp_path / f"{sample_path.stem}_patched.img"
    info = run_cli("lk-info", sample_path)
    assert info.returncode == 0, info.stderr
    combined = info.stdout + info.stderr
    assert "4d928" in combined.lower() or "0x4d928" in combined.lower()

    patch = run_cli("lk-patch", sample_path, "-o", out)
    assert patch.returncode == 0, patch.stderr
    assert out.is_file()
    data = out.read_bytes()
    assert data[0x4D928 : 0x4D928 + 8] == AARCH64_PATCH
    assert data[0x4CC58 : 0x4CC58 + 8] == AARCH64_PATCH
    assert sha256_file(sample_path) == expected_sha, "QA must never modify the local sample"

    verify = run_cli("lk-verify", sample_path, out)
    assert verify.returncode == 0, verify.stderr


# ===========================================================================
# 6. LOGO regression (the LK feature must not disturb the existing workflow)
# ===========================================================================
def test_logo_roundtrip_regression() -> None:
    original = REAL_LOGO.read_bytes()
    container = LogoContainer.load(REAL_LOGO)
    assert container.count == 51
    assert sha256(container.to_bytes()) == sha256(original) == REAL_LOGO_SHA
