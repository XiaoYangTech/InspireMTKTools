"""对话框测试：分辨率选择、替换图片、错误详情、导出选项、设置、帮助。"""

from __future__ import annotations

import pytest
from PIL import Image

from logobuilder.ui import demo, session as session_mod
from logobuilder.ui.dialogs import (
    AboutDialog,
    BootLogoDialog,
    ErrorDialog,
    ExportImgDialog,
    HelpDialog,
    ImagePreviewDialog,
    ReplaceImageDialog,
    ResolutionDialog,
    SettingsDialog,
)


def _candidates(core):
    candidates, length, pixels = demo.build_resolution_candidates(core)
    return candidates, length, pixels


def test_resolution_dialog_candidates_and_apply(app, core):
    candidates, length, pixels = _candidates(core)
    dialog = ResolutionDialog(
        app,
        block_index=3,
        decompressed_len=length,
        candidates=candidates,
        preview_provider=lambda size: core.pixels_to_image(pixels, size),
        same_size_count=3,
    )
    app.root.update_idletasks()
    assert len(dialog.tree.get_children()) == len(candidates)
    # 默认选中第一项并给出正确字节数提示
    assert "1,639,680" in dialog.message_label.cget("text")
    assert str(dialog.apply_button.cget("state")) == "normal"
    # 同尺寸应用勾选框默认勾选
    assert dialog.apply_all_var.get() is True
    assert dialog.remember_var.get() is True

    dialog._on_apply()
    assert dialog.result is not None
    assert session_mod.size_key(dialog.result.size) == length
    assert dialog.result.apply_all is True
    assert dialog.result.remember is True


def test_resolution_dialog_custom_invalid_and_mismatch(app, core):
    candidates, length, _pixels = _candidates(core)
    dialog = ResolutionDialog(
        app,
        block_index=0,
        decompressed_len=length,
        candidates=candidates,
        preview_provider=None,
    )
    # 非法输入 -> Apply 禁用
    dialog.width_var.set("abc")
    dialog._on_custom_changed()
    assert str(dialog.apply_button.cget("state")) == "disabled"
    # 合法但字节数不匹配 -> 禁用
    dialog.width_var.set("100")
    dialog.height_var.set("100")
    dialog._on_custom_changed()
    assert str(dialog.apply_button.cget("state")) == "disabled"
    assert "字节" in dialog.message_label.cget("text")


def test_resolution_dialog_skip(app, core):
    candidates, length, _pixels = _candidates(core)
    dialog = ResolutionDialog(app, block_index=0, decompressed_len=length, candidates=candidates)
    dialog._on_skip()
    assert dialog.result is not None and dialog.result.skipped
    dialog2 = ResolutionDialog(app, block_index=0, decompressed_len=length, candidates=candidates)
    dialog2._on_skip_all()
    assert dialog2.result is not None and dialog2.result.skip_all


def test_resolution_dialog_boot_logo_mode(app, core):
    """boot_logo 歧义尺寸：候选只允许 RGB565（色深 2）。"""
    size = core.LogoSize(64, 48, 2)
    image = demo.build_demo_image((64, 48), 2)
    pixels = core.image_to_pixels(image, size)
    length = int(size.key)
    candidates = session_mod.boot_logo_size_candidates(length)
    assert all(getattr(c.size, "color_depth", 0) == 2 for c in candidates)
    dialog = ResolutionDialog(
        app,
        block_index=0,
        decompressed_len=length,
        candidates=candidates,
        preview_provider=lambda candidate: core.pixels_to_image(pixels, candidate),
        subject="boot_logo 测试",
        allowed_depths=(2,),
        default_depth=2,
    )
    assert len(dialog.allowed_depths) == 1
    assert "2（RGB565）" in dialog.bpp_combo.cget("values")[dialog.bpp_combo.current()]
    dialog._on_skip()


def test_replace_image_dialog(app, core, tmp_path):
    target = core.LogoSize(135, 24, 4)
    current = core.pixels_to_image(core.image_to_pixels(demo.build_demo_image((135, 24), 3), target), target)
    dialog = ReplaceImageDialog(app, block_index=1, target_size=target, current_image=current)
    app.root.update_idletasks()
    assert str(dialog.apply_button.cget("state")) == "disabled"

    source = tmp_path / "replacement.png"
    demo.build_replace_image().save(source)
    dialog.load_file(source)
    app.root.update_idletasks()
    assert dialog.source_image is not None
    assert str(dialog.apply_button.cget("state")) == "normal"
    dialog.fit_var.set("contain")
    dialog._on_apply()
    assert dialog.result is not None
    assert dialog.result.image is not None
    assert dialog.result.fit == "contain"


def test_replace_image_dialog_bad_file(app):
    dialog = ReplaceImageDialog(app, block_index=0, target_size=None, current_image=None)
    bad = app.config_path.parent / "not-an-image.png"
    bad.write_bytes(b"not an image")
    dialog.load_file(bad)
    assert dialog.source_image is None
    assert "无法读取图片" in dialog.source_label.cget("text")
    dialog.destroy()


def test_error_dialog_details_and_copy(app):
    dialog = ErrorDialog(
        app,
        message="不是有效的 MTK LOGO 镜像",
        hint="请选择官方线刷包里的 logo.bin",
        traceback_text="Traceback (most recent call last):\nValueError: bad magic",
    )
    app.root.update_idletasks()
    assert dialog._details_visible is True
    assert dialog.details_frame.winfo_ismapped()
    dialog.toggle_details()
    assert dialog._details_visible is False
    dialog.toggle_details()
    dialog.copy_details()
    assert "bad magic" in dialog.clipboard_get()
    assert "bad magic" in dialog.details_text.get("1.0", "end")
    dialog.destroy()


def test_export_img_dialog(app, tmp_path):
    target = tmp_path / "logo.img"
    dialog = ExportImgDialog(app, default_path=target, actual_size=1000, suggested_target=4096)
    app.root.update_idletasks()
    dialog.path_var.set(str(target))
    dialog.pad_var.set(True)
    dialog.target_var.set("8192")
    dialog.fill_var.set(255)
    dialog._on_ok()
    assert dialog.result is not None
    assert dialog.result.path == target
    assert dialog.result.pad_target == 8192
    assert dialog.result.fill_byte == 255


def test_settings_dialog_fields(app):
    dialog = SettingsDialog(app)
    app.root.update_idletasks()
    # 中文单语 + 单一 bootstrap-light 主题：设置中不应再有主题/语言项
    assert not hasattr(dialog, "theme_var")
    assert not hasattr(dialog, "language_var")
    assert dialog.fit_var.get() == app.settings.default_fit
    assert dialog.padding_var.get() == app.settings.padding_byte
    dialog.fit_var.set("contain")
    dialog.padding_var.set(255)
    dialog._on_ok()
    assert dialog.result is not None
    assert dialog.result.default_fit == "contain"
    assert dialog.result.padding_byte == 255
    assert app.settings.default_fit == "contain"  # 与 app.settings 为同一对象


def test_help_about_bootlogo_preview_dialogs(app, core):
    help_dialog = HelpDialog(app)
    help_dialog.destroy()
    about_dialog = AboutDialog(app)
    about_dialog.destroy()
    boot_dialog = BootLogoDialog(app)
    boot_dialog.destroy()
    image = demo.build_demo_image((64, 48), 0)
    preview = ImagePreviewDialog(app, title="boot_logo", image=image, caption="64 × 48 × 2")
    app.root.update_idletasks()
    assert preview.image.size == (64, 48)
    preview.destroy()
