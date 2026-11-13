"""GUI 测试的公共配置：加入 ``src`` 路径、提供 Tk root 与主窗口 fixtures。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _display_available() -> bool:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.update()
        root.destroy()
        return True
    except Exception:
        return False


HAS_DISPLAY = _display_available()


def pytest_report_header(config: pytest.Config) -> str:
    return f"logobuilder UI tests: display={'yes' if HAS_DISPLAY else 'no'}"


@pytest.fixture()
def tk_root():
    """每个用例一个干净 root（无显示环境自动 skip）。"""
    if not HAS_DISPLAY:
        pytest.skip("当前环境没有可用的图形显示，跳过 GUI 测试")
    import warnings

    import ttkbootstrap as tb

    try:
        root = tb.Window(themename="bootstrap-light")
    except Exception:  # 极老版本 ttkbootstrap 没有 bootstrap-light 时的兜底
        root = tb.Window(themename="cosmo")
    root.maxsize(5000, 3600)
    root.geometry("1240x820+0+0")
    root.update()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


@pytest.fixture()
def app(tk_root, tmp_path):
    """已实例化的主窗口（test_mode=True：不弹阻塞对话框）。"""
    from logobuilder.ui.app import LogoBuilderApp
    from logobuilder.ui.settings import UiSettings

    settings = UiSettings()
    application = LogoBuilderApp(
        tk_root,
        settings=settings,
        config_path=tmp_path / "ui_settings.json",
        test_mode=True,
        repo_root=ROOT,
    )
    # 测试环境固定窗口尺寸（本机屏幕很小，app 初始化时可能自动 zoom）
    try:
        tk_root.state("normal")
    except Exception:
        pass
    try:
        tk_root.attributes("-zoomed", False)
    except Exception:
        pass
    tk_root.maxsize(6000, 4200)
    tk_root.geometry("1240x820+0+0")
    tk_root.update()
    yield application
    try:
        if tk_root.winfo_exists():
            tk_root.destroy()
    except Exception:
        pass


@pytest.fixture()
def core():
    """真实核心库；缺失时 skip（UI 本身不依赖 core 即可启动）。"""
    pytest.importorskip("logobuilder.core")
    import logobuilder.core as core_module

    return core_module
