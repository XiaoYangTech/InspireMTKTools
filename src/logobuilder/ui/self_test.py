"""``python app.py --self-test`` 的实现。

不打开对话框（``test_mode=True``），用生成的容器走完真实代码路径：
打开镜像 -> 选块 -> 替换图片 -> 恢复原图 -> 导出 img/bin/update.zip/boot_logo
-> 新建项目 -> 替换 + 保存 -> 重新打开项目 -> 未知尺寸识别 -> 重新打包校验。
成功打印 ``SELF-TEST OK`` 并返回 0。
"""

from __future__ import annotations

import tempfile
import traceback
import zipfile
from pathlib import Path
from typing import Any

from logobuilder.ui import core_bridge, session as session_mod


class SelfTestFailure(AssertionError):
    """自检断言失败。"""


def _check(condition: Any, message: str) -> None:
    if not condition:
        raise SelfTestFailure(message)


def _print_step(name: str) -> None:
    print(f"  [OK] {name}")


def run_self_test(app: Any) -> int:
    """执行自检；返回进程退出码（0 = 成功）。"""
    print("=" * 72)
    print("LogoBuilder v2.0 自检（--self-test）")
    print("=" * 72)
    core = core_bridge.get_core()
    print(f"核心库：{getattr(core, '__name__', core)}")
    steps = 0
    try:
        from . import demo

        with tempfile.TemporaryDirectory(prefix="logobuilder-selftest-") as tmp_name:
            tmp = Path(tmp_name)

            # 1. 生成小容器并落盘
            container = demo.build_demo_container(core)
            raw = container.to_bytes()
            _check(len(raw) > 512, "生成的容器太小")
            source = tmp / "logo.bin"
            source.write_bytes(raw)
            _print_step(f"生成测试容器（{len(container.blocks)} 块，{len(raw):,} 字节）")
            steps += 1

            # 2. 打开镜像（真实解析路径）
            session = app.load_container_sync(source)
            _check(app.session is not None, "会话未建立")
            _check(session.count == len(container.blocks), f"块数不符：{session.count}")
            _check(session.tag == "LOGO", f"tag 不符：{session.tag}")
            _check(app.block_list is not None and app.preview is not None, "界面组件缺失")
            _print_step(f"打开镜像：{session.count} 块，tag={session.tag}")
            steps += 1

            # 3. 替换图片
            image = demo.build_replace_image()
            _check(app.replace_image_direct(0, image, fit="contain"), "替换图片失败")
            view = app.session.block(0)
            _check(view is not None and view.modified, "替换后块状态未标记 modified")
            _check(app.session.can_restore(0), "替换后应可恢复原图")
            _print_step("替换第 1 块图片（contain 缩放）")
            steps += 1

            # 4. 恢复原图
            app.restore_image(0)
            _check(not app.session.block(0).modified, "恢复后仍标记为已修改")
            app.replace_image_direct(0, image, fit="stretch")
            _print_step("恢复原始图片并再次替换")
            steps += 1

            # 5. 导出 logo.img（带 0xFF 分区填充）
            out_dir = tmp / "out"
            intended = len(app.session.to_bytes()) + 4096
            img_path = out_dir / "logo.img"
            result = app.export_bytes_sync(img_path, pad_target=intended, fill_byte=0xFF)
            _check(img_path.is_file(), "logo.img 未生成")
            _check(result["size"] == intended, f"填充后大小不符：{result['size']} != {intended}")
            data = img_path.read_bytes()
            _check(data[-1] == 0xFF, "填充字节不是 0xFF")
            _check(data[:4] == (0x58881688).to_bytes(4, "little"), "magic 不符")
            _print_step(f"导出 logo.img（填充到 {intended:,} 字节，0xFF）")
            steps += 1

            # 6. 重新解析导出的 logo.img，确保可回读
            reparsed = core.LogoContainer.load(img_path, db=session_mod.get_size_db())
            _check(reparsed.count == session.count, "回读块数不符")
            _print_step("回读导出的 logo.img 校验通过")
            steps += 1

            # 7. 打包 logo.bin
            bin_path = out_dir / "logo.bin"
            app.export_bytes_sync(bin_path)
            _check(bin_path.is_file() and bin_path.stat().st_size == len(app.session.to_bytes()), "logo.bin 大小不符")
            _print_step("打包 logo.bin")
            steps += 1

            # 8. 生成卡刷包 update.zip
            zip_path = out_dir / "update.zip"
            zip_result = app.export_zip_sync(zip_path)
            _check(zip_path.is_file(), "update.zip 未生成")
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            _check("logo.bin" in names, f"update.zip 缺少 logo.bin：{names}")
            _check(
                "META-INF/com/google/android/updater-script" in names,
                "update.zip 缺少 updater-script",
            )
            _print_step(f"生成 update.zip（{zip_result.get('size'):,} 字节）")
            steps += 1

            # 9. 第二屏 boot_logo 读写（回读必须传入写入尺寸，否则裸数据可能多解）
            boot_path = out_dir / "boot_logo"
            boot_size = core.write_boot_logo(image, boot_path)
            _check(boot_path.is_file(), "boot_logo 未生成")
            expected = int(getattr(boot_size, "key", boot_path.stat().st_size))
            _check(boot_path.stat().st_size == expected, "boot_logo 大小不符")
            boot_image, read_size = core.read_boot_logo(boot_path, boot_size)
            _check(boot_image.size == image.size, "boot_logo 回读图像尺寸不符")
            _check(
                (read_size.width, read_size.height) == (image.width, image.height),
                f"boot_logo 回读尺寸不符：{read_size}",
            )
            _print_step(f"生成并回读 boot_logo（{session_mod.size_to_text(read_size)}）")
            steps += 1

            # 10. 从镜像创建项目（真实 core 路径）
            project_dir = tmp / "project"
            app.create_project_sync(source, project_dir)
            _check((project_dir / "header.bin").is_file(), "项目缺少 header.bin")
            _check(
                (project_dir / "project.json").is_file() or (project_dir / ".project").is_file(),
                "项目缺少 project.json",
            )
            _check(app.session is not None and app.session.mode == "project", "未切换到项目会话")
            _print_step(f"创建项目目录：{project_dir.name}")
            steps += 1

            # 11. 项目内替换图片 + 保存 + 重新打开
            _check(app.replace_image_direct(1, image, fit="cover"), "项目内替换图片失败")
            app.save_project()
            reopened = app.open_project_sync(project_dir)
            _check(reopened.count == session.count, "重新打开项目后块数不符")
            project_bin = out_dir / "from_project.bin"
            app.export_bytes_sync(project_bin)
            project_parsed = core.LogoContainer.load(project_bin, db=session_mod.get_size_db())
            _check(project_parsed.count == session.count, "项目打包后回读块数不符")
            _print_step("项目内替换图片 / 保存 / 重开 / 打包")
            steps += 1

            # 12. 未知尺寸块：解析 -> 手动指定 -> 打包
            unknown_dir = tmp / "unknown"
            unknown_dir.mkdir()
            unknown_container = demo.build_unknown_container(core)
            unknown_path = unknown_dir / "logo.bin"
            unknown_path.write_bytes(unknown_container.to_bytes())
            unknown_session = app.load_container_sync(unknown_path)
            pending = unknown_session.pending_unknowns()
            _check(pending == [0], f"未知尺寸块识别异常：{pending}")
            size = core.LogoSize(123, 45, 4)
            _check(app.assign_size_sync(0, size), "指定未知块尺寸失败")
            view = app.session.block(0)
            _check(view is not None and view.is_image and view.size is not None, "指定尺寸后未转为图像块")
            unknown_out = unknown_dir / "fixed.bin"
            app.export_bytes_sync(unknown_out)
            fixed = core.LogoContainer.load(unknown_out, db=session_mod.get_size_db())
            _check(fixed.count == 1, "未知尺寸块打包后块数不符")
            _print_step("未知尺寸块：识别 -> 指定 123×45×4 -> 打包回读")
            steps += 1

            # 13. 对话框可实例化（不进入 modal 循环）
            from .dialogs import ErrorDialog, ReplaceImageDialog, ResolutionDialog

            candidates, length, pixels = demo.build_resolution_candidates(core)
            dialog = ResolutionDialog(
                app,
                block_index=4,
                decompressed_len=length,
                candidates=candidates,
                preview_provider=lambda size: core.pixels_to_image(pixels, size),
                same_size_count=1,
            )
            _check(dialog.tree is not None and len(dialog.tree.get_children()) == len(candidates), "分辨率对话框候选列表异常")
            dialog._on_skip()
            _check(dialog.result is not None and dialog.result.skipped, "分辨率对话框跳过结果异常")
            replace_dialog = ReplaceImageDialog(
                app,
                block_index=0,
                target_size=core.LogoSize(540, 960, 4),
                current_image=image,
            )
            replace_dialog.destroy()
            error_dialog = ErrorDialog(app, message="自检用的错误示例", hint="这是一条提示", traceback_text="Traceback (mock)")
            error_dialog.destroy()
            _print_step("对话框实例化 / 候选列表 / 跳过逻辑")
            steps += 1

            # 14. 界面关键组件存在（含第二工作区 LK 修补）
            for attr in ("header", "toolbar", "block_list", "preview", "status_bar", "log_panel", "paned", "notebook", "lk_panel"):
                _check(hasattr(app, attr), f"缺少界面组件：{attr}")
            _check(app.toolbar.button_count() >= 10, "工具栏按钮数量不足")
            _check(app.root.winfo_exists(), "主窗口不存在")
            _check("InspireMTKTools" in app.root.title(), "窗口标题未更名")
            tabs = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
            _check(len(tabs) == 2, f"工作区 Tab 数量异常：{tabs}")
            # 中文单语 + 单一 litera 浅色主题：不存在语言/深色入口
            _check(app.tr.language == "zh_CN", "界面语言不是中文")
            _check(not hasattr(app, "toggle_theme"), "不应存在深色模式切换")
            _check(not hasattr(app, "toggle_language"), "不应存在语言切换")
            _check(not hasattr(app.header, "theme_link"), "顶栏不应有深色切换入口")
            _check(not hasattr(app.header, "lang_link"), "顶栏不应有英文切换入口")
            _check(
                not hasattr(app.settings, "theme") and not hasattr(app.settings, "language"),
                "设置不应含主题/语言字段",
            )
            _check(app.palette.kind == "litera", f"调色板不是 litera：{app.palette.kind}")
            _check(app.palette.accent == "#4582ec", f"litera 主色不符：{app.palette.accent}")
            from logobuilder.ui import theme as theme_module

            _check(not hasattr(theme_module, "DARK_PALETTE"), "不应保留深色调色板")
            _print_step(
                "界面组件检查（顶栏 / 工具栏 / Logo+LK 两工作区 / 状态栏 / 日志 / 品牌更名 / 单一 litera 中文界面）"
            )
            steps += 1

            # 15. LK 修补流程（核心就绪时）
            lk_ready, lk_error = core_bridge.lkpatch_status()
            if lk_ready:
                from . import demo as demo_module

                lk_source = tmp / "lk_selftest.bin"
                lk_original = demo_module.build_synthetic_lk_arm32()
                lk_source.write_bytes(lk_original)
                lk_entries = app.lk_panel.scan_sync([lk_source])
                _check(len(lk_entries) == 1, "LK 扫描结果数量异常")
                lk_entry = lk_entries[0]
                _check(lk_entry.error is None, f"LK 扫描失败：{lk_entry.error}")
                _check(bool(lk_entry.findings), "合成 LK 镜像未识别出候选补丁")
                dry = app.lk_panel.dry_run_sync()
                _check(dry["verify"].get("patch_ok") is True, "LK dry-run 预校验未通过")
                patched_candidate = lk_source.with_name(lk_source.stem + "_patched" + lk_source.suffix)
                _check(not patched_candidate.exists(), "LK dry-run 不应写文件")
                app.lk_panel.output_mode.set("new")
                lk_result = app.lk_panel.apply_sync()
                _check(lk_result.output is not None and lk_result.output.exists(), "LK 补丁输出文件未生成")
                _check(lk_result.backup is not None and Path(lk_result.backup).exists(), "LK 补丁备份未生成")
                patched_bytes = lk_result.output.read_bytes()
                _check(patched_bytes[:0x200] == lk_original[:0x200], "LK 头部 0x200 被修改")
                _check(len(patched_bytes) == len(lk_original), "LK 文件大小改变")
                lk_verify = app.lk_panel.verify_sync()
                _check(lk_verify.get("patch_ok") is True, f"LK 后置校验未通过：{lk_verify}")
                app.lk_panel.restore_sync(Path(lk_result.backup), lk_source)
                _check(lk_source.read_bytes() == lk_original, "LK 备份还原后字节不一致")
                _check(len(app.lk_panel.file_tree.get_children()) == 1, "LK 文件表未显示扫描结果")
                _check(len(app.lk_panel.findings_tree.get_children()) >= 1, "LK 候选补丁表未显示")
                _print_step(
                    f"LK 修补流程（合成 ARM32：{len(lk_entry.findings)} 个候选 → dry-run → 应用 → 校验 → 还原）"
                )
                steps += 1
            else:
                print(f"  [SKIP] LK 修补核心未就绪：{lk_error}（其它 {steps} 项检查继续通过）")

        print("-" * 72)
        print(f"SELF-TEST OK（{steps} 项检查全部通过）")
        return 0
    except SelfTestFailure as exc:
        print(f"SELF-TEST FAILED: {exc}", flush=True)
        traceback.print_exc()
        app.log("ERROR", f"self-test failed: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"SELF-TEST ERROR: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        app.log("ERROR", f"self-test error: {exc}")
        return 1
