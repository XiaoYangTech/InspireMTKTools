"""主窗口（LogoBuilderApp）：布局、菜单、工具栏、以及全部用户操作流程。

界面只通过 :mod:`logobuilder.ui.session` 使用核心库；耗时操作统一走
:class:`logobuilder.ui.workers.TaskRunner`（后台线程 + 进度 + 取消 + 可读错误）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable

import ttkbootstrap as tb
from PIL import Image

from logobuilder.i18n import Translator
from logobuilder.ui import core_bridge, session as session_mod
from logobuilder.ui.blocklist import BlockListPanel
from logobuilder.ui.dialogs import (
    AboutDialog,
    BootLogoDialog,
    ErrorDialog,
    ExportImgChoice,
    ExportImgDialog,
    HelpDialog,
    ImagePreviewDialog,
    ReplaceImageDialog,
    ResolutionDialog,
    SettingsDialog,
    SuccessDialog,
)
from logobuilder.ui.dnd import dnd_available, enable_dnd
from logobuilder.ui.imaging import platform_reveal_command
from logobuilder.ui.lkpatch_panel import LkPatchPanel
from logobuilder.ui.preview import PreviewPanel
from logobuilder.ui.settings import UiSettings, config_dir
from logobuilder.ui.theme import (
    Metrics,
    Palette,
    ThemeManager,
    UiFonts,
    build_fonts,
)
from logobuilder.ui.toolbar import Toolbar
from logobuilder.ui.widgets import HeaderBar, LogPanel, StatusBar
from logobuilder.ui.workers import TaskContext, TaskRunner

APP_NAME = "InspireMTKTools"
TOOLKIT_VERSION = "3.0.0"
LOG_LEVELS = ("INFO", "WARNING", "ERROR")


class LogoBuilderApp:
    """应用程序主对象；``root`` 由 :func:`logobuilder.ui.main.main` 创建。"""

    def __init__(
        self,
        root: Any,
        *,
        settings: UiSettings | None = None,
        config_path: Path | None = None,
        test_mode: bool = False,
        repo_root: Path | None = None,
    ) -> None:
        self.root = root
        self.test_mode = bool(test_mode)
        self.repo_root = Path(repo_root) if repo_root is not None else Path.cwd()
        self.config_dir = config_dir()
        self.config_path = Path(config_path) if config_path is not None else self.config_dir / "ui_settings.json"
        self.settings = settings if settings is not None else UiSettings.load(self.config_path)
        # 中文单语界面 + 单一 bootstrap-light 浅色主题（无语言/主题切换）
        self.tr = Translator()
        self.toolkit_version = TOOLKIT_VERSION

        # 主题固定 bootstrap-light / 缩放 / 字体（DPI 感知已在创建 root 之前开启）
        self.theme = ThemeManager(root, style=getattr(root, "style", None))
        self.theme.refresh_from_root()
        self._sync_theme_attrs()

        self.core_ready, self.core_error = core_bridge.core_status()
        self.core_version = core_bridge.core_version() if self.core_ready else "—"

        self.session: session_mod.UiSession | None = None
        self.selected_index: int = -1
        self.busy: bool = False
        self.last_error_info: dict[str, Any] | None = None
        self.drop_enabled: bool = False
        self._closing = False
        self._sash_width = 0
        self.runner = TaskRunner(root)
        self._shortcut_bindings: list[tuple[str, str]] = []

        self._configure_root()
        self._build_ui()
        self._build_menubar()
        self._bind_shortcuts()
        self._setup_dnd()
        self._startup_log()

    # ── 基础属性 ─────────────────────────────────────────
    def _sync_theme_attrs(self) -> None:
        self.metrics: Metrics = self.theme.metrics
        self.palette: Palette = self.theme.palette
        self.fonts: UiFonts = self.theme.fonts

    @property
    def interactive(self) -> bool:
        """真实交互模式（test_mode 下不弹阻塞对话框）。"""
        return not self.test_mode

    # ── 根窗口 ───────────────────────────────────────────
    def _configure_root(self) -> None:
        root = self.root
        try:
            root.title(self.tr.t("app.window_title_untitled"))
        except Exception:
            pass
        try:
            root.option_add("*tearOff", False)
        except Exception:
            pass
        try:
            root.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass
        try:
            root.configure(background=self.palette.window_bg)
        except Exception:
            pass
        try:
            root.maxsize(5000, 3600)
        except Exception:
            pass
        self._apply_initial_geometry()

    def _apply_initial_geometry(self) -> None:
        m = self.metrics
        root = self.root
        try:
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
        except Exception:
            screen_w, screen_h = 1280, 800
        min_w = min(m.window_min_width, max(400, screen_w - 16))
        min_h = min(m.window_min_height, max(300, screen_h - 16))
        try:
            root.minsize(min_w, min_h)
        except Exception:
            pass
        geometry = self._restored_geometry(screen_w, screen_h)
        if geometry is None:
            if screen_w >= m.window_default_width + 40 and screen_h >= m.window_default_height + 90:
                geometry = f"{m.window_default_width}x{m.window_default_height}"
            else:
                geometry = f"{max(min_w, screen_w - 16)}x{max(min_h, screen_h - 56)}+0+0"
        try:
            root.geometry(geometry)
        except Exception:
            pass
        if screen_w < 1000 or screen_h < 700:
            # 小屏幕/远程桌面：自动最大化，保证内容尽量可见
            try:
                if sys.platform == "win32":
                    root.state("zoomed")
                else:
                    root.attributes("-zoomed", True)
            except Exception:
                pass

    def _restored_geometry(self, screen_w: int, screen_h: int) -> str | None:
        if not self.settings.remember_geometry:
            return None
        geometry = (self.settings.geometry or "").strip()
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", geometry)
        if not match:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        x, y = int(match.group(3)), int(match.group(4))
        if width < 500 or height < 380:
            return None
        if x < -width + 100 or x > screen_w - 100:
            x = 0
        if y < -height + 100 or y > screen_h - 100:
            y = 0
        return f"{width}x{height}+{x}+{y}"

    # ── 界面搭建 ─────────────────────────────────────────
    def _build_ui(self) -> None:
        palette = self.palette
        m = self.metrics
        root = self.root
        for child in list(root.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        try:
            root.configure(background=palette.window_bg)
        except Exception:
            pass
        for row in range(7):
            try:
                root.grid_rowconfigure(row, weight=0)
            except Exception:
                pass
        try:
            root.grid_columnconfigure(0, weight=1)
        except Exception:
            pass

        self.header = HeaderBar(root, self)
        self.header.grid(row=0, column=0, sticky="ew")

        row = 1
        self.banner: tk.Frame | None = None
        if not self.core_ready:
            self.banner = self._build_core_banner(root)
            self.banner.grid(row=row, column=0, sticky="ew")
            row += 1

        self.toolbar = Toolbar(root, self)
        self.toolbar.grid(row=row, column=0, sticky="ew")
        row += 1

        self.notebook = tb.Notebook(root, bootstyle="secondary")
        self.notebook.grid(row=row, column=0, sticky="nsew", padx=m.pad, pady=(m.px(4), m.gap_small))
        try:
            root.grid_rowconfigure(row, weight=1)  # 只有主内容区吃掉多余高度
        except Exception:
            pass
        row += 1

        # Tab 1：开机 Logo（原有工作区，布局/行为保持不变）
        self.logo_tab = tk.Frame(self.notebook, bg=palette.window_bg)
        self.paned = tb.Panedwindow(self.logo_tab, orient="horizontal", bootstyle="secondary")
        self.paned.pack(fill="both", expand=True, pady=(m.px(4), 0))
        self.block_list = BlockListPanel(self.paned, self)
        self.preview = PreviewPanel(self.paned, self)
        self.paned.add(self.block_list, weight=1)
        self.paned.add(self.preview, weight=3)
        self.notebook.add(self.logo_tab, text=self.tr.t("lk.tab_logo"))

        # Tab 2：LK 引导程序修补（新增）
        self.lk_tab = tk.Frame(self.notebook, bg=palette.window_bg)
        self.lk_panel = LkPatchPanel(self.lk_tab, self)
        self.lk_panel.pack(fill="both", expand=True)
        self.notebook.add(self.lk_tab, text=self.tr.t("lk.tab_lk"))
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self.log_panel = LogPanel(root, self)
        self.log_panel.grid(row=row, column=0, sticky="ew")
        row += 1
        self.status_bar = StatusBar(root, self)
        self.status_bar.grid(row=row, column=0, sticky="ew")

        self._bind_session_to_widgets()
        try:
            self.root.after(40, self.header.refresh_compact)
        except Exception:
            pass
        self.root.after_idle(self._restore_sash)
        self._update_actions()
        self._update_status_info()
        self._update_title()

    # ── 工作区 Tab ───────────────────────────────────────
    def _current_tab(self) -> str:
        try:
            selected = self.notebook.select()
            return "lk" if str(selected) == str(self.lk_tab) else "logo"
        except Exception:
            return "logo"

    def _on_tab_changed(self, _event: tk.Event | None = None) -> None:
        if self._current_tab() == "lk":
            if not self.lk_panel.core_ready:
                self.set_status(self.tr.t("lk.core_missing", error=self.lk_panel.core_error or ""), "warning")
            else:
                self.set_status(self.tr.t("lk.title"), "normal")
        self._update_status_info()

    def show_logo_panel(self) -> None:
        try:
            self.notebook.select(self.logo_tab)
        except Exception:
            pass

    def show_lk_panel(self) -> None:
        if self.busy:
            return
        try:
            self.notebook.select(self.lk_tab)
        except Exception:
            pass
        if self.lk_panel.core_ready:
            self.set_status(self.tr.t("lk.title"), "normal")
        else:
            self.set_status(self.tr.t("lk.core_missing", error=self.lk_panel.core_error or ""), "warning")

    def _build_core_banner(self, master: Any) -> tk.Frame:
        palette = self.palette
        m = self.metrics
        frame = tk.Frame(master, bg=palette.tag_unknown_bg)
        message = self.tr.t("status.core_missing", error=self.core_error or self.tr.t("common.unknown"))
        label = tk.Label(
            frame,
            text="⚠  " + message,
            bg=palette.tag_unknown_bg,
            fg=palette.tag_unknown_fg,
            font=self.fonts.small,
            anchor="w",
            justify="left",
        )
        label.pack(side="left", padx=m.pad, pady=m.px(4))
        tk.Button(
            frame,
            text=self.tr.t("menu.help.help"),
            bg=palette.tag_unknown_bg,
            fg=palette.tag_unknown_fg,
            activebackground=palette.tag_unknown_bg,
            bd=0,
            highlightthickness=0,
            font=self.fonts.small,
            cursor="hand2",
            command=self.show_help,
        ).pack(side="right", padx=m.pad)
        return frame

    def _bind_session_to_widgets(self) -> None:
        session = self.session
        if session is None:
            self.block_list.set_session(None)
            self.preview.set_session(None)
            return
        selection = max(0, min(self.selected_index, session.count - 1)) if session.count else -1
        self.block_list.set_session(session, select=selection)
        self.preview.set_session(session)
        if selection >= 0:
            self.preview.show_block(selection)
            self.selected_index = selection

    # ── 菜单栏 ───────────────────────────────────────────
    def _build_menubar(self) -> None:
        tr = self.tr
        palette = self.palette
        font = (self.fonts.family, int(round(self.fonts.base_pt)))
        try:
            menubar = tk.Menu(self.root, tearoff=0, font=font)
        except Exception:
            return

        def make_menu() -> tk.Menu:
            return tk.Menu(menubar, tearoff=0, font=font)

        file_menu = make_menu()
        file_menu.add_command(label=tr.t("menu.file.open_image"), accelerator="Ctrl+O", command=self.open_image_dialog)
        file_menu.add_command(label=tr.t("menu.file.new_project"), accelerator="Ctrl+N", command=self.new_project_dialog)
        file_menu.add_command(label=tr.t("menu.file.open_project"), accelerator="Ctrl+Shift+O", command=self.open_project_dialog)
        file_menu.add_separator()
        file_menu.add_command(label=tr.t("menu.file.save_project"), accelerator="Ctrl+S", command=self.save_project)
        file_menu.add_separator()
        file_menu.add_command(label=tr.t("menu.file.export_img"), accelerator="Ctrl+E", command=self.export_logo_img)
        file_menu.add_command(label=tr.t("menu.file.export_bin"), accelerator="Ctrl+B", command=self.export_logo_bin)
        file_menu.add_command(label=tr.t("menu.file.export_zip"), accelerator="Ctrl+U", command=self.export_update_zip)
        file_menu.add_separator()
        file_menu.add_command(label=tr.t("menu.file.boot_logo"), command=self.show_boot_logo_dialog)
        file_menu.add_command(label=tr.t("menu.file.open_workdir"), command=self.open_workdir)
        file_menu.add_separator()
        file_menu.add_command(label=tr.t("menu.file.exit"), accelerator="Alt+F4", command=self._on_close)
        menubar.add_cascade(label=tr.t("menu.file"), menu=file_menu)

        edit_menu = make_menu()
        edit_menu.add_command(label=tr.t("menu.edit.replace"), accelerator="Ctrl+R", command=self.replace_image_dialog)
        edit_menu.add_command(label=tr.t("menu.edit.undo"), accelerator="Ctrl+Z", command=self.restore_selected_image)
        edit_menu.add_separator()
        edit_menu.add_command(label=tr.t("menu.edit.detect_sizes"), command=self.detect_all_unknown)
        edit_menu.add_command(label=tr.t("menu.edit.export_block"), command=self.export_selected_block_png)
        menubar.add_cascade(label=tr.t("menu.edit"), menu=edit_menu)

        view_menu = make_menu()
        view_menu.add_command(label=tr.t("menu.view.log_toggle"), accelerator="Ctrl+L", command=self.toggle_log)
        view_menu.add_separator()
        view_menu.add_command(label=tr.t("menu.view.zoom_fit"), command=lambda: self.preview.set_zoom("fit"))
        view_menu.add_command(label=tr.t("menu.view.zoom_one"), command=lambda: self.preview.set_zoom("one"))
        view_menu.add_separator()
        view_menu.add_command(label=tr.t("menu.view.tab_logo"), command=self.show_logo_panel)
        view_menu.add_command(label=tr.t("menu.view.tab_lk"), command=self.show_lk_panel)
        menubar.add_cascade(label=tr.t("menu.view"), menu=view_menu)

        tools_menu = make_menu()
        tools_menu.add_command(label=tr.t("menu.tools.settings"), accelerator="Ctrl+,", command=self.show_settings)
        tools_menu.add_separator()
        tools_menu.add_command(label=tr.t("menu.tools.lk_patch"), accelerator="Ctrl+K", command=self.show_lk_panel)
        tools_menu.add_command(label=tr.t("menu.tools.open_config"), command=self.open_config_dir)
        menubar.add_cascade(label=tr.t("menu.tools"), menu=tools_menu)

        help_menu = make_menu()
        help_menu.add_command(label=tr.t("menu.help.help"), accelerator="F1", command=self.show_help)
        help_menu.add_command(label=tr.t("menu.help.lk_guide"), command=self._show_lk_flash_guide)
        help_menu.add_command(label=tr.t("menu.help.about"), command=self.show_about)
        menubar.add_cascade(label=tr.t("menu.help"), menu=help_menu)
        try:
            self.root.configure(menu=menubar)
        except Exception:
            pass
        self.menubar = menubar

    # ── 快捷键 ───────────────────────────────────────────
    def _bind_shortcuts(self) -> None:
        bindings = (
            ("<Control-o>", self.open_image_dialog),
            ("<Control-n>", self.new_project_dialog),
            ("<Control-Shift-O>", self.open_project_dialog),
            ("<Control-s>", self.save_project),
            ("<Control-e>", self.export_logo_img),
            ("<Control-b>", self.export_logo_bin),
            ("<Control-u>", self.export_update_zip),
            ("<Control-r>", self.replace_image_dialog),
            ("<Control-z>", self.restore_selected_image),
            ("<Control-l>", self.toggle_log),
            ("<Control-comma>", self.show_settings),
            ("<F1>", self.show_help),
            ("<F5>", self.refresh_view),
            ("<Control-k>", self.show_lk_panel),
        )
        for sequence, callback in bindings:
            try:
                self.root.bind_all(sequence, self._shortcut_wrap(callback), add="+")
                self._shortcut_bindings.append((sequence, ""))
            except Exception:
                pass

    def _shortcut_wrap(self, callback: Callable[[], Any]) -> Callable[[tk.Event], str]:
        def handler(event: tk.Event | None = None) -> str:
            try:
                widget = self.root.focus_get() if self.root is not None else None
                if isinstance(widget, (tk.Entry, tk.Text, tk.Spinbox)):
                    # 输入框里 Ctrl+Z / Ctrl+O 等仍交给界面处理，但文本编辑键放行
                    if event is not None and getattr(event, "keysym", "") in ("z", "Z") and not getattr(event, "state", 0) & 0x4:
                        return ""
                callback()
            except Exception as exc:
                self.show_error(exc)
            return "break"

        return handler

    # ── 拖放 ─────────────────────────────────────────────
    def _setup_dnd(self) -> None:
        if not dnd_available():
            self.drop_enabled = False
            return
        try:
            self.drop_enabled = enable_dnd(self.root, self.handle_dropped_files)
        except Exception:
            self.drop_enabled = False
        if self.drop_enabled:
            self.log("INFO", self.tr.t("dnd.enabled_hint"))

    def handle_dropped_files(self, paths: list[str]) -> None:
        if not paths:
            return
        path = Path(paths[0])
        self.log("INFO", self.tr.t("open.dropped_file", name=path.name))
        if self.busy:
            self.set_status(self.tr.t("common.please_wait"), "warning")
            return
        if path.is_dir():
            if session_mod.is_project_dir(path):
                self._start_open_project(path)
            else:
                self.set_status(self.tr.t("open.dir_not_writable", path=str(path)), "warning")
            return
        self.open_image_path(path)

    # ── 启动日志 ─────────────────────────────────────────
    def _startup_log(self) -> None:
        self.log(
            "INFO",
            f"亿方联发科工具箱 InspireMTKTools v{TOOLKIT_VERSION} 启动"
            f" · Python {sys.version.split()[0]} · DPI scale {self.metrics.scale}",
        )
        if self.core_ready:
            self.log("INFO", f"核心库版本：{self.core_version}")
        else:
            self.log("ERROR", self.tr.t("status.core_missing", error=self.core_error or ""))
        lk_ready, lk_error = core_bridge.lkpatch_status()
        if lk_ready:
            self.log("INFO", self.tr.t("lk.core_ready"))
        else:
            self.log("WARNING", self.tr.t("lk.core_missing", error=lk_error or ""))
        if not dnd_available():
            self.log("INFO", "可选依赖 tkinterdnd2 未安装，已禁用拖放")
        if self.session is None:
            self.set_status(self.tr.t("status.ready_no_project"), "normal")
        self._update_status_info()

    # ── 通用状态 / 日志 / 错误 ───────────────────────────
    def set_status(self, text: str, level: str = "normal") -> None:
        try:
            self.status_bar.set_message(text, level)
        except Exception:
            pass

    def log(self, level: str, message: str) -> None:
        try:
            self.log_panel.append(level, message)
        except Exception:
            pass

    def _show_error_data(
        self,
        data: dict[str, Any],
        *,
        title: str | None = None,
        context: str | None = None,
    ) -> None:
        self.last_error_info = dict(data)
        self.log("ERROR", data.get("message") or self.tr.t("error.unexpected"))
        if self.settings.show_log_on_error:
            try:
                self.log_panel.ensure_visible()
            except Exception:
                pass
        self.set_status(self.tr.t("error.unexpected"), "error")
        if not self.interactive:
            return
        try:
            ErrorDialog(
                self,
                title=title,
                message=data.get("message") or self.tr.t("error.unexpected"),
                hint=data.get("hint"),
                traceback_text=data.get("traceback", ""),
                context=context,
            ).show_modal()
        except Exception:
            traceback.print_exc()

    def show_error(self, exc: BaseException | dict[str, Any], *, title: str | None = None, context: str | None = None) -> None:
        if isinstance(exc, dict):
            data = exc
        else:
            data = core_bridge.format_error(exc)
        self._show_error_data(data, title=title, context=context)

    # ── 后台任务 ─────────────────────────────────────────
    def _run_task(
        self,
        work: Callable[[TaskContext], Any],
        *,
        title: str,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        if self.busy:
            self.set_status(self.tr.t("common.please_wait"), "warning")
            return False
        self.busy = True
        self.set_status(title, "info")
        try:
            self.status_bar.set_busy(True, indeterminate=True)
            self.status_bar.set_progress(None)
            self.status_bar.set_cancel_enabled(True)
        except Exception:
            pass
        self._update_actions()

        def done(result: Any) -> None:
            self._finish_task()
            if on_done is not None:
                on_done(result)

        def failed(info: dict[str, Any]) -> None:
            self._finish_task()
            if on_error is not None:
                on_error(info)
            else:
                self._show_error_data(info)

        def cancelled() -> None:
            self._finish_task()
            self.log("WARNING", self.tr.t("status.cancelled"))
            self.set_status(self.tr.t("status.cancelled"), "warning")

        started = self.runner.run(
            work,
            title=title,
            on_done=done,
            on_error=failed,
            on_cancelled=cancelled,
            on_progress=lambda fraction, message: self.status_bar.set_progress(fraction),
        )
        if not started:
            self.busy = False
            self._finish_task()
            return False
        return True

    def _finish_task(self) -> None:
        self.busy = False
        try:
            self.status_bar.set_busy(False)
        except Exception:
            pass
        self._update_actions()
        if not self.busy:
            view = self._current_view()
            if view is not None:
                self.preview.show_block(self.selected_index)
                self.block_list.update_block(self.selected_index)

    def cancel_task(self) -> None:
        if not self.busy:
            return
        try:
            self.runner.cancel()
        except Exception:
            pass
        self.set_status(self.tr.t("status.cancelling"), "warning")
        try:
            self.status_bar.set_cancel_enabled(False)
        except Exception:
            pass
        self.log("WARNING", self.tr.t("status.cancelling"))

    def wait_for_task(self, timeout: float = 60.0) -> str | None:
        """测试用：驱动事件循环直到后台任务结束。"""
        deadline = time.time() + max(0.1, timeout)
        while time.time() < deadline:
            try:
                self.root.update_idletasks()
                self.root.update()
            except Exception:
                break
            if not self.busy and not self.runner.busy:
                return self.runner.last_outcome
            time.sleep(0.02)
        return None

    # ── 会话辅助 ─────────────────────────────────────────
    def _current_view(self) -> session_mod.BlockView | None:
        if self.session is None or self.selected_index < 0:
            return None
        return self.session.block(self.selected_index)

    def _require_session(self) -> session_mod.UiSession | None:
        if self.session is None:
            self.set_status(self.tr.t("export.no_session"), "warning")
            return None
        return self.session

    def _confirm_discard_changes(self) -> bool:
        if self.session is None or not self.session.dirty:
            return True
        if not self.interactive:
            return True
        return bool(
            messagebox.askyesno(
                self.tr.t("open.confirm_title"),
                self.tr.t("open.unsaved_changes"),
                parent=self.root,
            )
        )

    def _apply_session(self, session: session_mod.UiSession) -> None:
        self.session = session
        self.selected_index = 0 if session.count else -1
        self._bind_session_to_widgets()
        self._update_status_info()
        self._update_title()
        self._update_actions()

    def _update_title(self) -> None:
        try:
            if self.session is None:
                self.root.title(self.tr.t("app.window_title_untitled"))
            else:
                name = self.session.display_name or "untitled"
                if self.session.dirty:
                    name += " *"
                self.root.title(self.tr.t("app.window_title_path", name=name))
        except Exception:
            pass

    def _update_status_info(self) -> None:
        # LK 工作区：显示 LK 扫描概况，而不是 Logo 会话信息
        try:
            if hasattr(self, "notebook") and hasattr(self, "lk_panel") and self._current_tab() == "lk":
                count = len(getattr(self.lk_panel, "entries", {}) or {})
                self.status_bar.set_info(
                    self.tr.t("lk.status_info", count=count) if count else self.tr.t("lk.title")
                )
                return
        except Exception:
            pass
        if self.session is None:
            try:
                self.status_bar.set_info("")
            except Exception:
                pass
            return
        tr = self.tr
        parts: list[str] = []
        path = self.session.project_dir or self.session.source_path
        if path is not None:
            parts.append(tr.t("status.source", path=self._shorten_path(path)))
        parts.append(tr.t("status.blocks", count=self.session.count))
        raw_size = self.session.raw_size
        if raw_size:
            parts.append(tr.t("status.size", size=core_bridge.human_size(raw_size)))
        if self.session.dirty:
            parts.append(tr.t("status.dirty"))
        try:
            self.status_bar.set_info(" · ".join(parts))
        except Exception:
            pass

    @staticmethod
    def _shorten_path(path: Path, limit: int = 52) -> str:
        text = str(path)
        if len(text) <= limit:
            return text
        return "…" + text[-(limit - 1) :]

    def _on_block_selected(self, index: int) -> None:
        pass

    def on_block_selected(self, index: int) -> None:
        self.selected_index = index
        self._update_actions()
        if index >= 0 and self.session is not None:
            view = self.session.block(index)
            if view is not None:
                if view.is_unknown:
                    self.set_status(self.tr.t("blocks.status_unknown"), "warning")
                elif view.is_binary:
                    self.set_status(self.tr.t("blocks.binary_hint"), "info")
                else:
                    self.set_status(self.tr.t("preview.block_title", index=index + 1), "normal")
        self.preview.show_block(index)

    # ── 界面动作可用性 ───────────────────────────────────
    def _update_actions(self) -> None:
        if not hasattr(self, "toolbar"):
            return
        busy = self.busy
        session = self.session
        view = self._current_view()
        has_restore = bool(session is not None and self.selected_index >= 0 and session.can_restore(self.selected_index))
        self.toolbar.set_action_enabled("open_image_dialog", not busy)
        self.toolbar.set_action_enabled("new_project_dialog", not busy)
        self.toolbar.set_action_enabled("open_project_dialog", not busy)
        self.toolbar.set_action_enabled("replace_image_dialog", (not busy) and session is not None and view is not None and view.is_image)
        self.toolbar.set_action_enabled("restore_selected_image", (not busy) and has_restore)
        for name in ("export_logo_img", "export_logo_bin", "export_update_zip"):
            self.toolbar.set_action_enabled(name, (not busy) and session is not None)
        self.toolbar.set_action_enabled("show_boot_logo_dialog", not busy)
        self.toolbar.set_action_enabled("show_lk_panel", not busy)
        if hasattr(self, "lk_panel"):
            try:
                self.lk_panel.set_busy(busy)
            except Exception:
                pass
        try:
            self.status_bar.set_cancel_enabled(busy)
        except Exception:
            pass

    def refresh_view(self) -> None:
        try:
            if self.session is not None:
                self.block_list.refresh()
                self.preview.refresh()
            self.set_status(self.tr.t("common.processing"), "info")
            self.root.after(300, lambda: self.set_status(self.tr.t("status.ready"), "normal"))
        except Exception as exc:
            self.show_error(exc)

    # ── 打开镜像 / 新建项目 / 打开项目 ───────────────────
    def open_image_dialog(self) -> None:
        if self.busy:
            return
        self.show_logo_panel()
        path = filedialog.askopenfilename(
            parent=self.root,
            title=self.tr.t("open.choose_image"),
            initialdir=self.settings.last_open_dir or str(Path.home()),
            filetypes=[
                (self.tr.t("open.filter_logo"), "*.bin;*.img;*.logo"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not path:
            return
        self.open_image_path(path)

    def open_image_path(self, path: str | Path) -> bool:
        target = Path(path)
        if not self._confirm_discard_changes():
            return False
        self.settings.last_open_dir = str(target.parent)
        self._save_settings()

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_open"))
            return session_mod.open_container(target)

        return self._run_task(
            work,
            title=self.tr.t("status.busy_open"),
            on_done=lambda session: self._after_container_loaded(session),
        )

    def _after_container_loaded(self, session: session_mod.ContainerSession) -> None:
        try:
            self._resolve_unknown_sizes(session)
            self._apply_session(session)
            name = session.source_path.name if session.source_path else ""
            self.log("INFO", self.tr.t("open.image_loaded", name=name, count=session.count))
            if session.raw_mode:
                message = self.tr.t("open.raw_mode_warning")
                self.log("WARNING", message)
                self.set_status(message, "warning")
            else:
                self.set_status(
                    self.tr.t("open.image_loaded", name=name, count=session.count),
                    "success",
                )
        except Exception as exc:
            self.show_error(exc)

    def new_project_dialog(self) -> None:
        if self.busy:
            return
        self.show_logo_panel()
        source = filedialog.askopenfilename(
            parent=self.root,
            title=self.tr.t("open.choose_image"),
            initialdir=self.settings.last_open_dir or str(Path.home()),
            filetypes=[
                (self.tr.t("open.filter_logo"), "*.bin;*.img;*.logo"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not source:
            return
        project_dir = filedialog.askdirectory(
            parent=self.root,
            title=self.tr.t("open.choose_new_dir"),
            initialdir=self.settings.last_save_dir or str(Path(source).parent),
            mustexist=False,
        )
        if not project_dir:
            return
        self.start_new_project(source, project_dir)

    def start_new_project(self, source: str | Path, project_dir: str | Path) -> bool:
        target = Path(project_dir)
        error = self._validate_writable_dir(target)
        if error is not None:
            self._show_error_data({"message": error, "hint": None, "traceback": ""})
            return False
        if self.interactive and target.exists() and any(target.iterdir()):
            if not messagebox.askyesno(
                self.tr.t("open.confirm_title"),
                self.tr.t("open.dir_not_empty", path=str(target)),
                parent=self.root,
            ):
                return False
        if not self._confirm_discard_changes():
            return False
        self.settings.last_open_dir = str(Path(source).parent)
        self.settings.last_save_dir = str(target.parent)
        self._save_settings()

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_open"))
            return session_mod.open_container(source)

        return self._run_task(
            work,
            title=self.tr.t("status.busy_open"),
            on_done=lambda session: self._continue_new_project(session, target),
        )

    def _continue_new_project(self, container_session: session_mod.ContainerSession, project_dir: Path) -> None:
        try:
            self._resolve_unknown_sizes(container_session)
        except Exception as exc:
            self.show_error(exc)
            return

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_new_project"))
            return session_mod.new_project(
                project_dir, container_session.container, overwrite=True
            )

        self._run_task(
            work,
            title=self.tr.t("status.busy_new_project"),
            on_done=lambda project: self._after_project_created(project, project_dir),
        )

    def _after_project_created(self, project: session_mod.ProjectSession, project_dir: Path) -> None:
        self._apply_session(project)
        message = self.tr.t("open.project_created", path=str(project_dir))
        self.log("INFO", message)
        self.set_status(message, "success")

    def open_project_dialog(self) -> None:
        if self.busy:
            return
        self.show_logo_panel()
        directory = filedialog.askdirectory(
            parent=self.root,
            title=self.tr.t("open.choose_project"),
            initialdir=self.settings.last_save_dir or str(Path.home()),
            mustexist=True,
        )
        if not directory:
            return
        self._start_open_project(directory)

    def _start_open_project(self, directory: str | Path) -> bool:
        if not self._confirm_discard_changes():
            return False
        path = Path(directory)
        self.settings.last_save_dir = str(path.parent)
        self._save_settings()

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_open"))
            return session_mod.open_project(path)

        return self._run_task(
            work,
            title=self.tr.t("status.busy_open"),
            on_done=lambda project: self._after_project_opened(project, path),
        )

    def _after_project_opened(self, project: session_mod.ProjectSession, directory: Path) -> None:
        self._apply_session(project)
        message = self.tr.t("open.project_opened", path=str(directory))
        self.log("INFO", message)
        if project.raw_mode:
            warning = self.tr.t("open.raw_mode_warning")
            self.log("WARNING", warning)
            self.set_status(warning, "warning")
        else:
            self.set_status(message, "success")

    def _validate_writable_dir(self, path: Path) -> str | None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self.tr.t("open.dir_not_writable", path=f"{path}\n{exc}")
        probe = path / ".logobuilder_write_test"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            return self.tr.t("open.dir_not_writable", path=str(path))
        return None

    # ── 未知分辨率识别 ───────────────────────────────────
    def _resolve_unknown_sizes(self, session: session_mod.UiSession) -> None:
        pending = session.pending_unknowns()
        if not pending:
            return
        groups = session.unknown_groups()
        db = session_mod.get_size_db()
        neighbor_sizes = [b.size for b in session.blocks if b.size is not None][:4]
        resolved = 0
        for index in pending:
            block = session.block(index)
            if block is None or not block.is_unknown:
                continue
            length = int(block.decompressed_len or 0)
            candidates = session_mod.size_candidates(length, db=db, neighbors=neighbor_sizes)
            if not self.interactive:
                exact = [c for c in candidates if float(getattr(c, "score", 0) or 0) >= 0.9]
                if len(exact) == 1:
                    size = getattr(exact[0], "size", exact[0])
                    session.assign_size(index, size)
                    neighbor_sizes.append(size)
                    resolved += 1
                continue
            dialog = ResolutionDialog(
                self,
                block_index=index,
                decompressed_len=length,
                candidates=candidates,
                preview_provider=lambda size, i=index: session.preview_for_size(i, size),
                same_size_count=len(groups.get(length, [])),
            )
            result = dialog.show_modal()
            if result is None or result.skipped:
                if result is not None and result.skip_all:
                    break
                continue
            targets = [index]
            if result.apply_all:
                targets += [t for t in groups.get(length, []) if t != index]
            for target in targets:
                target_view = session.block(target)
                if target_view is not None and target_view.is_unknown:
                    try:
                        session.assign_size(target, result.size)
                        resolved += 1
                    except Exception as exc:
                        self.show_error(exc)
                        break
            if result.remember:
                session_mod.remember_size(result.size)
            neighbor_sizes.append(result.size)
        if resolved:
            self.log("INFO", self.tr.t("size_dialog.applied_all", count=resolved))

    def detect_size_for_block(self, index: int | None = None) -> None:
        if index is None:
            index = self.selected_index
        session = self.session
        if session is None or index < 0:
            return
        view = session.block(index)
        if view is None or not view.is_unknown:
            self.set_status(self.tr.t("blocks.status_ok"), "info")
            return
        length = int(view.decompressed_len or 0)
        db = session_mod.get_size_db()
        neighbors = [b.size for b in session.blocks if b.size is not None][:4]
        candidates = session_mod.size_candidates(length, db=db, neighbors=neighbors)
        dialog = ResolutionDialog(
            self,
            block_index=index,
            decompressed_len=length,
            candidates=candidates,
            preview_provider=lambda size, i=index: session.preview_for_size(i, size),
            same_size_count=len(session.unknown_groups().get(length, [])),
        )
        result = dialog.show_modal()
        if result is None or result.skipped:
            return
        targets = [index]
        if result.apply_all:
            targets += [t for t in session.unknown_groups().get(length, []) if t != index]
        for target in targets:
            target_view = session.block(target)
            if target_view is not None and target_view.is_unknown:
                try:
                    session.assign_size(target, result.size)
                except Exception as exc:
                    self.show_error(exc)
                    return
        if result.remember:
            session_mod.remember_size(result.size)
        self.block_list.refresh()
        self.preview.refresh()
        self._update_actions()
        self._update_status_info()
        self.set_status(self.tr.t("size_dialog.applied_all", count=len(targets)), "success")

    def detect_all_unknown(self) -> None:
        if self.session is None:
            return
        if self.busy:
            return
        try:
            self._resolve_unknown_sizes(self.session)
            self.block_list.refresh()
            self.preview.refresh()
            self._update_actions()
            self._update_status_info()
        except Exception as exc:
            self.show_error(exc)

    # ── 图片替换 / 恢复 ──────────────────────────────────
    def replace_image_dialog(self, index: int | None = None) -> None:
        if self.busy:
            return
        if index is None:
            index = self.selected_index if self.selected_index >= 0 else 0
        session = self._require_session()
        if session is None:
            return
        view = session.block(index)
        if view is None:
            return
        if view.is_unknown:
            self.set_status(self.tr.t("blocks.status_unknown"), "warning")
            self.detect_size_for_block(index)
            view = session.block(index)
            if view is None or not view.is_image:
                return
        if not view.is_image or view.size is None:
            self.set_status(self.tr.t("bootlogo.not_image_block"), "warning")
            return
        frame = self.preview.frame_index if view.is_multi_frame else 0
        target_size = view.frame_size if view.is_multi_frame else view.size
        current = (
            session.frame_image(index, frame) if view.is_multi_frame else session.image(index)
        )
        if not self.interactive:
            return
        frame_hint = None
        if view.is_multi_frame:
            frame_hint = self.tr.t("replace.frame_hint", n=frame + 1, count=view.frame_count)
        dialog = ReplaceImageDialog(
            self,
            block_index=index,
            target_size=target_size,
            current_image=current,
            frame_hint=frame_hint,
        )
        result = dialog.show_modal()
        if result is None or result.image is None:
            return
        self.replace_image_direct(index, result.image, fit=result.fit, frame=frame if view.is_multi_frame else None)

    def replace_image_direct(
        self,
        index: int,
        image: Image.Image,
        *,
        fit: str = "stretch",
        frame: int | None = None,
    ) -> bool:
        session = self._require_session()
        if session is None:
            return False
        view = session.block(index)
        target_frame = frame
        try:
            if target_frame is not None and view is not None and view.is_multi_frame:
                session.replace_frame(index, target_frame, image, fit=fit)
            else:
                session.replace_image(index, image, fit=fit)
        except Exception as exc:
            self.show_error(exc)
            return False
        self.block_list.update_block(index)
        self.preview.show_block(index)
        self._update_actions()
        self._update_title()
        self._update_status_info()
        if target_frame is not None and view is not None and view.is_multi_frame:
            message = self.tr.t("replace.frame_done", index=index + 1, frame=target_frame + 1)
        else:
            message = self.tr.t("replace.done", index=index + 1)
        self.set_status(message, "success")
        self.log("INFO", message)
        return True

    def restore_selected_image(self) -> None:
        if self.selected_index < 0:
            self.set_status(self.tr.t("preview.no_selection"), "warning")
            return
        self.restore_image(self.selected_index)

    def restore_image(self, index: int | None = None) -> None:
        session = self.session
        if session is None:
            return
        if index is None:
            index = self.selected_index
        if index < 0:
            return
        try:
            restored = session.restore_original(index)
        except Exception as exc:
            self.show_error(exc)
            return
        if not restored:
            self.set_status(self.tr.t("blocks.ctx_restore"), "warning")
            return
        self.block_list.update_block(index)
        self.preview.show_block(index)
        self.preview.reset_frame()
        self._update_actions()
        self._update_title()
        self._update_status_info()
        self.set_status(self.tr.t("blocks.ctx_restore"), "success")

    # ── 块导出 / 复制信息 ────────────────────────────────
    def export_selected_block_png(self) -> None:
        if self.selected_index < 0:
            return
        self.export_block_png(self.selected_index)

    def export_block_png(self, index: int) -> None:
        session = self.session
        if session is None:
            return
        view = session.block(index)
        if view is None or not view.is_image:
            self.set_status(self.tr.t("bootlogo.not_image_block"), "warning")
            return
        frame = self.preview.frame_index if view.is_multi_frame else 0
        image = (
            session.frame_image(index, frame) if view.is_multi_frame else session.image(index)
        )
        if image is None:
            self.set_status(self.tr.t("preview.empty_canvas"), "warning")
            return
        if view.is_multi_frame:
            default_name = f"block_{index + 1:03d}_frame{frame + 1}.png"
            dialog_title = self.tr.t("export.block_png_frame", index=index + 1, frame=frame + 1)
        else:
            default_name = f"block{index + 1}.png"
            dialog_title = self.tr.t("export.block_png")
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title=dialog_title,
            initialdir=self._default_export_dir(),
            initialfile=default_name,
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), (self.tr.t("open.filter_all"), "*.*")],
        )
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "PNG")
        except Exception as exc:
            self.show_error(exc)
            return
        message = self.tr.t("export.block_saved", path=path)
        self.log("INFO", message)
        self.set_status(message, "success")
        if self.interactive:
            SuccessDialog(self, title=dialog_title, path=Path(path)).show_modal()

    def copy_block_info(self, index: int | None = None) -> None:
        session = self.session
        if session is None:
            return
        if index is None:
            index = self.selected_index
        view = session.block(index)
        if view is None:
            return
        lines = [
            f"#{index + 1}",
            f"type={view.kind}",
            f"size={session_mod.size_to_text(view.size, compact=False)}",
            f"decompressed={view.decompressed_len}",
            f"compressed={view.compressed_len}",
            f"status={view.status}",
            f"tag={session.tag}",
            f"source={session.project_dir or session.source_path}",
        ]
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(lines))
            self.set_status(self.tr.t("common.copied"), "success")
        except Exception:
            pass

    # ── 导出：logo.img / logo.bin / update.zip ───────────
    def _default_export_dir(self) -> str:
        session = self.session
        if session is not None:
            if session.project_dir is not None:
                return str(session.project_dir)
            if session.source_path is not None:
                return str(session.source_path.parent)
        return self.settings.last_save_dir or str(Path.home())

    def _default_name(self, suffix: str) -> str:
        session = self.session
        stem = "logo"
        if session is not None:
            source = session.source_path
            if source is not None and source.stem:
                stem = source.stem
        return f"{stem}.{suffix}"

    def export_logo_img(self) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return
        default_path = Path(self._default_export_dir()) / self._default_name("img")
        actual_size = session.raw_size
        dialog = ExportImgDialog(
            self,
            default_path=default_path,
            actual_size=actual_size,
            suggested_target=actual_size or 0,
            fill_byte=self.settings.padding_byte,
        )
        choice = dialog.show_modal()
        if choice is None or choice.path is None:
            return
        self._export_img_choice(choice)

    def _export_img_choice(self, choice: ExportImgChoice) -> None:
        session = self.session
        if session is None:
            return

        def work(ctx: TaskContext) -> dict[str, Any]:
            ctx.progress(None, self.tr.t("status.busy_pack"))
            target_size = int(choice.pad_target or 0)
            data = session.to_bytes(
                pad_to_size=target_size if target_size else None,
                pad_byte=int(choice.fill_byte or 0),
            )
            ctx.check_cancel()
            path = Path(choice.path)
            self._atomic_write(path, data)
            return {
                "path": path,
                "size": len(data),
                "padded": bool(target_size and len(data) >= target_size),
            }

        self._run_task(work, title=self.tr.t("status.busy_export"), on_done=self._on_export_done)

    def export_logo_bin(self) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return
        default_path = Path(self._default_export_dir()) / self._default_name("bin")
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title=self.tr.t("export.bin_title"),
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=".bin",
            filetypes=[
                (self.tr.t("open.filter_logo"), "*.bin;*.img"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not path:
            return
        self.settings.last_save_dir = str(Path(path).parent)
        self._save_settings()

        def work(ctx: TaskContext) -> dict[str, Any]:
            ctx.progress(None, self.tr.t("status.busy_pack"))
            data = session.to_bytes()
            ctx.check_cancel()
            target = Path(path)
            self._atomic_write(target, data)
            return {"path": target, "size": len(data), "padded": False}

        self._run_task(work, title=self.tr.t("status.busy_pack"), on_done=self._on_export_done)

    def export_update_zip(self) -> None:
        session = self._require_session()
        if session is None or self.busy:
            return
        default_path = Path(self._default_export_dir()) / "update.zip"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title=self.tr.t("export.zip_title"),
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
            defaultextension=".zip",
            filetypes=[("ZIP", "*.zip"), (self.tr.t("open.filter_all"), "*.*")],
        )
        if not path:
            return
        self.settings.last_save_dir = str(Path(path).parent)
        self._save_settings()
        script_text: str | None = None
        binary_path: Path | None = None
        if isinstance(session, session_mod.ProjectSession):
            script_text, binary_path = session.custom_zip_assets()

        def work(ctx: TaskContext) -> dict[str, Any]:
            ctx.progress(None, self.tr.t("status.busy_zip"))
            data = session.to_bytes()
            ctx.check_cancel()
            core = core_bridge.get_core()
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            core.make_update_zip(
                data,
                target,
                updater_script=script_text,
                update_binary=binary_path,
            )
            return {
                "path": target,
                "size": target.stat().st_size if target.exists() else None,
                "custom": bool(script_text or binary_path),
            }

        self._run_task(work, title=self.tr.t("status.busy_zip"), on_done=self._on_zip_done)

    def _on_export_done(self, result: dict[str, Any]) -> None:
        path: Path = result.get("path")
        message = self.tr.t("export.success_body", path=str(path))
        self.log("INFO", f"export ok: {path} ({result.get('size')} bytes)")
        self.set_status(self.tr.t("status.done"), "success")
        self._update_status_info()
        if self.interactive:
            lines = []
            if result.get("size") is not None:
                lines.append(self.tr.t("export.size_info", size=core_bridge.human_size(result["size"])))
            if result.get("padded"):
                lines.append(self.tr.t("export.pad_hint"))
            SuccessDialog(self, title=self.tr.t("export.success_title"), path=path, lines=lines).show_modal()

    def _on_zip_done(self, result: dict[str, Any]) -> None:
        path: Path = result.get("path")
        self.log("INFO", f"update.zip ok: {path}")
        self.set_status(self.tr.t("status.done"), "success")
        if self.interactive:
            lines = []
            if result.get("size") is not None:
                lines.append(self.tr.t("export.size_info", size=core_bridge.human_size(result["size"])))
            lines.append(self.tr.t("export.zip_hint"))
            if result.get("custom"):
                lines.append(self.tr.t("export.zip_custom_script"))
            SuccessDialog(self, title=self.tr.t("export.zip_title"), path=path, lines=lines).show_modal()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    # ── 第二屏 boot_logo ─────────────────────────────────
    def show_boot_logo_dialog(self) -> None:
        if self.busy:
            return
        BootLogoDialog(self).show_modal()

    def _bootlogo_save_path(self, default_name: str) -> str | None:
        return filedialog.asksaveasfilename(
            parent=self.root,
            title=self.tr.t("bootlogo.select_out"),
            initialdir=self._default_export_dir(),
            initialfile=default_name,
            filetypes=[
                ("boot_logo", "boot_logo"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )

    def bootlogo_from_image(self) -> None:
        if self.busy:
            return
        source = filedialog.askopenfilename(
            parent=self.root,
            title=self.tr.t("bootlogo.select_image"),
            initialdir=self.settings.last_open_dir or str(Path.home()),
            filetypes=[
                (self.tr.t("open.filter_image"), "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not source:
            return
        out = self._bootlogo_save_path("boot_logo")
        if not out:
            return

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_bootlogo"))
            core = core_bridge.get_core()
            with Image.open(source) as image:
                image.load()
                size = core.write_boot_logo(image, out)
            return {"path": Path(out), "size": size}

        self._run_task(work, title=self.tr.t("status.busy_bootlogo"), on_done=self._on_bootlogo_done)

    def bootlogo_from_block(self) -> None:
        session = self.session
        if session is None or self.selected_index < 0:
            self.set_status(self.tr.t("bootlogo.no_project"), "warning")
            return
        view = session.block(self.selected_index)
        if view is None or not view.is_image:
            self.set_status(self.tr.t("bootlogo.not_image_block"), "warning")
            return
        image = session.image(self.selected_index)
        if image is None:
            return
        out = self._bootlogo_save_path("boot_logo")
        if not out:
            return

        def work(ctx: TaskContext) -> Any:
            ctx.progress(None, self.tr.t("status.busy_bootlogo"))
            core = core_bridge.get_core()
            size = core.write_boot_logo(image, out)
            return {"path": Path(out), "size": size}

        self._run_task(work, title=self.tr.t("status.busy_bootlogo"), on_done=self._on_bootlogo_done)

    def bootlogo_open_preview(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title=self.tr.t("bootlogo.select_open"),
            initialdir=self.settings.last_open_dir or str(Path.home()),
            filetypes=[
                ("boot_logo", "boot_logo"),
                (self.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not path:
            return
        try:
            core = core_bridge.get_core()
            data = Path(path).read_bytes()
        except Exception as exc:
            self.show_error(exc)
            return
        if not data:
            self._show_error_data(
                {"message": self.tr.t("bootlogo.open_failed", error="文件为空（0 字节）"), "hint": None, "traceback": ""}
            )
            return
        length = len(data)
        candidates = session_mod.boot_logo_size_candidates(length)
        size = session_mod.boot_logo_unique_size(length)
        if size is None and self.interactive and candidates:
            # 裸数据没有宽高信息，长度可能对应多个尺寸：让用户确认，而不是静默取第一个
            dialog = ResolutionDialog(
                self,
                block_index=0,
                decompressed_len=length,
                candidates=candidates,
                preview_provider=lambda candidate_size: core.pixels_to_image(data, candidate_size),
                subject=self.tr.t("bootlogo.choose_size", size=f"{length:,}"),
                allowed_depths=(2,),
                default_depth=2,
            )
            result = dialog.show_modal()
            if result is None or result.skipped or result.size is None:
                self.set_status(self.tr.t("open.cancelled_by_user"), "info")
                return
            size = result.size
        if size is None:
            # 非交互模式或无法反解候选：退回 core 的最佳推断
            try:
                from logobuilder.ui import core_bridge as _cb  # 保持延迟导入

                size = _cb.get_core().infer_boot_logo_size(length)
            except Exception as exc:
                self.show_error(exc)
                return
        try:
            image, size = core.read_boot_logo(path, size)
        except Exception as exc:
            self.show_error(exc)
            return
        caption = self.tr.t("bootlogo.inferred", size=session_mod.size_to_text(size, compact=False))
        ImagePreviewDialog(
            self,
            title=self.tr.t("bootlogo.title"),
            image=image,
            caption=caption,
        ).show_modal()

    def _on_bootlogo_done(self, result: dict[str, Any]) -> None:
        path = result["path"]
        message = self.tr.t("bootlogo.saved", path=str(path))
        self.log("INFO", message)
        self.set_status(message, "success")
        if self.interactive:
            SuccessDialog(self, title=self.tr.t("bootlogo.title"), path=path).show_modal()

    # ── 设置 ─────────────────────────────────────────────
    def show_settings(self) -> None:
        if self.busy:
            return
        dialog = SettingsDialog(self)
        result = dialog.show_modal()
        if result is None:
            return
        self.settings = result
        self.settings.save(self.config_path)
        self.set_status(self.tr.t("settings.saved"), "success")
        self.log("INFO", self.tr.t("settings.saved"))

    def apply_tk_scale(self, scale: float | None = None) -> None:
        """重新应用 Tk 缩放与字体（供 DPI 变化 / 截图模式使用）。"""
        if scale is None:
            scale = self.theme.metrics.scale
        self.theme.apply_scale(scale, fonts=build_fonts(self.root, self.theme.fonts))
        self._sync_theme_attrs()

    def _save_settings(self) -> None:
        try:
            self.settings.save(self.config_path)
        except Exception:
            pass

    # ── 帮助 / 关于 / 打开目录 ───────────────────────────
    def _show_lk_flash_guide(self) -> None:
        try:
            entries = [self.lk_panel.entries[key] for key in self.lk_panel.order]
        except Exception:
            entries = []
        from logobuilder.ui.lkpatch_panel import LkFlashGuideDialog

        LkFlashGuideDialog(self, entries=entries).show_modal()

    def show_help(self) -> None:
        if self.busy:
            return
        HelpDialog(self).show_modal()

    def show_about(self) -> None:
        if self.busy:
            return
        AboutDialog(self).show_modal()

    def open_workdir(self) -> None:
        session = self.session
        path: Path | None = None
        if session is not None:
            path = session.project_dir or (session.source_path.parent if session.source_path else None)
        if path is None:
            path = Path(self.settings.last_open_dir or str(Path.home()))
        self.reveal_path(path)

    def open_config_dir(self) -> None:
        self.reveal_path(self.config_dir)

    def reveal_path(self, path: str | Path) -> None:
        target = Path(path)
        command = platform_reveal_command(target)
        if command is None:
            self.set_status(str(target), "normal")
            return
        try:
            subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as exc:
            self._show_error_data(
                {
                    "message": self.tr.t("open.reveal_failed", error=str(exc)),
                    "hint": None,
                    "traceback": traceback.format_exc(),
                }
            )

    # ── 保存项目 ─────────────────────────────────────────
    def save_project(self) -> None:
        session = self.session
        if session is None:
            self.set_status(self.tr.t("export.no_session"), "warning")
            return
        if not isinstance(session, session_mod.ProjectSession):
            self.set_status(self.tr.t("common.info"), "info")
            return
        try:
            session.save()
        except Exception as exc:
            self.show_error(exc)
            return
        self._update_title()
        self._update_status_info()
        message = self.tr.t("open.project_saved", path=str(session.project_dir))
        self.log("INFO", message)
        self.set_status(message, "success")

    # ── 面板开关 ─────────────────────────────────────────
    def toggle_log(self) -> None:
        try:
            self.log_panel.toggle()
        except Exception:
            pass

    # ── 布局辅助 ─────────────────────────────────────────
    def _restore_sash(self) -> None:
        m = self.metrics
        try:
            total = self.paned.winfo_width()
            if total <= 1:
                self.root.after(50, self._restore_sash)
                return
            wanted = self.settings.paned_sash or self._sash_width or m.list_width
            wanted = max(m.list_min_width, min(wanted, int(total * 0.5)))
            self.paned.sashpos(0, wanted)
            self._sash_width = wanted
        except Exception:
            pass

    def _current_sash(self) -> int:
        try:
            return int(self.paned.sashpos(0))
        except Exception:
            return self._sash_width

    # ── 重建界面（主题 / 语言变化）──────────────────────
    def _rebuild_ui(self) -> None:
        if self.busy:
            return
        saved_selected = self.selected_index
        try:
            saved_log_visible = self.log_panel.visible
            saved_records = list(self.log_panel.records)
        except Exception:
            saved_log_visible = False
            saved_records = []
        saved_sash = self._current_sash()
        saved_tab = self._current_tab()
        try:
            saved_lk_state = self.lk_panel.snapshot_state()
        except Exception:
            saved_lk_state = None
        self._sync_theme_attrs()
        self._build_ui()
        self._build_menubar()
        try:
            if saved_lk_state:
                self.lk_panel.restore_state(saved_lk_state)
            if saved_tab == "lk":
                self.show_lk_panel()
        except Exception:
            pass
        try:
            for stamp, level, message in saved_records:
                self.log_panel.append(level, message, timestamp=stamp)
            self.log_panel.set_visible(saved_log_visible)
        except Exception:
            pass
        if self.session is not None:
            self.selected_index = max(0, min(saved_selected, self.session.count - 1))
            self._bind_session_to_widgets()
        if saved_sash > 0:
            self._sash_width = saved_sash
            self.root.after_idle(self._restore_sash)
        self._update_actions()
        self._update_status_info()
        self._update_title()

    # ── 关闭 ─────────────────────────────────────────────
    def _on_close(self) -> None:
        if self._closing:
            return
        if not self._confirm_discard_changes():
            return
        self._closing = True
        if self.settings.remember_geometry:
            try:
                self.settings.geometry = self.root.winfo_geometry()
                self.settings.paned_sash = self._current_sash()
            except Exception:
                pass
        self._save_settings()
        try:
            self.status_bar.set_busy(False)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ── 测试 / 截图辅助（同步接口）──────────────────────
    def load_container_sync(self, path: str | Path) -> session_mod.ContainerSession:
        session = session_mod.open_container(path)
        self._resolve_unknown_sizes(session)
        self._apply_session(session)
        return session

    def create_project_sync(self, source: str | Path, project_dir: str | Path) -> session_mod.ProjectSession:
        container_session = session_mod.open_container(source)
        self._resolve_unknown_sizes(container_session)
        project = session_mod.new_project(project_dir, container_session.container)
        self._apply_session(project)
        return project

    def open_project_sync(self, directory: str | Path) -> session_mod.ProjectSession:
        project = session_mod.open_project(directory)
        self._apply_session(project)
        return project

    def assign_size_sync(self, index: int, size: Any) -> bool:
        session = self.session
        if session is None:
            return False
        try:
            session.assign_size(index, size)
        except Exception as exc:
            self.last_error_info = core_bridge.format_error(exc)
            return False
        self.block_list.refresh()
        self.preview.refresh()
        self._update_actions()
        return True

    def export_bytes_sync(
        self,
        out_path: str | Path,
        *,
        pad_target: int = 0,
        fill_byte: int = 0x00,
    ) -> dict[str, Any]:
        session = self._require_session()
        if session is None:
            raise RuntimeError("no session")
        data = session.to_bytes(
            pad_to_size=int(pad_target) if pad_target else None,
            pad_byte=int(fill_byte or 0),
        )
        target = Path(out_path)
        self._atomic_write(target, data)
        return {"path": target, "size": len(data)}

    def export_zip_sync(self, out_path: str | Path) -> dict[str, Any]:
        session = self._require_session()
        if session is None:
            raise RuntimeError("no session")
        core = core_bridge.get_core()
        script_text: str | None = None
        binary_path: Path | None = None
        if isinstance(session, session_mod.ProjectSession):
            script_text, binary_path = session.custom_zip_assets()
        target = Path(out_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        core.make_update_zip(
            session.to_bytes(),
            target,
            updater_script=script_text,
            update_binary=binary_path,
        )
        return {"path": target, "size": target.stat().st_size if target.exists() else None}
