"""工程（项目目录）模型测试：新建/打开/编辑/校验/修复/旧版兼容/无损回包。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from logobuilder.core import (
    HEADER_FILE,
    LEGACY_PROJECT_FILE,
    PROJECT_FILE,
    BinaryBlock,
    ImageBlock,
    LogoContainer,
    LogoProject,
    LogoSize,
    ProjectBlock,
    ProjectError,
    ProjectIssue,
    SizeDB,
    image_to_pixels,
)

from helpers import LOGO_BIN, make_container_bytes, make_header, requires_samples


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_image(size: LogoSize, color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", (size.width, size.height), color)


def make_synthetic_container() -> LogoContainer:
    """4 块：bpp4 图、bpp2 图、未知尺寸 zlib 数据（pending）、不透明二进制块。"""
    import zlib

    size4 = LogoSize(16, 8, 4)
    size2 = LogoSize(16, 8, 2)
    image4 = make_image(size4, (10, 20, 30, 255))
    image2 = make_image(size2, (200, 100, 50, 255))
    payloads = [
        zlib.compress(image_to_pixels(image4, size4), 9),
        zlib.compress(image_to_pixels(image2, size2), 9),
        # 未知尺寸块：100 字节（5x10x2 的合法长度但内置表里没有）
        zlib.compress(bytes(range(100)), 9),
        # 不透明块：不是 zlib 数据，原样保留
        b"opaque-binary-payload",
    ]
    data = make_container_bytes(payloads, compress=False)
    return LogoContainer.parse(data, db=SizeDB(), block_sizes=[size4, size2, None, None])


# ----------------------------------------------------------------------
# 新建 / 文件布局
# ----------------------------------------------------------------------
def test_project_new_layout(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    directory = project.project_dir
    assert (directory / HEADER_FILE).is_file()
    assert (directory / HEADER_FILE).stat().st_size == 512
    assert (directory / PROJECT_FILE).is_file()
    assert (directory / LEGACY_PROJECT_FILE).is_file()
    assert (directory / "sizes.txt").is_file()
    assert len(project.blocks) == 4
    payload = json.loads((directory / PROJECT_FILE).read_text(encoding="utf-8"))
    assert payload["format"] == "logobuilder-project"
    assert payload["version"] == 1
    assert payload["mode"] == "block-table"
    assert len(payload["blocks"]) == 4
    # 图像块命名 img1.png/img2.png（N 从 1 连续）；二进制块 block{index}.dat
    assert project.blocks[0].image_path == Path("img1.png")
    assert project.blocks[1].image_path == Path("img2.png")
    assert project.blocks[2].binary_path == Path("block2.dat")
    assert project.blocks[2].kind == "image"  # 解压成功但尺寸未知 -> GUI 显示"待识别"
    assert project.blocks[2].size is None
    assert project.blocks[2].image_path is None
    assert project.blocks[3].kind == "binary"
    assert project.blocks[3].binary_path == Path("block3.dat")
    assert (directory / "img1.png").is_file()
    assert (directory / "block2.dat").is_file()
    # 原版头文件名 header 与 header.bin 同时存在
    assert (directory / "header").read_bytes() == (directory / HEADER_FILE).read_bytes()
    # 无损原始压缩数据已保存
    assert (directory / "raw" / "0.z").is_file()
    assert (directory / "raw" / "1.z").is_file()
    assert not project.dirty


def test_project_pending_block_shape_for_gui(tmp_path):
    """GUI 约定：kind=="image" 且 size is None 的块显示为"待识别分辨率"。"""
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    pending = project.blocks[2]
    assert pending.kind == "image"
    assert pending.size is None
    # 未指定尺寸时打包仍按二进制无损保留
    container = project.to_container()
    assert isinstance(container.blocks[2], BinaryBlock)
    assert container.blocks[2].raw_compressed is not None
    # 指定尺寸后变为真正图像块（见 set_block_size 测试）
    project.set_block_size(2, LogoSize(5, 10, 2))
    assert project.blocks[2].kind == "image"
    assert project.blocks[2].size == LogoSize(5, 10, 2)
    assert isinstance(project.to_container().blocks[2], ImageBlock)


def test_project_to_container_byte_identical(tmp_path):
    source = make_synthetic_container()
    original = source.to_bytes()
    project = LogoProject.new(tmp_path / "proj", source)
    rebuilt = project.to_container().to_bytes()
    assert sha256(rebuilt) == sha256(original)


@requires_samples
def test_real_logo_project_roundtrip_byte_identical(tmp_path, real_logo_bytes):
    container = LogoContainer.parse(real_logo_bytes)
    project = LogoProject.new(tmp_path / "real", container)
    assert project.count == 51
    rebuilt = project.to_container().to_bytes()
    assert sha256(rebuilt) == sha256(real_logo_bytes)


def test_project_new_rejects_non_empty_dir(tmp_path):
    source = make_synthetic_container()
    directory = tmp_path / "proj"
    directory.mkdir()
    (directory / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ProjectError):
        LogoProject.new(directory, source)
    # overwrite=True 允许
    project = LogoProject.new(directory, source, overwrite=True)
    assert project.count == 4


def test_project_new_type_error(tmp_path):
    with pytest.raises(TypeError):
        LogoProject.new(tmp_path / "proj", "not a container")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 打开 / 保存 / dirty
# ----------------------------------------------------------------------
def test_project_open_and_save(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    reopened = LogoProject.open(project.project_dir)
    assert reopened.count == 4
    assert reopened.tag == "LOGO"
    assert not reopened.dirty
    assert reopened.warnings == []
    assert sha256(reopened.to_container().to_bytes()) == sha256(source.to_bytes())
    # 修改后保存 -> 重新打开仍保持修改
    image = make_image(LogoSize(16, 8, 4), (1, 2, 3, 255))
    reopened.replace_image(0, image)
    assert reopened.dirty
    reopened.save()
    assert not reopened.dirty
    again = LogoProject.open(project.project_dir)
    rebuilt = again.to_container()
    first = rebuilt.blocks[0]
    assert isinstance(first, ImageBlock)
    decoded = first.to_image()
    assert decoded.getpixel((0, 0)) == (1, 2, 3, 255)


def test_open_errors(tmp_path):
    with pytest.raises(ProjectError):
        LogoProject.open(tmp_path / "missing")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ProjectError):
        LogoProject.open(empty)
    # 缺少 project.json 但有旧版 .project 时自动导入
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / HEADER_FILE).write_bytes(bytes(make_header("LOGO", 8)))
    (legacy / LEGACY_PROJECT_FILE).write_text("binary:block0.dat\n", encoding="utf-8")
    project = LogoProject.open(legacy)
    assert project.count == 1
    # 坏 JSON
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / HEADER_FILE).write_bytes(bytes(make_header("LOGO", 8)))
    (broken / PROJECT_FILE).write_text("{oops", encoding="utf-8")
    with pytest.raises(ProjectError):
        LogoProject.open(broken)


def test_open_reconstructs_missing_header(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    (project.project_dir / HEADER_FILE).unlink()
    reopened = LogoProject.open(project.project_dir)
    # 旧版名 header 还在 -> 可以打开并给出提示
    assert reopened.warnings
    issues = reopened.validate()
    assert any(issue.level == "warning" and "header.bin" in issue.message for issue in issues)
    messages = reopened.repair(repair=True)
    assert any("已修复" in message for message in messages)
    assert (reopened.project_dir / HEADER_FILE).stat().st_size == 512
    assert not [issue for issue in reopened.validate() if issue.level == "error"]
    # 彻底删除两种头文件 -> open 用默认头并警告，repair 重建
    (reopened.project_dir / HEADER_FILE).unlink()
    (reopened.project_dir / "header").unlink()
    reopened2 = LogoProject.open(reopened.project_dir)
    assert any("缺少" in warning for warning in reopened2.warnings)
    assert any(issue.level == "error" for issue in reopened2.validate())
    reopened2.repair(repair=True)
    assert (reopened2.project_dir / HEADER_FILE).is_file()
    assert not [issue for issue in reopened2.validate() if issue.level == "error"]


# ----------------------------------------------------------------------
# 编辑：replace_image / set_block_size
# ----------------------------------------------------------------------
def test_replace_image(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    before = sha256(project.to_container().to_bytes())
    new_image = make_image(LogoSize(100, 50, 4), (250, 10, 10, 255))
    project.replace_image(0, new_image)
    after = project.to_container()
    block = after.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.size == LogoSize(16, 8, 4)
    assert block.to_image().getpixel((0, 0)) == (250, 10, 10, 255)  # 拉伸后仍为红色（bpp4 不量化）
    assert sha256(project.to_container().to_bytes()) != before
    assert project.dirty
    with pytest.raises(ProjectError):
        project.replace_image(2, new_image)  # 二进制块
    with pytest.raises(ProjectError):
        project.replace_image(99, new_image)


def test_set_block_size_resizes_image(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    project.set_block_size(0, LogoSize(32, 16, 4))
    assert project.blocks[0].size == LogoSize(32, 16, 4)
    container = project.to_container()
    block = container.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.size == LogoSize(32, 16, 4)
    assert len(block.pixels) == 32 * 16 * 4
    reparsed = LogoContainer.parse(container.to_bytes(), db=SizeDB(), block_sizes=[LogoSize(32, 16, 4), None, None, None])
    assert reparsed.blocks[0].size == LogoSize(32, 16, 4)


def test_set_block_size_converts_pending_binary_and_removes_stale_file(tmp_path):
    """QA BUG-2：二进制块转图像块后，旧的 blockN.dat 应被删除且 validate 不再报警。"""
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    pending_index = 2
    assert pending_index in project.pending
    stale = project.project_dir / project.blocks[pending_index].binary_path
    assert stale.is_file()
    project.set_block_size(pending_index, LogoSize(5, 10, 2))
    assert project.blocks[pending_index].kind == "image"
    assert project.blocks[pending_index].binary_path is None
    assert not stale.exists(), "转换后遗留的 blockN.dat 应被清理"
    assert pending_index not in project.pending
    issues = project.validate()
    assert not any("未引用" in issue.message for issue in issues)
    container = project.to_container()
    block = container.blocks[pending_index]
    assert isinstance(block, ImageBlock)
    assert block.size == LogoSize(5, 10, 2)
    assert len(block.pixels) == 100
    # 打回后再解包仍然自洽
    reparsed = LogoContainer.parse(container.to_bytes(), db=SizeDB(), block_sizes=[None, None, LogoSize(5, 10, 2), None])
    assert reparsed.blocks[pending_index].size == LogoSize(5, 10, 2)


def test_set_block_size_errors(tmp_path):
    source = make_synthetic_container()
    project = LogoProject.new(tmp_path / "proj", source)
    with pytest.raises(ProjectError):
        project.set_block_size(2, LogoSize(7, 7, 2))  # 49 != 100
    with pytest.raises(ProjectError):
        project.set_block_size(3, LogoSize(5, 10, 2))  # 不透明块不可转
    with pytest.raises(TypeError):
        project.set_block_size(0, "540x960x4")  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 校验 / 修复
# ----------------------------------------------------------------------
def test_validate_clean_project(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    issues = project.validate()
    assert [issue for issue in issues if issue.level == "error"] == []


def test_validate_reports_missing_files(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    (project.project_dir / "img1.png").unlink()
    issues = project.validate()
    assert any(issue.level == "error" and "img1.png" in issue.message for issue in issues)


def test_repair_restores_missing_image_from_raw(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    image_file = project.project_dir / "img1.png"
    image_file.unlink()
    messages = project.repair(repair=True)
    assert any("已修复" in message and "img1.png" in message for message in messages)
    assert image_file.is_file()
    assert not [issue for issue in project.validate() if issue.level == "error"]


def test_repair_resizes_mismatched_image(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    # 用一个尺寸不对的 PNG 覆盖
    make_image(LogoSize(99, 77, 2), (1, 1, 1, 255)).save(project.project_dir / "img1.png")
    issues = project.validate()
    assert any(issue.level == "warning" and "不一致" in issue.message for issue in issues)
    messages = project.repair(repair=True)
    assert any("已修复" in message for message in messages)
    with Image.open(project.project_dir / "img1.png") as handle:
        assert handle.size == (16, 8)


def test_repair_removes_orphan_files(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    orphan = project.project_dir / "block99.dat"
    orphan.write_bytes(b"stale")
    issues = project.validate()
    assert any("未引用" in issue.message for issue in issues)
    project.repair(repair=True)
    assert not orphan.exists()
    assert not any("未引用" in issue.message for issue in project.validate())


def test_repair_without_repair_flag_returns_issue_strings(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    (project.project_dir / "img2.png").unlink()
    messages = project.repair()
    assert messages
    assert all(isinstance(message, str) for message in messages)
    assert any("error" in message for message in messages)


def test_project_issue_str():
    assert str(ProjectIssue("error", 3, "坏了")) == "[error] 块 3：坏了"
    assert str(ProjectIssue("warning", None, "注意")) == "[warning] 注意"
    with pytest.raises(ValueError):
        ProjectIssue("fatal", None, "x")


# ----------------------------------------------------------------------
# 旧版 .project 兼容
# ----------------------------------------------------------------------
def test_legacy_original_header_name(tmp_path):
    """原版工程头文件名为无扩展名的 ``header``；import/open/save 都要兼容。"""
    from logobuilder.core import LEGACY_HEADER_FILE

    directory = tmp_path / "legacy"
    directory.mkdir()
    size = LogoSize(16, 8, 4)
    image = make_image(size, (7, 8, 9, 255))
    image.save(directory / "img1.png")
    (directory / "block1.dat").write_bytes(b"binary-payload")
    header = bytes(make_header("LOGO", 8))
    (directory / LEGACY_HEADER_FILE).write_bytes(header)
    (directory / LEGACY_PROJECT_FILE).write_text(
        "image:img1.png 16 8 4\nbinary:block1.dat\n", encoding="utf-8"
    )
    # 只有原版 header / .project 时也能自动导入
    project = LogoProject.open(directory)
    assert project.count == 2
    assert project.header == header
    container = project.to_container()
    assert isinstance(container.blocks[0], ImageBlock)
    assert container.blocks[0].size == size
    assert isinstance(container.blocks[1], BinaryBlock)
    # save() 会补齐 header.bin 并写回原版 header
    project.save()
    assert (directory / HEADER_FILE).read_bytes() == header
    assert (directory / LEGACY_HEADER_FILE).read_bytes() == header
    assert (directory / PROJECT_FILE).is_file()
    assert not [issue for issue in project.validate() if issue.level == "error"]


def test_legacy_export_and_import(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    lines = project.legacy_lines()
    assert lines[0].startswith("image:img1.png 16 8 4")
    assert lines[1].startswith("image:img2.png 16 8 2")
    assert lines[2] == "binary:block2.dat"
    assert lines[3] == "binary:block3.dat"
    legacy_path = project.project_dir / LEGACY_PROJECT_FILE
    assert legacy_path.read_text(encoding="utf-8").splitlines() == lines

    # 自动挂接 raw/N.z -> 无损回包
    imported = LogoProject.import_legacy(project.project_dir)
    assert imported.count == 4
    assert sha256(imported.to_container().to_bytes()) == sha256(project.to_container().to_bytes())

    # 显式 source_container
    source = make_synthetic_container()
    imported2 = LogoProject.import_legacy(project.project_dir, source_container=source)
    assert sha256(imported2.to_container().to_bytes()) == sha256(source.to_bytes())


def test_legacy_import_errors(tmp_path):
    with pytest.raises(ProjectError):
        LogoProject.import_legacy(tmp_path / "missing")
    directory = tmp_path / "noheader"
    directory.mkdir()
    (directory / LEGACY_PROJECT_FILE).write_text("binary:block0.dat\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        LogoProject.import_legacy(directory)
    directory2 = tmp_path / "noproject"
    directory2.mkdir()
    (directory2 / HEADER_FILE).write_bytes(bytes(make_header("LOGO", 8)))
    with pytest.raises(ProjectError):
        LogoProject.import_legacy(directory2)
    # 坏行
    directory3 = tmp_path / "badline"
    directory3.mkdir()
    (directory3 / HEADER_FILE).write_bytes(bytes(make_header("LOGO", 8)))
    (directory3 / LEGACY_PROJECT_FILE).write_text("garbage-line\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        LogoProject.import_legacy(directory3)


def test_legacy_import_short_line(tmp_path):
    directory = tmp_path / "legacy"
    directory.mkdir()
    (directory / HEADER_FILE).write_bytes(bytes(make_header("LOGO", 8)))
    (directory / LEGACY_PROJECT_FILE).write_text("image:img1.png 540\n", encoding="utf-8")
    with pytest.raises(ProjectError):
        LogoProject.import_legacy(directory)


# ----------------------------------------------------------------------
# raw 模式工程（lk.bin）
# ----------------------------------------------------------------------
@requires_samples
def test_raw_project_roundtrip(tmp_path, real_lk_bytes):
    container = LogoContainer.parse(real_lk_bytes)
    project = LogoProject.new(tmp_path / "lk", container)
    rebuilt = project.to_container()
    assert rebuilt.mode == "raw"
    assert sha256(rebuilt.to_bytes()) == sha256(real_lk_bytes)
    assert not [issue for issue in project.validate() if issue.level == "error"]


def test_project_block_validation():
    with pytest.raises(ValueError):
        ProjectBlock(0, "weird")
    with pytest.raises(ValueError):
        ProjectBlock(0, "image", size="540x960x4")  # type: ignore[arg-type]


def test_project_image_file_and_errors(tmp_path):
    project = LogoProject.new(tmp_path / "proj", make_synthetic_container())
    assert project.image_file(0).name == "img1.png"
    assert project.binary_file(3).name == "block3.dat"
    with pytest.raises(ProjectError):
        project.image_file(3)
    with pytest.raises(ProjectError):
        project.binary_file(0)
    with pytest.raises(ProjectError):
        project.block_at(99)
