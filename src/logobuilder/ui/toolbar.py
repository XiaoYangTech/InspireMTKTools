"""主工具栏：按钮按窗口宽度自动折行，窄窗口下不会有按钮被裁掉。

单独成模块（而不是塞进 widgets.py）的原因：折行布局逻辑稍复杂，单独可测。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Any

import ttkbootstrap as tb

from .theme import Metrics, Palette
from .widgets import Tooltip

# (翻译 key, 主窗口回调名, bootstyle, 分组号)
BUTTON_SPECS: tuple[tuple[str, str, str, int], ...] = (
    ("toolbar.open_image", "open_image_dialog", "outline-primary", 0),
    ("toolbar.new_project", "new_project_dialog", "outline-primary", 0),
    ("toolbar.open_project", "open_project_dialog", "outline-primary", 0),
    ("toolbar.replace_image", "replace_image_dialog", "primary", 1),
    ("toolbar.restore_image", "restore_selected_image", "outline-secondary", 1),
    ("toolbar.export_img", "export_logo_img", "outline-secondary", 2),
    ("toolbar.pack_bin", "export_logo_bin", "outline-secondary", 2),
    ("toolbar.make_zip", "export_update_zip", "outline-secondary", 2),
    ("toolbar.boot_logo", "show_boot_logo_dialog", "outline-secondary", 2),
    ("toolbar.lk_patch", "show_lk_panel", "outline-primary", 3),
)


class Toolbar(tk.Frame):
    """工具按钮行；``relayout()`` 在窗口尺寸变化时按需折行。"""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        metrics: Metrics = app.metrics
        super().__init__(master, bg=palette.surface)
        self.app = app
        self.palette = palette
        self.metrics = metrics
        self.buttons: dict[str, Any] = {}
        self._separators: list[tk.Frame] = []
        self._enabled: dict[str, bool] = {}
        self._last_width = -1
        self._relayout_after: str | None = None
        self._inner = tk.Frame(self, bg=palette.surface)
        self._inner.pack(fill="x", padx=metrics.pad, pady=(0, metrics.px(2)))
        self._measure_font = tkfont.Font(
            root=self, family=app.fonts.family, size=int(round(app.fonts.base_pt))
        )
        self._build_buttons()
        self.bind("<Configure>", self._on_configure)
        self.after_idle(self.relayout)

    # ── 构建 ─────────────────────────────────────────────
    def _build_buttons(self) -> None:
        tr = self.app.tr
        for key, callback_name, bootstyle, group in BUTTON_SPECS:
            command = getattr(self.app, callback_name, None)
            button = tb.Button(
                self._inner,
                text=tr.t(key),
                bootstyle=bootstyle,
                command=command,
                takefocus=True,
            )
            self.buttons[callback_name] = button
            self._enabled[callback_name] = True
            Tooltip(button, tr.t(key))

    def _measure(self, callback_name: str) -> int:
        button = self.buttons[callback_name]
        try:
            width = button.winfo_reqwidth()
            if width > 1:
                return width
        except Exception:
            pass
        # 尚未映射时按文字宽度估算
        text = button.cget("text")
        try:
            text_width = self._measure_font.measure(str(text))
        except Exception:
            text_width = len(str(text)) * 12
        return text_width + self.metrics.px(40)

    # ── 折行 ─────────────────────────────────────────────
    def _row_plan(self, available: int) -> list[list[tuple[str, bool]]]:
        gap = self.metrics.px(7)
        sep_gap = self.metrics.px(14)
        rows: list[list[tuple[str, bool]]] = [[]]
        used = 0
        previous_group: int | None = None
        for key, callback_name, _style, group in BUTTON_SPECS:
            separator = previous_group is not None and group != previous_group
            need = self._measure(callback_name) + gap + (sep_gap if separator else 0)
            if used + need > available and rows[-1]:
                rows.append([])
                used = 0
                separator = False
            rows[-1].append((callback_name, separator))
            used += need
            previous_group = group
        return rows

    def relayout(self) -> None:
        width = self.winfo_width()
        if width <= 1:
            width = self.master.winfo_width() if self.master is not None else width
        if width <= 1:
            self._schedule_relayout()
            return
        self._last_width = width
        available = max(self.metrics.px(180), width - self.metrics.pad * 2)
        rows = self._row_plan(available)
        inner = self._inner
        for child in inner.winfo_children():
            child.grid_forget()
        for separator in self._separators:
            try:
                separator.destroy()
            except Exception:
                pass
        self._separators.clear()
        gap = self.metrics.px(7)
        for row_index, row in enumerate(rows):
            column = 0
            for callback_name, needs_separator in row:
                if needs_separator:
                    separator = tk.Frame(
                        inner,
                        bg=self.palette.border,
                        width=1,
                        height=self.metrics.px(19),
                    )
                    separator.grid(
                        row=row_index,
                        column=column,
                        padx=self.metrics.px(7),
                        pady=self.metrics.px(8),
                        sticky="ns",
                    )
                    self._separators.append(separator)
                    column += 1
                button = self.buttons[callback_name]
                button.grid(
                    row=row_index,
                    column=column,
                    padx=(0, gap),
                    pady=(self.metrics.px(3), self.metrics.px(2)),
                    sticky="w",
                )
                column += 1
        self._inner.grid_columnconfigure(len(rows[0]) * 2, weight=1)

    def _schedule_relayout(self) -> None:
        if self._relayout_after is not None:
            return
        try:
            self._relayout_after = self.after(40, self._do_scheduled)
        except Exception:
            self._relayout_after = None

    def _do_scheduled(self) -> None:
        self._relayout_after = None
        self.relayout()

    def _on_configure(self, event: tk.Event) -> None:
        width = event.width
        if width <= 1:
            return
        if self._last_width > 0 and abs(width - self._last_width) < self.metrics.px(24):
            return
        self._schedule_relayout()

    # ── 状态 ─────────────────────────────────────────────
    def set_enabled(self, enabled: bool) -> None:
        for callback_name, button in self.buttons.items():
            state = "normal" if enabled else "disabled"
            try:
                button.configure(state=state)
            except Exception:
                pass

    def set_action_enabled(self, callback_name: str, enabled: bool) -> None:
        self._enabled[callback_name] = bool(enabled)
        button = self.buttons.get(callback_name)
        if button is not None:
            try:
                button.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

    def button_by_action(self, callback_name: str) -> Any:
        return self.buttons.get(callback_name)

    def button_count(self) -> int:
        return len(self.buttons)

    def row_count(self) -> int:
        """当前排成了几行（测试用）。"""
        rows = {int(button.grid_info().get("row", 0)) for button in self.buttons.values() if button.winfo_ismapped()}
        return len(rows) if rows else 0
