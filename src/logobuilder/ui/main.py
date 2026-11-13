"""命令行入口：``python app.py [--self-test] [--screenshot out.png]``。

负责：
1. 在创建 Tk root **之前** 开启 Windows DPI 感知；
2. 创建 ttkbootstrap 根窗口（固定 bootstrap-light 浅色主题、中文单语界面）；
3. 按参数进入自检 / 截图 / 正常 GUI 模式；
4. 对无显示环境给出可读错误（退出码 2）。
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Sequence

from logobuilder.ui import core_bridge
from logobuilder.ui.settings import UiSettings
from logobuilder.ui.theme import LIGHT_THEME, enable_high_dpi


def _repo_root() -> Path:
    # .../src/logobuilder/ui/main.py -> repo root
    return Path(__file__).resolve().parents[3]


def _create_root() -> Any:
    import warnings

    import ttkbootstrap as tb

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return tb.Window(themename=LIGHT_THEME)
    except Exception:
        return tb.Window()


def _ensure_core_for_selftest(repo_root: Path) -> str | None:
    """自检模式下若核心库缺失，允许加载 tests 目录下的测试桩（仅自检，不进 src）。"""
    core = core_bridge.load_core()
    if core is not None:
        return None
    stub_dir = repo_root / "tests" / "test_ui" / "stubs"
    if not stub_dir.is_dir():
        return None
    stub_path = str(stub_dir)
    if stub_path not in sys.path:
        sys.path.insert(0, stub_path)
    try:
        import fake_core  # type: ignore[import-not-found]

        core_bridge.install_core(fake_core)
        print("[self-test] 核心库暂不可用，使用测试桩完成自检（真实核心库就绪后会自动使用真实实现）")
        return "stub"
    except Exception:
        return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="InspireMTKTools",
        description="亿方联发科工具箱 InspireMTKTools v3.0.0 — MediaTek 开机 Logo / LK 修补工具（GUI）",
    )
    parser.add_argument("--self-test", action="store_true", help="运行自检（无界面交互），成功退出码 0")
    parser.add_argument("--screenshot", metavar="OUT.png", default=None, help="截图模式：保存主窗口截图后退出")
    parser.add_argument(
        "--smoke",
        type=float,
        nargs="?",
        const=2.0,
        default=None,
        metavar="SECONDS",
        help="开发用：启动 GUI 后自动关闭（验证启动是否正常）",
    )
    parser.add_argument("--verbose", action="store_true", help="打印调试信息")
    return parser


def _configure_console_encoding() -> None:
    """Windows GBK 控制台下把标准输出切到 UTF-8，避免中文乱码/UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main(argv: Sequence[str] | None = None) -> int:
    _configure_console_encoding()
    parser = _build_parser()
    args, unknown = parser.parse_known_args(list(argv) if argv is not None else None)
    if unknown and args.verbose:
        print(f"[warn] 忽略未知参数：{unknown}", file=sys.stderr)

    repo_root = _repo_root()
    # 关键：DPI 感知必须在创建 Tk root 之前
    dpi_mode = enable_high_dpi()

    try:
        root = _create_root()
    except Exception as exc:  # pragma: no cover - 依赖显示环境
        print(
            "无法创建图形界面（可能没有可用的显示环境）："
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2

    from logobuilder.ui.app import LogoBuilderApp

    app: LogoBuilderApp | None = None
    exit_code = 0
    try:
        if args.self_test:
            _ensure_core_for_selftest(repo_root)
            settings = UiSettings()
            config_path = Path(tempfile.mkdtemp(prefix="logobuilder-selftest-cfg-")) / "ui_settings.json"
            app = LogoBuilderApp(
                root,
                settings=settings,
                config_path=config_path,
                test_mode=True,
                repo_root=repo_root,
            )
            from logobuilder.ui.self_test import run_self_test

            exit_code = run_self_test(app)
            return exit_code

        if args.screenshot:
            settings = UiSettings()
            config_path = Path(tempfile.mkdtemp(prefix="logobuilder-shot-cfg-")) / "ui_settings.json"
            app = LogoBuilderApp(
                root,
                settings=settings,
                config_path=config_path,
                test_mode=True,
                repo_root=repo_root,
            )
            from logobuilder.ui.screenshot_mode import run_screenshot

            exit_code = run_screenshot(app, args.screenshot, repo_root)
            return exit_code

        # ── 正常 GUI 模式 ──
        settings = UiSettings.load()
        app = LogoBuilderApp(root, settings=settings, repo_root=repo_root)
        if args.smoke is not None:
            close_after = max(0.2, float(args.smoke))
            print(f"[smoke] GUI 已启动（DPI 感知模式：{dpi_mode}），{close_after:.1f} 秒后自动关闭")
            root.after(int(close_after * 1000), app._on_close)
        root.mainloop()
        return 0
    except Exception as exc:  # noqa: BLE001 - 顶层兜底，保证不裸崩溃
        print(f"启动失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        try:
            if root is not None and root.winfo_exists():
                root.destroy()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
