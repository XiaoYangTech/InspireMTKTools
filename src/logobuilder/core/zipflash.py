"""生成 CWM / TWRP 卡刷包（update.zip）。

原版流程：复制内嵌模板 ``LogoBuilder.update.zip``（CWM 通用签名包），
把新的 ``logo.bin`` 放到 zip 根目录；若工程目录存在 ``updater-script`` /
``update-binary``，则覆盖模板 ``META-INF/com/google/android/`` 下的同名文件。

本模块额外支持：

* 模板缺失时生成"纯净" update.zip（含生成的 updater-script）；
* 条目顺序/时间戳固定，输出可复现（同一输入两次生成 SHA-256 相同）；
* ``sign_stub=True`` 时补充占位 ``MANIFEST.MF``（仅用于兼容性提示，
  不能用于开启了签名校验的 recovery——真正的 JAR 签名需要私钥）。
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from .errors import ExportError

__all__ = [
    "DEFAULT_UPDATER_SCRIPT",
    "ANDROID_META_DIR",
    "UPDATE_BINARY_NAME",
    "UPDATER_SCRIPT_NAME",
    "default_template_path",
    "make_update_zip",
    "inspect_update_zip",
]

#: 模板中的原版 updater-script（ASCII/UTF-8）。
DEFAULT_UPDATER_SCRIPT = """ui_print("Flashing logo...");
show_progress(0.200000, 0);
package_extract_file("logo.bin", "/dev/logo");
ui_print("Patched!");
ui_print("");
ui_print("Now you can reboot your phone");
"""

#: recovery 脚本/二进制在 zip 内的目录。
ANDROID_META_DIR = "META-INF/com/google/android"
UPDATE_BINARY_PATH = f"{ANDROID_META_DIR}/update-binary"
UPDATER_SCRIPT_PATH = f"{ANDROID_META_DIR}/updater-script"
UPDATE_BINARY_NAME = "update-binary"
UPDATER_SCRIPT_NAME = "updater-script"

#: 新条目固定时间戳（保证可复现）。
FIXED_DATE_TIME = (2020, 1, 1, 0, 0, 0)
#: 新条目固定权限（rw-r--r--）。
FIXED_EXTERNAL_ATTR = (0o100644 & 0xFFFF) << 16


def default_template_path() -> Path:
    """返回内置 update.zip 模板路径（``assets/update_template.zip``）。"""
    return Path(__file__).resolve().parent.parent / "assets" / "update_template.zip"


def _new_zipinfo(name: str, *, date_time: tuple[int, int, int, int, int, int] = FIXED_DATE_TIME) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = FIXED_EXTERNAL_ATTR
    info.create_system = 3  # Unix
    return info


def _atomic_write_zip(target: Path, writer) -> None:  # type: ignore[no-untyped-def]
    """在目标目录写临时文件后原子替换，避免留下半个 zip。"""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
        os.close(fd)
        try:
            writer(Path(temp_name))
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover
                pass
            raise
    except OSError as exc:
        raise ExportError(
            f"无法写入 zip 文件：{target}（{exc}）",
            hint="请检查目标目录是否存在、是否有写权限，以及 zip 是否被其它程序占用。",
            context={"path": str(target)},
        ) from exc


def _read_update_binary(path: str | Path) -> bytes:
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise ExportError(
            f"无法读取 update-binary：{source}（{exc}）",
            hint="请确认该文件是可执行的 ARM update-binary。",
            context={"path": str(source)},
        ) from exc
    return data


def _normalize_logo_name(name: str) -> str:
    value = str(name).replace("\\", "/").strip()
    if not value or value.endswith("/"):
        raise ExportError(f"logo 文件名非法：{name!r}")
    if value.startswith("/") or ".." in Path(value).parts:
        raise ExportError(f"logo 文件名必须是 zip 内的相对路径：{name!r}")
    return value


def make_update_zip(
    logo_bytes: bytes,
    out_zip: str | Path,
    *,
    template: str | Path | None = None,
    logo_filename: str = "logo.bin",
    updater_script: str | None = None,
    update_binary: str | Path | None = None,
    sign_stub: bool = False,
) -> Path:
    """生成卡刷包 ``update.zip``。

    Parameters
    ----------
    logo_bytes:
        新的 logo.bin / logo.img 文件内容。
    out_zip:
        输出 zip 路径（会原子覆盖）。
    template:
        模板 zip；``None`` 时用内置 ``assets/update_template.zip``。
        内置模板缺失时自动生成纯净包；显式指定的模板不存在则抛 :class:`ExportError`。
    logo_filename:
        logo 在 zip 内的文件名（默认 ``logo.bin``）。
    updater_script:
        自定义 updater-script 文本；会覆盖模板中的同名文件。
    update_binary:
        自定义 update-binary 文件路径；会覆盖模板中的同名文件。
    sign_stub:
        模板缺少 META-INF/MANIFEST.MF 时补充占位清单（不做真正签名）。
    """
    if not isinstance(logo_bytes, (bytes, bytearray, memoryview)):
        raise TypeError(f"logo_bytes 必须是 bytes，收到 {type(logo_bytes).__name__}")
    logo_data = bytes(logo_bytes)
    if not logo_data:
        raise ExportError("logo 数据为空，拒绝生成卡刷包")
    target = Path(out_zip)
    logo_name = _normalize_logo_name(logo_filename)

    explicit_template = template is not None
    template_path = Path(template) if template is not None else default_template_path()
    if explicit_template and not template_path.is_file():
        raise ExportError(
            f"模板 zip 不存在：{template_path}",
            hint="请检查 --template 路径，或省略该参数使用内置模板。",
            context={"path": str(template_path)},
        )
    use_template = template_path.is_file()

    # 从模板读取条目（保留原顺序、时间戳与 zip 注释）
    entries: list[tuple[str, bytes, zipfile.ZipInfo]] = []
    template_comment = b""
    if use_template:
        try:
            with zipfile.ZipFile(template_path, "r") as source_zip:
                template_comment = source_zip.comment
                for info in source_zip.infolist():
                    if info.is_dir():
                        continue
                    entries.append((info.filename, source_zip.read(info), info))
        except (OSError, zipfile.BadZipFile) as exc:
            raise ExportError(
                f"无法读取模板 zip：{template_path}（{exc}）",
                hint="模板 zip 已损坏；请恢复内置 assets/update_template.zip。",
                context={"path": str(template_path)},
            ) from exc

    overrides: dict[str, bytes] = {}
    if updater_script is not None:
        overrides[UPDATER_SCRIPT_PATH] = str(updater_script).replace("\r\n", "\n").encode("utf-8")
    if update_binary is not None:
        overrides[UPDATE_BINARY_PATH] = _read_update_binary(update_binary)
    if not use_template and UPDATER_SCRIPT_PATH not in overrides:
        overrides[UPDATER_SCRIPT_PATH] = DEFAULT_UPDATER_SCRIPT.encode("utf-8")

    existing_names = {name for name, _data, _info in entries}
    if logo_name in existing_names:
        replaced_logo = True
    else:
        replaced_logo = False

    manifest_stub: bytes | None = None
    if sign_stub and "META-INF/MANIFEST.MF" not in existing_names:
        manifest_stub = (
            "Manifest-Version: 1.0\r\n"
            "Created-By: LogoBuilder\r\n"
            "X-LogoBuilder-Note: unsigned placeholder manifest (no JAR signature)\r\n"
            "\r\n"
        ).encode("utf-8")

    def write_zip(temp_path: Path) -> None:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as out:
            if template_comment:
                out.comment = template_comment
            # 1) 模板条目（覆盖项替换内容，但保持原位置与元数据）
            written: set[str] = set()
            for name, data, info in entries:
                if name in overrides:
                    data = overrides[name]
                if name == logo_name:
                    data = logo_data
                new_info = zipfile.ZipInfo(filename=name, date_time=info.date_time)
                new_info.compress_type = zipfile.ZIP_DEFLATED
                new_info.external_attr = info.external_attr or FIXED_EXTERNAL_ATTR
                new_info.create_system = info.create_system
                new_info.internal_attr = info.internal_attr
                new_info.flag_bits |= info.flag_bits & 0x800  # 保留 UTF-8 文件名标记
                out.writestr(new_info, data)
                written.add(name)
            # 2) 覆盖项里模板没有的（顺序固定：update-binary 在前，updater-script 在后）
            for name in (UPDATE_BINARY_PATH, UPDATER_SCRIPT_PATH):
                if name in overrides and name not in written and name != logo_name:
                    out.writestr(_new_zipinfo(name), overrides[name])
                    written.add(name)
            # 3) logo 本体
            if not replaced_logo:
                out.writestr(_new_zipinfo(logo_name), logo_data)
                written.add(logo_name)
            # 4) 占位 MANIFEST（可选）
            if manifest_stub is not None:
                out.writestr(_new_zipinfo("META-INF/MANIFEST.MF"), manifest_stub)

    _atomic_write_zip(target, write_zip)
    return target


def inspect_update_zip(path: str | Path) -> dict[str, Any]:
    """检查生成的卡刷包内容。

    返回 ``{"files": [...], "script": str, "binary_sha256": str}``。
    文件缺失/损坏抛 :class:`ExportError`。
    """
    target = Path(path)
    if not target.is_file():
        raise ExportError(f"zip 文件不存在：{target}", context={"path": str(target)})
    try:
        with zipfile.ZipFile(target, "r") as archive:
            files = [info.filename for info in archive.infolist() if not info.is_dir()]
            names = set(files)
            script = ""
            if UPDATER_SCRIPT_PATH in names:
                script = archive.read(UPDATER_SCRIPT_PATH).decode("utf-8", errors="replace")
            binary_sha256 = ""
            if UPDATE_BINARY_PATH in names:
                binary_sha256 = hashlib.sha256(archive.read(UPDATE_BINARY_PATH)).hexdigest()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportError(
            f"无法读取 zip：{target}（{exc}）",
            hint="zip 文件可能损坏或不是有效的 zip。",
            context={"path": str(target)},
        ) from exc
    return {"files": files, "script": script, "binary_sha256": binary_sha256}
