"""``python app.py --screenshot out.png`` 的实现。

生成演示数据并截取（共 12 张，全部 bootstrap-light 浅色）：
* 设置 / 分辨率 / 替换图片 / 错误 / 帮助对话框
* 150% 缩放主窗口
* LK 修补面板、补丁预览/校验/刷写说明对话框
* **最后**再截主窗口 → ``out.png`` 以及 ``analysis/ui_screenshots/main_window.png``
  （保证 README 主图里没有任何对话框/其它窗口遮挡）

Windows 下窗口比屏幕大时会自动分块截图再拼接（见 :mod:`logobuilder.ui.screenshot`）。
"""

from __future__ import annotations

import shutil
import tempfile
import traceback
from pathlib import Path
from typing import Any

from PIL import Image

from logobuilder.ui import core_bridge
from logobuilder.ui.dialogs import (
    ErrorDialog,
    HelpDialog,
    ReplaceImageDialog,
    ResolutionDialog,
    SettingsDialog,
)
from logobuilder.ui.lkpatch_panel import LkFlashGuideDialog, LkPreviewDialog, LkVerifyDialog
from logobuilder.ui.screenshot import save_window_screenshot, wait_ui
from logobuilder.ui.theme import build_fonts


def _prepare_root(app: Any, width: int, height: int) -> None:
    root = app.root
    try:
        root.deiconify()
    except Exception:
        pass
    try:
        if root.state() != "normal":
            root.state("normal")
    except Exception:
        pass
    try:
        root.attributes("-zoomed", False)
    except Exception:
        pass
    try:
        root.maxsize(6000, 4200)
    except Exception:
        pass
    try:
        root.geometry(f"{int(width)}x{int(height)}+0+0")
    except Exception:
        pass
    wait_ui(root, 320)


def _prepare_dialog(app: Any, dialog: Any, width: int, height: int) -> None:
    try:
        dialog.deiconify()
        dialog.maxsize(6000, 4200)
        dialog.geometry(f"{int(width)}x{int(height)}+0+0")
    except Exception:
        pass
    wait_ui(app.root, 260)


def _prepare_target_window(app: Any, target: Any) -> None:
    """把待截窗口置前，并临时隐藏本进程其它 Toplevel，防止对话框遮挡截图。"""
    try:
        toplevels = [child for child in app.root.winfo_children() if child.winfo_class() == "Toplevel"]
    except Exception:
        toplevels = []
    for child in toplevels:
        if child is target:
            continue
        try:
            child.withdraw()
        except Exception:
            pass
    try:
        target.deiconify()
    except Exception:
        pass
    try:
        target.lift()
        target.focus_force()
        target.attributes("-topmost", True)
    except Exception:
        pass
    wait_ui(app.root, 180)


def _release_target_window(target: Any) -> None:
    try:
        target.attributes("-topmost", False)
    except Exception:
        pass


def _capture(app: Any, target: Any, path: Path, *, label: str, results: list[Path]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _prepare_target_window(app, target)
        save_window_screenshot(target, path, include_titlebar=False)
        _release_target_window(target)
        print(f"  [screenshot] {label}: {path}")
        results.append(path)
        return True
    except Exception as exc:  # noqa: BLE001 - 截图失败不终止其它截图
        _release_target_window(target)
        print(f"  [warn] 截图失败（{label}）：{exc}")
        return False


def run_screenshot(app: Any, out_path: str | Path, repo_root: str | Path | None = None) -> int:
    """执行截图流程；返回进程退出码。"""
    core = core_bridge.get_core()
    repo_root = Path(repo_root) if repo_root is not None else Path.cwd()
    shots_dir = repo_root / "analysis" / "ui_screenshots"
    results: list[Path] = []
    from . import demo

    print("=" * 72)
    print("LogoBuilder v2.0 截图（--screenshot）")
    print("=" * 72)
    try:
        app.test_mode = True
        with tempfile.TemporaryDirectory(prefix="logobuilder-shots-") as tmp_name:
            tmp = Path(tmp_name)
            container = demo.build_demo_container(core)
            source = tmp / "logo.bin"
            source.write_bytes(container.to_bytes())
            app.load_container_sync(source)
            if app.session is not None and app.session.count:
                app.on_block_selected(0)
                app.preview.set_zoom("fit")
            wait_ui(app.root, 260)

            metrics = app.metrics
            width = metrics.window_default_width
            height = metrics.window_default_height

            # 1) 设置对话框（单一 bootstrap-light 主题，无深色模式）
            settings_dialog = SettingsDialog(app)
            _prepare_dialog(app, settings_dialog, metrics.px(620), metrics.px(432))
            _capture(app, settings_dialog, shots_dir / "settings_dialog.png", label="设置对话框", results=results)
            settings_dialog.destroy()

            # 3) 分辨率选择对话框
            candidates, length, pixels = demo.build_resolution_candidates(core)
            size_dialog = ResolutionDialog(
                app,
                block_index=7,
                decompressed_len=length,
                candidates=candidates,
                preview_provider=lambda size: core.pixels_to_image(pixels, size),
                same_size_count=3,
            )
            size_dialog.show()
            _prepare_dialog(app, size_dialog, metrics.px(880), metrics.px(560))
            _capture(app, size_dialog, shots_dir / "resolution_dialog.png", label="分辨率选择对话框", results=results)
            size_dialog.destroy()

            # 4) 替换图片对话框
            replace_source = tmp / "replacement.png"
            demo.build_replace_image().save(replace_source)
            target_view = app.session.block(0) if app.session is not None else None
            replace_dialog = ReplaceImageDialog(
                app,
                block_index=0,
                target_size=target_view.size if target_view is not None else core.LogoSize(540, 960, 4),
                current_image=app.session.image(0) if app.session is not None else None,
            )
            replace_dialog.show()
            replace_dialog.load_file(replace_source)
            _prepare_dialog(app, replace_dialog, metrics.px(820), metrics.px(520))
            _capture(app, replace_dialog, shots_dir / "replace_image_dialog.png", label="替换图片对话框", results=results)
            replace_dialog.destroy()

            # 5) 错误详情对话框（B8）
            error_dialog = ErrorDialog(
                app,
                message="不是有效的 MediaTek LOGO 镜像：magic 校验失败（期望 0x58881688）",
                hint="请确认选择的是官方线刷包里的 logo.bin / logo.img。",
                traceback_text=(
                    "Traceback (most recent call last):\n"
                    '  File "src/logobuilder/core/container.py", line 118, in parse\n'
                    "    raise NotLogoFile(message)\n"
                    "logobuilder.core.errors.NotLogoFile: 不是 MTK LOGO 镜"
                    "像（magic 不符）\n\n"
                    "context: file=D:/firmware/logo.bin size=1,048,576\n"
                    "hint: 请使用官方线刷包中的 logo.bin"
                ),
                context="文件：D:/firmware/logo.bin",
            )
            error_dialog.show()
            _prepare_dialog(app, error_dialog, metrics.px(720), metrics.px(500))
            _capture(app, error_dialog, shots_dir / "error_dialog.png", label="错误详情对话框", results=results)
            error_dialog.destroy()

            # 6) 帮助对话框
            help_dialog = HelpDialog(app)
            _prepare_dialog(app, help_dialog, metrics.px(660), metrics.px(540))
            _capture(app, help_dialog, shots_dir / "help_dialog.png", label="帮助对话框", results=results)
            help_dialog.destroy()

            # 7) 150% 缩放
            app.theme.apply_scale(1.5, fonts=build_fonts(app.root, app.fonts))
            app._sync_theme_attrs()
            app._rebuild_ui()
            scaled = app.metrics
            _prepare_root(app, scaled.window_default_width, scaled.window_default_height)
            _capture(app, app.root, shots_dir / "main_window_150.png", label="主窗口（150% 缩放）", results=results)

            # 恢复 100% 缩放
            app.theme.apply_scale(1.0, fonts=build_fonts(app.root, app.fonts))
            app._sync_theme_attrs()
            app._rebuild_ui()
            base = app.metrics
            _prepare_root(app, base.window_default_width, base.window_default_height)

            # 8) LK 修补工作区（真实样本优先：analysis/lk/samples/lk_a.bin / lk_b.bin）
            _capture_lk_screens(app, tmp, shots_dir, results)

            # 9) 收尾：所有对话框关闭后回到 Logo 工作区再截主窗口，保证 README 主图干净
            app.show_logo_panel()
            try:
                app.status_bar.set_busy(False)
                app.set_status("", "normal")
            except Exception:
                pass
            _prepare_root(app, base.window_default_width, base.window_default_height)
            main_target = Path(out_path)
            _capture(app, app.root, main_target, label="主窗口（100%）", results=results)
            _capture(app, app.root, shots_dir / "main_window.png", label="主窗口（备份）", results=results)

        print("-" * 72)
        if not results:
            print("SCREENSHOT FAILED：没有生成任何截图")
            return 1
        print(f"SCREENSHOT OK（{len(results)} 张）")
        for path in results:
            print(f"  - {path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"SCREENSHOT ERROR: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 1

# ── LK 修补截图 ────────────────────────────────────────────
def _capture_lk_screens(app: Any, tmp: Path, shots_dir: Path, results: list[Path]) -> None:
    """扫描真实 LK 样本（或合成镜像）并截取 LK 面板/预览/校验/刷写说明。"""
    from logobuilder.ui import core_bridge
    from logobuilder.ui import demo as demo_module

    lk_ready, lk_error = core_bridge.lkpatch_status()
    app.show_lk_panel()
    wait_ui(app.root, 200)
    panel = app.lk_panel
    if not lk_ready:
        print(f"  [warn] LK 修补核心未就绪，跳过 LK 截图：{lk_error}")
        return

    # 真实样本只读复制到临时目录，避免改动 analysis/**
    source_dir = app.repo_root / "analysis" / "lk" / "samples"
    copies: list[Path] = []
    if source_dir.is_dir():
        for name in ("lk_a.bin", "lk_b.bin"):
            sample = source_dir / name
            if sample.is_file():
                target = tmp / name
                try:
                    shutil.copy2(sample, target)
                    copies.append(target)
                except OSError:
                    pass
    if not copies:
        synthetic = tmp / "lk_synthetic.bin"
        synthetic.write_bytes(demo_module.build_synthetic_lk_arm32())
        copies = [synthetic]
    try:
        entries = panel.scan_sync(copies)
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] LK 扫描失败，跳过 LK 截图：{exc}")
        return
    if not entries:
        print("  [warn] LK 扫描没有结果，跳过 LK 截图")
        return
    # 真实样本若当前核心库版本未识别出候选（核心并行开发中），补一个合成镜像保证预览/应用截图有内容
    if not any(entry.findings for entry in entries if entry.error is None):
        synthetic = tmp / "lk_synthetic.bin"
        synthetic.write_bytes(demo_module.build_synthetic_lk_arm32())
        synthetic_entries = panel.scan_sync([synthetic])
        entries = list(entries) + list(synthetic_entries)
    good = next((entry for entry in entries if entry.error is None and entry.findings), None)
    if good is None:
        good = next((entry for entry in entries if entry.error is None), entries[0])
    panel.select_path(good.key)
    app.root.update_idletasks()
    app.root.update()
    metrics = app.metrics
    _prepare_root(app, metrics.window_default_width, metrics.window_default_height)
    _capture(app, app.root, shots_dir / "lk_patch_scan.png", label="LK 修补面板（扫描结果）", results=results)

    # 预览补丁对话框（dry-run，不写文件）
    try:
        categories = [category for category in ("orange_state", "red_state", "dm_verity") if category in {
            str(getattr(finding, "category", "")) for finding in good.findings
        }] or ["orange_state", "red_state", "dm_verity"]
        dry = panel.dry_run_sync() if panel.selected_entry() is good else None
        if dry is None:
            from logobuilder.ui.lkpatch_panel import dry_run_entry

            dry = dry_run_entry(good, categories, False, core_bridge.get_lkpatch())
        preview_dialog = LkPreviewDialog(app, panel=panel, entry=good, result=dry)
        _prepare_dialog(app, preview_dialog, metrics.px(980), metrics.px(640))
        _capture(app, preview_dialog, shots_dir / "lk_patch_preview.png", label="LK 补丁预览对话框", results=results)
        preview_dialog.destroy()
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] LK 预览截图失败：{exc}")

    # 应用补丁（临时副本）→ 校验对话框
    try:
        panel.output_mode.set("new")
        apply_result = panel.apply_sync()
        verify_dialog = LkVerifyDialog(
            app,
            entry=good,
            verify=apply_result.verify,
            backup=apply_result.backup,
        )
        _prepare_dialog(app, verify_dialog, metrics.px(760), metrics.px(560))
        _capture(app, verify_dialog, shots_dir / "lk_patch_verify.png", label="LK 补丁校验对话框", results=results)
        verify_dialog.destroy()
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] LK 校验截图失败：{exc}")

    # 刷写说明（A/B 双槽）
    try:
        guide = LkFlashGuideDialog(app, entries=[entry for entry in entries if entry.error is None])
        _prepare_dialog(app, guide, metrics.px(820), metrics.px(700))
        _capture(app, guide, shots_dir / "lk_patch_flash_guide.png", label="LK 刷写说明对话框", results=results)
        guide.destroy()
    except Exception as exc:  # noqa: BLE001
        print(f"  [warn] LK 刷写说明截图失败：{exc}")
    app.show_logo_panel()
    wait_ui(app.root, 120)

