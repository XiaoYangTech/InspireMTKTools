#!/usr/bin/env python3
"""亿方联发科工具箱 InspireMTKTools v3.0.0 —— MediaTek 开机 Logo / LK 修补工具（GUI 入口）。

用法：
    python app.py                 # 启动图形界面
    python app.py --self-test     # 自检，成功退出码 0
    python app.py --screenshot out.png   # 保存界面截图后退出

依赖：Python 3.10+、ttkbootstrap、Pillow（核心库还要求其自身依赖）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _configure_console_encoding() -> None:
    """Windows GBK 控制台下把标准输出切到 UTF-8，避免中文乱码/UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_console_encoding()
    from logobuilder.ui.main import main as ui_main

    return ui_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
