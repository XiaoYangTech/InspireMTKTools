"""端到端集成：真实 core 的打开 / 替换 / 导出 / 项目 / 未知尺寸 / 像素格式显示。"""

from __future__ import annotations

import zipfile

import pytest
from PIL import Image

from logobuilder.ui import demo, session as session_mod


@pytest.fixture()
def loaded_app(app, core, tmp_path):
    container = demo.build_demo_container(core)
    source = tmp_path / "logo.bin"
    source.write_bytes(container.to_bytes())
    session = app.load_container_sync(source)
    app.root.update_idletasks()
    return app, session, source


def test_open_container_shows_blocks(loaded_app):
    app, session, _source = loaded_app
    assert session.count == 7
    assert session.tag == "LOGO"
    assert len(app.block_list.cards) == 7
    assert app.selected_index == 0
    assert app.preview._current_view() is not None
    assert str(app.toolbar.button_by_action("export_logo_img").cget("state")) == "normal"


def test_preview_info_matches_selected_block_bpp(loaded_app):
    """选中 bpp2 的块时，信息卡必须显示 RGB565 · 2 bpp（而不是沿用上一块）。"""
    app, session, _source = loaded_app
    # 第 2 块（index=1）是 540x960x2
    view = session.block(1)
    assert view is not None and view.size is not None
    assert view.size.color_depth == 2
    app.on_block_selected(1)
    app.root.update_idletasks()
    app.preview._update_info(app.preview._current_view())
    assert app.preview._info_vars["format"].get() == "RGB565 · 2 bpp"
    assert app.preview._info_vars["resolution"].get().startswith("540 × 960")
    # 第 1 块（index=0）是 BGRA
    app.on_block_selected(0)
    app.preview._update_info(app.preview._current_view())
    assert app.preview._info_vars["format"].get() == "BGRA8888 · 4 bpp"


def test_replace_and_restore(loaded_app):
    app, session, _source = loaded_app
    image = demo.build_replace_image()
    assert app.replace_image_direct(0, image, fit="contain")
    view = session.block(0)
    assert view is not None and view.modified
    assert session.can_restore(0)
    assert app.preview._current_view() is not None
    app.restore_image(0)
    assert session.block(0) is not None and not session.block(0).modified
    assert not session.can_restore(0)


def test_export_img_bin_zip_bootlogo(loaded_app, core, tmp_path):
    app, session, source = loaded_app
    out_dir = tmp_path / "out"

    img_path = out_dir / "logo.img"
    result = app.export_bytes_sync(img_path, pad_target=len(source.read_bytes()) + 2048, fill_byte=0xFF)
    assert img_path.is_file()
    assert result["size"] == len(source.read_bytes()) + 2048
    data = img_path.read_bytes()
    assert data[-1] == 0xFF
    reparsed = core.LogoContainer.load(img_path, db=session_mod.get_size_db())
    assert reparsed.count == session.count

    bin_path = out_dir / "logo.bin"
    app.export_bytes_sync(bin_path)
    parsed = core.LogoContainer.load(bin_path, db=session_mod.get_size_db())
    assert parsed.count == session.count

    zip_path = out_dir / "update.zip"
    app.export_zip_sync(zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
    assert "logo.bin" in names
    assert "META-INF/com/google/android/updater-script" in names

    boot_path = out_dir / "boot_logo"
    image = session.image(0)
    assert image is not None
    size = core.write_boot_logo(image, boot_path)
    read_image, read_size = core.read_boot_logo(boot_path, size)
    assert read_image.size == image.size
    assert (read_size.width, read_size.height) == image.size


def test_project_create_replace_reopen(loaded_app, core, tmp_path):
    app, session, source = loaded_app
    project_dir = tmp_path / "project"
    project = app.create_project_sync(source, project_dir)
    assert project.mode == "project"
    assert (project_dir / "header.bin").is_file()
    assert (project_dir / "project.json").is_file()
    assert project.count == session.count

    assert app.replace_image_direct(1, demo.build_replace_image(), fit="cover")
    app.save_project()
    reopened = app.open_project_sync(project_dir)
    assert reopened.count == session.count
    out = tmp_path / "from_project.bin"
    app.export_bytes_sync(out)
    parsed = core.LogoContainer.load(out, db=session_mod.get_size_db())
    assert parsed.count == session.count
    assert session_mod.is_project_dir(project_dir)


def test_unknown_size_detection_and_assign(app, core, tmp_path):
    container = demo.build_unknown_container(core)
    source = tmp_path / "unknown.bin"
    source.write_bytes(container.to_bytes())
    session = app.load_container_sync(source)
    assert session.pending_unknowns() == [0]
    assert app.block_list.cards[0].view.is_unknown
    size = core.LogoSize(123, 45, 4)
    assert app.assign_size_sync(0, size)
    view = session.block(0)
    assert view is not None and view.is_image and view.size == size
    out = tmp_path / "fixed.bin"
    app.export_bytes_sync(out)
    parsed = core.LogoContainer.load(out, db=session_mod.get_size_db())
    assert parsed.count == 1


def test_boot_logo_size_uniqueness_helpers(core):
    # 6480 字节 = 135x24x2（内置尺寸表精确命中）-> 唯一
    unique = session_mod.boot_logo_unique_size(6480)
    assert unique is not None and int(unique.key) == 6480
    # 只有 4bpp 命中相同 key 时，应能用因式分解补充出 RGB565 候选（供用户确认）
    candidates = session_mod.boot_logo_size_candidates(int(core.LogoSize(135, 24, 4).key))
    assert candidates, "4bpp 命中相同长度时也应给出 RGB565 候选"
    assert all(int(c.size.key) == 135 * 24 * 4 for c in candidates)
    # 任意偶数长度都应有候选，且多解时 unique 返回 None
    many = session_mod.boot_logo_size_candidates(6480)
    assert many
    # 奇数长度无法是 RGB565 裸数据
    assert session_mod.boot_logo_size_candidates(12345) == []


def test_project_unknown_block_assign_size_updates_kind(core, tmp_path):
    """防回归：项目会话里把「尺寸未知」块指定尺寸后，kind 必须从 unknown 变为 image，
    且 session.image(index) 能立即取到图像（无需重开工程）。"""
    from logobuilder.ui import demo as demo_module
    from logobuilder.ui import session as session_mod

    container = demo_module.build_unknown_container(core, width=123, height=45, depth=4)
    source = tmp_path / "unknown_size.bin"
    source.write_bytes(container.to_bytes())
    container_session = session_mod.open_container(source)
    assert container_session.pending_unknowns() == [0]

    project = session_mod.new_project(tmp_path / "proj_unknown", container_session.container)
    view = project.block(0)
    assert view is not None and view.is_unknown
    assert project.image(0) is None

    size = core.LogoSize(123, 45, 4)
    project.assign_size(0, size)
    view = project.block(0)
    assert view is not None
    assert view.kind == "image", "assign_size 后 kind 必须更新为 image"
    assert view.size == size
    image = project.image(0)
    assert image is not None and image.size == (123, 45)
