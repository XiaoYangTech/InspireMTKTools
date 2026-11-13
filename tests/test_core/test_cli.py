"""CLI 端到端测试（含 ``python -m logobuilder.core.cli`` 子进程调用）。

验收命令::

    cd src && python -m logobuilder.core.cli info ../LogoBuilder_jb51/LogoBuilder_v1.6中文版/logo.bin
    cd src && python -m logobuilder.core.cli extract <logo.bin> <dir>
    cd src && python -m logobuilder.core.cli pack <dir> <out.bin>
    cd src && python -m logobuilder.core.cli zip <dir> <update.zip>
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from logobuilder.core.cli import main

from helpers import LK_BIN, LOGO_BIN, requires_samples

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_module(*args: str, cwd: Path, env_extra: dict[str, str] | None = None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "logobuilder.core.cli", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


# ----------------------------------------------------------------------
# info
# ----------------------------------------------------------------------
@requires_samples
def test_info_real_logo(capsys):
    code = main(["info", str(LOGO_BIN)])
    captured = capsys.readouterr()
    assert code == 0
    assert "LOGO" in captured.out
    assert "块数量: 51" in captured.out
    assert "dataLen: 2005959" in captured.out
    assert "540x960x4" in captured.out
    assert captured.err == ""


@requires_samples
def test_info_json(capsys):
    code = main(["info", str(LOGO_BIN), "--json"])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["tag"] == "LOGO"
    assert payload["count"] == 51
    assert payload["data_len"] == 2_005_959
    assert payload["mode"] == "block-table"


@requires_samples
def test_info_lk_prints_warnings_to_stderr(capsys):
    code = main(["info", str(LK_BIN)])
    captured = capsys.readouterr()
    assert code == 0
    assert "raw" in captured.out
    assert "警告" in captured.err
    assert "raw" in captured.err


def test_info_errors(tmp_path, capsys):
    corrupt = tmp_path / "corrupt.bin"
    corrupt.write_bytes(b"hello not a logo" * 100)
    code = main(["info", str(corrupt)])
    captured = capsys.readouterr()
    assert code == 1
    assert "错误" in captured.err
    assert "Traceback" not in captured.err

    code = main(["info", str(tmp_path / "missing.bin")])
    captured = capsys.readouterr()
    assert code == 1
    assert "错误" in captured.err


def test_bad_arguments_return_2(capsys):
    assert main(["info"]) == 2  # 缺少位置参数
    assert main(["no-such-command"]) == 2
    captured = capsys.readouterr()
    assert captured.err  # argparse usage 输出


# ----------------------------------------------------------------------
# extract / pack（验收：解包再打包 == 原文件）
# ----------------------------------------------------------------------
@requires_samples
def test_extract_pack_roundtrip(tmp_path, real_logo_bytes, capsys):
    project_dir = tmp_path / "proj"
    code = main(["extract", str(LOGO_BIN), str(project_dir)])
    captured = capsys.readouterr()
    assert code == 0
    assert (project_dir / "project.json").is_file()
    assert (project_dir / "header.bin").is_file()
    assert "51" in captured.out

    output = tmp_path / "out.bin"
    code = main(["pack", str(project_dir), str(output)])
    captured = capsys.readouterr()
    assert code == 0
    assert sha256(output.read_bytes()) == sha256(real_logo_bytes)


@requires_samples
def test_unpack_alias(tmp_path, real_logo_bytes, capsys):
    project_dir = tmp_path / "proj-unpack"
    code = main(["unpack", str(LOGO_BIN), str(project_dir)])
    assert code == 0
    output = tmp_path / "out.bin"
    assert main(["pack", str(project_dir), str(output)]) == 0
    assert output.read_bytes() == real_logo_bytes


@requires_samples
def test_pack_pad_to(tmp_path, capsys):
    project_dir = tmp_path / "proj"
    assert main(["extract", str(LOGO_BIN), str(project_dir)]) == 0
    output = tmp_path / "padded.bin"
    code = main(["pack", str(project_dir), str(output), "--pad-to", "2500000"])
    assert code == 0
    data = output.read_bytes()
    assert len(data) == 2_500_000
    assert data[-1] == 0


# ----------------------------------------------------------------------
# zip
# ----------------------------------------------------------------------
@requires_samples
def test_zip_command(tmp_path, real_logo_bytes, capsys):
    project_dir = tmp_path / "proj"
    assert main(["extract", str(LOGO_BIN), str(project_dir)]) == 0
    update_zip = tmp_path / "update.zip"
    code = main(["zip", str(project_dir), str(update_zip)])
    assert code == 0
    with zipfile.ZipFile(update_zip) as archive:
        names = archive.namelist()
        assert "logo.bin" in names
        assert "META-INF/com/google/android/updater-script" in names
        assert archive.read("logo.bin") == real_logo_bytes


@requires_samples
def test_zip_from_bin_and_custom_script(tmp_path, real_logo_bytes):
    script = tmp_path / "updater-script"
    script.write_text('ui_print("custom");\n', encoding="utf-8")
    output = tmp_path / "update.zip"
    code = main(["zip", str(LOGO_BIN), str(output), "--script", str(script), "--name", "logo.img"])
    assert code == 0
    with zipfile.ZipFile(output) as archive:
        assert archive.read("logo.img") == real_logo_bytes
        assert archive.read("META-INF/com/google/android/updater-script").decode() == 'ui_print("custom");\n'


# ----------------------------------------------------------------------
# bootlogo / img2bl / bl2img
# ----------------------------------------------------------------------
def test_bootlogo_commands(tmp_path, capsys):
    size = (320, 480)
    image = Image.new("RGBA", size, (10, 200, 30, 255))
    source_png = tmp_path / "logo.png"
    image.save(source_png)

    boot_logo = tmp_path / "boot_logo"
    assert main(["bootlogo", str(source_png), str(boot_logo)]) == 0
    assert boot_logo.stat().st_size == 320 * 480 * 2
    capsys.readouterr()

    # bl2img：裸数据 -> PNG（原版命令名）
    restored = tmp_path / "restored.png"
    assert main(["bl2img", str(boot_logo), str(restored)]) == 0
    with Image.open(restored) as handle:
        assert handle.size == size

    # img2bl：图片 -> 裸数据（原版命令名）
    boot_logo2 = tmp_path / "boot_logo2"
    assert main(["img2bl", str(source_png), str(boot_logo2)]) == 0
    assert boot_logo2.read_bytes() == boot_logo.read_bytes()

    # 显式 --size
    boot_logo3 = tmp_path / "boot_logo3"
    assert main(["bootlogo", str(source_png), str(boot_logo3), "--size", "320x480x2"]) == 0
    assert boot_logo3.read_bytes() == boot_logo.read_bytes()


def test_bl2img_errors(tmp_path, capsys):
    broken = tmp_path / "broken"
    broken.write_bytes(b"\x00\x01\x02")  # 奇数长度
    code = main(["bl2img", str(broken), str(tmp_path / "out.png")])
    captured = capsys.readouterr()
    assert code == 1
    assert "错误" in captured.err


# ----------------------------------------------------------------------
# sizes / --help
# ----------------------------------------------------------------------
def test_sizes_command(capsys):
    assert main(["sizes"]) == 0
    captured = capsys.readouterr()
    assert "540x960x4" in captured.out
    assert "内置 50 项" in captured.out


def test_sizes_json(capsys):
    assert main(["sizes", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "540x960x4" in payload
    assert len(payload) >= 50


def test_help_returns_zero(capsys):
    assert main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "usage" in captured.out.lower()


# ----------------------------------------------------------------------
# 子进程：python -m logobuilder.core.cli
# ----------------------------------------------------------------------
@requires_samples
def test_module_invocation_from_src_dir(tmp_path):
    logo = LOGO_BIN.resolve()
    result = run_module("info", str(logo), cwd=SRC)
    assert result.returncode == 0, result.stderr
    assert "LOGO" in result.stdout
    assert "块数量: 51" in result.stdout


@requires_samples
def test_module_invocation_with_pythonpath_from_repo_root(tmp_path):
    env = {"PYTHONPATH": str(SRC)}
    result = run_module("info", str(LOGO_BIN.resolve()), cwd=ROOT, env_extra=env)
    assert result.returncode == 0, result.stderr
    assert "LOGO" in result.stdout


@requires_samples
def test_module_extract_pack_zip_flow(tmp_path):
    project_dir = tmp_path / "proj"
    out_bin = tmp_path / "out.bin"
    update_zip = tmp_path / "update.zip"
    result = run_module("extract", str(LOGO_BIN.resolve()), str(project_dir), cwd=SRC)
    assert result.returncode == 0, result.stderr
    result = run_module("pack", str(project_dir), str(out_bin), cwd=SRC)
    assert result.returncode == 0, result.stderr
    assert sha256(out_bin.read_bytes()) == sha256(LOGO_BIN.read_bytes())
    result = run_module("zip", str(out_bin), str(update_zip), cwd=SRC)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(update_zip) as archive:
        assert "logo.bin" in archive.namelist()


# ----------------------------------------------------------------------
# raw 模式（lk.bin）：CLI 解包/打包仍逐字节无损 + 警告走 stderr
# ----------------------------------------------------------------------
@requires_samples
def test_cli_lk_raw_roundtrip(tmp_path, real_lk_bytes, capsys):
    project_dir = tmp_path / "lkproj"
    code = main(["extract", str(LK_BIN), str(project_dir)])
    captured = capsys.readouterr()
    assert code == 0
    assert "警告" in captured.err
    assert "raw" in captured.err
    output = tmp_path / "lk.bin"
    code = main(["pack", str(project_dir), str(output)])
    capsys.readouterr()
    assert code == 0
    assert sha256(output.read_bytes()) == sha256(real_lk_bytes)

