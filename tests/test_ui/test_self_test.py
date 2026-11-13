"""自检模式与截图基础设施测试。"""

from __future__ import annotations

import sys

import pytest
from PIL import Image


def test_self_test_process(app):
    from logobuilder.ui.self_test import run_self_test

    assert run_self_test(app) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="截图拼接逻辑针对 Windows 实现")
def test_capture_small_window(tk_root, tmp_path):
    from logobuilder.ui.screenshot import capture_window, save_window_screenshot

    top = __import__("tkinter").Toplevel(tk_root)
    top.geometry("320x200+0+0")
    top.configure(bg="#123456")
    tk_root.update_idletasks()
    tk_root.update()
    image = capture_window(top, include_titlebar=False)
    assert image.size == (320, 200)
    assert image.mode == "RGB"
    target = save_window_screenshot(top, tmp_path / "shot.png", include_titlebar=False)
    assert target.is_file()
    with Image.open(target) as loaded:
        assert loaded.size == (320, 200)
    top.destroy()


def test_screenshot_cli_smoke(core):
    """--screenshot 由 subprocess 覆盖代价太高，这里验证入口函数可导入且参数解析正常。"""
    from logobuilder.ui.main import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["--screenshot", "out.png"])
    assert args.screenshot == "out.png"
    args2 = parser.parse_args(["--self-test"])
    assert args2.self_test is True
    args3 = parser.parse_args([])
    assert args3.screenshot is None and args3.self_test is False
