"""公共 API 契约测试（对齐 docs/spec_api.md）与依赖约束。"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CORE_DIR = SRC / "logobuilder" / "core"

import logobuilder.core as core  # noqa: E402


REQUIRED_API = [
    # errors
    "LogoBuilderError",
    "NotLogoFile",
    "CorruptContainer",
    "UnknownImageSize",
    "ProjectError",
    "ExportError",
    # sizes
    "LogoSize",
    "SizeCandidate",
    "SizeDB",
    "guess_sizes_for_blocks",
    # pixel
    "decode_pixels",
    "pixels_to_image",
    "image_to_pixels",
    "encode_pixels",
    "resize_image",
    # container
    "HEADER_SIZE",
    "MAGIC",
    "ImageBlock",
    "BinaryBlock",
    "LogoContainer",
    "sniff_format",
    "is_mtk_logo",
    # project
    "PROJECT_FILE",
    "HEADER_FILE",
    "ProjectBlock",
    "ProjectIssue",
    "LogoProject",
    # zipflash
    "DEFAULT_UPDATER_SCRIPT",
    "make_update_zip",
    "inspect_update_zip",
    # bootlogo
    "read_boot_logo",
    "write_boot_logo",
]


@pytest.mark.parametrize("name", REQUIRED_API)
def test_required_api_exported(name):
    assert hasattr(core, name), f"logobuilder.core 缺少公共 API：{name}"
    assert name in core.__all__


def test_version():
    import logobuilder

    assert logobuilder.__version__ == "3.0.0"


def test_constants():
    assert core.HEADER_SIZE == 512
    assert core.MAGIC == 0x58881688
    assert core.HEADER_FILE == "header.bin"
    assert core.PROJECT_FILE == "project.json"
    assert core.LEGACY_HEADER_FILE == "header"
    assert core.SUPPORTED_FITS == ("stretch", "contain", "cover")


def test_method_signatures():
    parse_params = inspect.signature(core.LogoContainer.parse).parameters
    for name in ("data", "path", "db", "block_sizes"):
        assert name in parse_params
    assert parse_params["path"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parse_params["db"].kind is inspect.Parameter.KEYWORD_ONLY

    to_bytes_params = inspect.signature(core.LogoContainer.to_bytes).parameters
    assert "preserve_binary" in to_bytes_params
    assert "pad_to_size" in to_bytes_params
    assert "pad_byte" in to_bytes_params
    assert to_bytes_params["preserve_binary"].kind is inspect.Parameter.KEYWORD_ONLY

    for name in ("load", "create", "assign_size", "to_image_map", "save", "summary"):
        assert hasattr(core.LogoContainer, name)

    load_params = inspect.signature(core.LogoContainer.load).parameters
    assert list(load_params)[:2] == ["path", "db"]

    new_params = inspect.signature(core.LogoProject.new).parameters
    assert list(new_params)[:3] == ["project_dir", "source", "overwrite"]
    open_params = inspect.signature(core.LogoProject.open).parameters
    assert "project_dir" in open_params
    import_params = inspect.signature(core.LogoProject.import_legacy).parameters
    assert "project_dir" in import_params and "source_container" in import_params

    for name in (
        "project_dir",
        "save",
        "image_file",
        "replace_image",
        "set_block_size",
        "to_container",
        "validate",
        "repair",
        "dirty",
    ):
        assert hasattr(core.LogoProject, name)

    zip_params = inspect.signature(core.make_update_zip).parameters
    for name in ("logo_bytes", "out_zip", "template", "logo_filename", "updater_script", "update_binary", "sign_stub"):
        assert name in zip_params

    boot_params = inspect.signature(core.read_boot_logo).parameters
    assert list(boot_params)[:2] == ["path", "size"]

    from logobuilder.core.cli import main

    assert list(inspect.signature(main).parameters)[0] == "argv"


def test_dataclass_fields():
    assert is_dataclass(core.ImageBlock)
    image_fields = [field.name for field in fields(core.ImageBlock)]
    for name in ("index", "size", "pixels", "raw_compressed"):
        assert name in image_fields
    binary_fields = [field.name for field in fields(core.BinaryBlock)]
    for name in ("index", "data", "raw_compressed", "decompressed_ok"):
        assert name in binary_fields
    container_fields = [field.name for field in fields(core.LogoContainer)]
    for name in ("header", "tag", "blocks", "source_path", "mode", "raw_payload"):
        assert name in container_fields
    project_fields = [field.name for field in fields(core.ProjectBlock)]
    for name in ("index", "kind", "size", "image_path", "binary_path", "raw_compressed_path", "modified"):
        assert name in project_fields
    issue_fields = [field.name for field in fields(core.ProjectIssue)]
    assert issue_fields == ["level", "block_index", "message"]
    size_fields = [field.name for field in fields(core.LogoSize)]
    assert size_fields == ["width", "height", "color_depth"]
    candidate_fields = [field.name for field in fields(core.SizeCandidate)]
    assert candidate_fields == ["size", "score", "reason"]


def test_dataclass_construction_like_gui():
    """GUI 会按 spec_api 的形状构造块/容器（命名参数）。"""
    from PIL import Image

    size = core.LogoSize(4, 4, 2)
    pixels = core.image_to_pixels(Image.new("RGBA", (4, 4), (1, 2, 3, 255)), size)
    image_block = core.ImageBlock(index=0, size=size, pixels=pixels, raw_compressed=None)
    binary_block = core.BinaryBlock(index=1, data=b"raw", raw_compressed=None, decompressed_ok=False)
    container = core.LogoContainer(header=bytes(512), tag="LOGO", blocks=[image_block, binary_block])
    assert container.count == 2
    assert container.mode == "block-table"
    data = container.to_bytes()
    assert data[:4] == core.MAGIC.to_bytes(4, "little")
    project_block = core.ProjectBlock(index=0, kind="image", size=size, image_path=Path("img1.png"))
    assert project_block.size == size
    issue = core.ProjectIssue(level="error", block_index=None, message="x")
    assert issue.level == "error"


def test_sniff_format_contract():
    assert core.sniff_format(b"") == "unknown"
    assert core.sniff_format(b"not mtk" * 100) == "unknown"
    empty_container = core.LogoContainer.create([]).to_bytes()
    assert core.sniff_format(empty_container) == "mtk-logo-block"
    assert core.is_mtk_logo(empty_container)
    assert not core.is_mtk_logo(b"")


# ----------------------------------------------------------------------
# 依赖 / 兼容性约束
# ----------------------------------------------------------------------
FORBIDDEN_MODULES = ("tkinter", "ttkbootstrap", "numpy", "cv2", "PyQt5", "PySide6", "wx")
ALLOWED_THIRD_PARTY = {"PIL", "capstone"}  # capstone = LK 修补的可选依赖（延迟导入）


def _imported_top_levels(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("path", sorted(CORE_DIR.glob("*.py")), ids=lambda p: p.name)
def test_core_imports_are_pure(path: Path):
    source = path.read_text(encoding="utf-8")
    modules = _imported_top_levels(source)
    for forbidden in FORBIDDEN_MODULES:
        assert forbidden not in modules, f"{path.name} 不得 import {forbidden}"
    # 第三方只允许 Pillow：其它非标准库顶层模块都不应出现
    stdlib = set(sys.stdlib_module_names)
    for module in modules:
        if module in stdlib or module == "logobuilder":
            continue
        if module in ALLOWED_THIRD_PARTY:
            continue
        raise AssertionError(f"{path.name} 引入了未批准的依赖：{module}")


@pytest.mark.parametrize("path", sorted(CORE_DIR.glob("*.py")), ids=lambda p: p.name)
def test_core_python310_syntax(path: Path):
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source, filename=str(path), feature_version=(3, 10))
    except SyntaxError as exc:  # pragma: no cover - 失败即不兼容 3.10
        raise AssertionError(f"{path.name} 使用了 Python 3.10 不支持的语法：{exc}") from exc


@pytest.mark.parametrize("path", sorted(CORE_DIR.glob("*.py")), ids=lambda p: p.name)
def test_core_compiles(path: Path):
    compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_import_does_not_pull_gui_modules():
    code = "import logobuilder.core, sys; print('tkinter' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SRC),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
