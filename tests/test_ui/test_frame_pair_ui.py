"""左右双帧（480x160 = 2 x 240x160）UI 适配测试。"""

from __future__ import annotations

import json

import pytest
from PIL import Image

from logobuilder.ui import demo

PAIR_WIDTH = 480
PAIR_HEIGHT = 160
FRAME_WIDTH = 240
FRAME_HEIGHT = 160
PAIR_INDEX = 6  # 演示容器：6 单帧 + 1 双帧 + 1 二进制


@pytest.fixture()
def loaded(app, core, tmp_path):
    container = demo.build_demo_container(core)
    source = tmp_path / "logo.bin"
    source.write_bytes(container.to_bytes())
    session = app.load_container_sync(source)
    app.root.update_idletasks()
    app.root.update()
    return app, session


def _pair_view(session):
    view = next(
        (
            item
            for item in session.blocks
            if item.is_multi_frame
            and item.size is not None
            and item.size.width == PAIR_WIDTH
            and item.size.height == PAIR_HEIGHT
        ),
        None,
    )
    assert view is not None, "演示容器缺少 480x160 双帧块"
    return view


def _red_frame() -> Image.Image:
    return Image.new("RGBA", (FRAME_WIDTH, FRAME_HEIGHT), (255, 0, 0, 255))


# ── 会话层 ────────────────────────────────────────────────
def test_pair_view_flags(loaded):
    _app, session = loaded
    view = _pair_view(session)
    assert view.frame_count == 2
    assert view.is_multi_frame
    assert view.frame_width == FRAME_WIDTH
    assert view.frame_height == FRAME_HEIGHT
    frame_size = view.frame_size
    assert frame_size is not None
    assert (frame_size.width, frame_size.height, frame_size.color_depth) == (240, 160, 4)


def test_session_frame_image_container(loaded):
    _app, session = loaded
    view = _pair_view(session)
    frame0 = session.frame_image(view.index, 0)
    frame1 = session.frame_image(view.index, 1)
    assert frame0 is not None and frame1 is not None
    assert frame0.size == frame1.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert frame0.tobytes() != frame1.tobytes()
    with pytest.raises(IndexError):
        session.frame_image(view.index, 2)
    # 单帧块：frame_image 等价于整图
    single = session.block(0)
    assert session.frame_image(0).size == (single.size.width, single.size.height)


def test_container_replace_frame_keeps_other_frame(loaded):
    _app, session = loaded
    view = _pair_view(session)
    before0 = session.frame_image(view.index, 0).tobytes()
    before1 = session.frame_image(view.index, 1).tobytes()
    session.replace_frame(view.index, 1, _red_frame())
    after0 = session.frame_image(view.index, 0)
    after1 = session.frame_image(view.index, 1)
    assert after0.tobytes() == before0
    assert after1.tobytes() != before1
    assert after1.getpixel((10, 10)) == (255, 0, 0, 255)
    assert session.block(view.index).frame_count == 2
    assert session.block(view.index).modified
    assert session.can_restore(view.index)
    # 恢复整块
    assert session.restore_original(view.index)
    restored = session.frame_image(view.index, 1)
    assert restored.tobytes() == before1
    assert session.block(view.index).frame_count == 2


# ── 预览面板 ──────────────────────────────────────────────
def test_preview_frame_selector_and_info(loaded):
    app, session = loaded
    view = _pair_view(session)
    app.block_list.select(view.index, notify=True)
    app.root.update_idletasks()
    app.root.update()
    assert app.preview.frame_bar.winfo_manager() == "pack"
    assert len(app.preview.frame_buttons) == 2
    assert app.preview.frame_index == 0
    assert app.preview._info_vars["frame_layout"].get() == "左右双帧（每帧 240×160）"
    # 切到帧 2 后立即刷新
    app.preview.set_frame(1)
    app.root.update_idletasks()
    app.root.update()
    assert app.preview.frame_index == 1
    assert app.preview.frame_var.get() == 1
    # 单帧块隐藏帧选择
    app.block_list.select(0, notify=True)
    app.root.update_idletasks()
    assert app.preview.frame_bar.winfo_manager() == ""
    assert app.preview.frame_index == 0
    assert app.preview._info_vars["frame_layout"].get() == "单帧"


def test_block_card_title_contains_frame_layout(loaded):
    app, session = loaded
    view = _pair_view(session)
    app.block_list.refresh()
    app.root.update_idletasks()
    card = app.block_list.cards[view.index]
    title = card.title_label.cget("text")
    assert "480 × 160 × 4" in title or "480×160×4" in title
    assert "2帧" in title
    assert "每帧240×160" in title


# ── 替换 / 导出 / 恢复（GUI 入口）────────────────────────
def test_replace_current_frame_via_app(loaded):
    app, session = loaded
    view = _pair_view(session)
    before0 = session.frame_image(view.index, 0).tobytes()
    app.block_list.select(view.index, notify=True)
    app.preview.set_frame(1)
    assert app.replace_image_direct(view.index, _red_frame(), fit="stretch", frame=1)
    assert session.frame_image(view.index, 0).tobytes() == before0
    assert session.frame_image(view.index, 1).getpixel((10, 10)) == (255, 0, 0, 255)
    # 恢复后自动回到帧 1
    app.restore_image(view.index)
    app.root.update_idletasks()
    assert app.preview.frame_index == 0


def test_export_current_frame_png(loaded, tmp_path, monkeypatch):
    app, session = loaded
    view = _pair_view(session)
    app.block_list.select(view.index, notify=True)
    app.preview.set_frame(1)
    captured: dict[str, str] = {}

    def fake_save(*_args, **kwargs):
        captured.update({key: str(value) for key, value in kwargs.items()})
        target = tmp_path / "frame.png"
        return str(target)

    monkeypatch.setattr("logobuilder.ui.app.filedialog.asksaveasfilename", fake_save)
    app.export_block_png(view.index)
    target = tmp_path / "frame.png"
    assert target.is_file()
    with Image.open(target) as saved:
        assert saved.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert captured.get("initialfile") == "block_007_frame2.png"


# ── 项目会话 ──────────────────────────────────────────────
def test_project_session_replace_frame(loaded, tmp_path):
    app, session = loaded
    source = session.source_path
    project_dir = tmp_path / "project"
    project = app.create_project_sync(source, project_dir)
    assert project.mode == "project"
    view = _pair_view(project)
    assert view.frame_count == 2
    frame0_before = project.frame_image(view.index, 0).tobytes()
    frame1_before = project.frame_image(view.index, 1).tobytes()
    project.replace_frame(view.index, 0, _red_frame())
    frame0_after = project.frame_image(view.index, 0)
    assert frame0_after.getpixel((10, 10)) == (255, 0, 0, 255)
    assert project.frame_image(view.index, 1).tobytes() == frame1_before
    assert project.block(view.index).frame_count == 2
    # frame_count 持久化到 project.json
    payload = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    pair_block = payload["blocks"][view.index]
    assert pair_block.get("frames") == 2
    # 恢复整块
    assert project.restore_original(view.index)
    assert project.frame_image(view.index, 0).tobytes() == frame0_before


def test_project_open_preserves_frames(loaded, tmp_path):
    app, session = loaded
    project_dir = tmp_path / "project"
    app.create_project_sync(session.source_path, project_dir)
    reopened = app.open_project_sync(project_dir)
    view = _pair_view(reopened)
    assert view.frame_count == 2
    frame0 = reopened.frame_image(view.index, 0)
    frame1 = reopened.frame_image(view.index, 1)
    assert frame0.size == frame1.size == (FRAME_WIDTH, FRAME_HEIGHT)
    assert frame0.tobytes() != frame1.tobytes()
