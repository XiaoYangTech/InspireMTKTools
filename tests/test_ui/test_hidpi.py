"""HiDPI 布局测试：模拟 100% / 150% 缩放，验证关键控件不被裁切。"""

from __future__ import annotations

import pytest

from logobuilder.ui.theme import build_fonts


def _critical_widgets(app):
    widgets = {
        "header": app.header,
        "header.brand": app.header.brand,
        "header.title": app.header.title_label,
        "header.subtitle": app.header.subtitle_label,
        "header.settings_link": app.header.settings_link,
        "header.help_link": app.header.help_link,
        "toolbar": app.toolbar,
        "blocks.title": app.block_list.title_label,
        "blocks.filter": app.block_list.filter_entry,
        "preview.title": app.preview.title_label,
        "preview.replace": app.preview.replace_button,
        "preview.export": app.preview.export_button,
        "status": app.status_bar,
        "status.label": app.status_bar.status_label,
        "status.log_button": app.status_bar.log_button,
    }
    for action, button in app.toolbar.buttons.items():
        widgets[f"toolbar.{action}"] = button
    return widgets


def _clipped_widgets(app) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    for name, widget in _critical_widgets(app).items():
        try:
            if not widget.winfo_ismapped():
                problems.append((name, "未显示"))
                continue
            if widget.winfo_width() + 1 < widget.winfo_reqwidth():
                problems.append((name, f"宽 {widget.winfo_width()} < 需要 {widget.winfo_reqwidth()}"))
            if widget.winfo_height() + 1 < widget.winfo_reqheight():
                problems.append((name, f"高 {widget.winfo_height()} < 需要 {widget.winfo_reqheight()}"))
        except Exception as exc:  # pragma: no cover - 防御
            problems.append((name, f"检查失败：{exc}"))
    return problems


def _normalize_root(app) -> None:
    root = app.root
    try:
        root.state("normal")
    except Exception:
        pass
    try:
        root.attributes("-zoomed", False)
    except Exception:
        pass
    root.maxsize(6000, 4200)


@pytest.mark.parametrize("scale", [1.0, 1.5])
def test_no_clipped_widgets(app, scale):
    root = app.root
    app.theme.apply_scale(scale, fonts=build_fonts(root, app.fonts))
    app._sync_theme_attrs()
    app._rebuild_ui()
    _normalize_root(app)
    root.update_idletasks()

    width = max(root.winfo_reqwidth(), app.metrics.window_default_width)
    height = max(root.winfo_reqheight(), app.metrics.window_default_height)
    root.geometry(f"{width}x{height}")
    root.update_idletasks()
    root.update()

    problems = _clipped_widgets(app)
    assert not problems, f"缩放 {scale:.2f} 时存在被裁切控件：{problems}"


def test_metrics_and_scaling(app):
    app.theme.apply_scale(1.5, fonts=build_fonts(app.root, app.fonts))
    app._sync_theme_attrs()
    metrics = app.metrics
    assert metrics.scale == pytest.approx(1.5)
    # 关键尺寸随缩放放大
    assert metrics.pad > 14
    assert metrics.window_default_width > 1240
    # tk scaling 与 DPI 一致：scale*96/72（Tk 在 Windows 上会有极小的取整误差）
    assert float(app.root.tk.call("tk", "scaling")) == pytest.approx(2.0, rel=0.02)

    app.theme.apply_scale(1.0, fonts=build_fonts(app.root, app.fonts))
    app._sync_theme_attrs()
    assert app.metrics.scale == pytest.approx(1.0)
    assert float(app.root.tk.call("tk", "scaling")) == pytest.approx(96.0 / 72.0, rel=0.02)


def test_header_not_clipped_at_150(app):
    root = app.root
    app.theme.apply_scale(1.5, fonts=build_fonts(root, app.fonts))
    app._sync_theme_attrs()
    app._rebuild_ui()
    _normalize_root(app)
    root.geometry(f"{app.metrics.window_default_width}x{app.metrics.window_default_height}")
    root.update_idletasks()
    root.update()
    assert app.header.winfo_height() >= app.header.brand.winfo_reqheight()
