"""update.zip 卡刷包生成测试。"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from logobuilder.core import (
    ANDROID_META_DIR,
    DEFAULT_UPDATER_SCRIPT,
    ExportError,
    default_template_path,
    inspect_update_zip,
    make_update_zip,
)
from logobuilder.core.zipflash import UPDATE_BINARY_PATH, UPDATER_SCRIPT_PATH

LOGO_BYTES = b"synthetic-logo-bytes" * 200


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_default_template_exists():
    template = default_template_path()
    assert template.is_file()
    with zipfile.ZipFile(template) as archive:
        names = set(archive.namelist())
    assert UPDATE_BINARY_PATH in names
    assert UPDATER_SCRIPT_PATH in names


def test_make_update_zip_from_template(tmp_path):
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip")
    assert output == tmp_path / "update.zip"
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "logo.bin" in names
        assert UPDATE_BINARY_PATH in names
        assert UPDATER_SCRIPT_PATH in names
        assert archive.read("logo.bin") == LOGO_BYTES
        assert archive.read(UPDATER_SCRIPT_PATH).decode("utf-8") == DEFAULT_UPDATER_SCRIPT
    info = inspect_update_zip(output)
    assert "logo.bin" in info["files"]
    assert info["script"] == DEFAULT_UPDATER_SCRIPT
    assert len(info["binary_sha256"]) == 64
    template_binary = default_template_path()
    with zipfile.ZipFile(template_binary) as archive:
        assert info["binary_sha256"] == sha256(archive.read(UPDATE_BINARY_PATH))


def test_update_zip_deterministic(tmp_path):
    first = make_update_zip(LOGO_BYTES, tmp_path / "a.zip")
    second = make_update_zip(LOGO_BYTES, tmp_path / "b.zip")
    assert sha256(first.read_bytes()) == sha256(second.read_bytes())


def test_custom_logo_filename(tmp_path):
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip", logo_filename="logo.img")
    info = inspect_update_zip(output)
    assert "logo.img" in info["files"]
    assert "logo.bin" not in info["files"]
    with zipfile.ZipFile(output) as archive:
        assert archive.read("logo.img") == LOGO_BYTES


def test_custom_updater_script(tmp_path):
    script = 'ui_print("custom logo");\npackage_extract_file("logo.bin", "/dev/block/by-name/logo");\n'
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip", updater_script=script)
    info = inspect_update_zip(output)
    assert info["script"] == script
    # 其它模板文件仍在，且顺序/位置稳定
    assert UPDATE_BINARY_PATH in info["files"]
    # 换行统一为 \n
    output2 = make_update_zip(LOGO_BYTES, tmp_path / "update2.zip", updater_script=script.replace("\n", "\r\n"))
    assert inspect_update_zip(output2)["script"] == script


def test_custom_update_binary(tmp_path):
    binary = tmp_path / "my-update-binary"
    binary.write_bytes(b"\x7fELF-fake-update-binary")
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip", update_binary=binary)
    info = inspect_update_zip(output)
    assert info["binary_sha256"] == sha256(binary.read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.read(UPDATE_BINARY_PATH) == binary.read_bytes()


def test_missing_explicit_template_raises(tmp_path):
    with pytest.raises(ExportError):
        make_update_zip(LOGO_BYTES, tmp_path / "update.zip", template=tmp_path / "nope.zip")


def test_fallback_when_default_template_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("logobuilder.core.zipflash.default_template_path", lambda: tmp_path / "missing.zip")
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip")
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "logo.bin" in names
        assert UPDATER_SCRIPT_PATH in names
        assert UPDATE_BINARY_PATH not in names
        assert archive.read(UPDATER_SCRIPT_PATH).decode("utf-8") == DEFAULT_UPDATER_SCRIPT


def test_fallback_sign_stub(tmp_path, monkeypatch):
    monkeypatch.setattr("logobuilder.core.zipflash.default_template_path", lambda: tmp_path / "missing.zip")
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip", sign_stub=True)
    with zipfile.ZipFile(output) as archive:
        assert "META-INF/MANIFEST.MF" in archive.namelist()


def test_custom_template(tmp_path):
    template = tmp_path / "custom.zip"
    with zipfile.ZipFile(template, "w") as archive:
        archive.writestr("META-INF/custom.txt", "hello")
        archive.writestr(UPDATER_SCRIPT_PATH, 'ui_print("template");\n')
    output = make_update_zip(LOGO_BYTES, tmp_path / "update.zip", template=template)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "META-INF/custom.txt" in names
        assert "logo.bin" in names
        # 默认脚本不会覆盖模板脚本
        assert archive.read(UPDATER_SCRIPT_PATH).decode("utf-8") == 'ui_print("template");\n'
    # 显式 updater_script 覆盖模板
    output2 = make_update_zip(
        LOGO_BYTES, tmp_path / "update2.zip", template=template, updater_script="ui_print(1);\n"
    )
    assert inspect_update_zip(output2)["script"] == "ui_print(1);\n"


def test_invalid_inputs(tmp_path):
    with pytest.raises(TypeError):
        make_update_zip("not-bytes", tmp_path / "update.zip")  # type: ignore[arg-type]
    with pytest.raises(ExportError):
        make_update_zip(b"", tmp_path / "update.zip")
    with pytest.raises(ExportError):
        make_update_zip(LOGO_BYTES, tmp_path / "update.zip", logo_filename="../evil.bin")
    with pytest.raises(ExportError):
        make_update_zip(LOGO_BYTES, tmp_path / "update.zip", logo_filename="/abs/evil.bin")
    with pytest.raises(ExportError):
        make_update_zip(LOGO_BYTES, tmp_path / "update.zip", update_binary=tmp_path / "nope.bin")


def test_inspect_errors(tmp_path):
    with pytest.raises(ExportError):
        inspect_update_zip(tmp_path / "nope.zip")
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")
    with pytest.raises(ExportError):
        inspect_update_zip(broken)


def test_output_in_nested_directory(tmp_path):
    output = make_update_zip(LOGO_BYTES, tmp_path / "a" / "b" / "update.zip")
    assert output.is_file()


def test_zip_entries_are_deterministic_metadata(tmp_path):
    first = make_update_zip(LOGO_BYTES, tmp_path / "a.zip")
    second = make_update_zip(LOGO_BYTES, tmp_path / "b.zip")
    with zipfile.ZipFile(first) as a, zipfile.ZipFile(second) as b:
        assert [info.filename for info in a.infolist()] == [info.filename for info in b.infolist()]
        for info_a, info_b in zip(a.infolist(), b.infolist()):
            assert info_a.date_time == info_b.date_time
            assert info_a.CRC == info_b.CRC
