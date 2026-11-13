"""主窗口冒烟测试：组件存在、主题/语言/日志切换、菜单。"""

from __future__ import annotations

import pytest

from logobuilder.ui import core_bridge


def test_window_and_core_bridge(app):
    root = app.root
    assert root.winfo_exists()
    assert "InspireMTKTools" in root.title()
    for attr in ("header", "toolbar", "block_list", "preview", "status_bar", "log_panel", "paned", "menubar"):
        assert hasattr(app, attr), f"缺少组件 {attr}"
    ready, error = core_bridge.core_status()
    assert ready, f"核心库不可用：{error}"
    assert app.session is None
    assert app.status_bar.status_label.cget("text")


def test_toolbar_buttons(app):
    toolbar = app.toolbar
    assert toolbar.button_count() == 10
    expected = {
        "open_image_dialog",
        "new_project_dialog",
        "open_project_dialog",
        "replace_image_dialog",
        "restore_selected_image",
        "export_logo_img",
        "export_logo_bin",
        "export_update_zip",
        "show_boot_logo_dialog",
        "show_lk_panel",
    }
    assert set(toolbar.buttons) == expected
    # 未打开会话时导出按钮应禁用、打开类按钮可用
    assert str(toolbar.button_by_action("export_logo_img").cget("state")) == "disabled"
    assert str(toolbar.button_by_action("open_image_dialog").cget("state")) == "normal"


def test_single_light_theme(app):
    """只保留 bootstrap-light 浅色主题：调色板固定、无深色切换入口。"""
    from logobuilder.ui import theme as theme_module

    assert app.palette.kind == "bootstrap-light"
    assert app.palette.accent == "#0a58ca"
    assert app.palette.window_bg == "#ffffff"
    assert app.palette.surface == "#ffffff"
    assert app.palette.text == "#212529"
    assert app.palette.border == "#d6d6d6"
    # 无深色入口/深色实现
    assert not hasattr(app, "toggle_theme")
    assert not hasattr(app.header, "theme_link")
    assert not hasattr(theme_module, "DARK_PALETTE")
    assert not hasattr(app.settings, "theme")
    # ttkbootstrap 当前主题名是 bootstrap-light
    assert app.theme.style.theme_use() == "bootstrap-light"


def test_no_language_entry(app):
    """中文单语：不存在英文切换入口、i18n 仅有 zh_CN。"""
    from logobuilder.i18n import LANGUAGES, available_languages

    assert app.tr.language == "zh_CN"
    assert available_languages() == {"zh_CN": "简体中文"}
    assert set(LANGUAGES) == {"zh_CN"}
    assert not hasattr(app, "toggle_language")
    assert not hasattr(app.header, "lang_link")
    assert not hasattr(app.settings, "language")
    assert app.toolbar.button_by_action("open_image_dialog").cget("text") == "打开镜像"


def test_log_panel_toggle_and_append(app):
    panel = app.log_panel
    assert panel.visible is False
    app.toggle_log()
    app.root.update_idletasks()
    assert panel.visible is True
    assert panel.body.winfo_ismapped()

    app.log("INFO", "测试日志")
    app.log("ERROR", "测试错误")
    assert any("测试日志" in record[2] for record in panel.records)
    assert any(level == "ERROR" for _stamp, level, _msg in panel.records)
    content = panel.text_content()
    assert "测试日志" in content and "测试错误" in content

    panel.clear()
    app.root.update_idletasks()
    # clear() 会写入一条 "已清空" 的 INFO 记录
    assert len(panel.records) == 1
    assert panel.records[0][1] == "INFO"


def test_block_list_empty_state(app):
    assert app.block_list.cards == {}
    assert app.block_list.empty_frame.winfo_ismapped()
    assert app.block_list.empty_title.cget("text")


def test_menu_has_entries(app):
    menubar = app.menubar
    assert menubar.index("end") and menubar.index("end") >= 4


def test_header_hugs_content(app):
    """顶栏高度必须能容纳品牌区两行文字（修复副标题被裁切）。

    RDP 小屏（逻辑宽度 < 900）下窗口无法真正变宽，HeaderBar 会进入 compact 模式隐藏副标题，
    这属于预期行为；此用例在大窗口下断言「不裁切」，小屏环境 skip。
    """
    tk_root = app.root
    # 用 overrideredirect 绕过窗口管理器对小屏的尺寸钳制，模拟 1240x820 的设计窗口
    try:
        tk_root.overrideredirect(True)
    except Exception:
        pass
    try:
        tk_root.geometry("1240x820+0+0")
    except Exception:
        pass
    for _ in range(20):
        tk_root.update_idletasks()
        tk_root.update()
        app.header.refresh_compact()
        if app.header.winfo_width() >= app.metrics.px(980) or app.header.winfo_width() <= 0:
            break
    header = app.header
    if header.winfo_width() < app.metrics.px(980):
        pytest.skip(f"小屏/虚拟缩放环境（窗口宽 {header.winfo_width()}px < 阈值 {app.metrics.px(980)}px），compact 模式隐藏副标题属预期行为")
    assert header.winfo_height() >= header.brand.winfo_reqheight()
    assert header.subtitle_label.winfo_ismapped()
    assert header.subtitle_label.winfo_manager() == "pack"


def test_status_bar_visible(app):
    app.root.update_idletasks()
    assert app.status_bar.winfo_ismapped()
    assert app.status_bar.winfo_height() >= app.status_bar.winfo_reqheight() - 1
