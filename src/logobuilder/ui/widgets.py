"""可复用的界面组件：顶栏、工具栏、状态栏、可折叠日志面板、链接按钮、提示气泡。

这些组件只依赖调色板/字体/尺寸（:mod:`logobuilder.ui.theme`），
通过回调与主窗口交互，方便单独做单元测试。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import ttkbootstrap as tb

from .imaging import to_photo
from .theme import Metrics, Palette, UiFonts


# ── 通用小部件 ─────────────────────────────────────────────
class LinkButton(tk.Label):
    """扁平文字按钮（用于顶栏/日志栏，避免 ttk 按钮在深色背景上突兀）。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        command: Callable[[], Any] | None = None,
        *,
        fg: str,
        hover_fg: str,
        bg: str,
        font: tuple | tkfont.Font,
        underline: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text,
            fg=fg,
            bg=bg,
            font=font,
            cursor="hand2",
            padx=4,
            pady=2,
            **kwargs,
        )
        self._fg = fg
        self._hover_fg = hover_fg
        self._command = command
        self._underline = underline
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<space>", self._on_click)

    def _on_click(self, _event: tk.Event | None = None) -> str:
        if self._command is not None:
            self._command()
        return "break"

    def _on_enter(self, _event: tk.Event | None = None) -> None:
        self.configure(fg=self._hover_fg)
        if self._underline:
            try:
                self.configure(font=(self._font_family, self._font_size, "underline"))
            except Exception:
                pass

    def _on_leave(self, _event: tk.Event | None = None) -> None:
        self.configure(fg=self._fg)
        if self._underline:
            try:
                self.configure(font=(self._font_family, self._font_size, "normal"))
            except Exception:
                pass

    # 语言切换时重建，但保留 set_text 供动态更新
    def set_text(self, text: str) -> None:
        self.configure(text=text)

    def set_command(self, command: Callable[[], Any] | None) -> None:
        self._command = command


class Tooltip:
    """悬停提示气泡（延迟出现，跟随控件）。"""

    def __init__(self, widget: tk.Misc, text: str, *, delay: int = 550) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._window is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.wm_attributes("-alpha", 0.97)
        except Exception:
            pass
        bg = "#1f2937"
        label = tk.Label(
            win,
            text=self.text,
            bg=bg,
            fg="#f3f4f6",
            font=("TkDefaultFont", 9),
            padx=8,
            pady=4,
            justify="left",
        )
        label.pack()
        win.wm_geometry(f"+{x}+{y}")
        self._window = win

    def _hide(self, _event: tk.Event | None = None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None


def hline(master: tk.Misc, palette: Palette, *, height: int = 1) -> tk.Frame:
    line = tk.Frame(master, bg=palette.border, height=max(1, height))
    line.pack_propagate(False)
    return line


def pill(
    master: tk.Misc,
    text: str,
    *,
    bg: str,
    fg: str,
    font: tuple | tkfont.Font,
    padx: int = 8,
    pady: int = 2,
) -> tk.Label:
    return tk.Label(
        master,
        text=text,
        bg=bg,
        fg=fg,
        font=font,
        padx=padx,
        pady=pady,
        bd=0,
        highlightthickness=0,
    )


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs: Any,
) -> int:
    """在 Canvas 上画圆角矩形（用 smooth polygon 近似）。"""
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ── 顶栏 ───────────────────────────────────────────────────
class HeaderBar(tk.Frame):
    """应用顶栏（litera 风格）：白底 + 底部 1px 分隔线 + 品牌区 + 设置/帮助入口。"""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        metrics: Metrics = app.metrics
        fonts: UiFonts = app.fonts
        super().__init__(master, bg=palette.header_bg)
        self.app = app
        self.palette = palette
        self.metrics = metrics
        self.fonts = fonts
        self._compact = False

        # 底部 1px 分隔线（litera 顶栏用细线区分工具栏，而不是深色块）
        self.bottom_line = tk.Frame(self, bg=palette.border, height=max(1, metrics.px(1)))
        self.bottom_line.pack(side="bottom", fill="x")

        # 品牌区
        self.brand = tk.Frame(self, bg=palette.header_bg)
        self.brand.pack(side="left", padx=(metrics.header_pad_x, 0), pady=(metrics.px(8), metrics.px(9)))

        logo_size = metrics.px(34)
        self.logo_canvas = tk.Canvas(
            self.brand,
            width=logo_size,
            height=logo_size,
            bg=palette.header_bg,
            highlightthickness=0,
            bd=0,
        )
        self.logo_canvas.pack(side="left")
        self._draw_logo(logo_size)

        text_frame = tk.Frame(self.brand, bg=palette.header_bg)
        text_frame.pack(side="left", padx=(metrics.px(10), 0))
        self.title_label = tk.Label(
            text_frame,
            text=app.tr.t("app.short_title"),
            bg=palette.header_bg,
            fg=palette.text,
            font=fonts.title,
            anchor="w",
        )
        self.title_label.pack(anchor="w")
        self.subtitle_label = tk.Label(
            text_frame,
            text=app.tr.t("app.subtitle"),
            bg=palette.header_bg,
            fg=palette.header_sub,
            font=fonts.small,
            anchor="w",
        )
        self.subtitle_label.pack(anchor="w")

        # 右侧入口（仅中文界面：设置 / 帮助，无深色/语言切换）
        self.right = tk.Frame(self, bg=palette.header_bg)
        self.right.pack(side="right", padx=(0, metrics.header_pad_x), pady=metrics.px(12))

        self.help_link = LinkButton(
            self.right,
            app.tr.t("toolbar.help"),
            app.show_help,
            fg=palette.muted,
            hover_fg=palette.accent,
            bg=palette.header_bg,
            font=fonts.small,
            underline=True,
        )
        self.help_link.pack(side="right", padx=metrics.px(8))
        self.settings_link = LinkButton(
            self.right,
            app.tr.t("toolbar.settings"),
            app.show_settings,
            fg=palette.muted,
            hover_fg=palette.accent,
            bg=palette.header_bg,
            font=fonts.small,
            underline=True,
        )
        self.settings_link.pack(side="right", padx=metrics.px(8))
        sep = tk.Frame(self.right, bg=palette.border, width=1, height=metrics.px(14))
        sep.pack(side="right", padx=metrics.px(8))

        self.badge = pill(
            self.right,
            app.tr.t("app.badge_beta"),
            bg=palette.accent_soft,
            fg=palette.accent,
            font=fonts.badge,
            padx=metrics.px(9),
            pady=metrics.px(2),
        )
        self.badge.pack(side="right", padx=(metrics.px(10), 0))

        for widget in (self.help_link, self.settings_link):
            Tooltip(widget, widget.cget("text"))

        self.bind("<Configure>", self._on_configure)

    def _draw_logo(self, size: int) -> None:
        palette = self.palette
        canvas = self.logo_canvas
        canvas.delete("all")
        # litera primary 蓝底白字圆角块
        rounded_rect(canvas, 0, 0, size, size, size * 0.26, fill=palette.accent, outline="")
        canvas.create_text(
            size / 2,
            size / 2 + 1,
            text="亿",
            fill="#ffffff",
            font=(self.fonts.family, max(8, int(size * 0.42)), "bold"),
        )

    def _apply_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.subtitle_label.pack_forget()
        else:
            self.subtitle_label.pack(anchor="w")

    def _on_configure(self, event: tk.Event) -> None:
        # 窗口创建初期可能出现 width=1 的 Configure 事件，此时不要误判成窄窗口
        if event.width <= 1:
            return
        self._apply_compact(event.width < self.metrics.px(980))

    def refresh_compact(self) -> None:
        """布局稳定后按当前实际宽度校正一次（首帧 Configure 事件可能早于真实宽度）。"""
        try:
            width = self.winfo_width()
        except Exception:
            return
        if width <= 1:
            self.after(30, self.refresh_compact)
            return
        self._apply_compact(width < self.metrics.px(980))


# ── 状态栏 ─────────────────────────────────────────────────
class StatusBar(tk.Frame):
    """底部状态栏：状态文字 + 项目信息 + 进度条 / 取消 / 日志开关。"""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        metrics: Metrics = app.metrics
        fonts: UiFonts = app.fonts
        super().__init__(master, bg=palette.surface)
        self.app = app
        self.palette = palette
        self.metrics = metrics
        self.fonts = fonts

        top_line = tk.Frame(self, bg=palette.border, height=1)
        top_line.pack(fill="x", side="top")

        content = tk.Frame(self, bg=palette.surface)
        content.pack(fill="both", expand=True, padx=metrics.pad, pady=(metrics.px(3), metrics.px(3)))

        self.status_label = tk.Label(
            content,
            text=app.tr.t("status.ready_no_project"),
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.small,
            anchor="w",
        )
        self.status_label.pack(side="left")

        self.info_label = tk.Label(
            content,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.small,
            anchor="w",
        )
        self.info_label.pack(side="left", padx=(metrics.px(16), 0))

        # 右侧：日志开关 / 取消 / 进度
        self.log_button = tb.Button(
            content,
            text=app.tr.t("log.show"),
            bootstyle="outline-secondary",
            command=app.toggle_log,
            takefocus=True,
        )
        self.log_button.pack(side="right", padx=(metrics.px(6), 0))
        self.cancel_button = tb.Button(
            content,
            text=app.tr.t("status.cancel"),
            bootstyle="danger-outline",
            command=app.cancel_task,
            takefocus=True,
        )
        self.cancel_button.pack(side="right", padx=(metrics.px(6), 0))
        self.progress = tb.Progressbar(
            content,
            orient="horizontal",
            mode="indeterminate",
            length=metrics.px(190),
            bootstyle="info-striped",
        )
        self.progress.pack(side="right", padx=(metrics.px(10), 0))

        self.cancel_button.pack_forget()
        self.progress.pack_forget()
        self._busy = False
        self._level_colors = {
            "normal": palette.muted,
            "info": palette.text,
            "success": palette.success,
            "warning": palette.warning,
            "error": palette.danger,
        }

    # ── 状态 ─────────────────────────────────────────────
    def set_message(self, text: str, level: str = "normal") -> None:
        color = self._level_colors.get(level, self.palette.muted)
        self.status_label.configure(text=text, fg=color)

    def set_info(self, text: str) -> None:
        self.info_label.configure(text=text)

    def set_busy(self, busy: bool, *, indeterminate: bool = True) -> None:
        if busy == self._busy:
            if busy and indeterminate != self._indeterminate:
                self._start_progress(indeterminate)
            return
        self._busy = busy
        self._indeterminate = indeterminate
        if busy:
            self.progress.pack(side="right", padx=(self.metrics.px(10), 0), before=self.cancel_button)
            self.cancel_button.pack(side="right", padx=(self.metrics.px(6), 0))
            self._start_progress(indeterminate)
        else:
            try:
                self.progress.stop()
            except Exception:
                pass
            self.progress.pack_forget()
            self.cancel_button.pack_forget()

    def _start_progress(self, indeterminate: bool) -> None:
        progress = self.progress
        try:
            progress.stop()
        except Exception:
            pass
        if indeterminate:
            progress.configure(mode="indeterminate")
            progress.start(14)
        else:
            progress.configure(mode="determinate", value=0)

    def set_progress(self, fraction: float | None) -> None:
        if fraction is None:
            if self.progress.cget("mode") != "indeterminate":
                self._start_progress(True)
            return
        try:
            if self.progress.cget("mode") != "determinate":
                self.progress.stop()
                self.progress.configure(mode="determinate")
            self.progress.configure(value=max(0.0, min(1.0, fraction)) * 100)
        except Exception:
            pass

    def set_cancel_enabled(self, enabled: bool) -> None:
        try:
            self.cancel_button.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def set_log_text(self, text: str) -> None:
        self.log_button.configure(text=text)

    def set_theme(self, palette: Palette) -> None:
        """主题切换后刷新自绘颜色（若未整体重建 UI）。"""
        self.palette = palette
        self.configure(bg=palette.surface)
        self.status_label.configure(bg=palette.surface)
        self.info_label.configure(bg=palette.surface)

    @property
    def busy(self) -> bool:
        return self._busy


# ── 可折叠日志面板 ─────────────────────────────────────────
class LogPanel(tk.Frame):
    """底部可折叠日志面板：INFO/WARN/ERROR 分级着色、复制、清空、保存。"""

    LEVELS = ("INFO", "WARNING", "ERROR")

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        metrics: Metrics = app.metrics
        fonts: UiFonts = app.fonts
        super().__init__(master, bg=palette.log_bg)
        self.app = app
        self.palette = palette
        self.metrics = metrics
        self.fonts = fonts
        self.records: list[tuple[str, str, str]] = []  # (时间, 级别, 文本)
        self.visible = False

        self.top_line = tk.Frame(self, bg=palette.border, height=1)
        self.top_line.pack(fill="x", side="top")

        self.header = tk.Frame(self, bg=palette.surface_alt)
        self.header.pack(fill="x")
        self.toggle_link = LinkButton(
            self.header,
            app.tr.t("log.show") + "  ▴",
            self.toggle,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface_alt,
            font=fonts.small,
        )
        self.toggle_link.pack(side="left", padx=(metrics.pad, 0), pady=metrics.px(3))
        self.count_label = tk.Label(
            self.header,
            text="",
            bg=palette.surface_alt,
            fg=palette.muted,
            font=fonts.tiny,
        )
        self.count_label.pack(side="left", padx=(metrics.px(8), 0))

        self.clear_link = LinkButton(
            self.header,
            app.tr.t("log.clear"),
            self.clear,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface_alt,
            font=fonts.small,
        )
        self.clear_link.pack(side="right", padx=(metrics.px(10), metrics.pad), pady=metrics.px(3))
        self.save_link = LinkButton(
            self.header,
            app.tr.t("log.save"),
            self.save_dialog,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface_alt,
            font=fonts.small,
        )
        self.save_link.pack(side="right", padx=metrics.px(10), pady=metrics.px(3))
        self.copy_link = LinkButton(
            self.header,
            app.tr.t("log.copy"),
            self.copy_to_clipboard,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface_alt,
            font=fonts.small,
        )
        self.copy_link.pack(side="right", padx=metrics.px(10), pady=metrics.px(3))

        self.body = tk.Frame(self, bg=palette.log_bg)
        self.text = tk.Text(
            self.body,
            height=metrics.log_height_lines,
            bg=palette.log_bg,
            fg=palette.log_fg,
            insertbackground=palette.log_fg,
            font=fonts.mono,
            wrap="word",
            bd=0,
            relief="flat",
            padx=metrics.px(10),
            pady=metrics.px(6),
            state="disabled",
        )
        self.scrollbar = tb.Scrollbar(self.body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=self.scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.text.tag_configure("INFO", foreground=palette.log_info)
        self.text.tag_configure("WARNING", foreground=palette.log_warn)
        self.text.tag_configure("ERROR", foreground=palette.log_error)
        self.text.tag_configure("TIME", foreground=palette.muted)
        self.text.tag_configure("BODY", foreground=palette.log_fg)

        self.body.pack_forget()
        self.set_visible(False)

    # ── 内容 ─────────────────────────────────────────────
    def append(self, level: str, message: str, *, timestamp: str | None = None) -> None:
        level = level.upper() if level else "INFO"
        if level not in self.LEVELS:
            level = "INFO"
        stamp = timestamp or datetime.now().strftime("%H:%M:%S")
        self.records.append((stamp, level, message))
        self.text.configure(state="normal")
        prefix = f"[{stamp}] "
        self.text.insert("end", prefix, ("TIME",))
        self.text.insert("end", f"{level:<7}", (level,))
        self.text.insert("end", message + "\n", ("BODY",))
        self.text.see("end")
        self.text.configure(state="disabled")
        self._update_count()

    def _update_count(self) -> None:
        errors = sum(1 for _t, level, _m in self.records if level == "ERROR")
        warnings = sum(1 for _t, level, _m in self.records if level == "WARNING")
        parts = []
        if errors:
            parts.append(f"{errors} 错误")
        if warnings:
            parts.append(f"{warnings} 警告")
        text = " · ".join(parts)
        self.count_label.configure(text=text, fg=self.palette.danger if errors else self.palette.muted)

    def clear(self) -> None:
        self.records.clear()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._update_count()
        self.app.log("INFO", self.app.tr.t("log.cleared"))

    def text_content(self) -> str:
        return "\n".join(f"[{stamp}] {level:<7}{message}" for stamp, level, message in self.records)

    def copy_to_clipboard(self) -> None:
        content = self.text_content()
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self.app.set_status(self.app.tr.t("log.copied"), "success")
        except Exception:
            pass

    def save_dialog(self) -> None:
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title=self.app.tr.t("log.save"),
            defaultextension=".log",
            filetypes=[("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")],
            initialfile="logobuilder.log",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.text_content(), encoding="utf-8")
            self.app.set_status(self.app.tr.t("log.saved", path=path), "success")
        except OSError as exc:
            self.app.log("ERROR", f"save log failed: {exc}")
            self.app.show_error(exc, title=self.app.tr.t("error.save_log"))

    # ── 展开 / 收起 ──────────────────────────────────────
    def toggle(self) -> None:
        self.set_visible(not self.visible)

    def set_visible(self, visible: bool) -> None:
        self.visible = bool(visible)
        if self.visible:
            self.body.pack(fill="both", expand=True)
            self.toggle_link.set_text(self.app.tr.t("log.hide") + "  ▾")
        else:
            self.body.pack_forget()
            self.toggle_link.set_text(self.app.tr.t("log.show") + "  ▴")
        try:
            self.app.status_bar.set_log_text(
                self.app.tr.t("log.hide") if self.visible else self.app.tr.t("log.show")
            )
        except Exception:
            pass

    def ensure_visible(self) -> None:
        if not self.visible:
            self.set_visible(True)

    def update_texts(self) -> None:
        """语言切换后刷新静态文案（若未整体重建）。"""
        tr = self.app.tr
        self.copy_link.set_text(tr.t("log.copy"))
        self.clear_link.set_text(tr.t("log.clear"))
        self.save_link.set_text(tr.t("log.save"))
        self.set_visible(self.visible)
