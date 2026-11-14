"""所有对话框：分辨率选择、替换图片、错误详情、设置、帮助、关于、第二屏。

要点（对应原版缺陷）：
* 分辨率选择（B1/B6）：候选列表 + 置信度 + 判定依据 + 实时预览 + 自定义 W×H×位深，
  并支持「应用到所有相同大小的块」和「记住选择」；
* 错误对话框（B8）：中文可读信息 + 建议 + 可展开/复制/保存的完整 traceback；
* 所有对话框都随主题调色板绘制，支持高 DPI（尺寸全部经过 Metrics 缩放）。

对话框本身不阻塞构造（``show_modal()`` 才进入 modal 循环），便于截图与单元测试。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import ttkbootstrap as tb
from PIL import Image

from . import core_bridge, session as session_mod
from .imaging import (
    fit_scale,
    make_checkerboard,
    make_thumbnail,
    scaled_size,
    to_photo,
)
from .theme import Metrics, Palette, UiFonts
from .widgets import LinkButton, Tooltip


# ── 基类 ───────────────────────────────────────────────────
class BaseDialog(tk.Toplevel):
    """统一风格的模态对话框基类（不自动等待，调用 :meth:`show_modal`）。"""

    def __init__(
        self,
        app: Any,
        title: str,
        *,
        resizable: tuple[bool, bool] = (False, False),
        modal: bool = True,
    ) -> None:
        palette: Palette = app.palette
        super().__init__(app.root)
        self.app = app
        self.palette = palette
        self.metrics: Metrics = app.metrics
        self.fonts: UiFonts = app.fonts
        self.result: Any = None
        self._modal = modal
        self.title(title)
        self.configure(bg=palette.window_bg)
        self.resizable(*resizable)
        self.transient(app.root)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self._on_close())
        # 图标与主窗口一致由 ttkbootstrap 默认处理
        self.body = tk.Frame(self, bg=palette.window_bg)
        self.body.pack(fill="both", expand=True, padx=self.metrics.pad, pady=(self.metrics.pad, 0))
        self.button_bar = tk.Frame(self, bg=palette.window_bg)
        self.button_bar.pack(fill="x", padx=self.metrics.pad, pady=self.metrics.pad)
        self._buttons: list[tb.Button] = []
        self._centered = False
        try:
            self.withdraw()
        except Exception:
            pass

    # ── 按钮 / 布局 ───────────────────────────────────────
    def add_button(
        self,
        text: str,
        command: Callable[[], Any] | None = None,
        *,
        bootstyle: str = "secondary",
        side: str = "right",
        default: bool = False,
    ) -> tb.Button:
        button = tb.Button(
            self.button_bar,
            text=text,
            bootstyle=bootstyle,
            command=command or (lambda: None),
            takefocus=True,
            default="active" if default else "normal",
        )
        button.pack(side=side, padx=(self.metrics.px(6), 0) if side == "right" else (0, self.metrics.px(6)))
        self._buttons.append(button)
        return button

    def separator(self) -> None:
        tk.Frame(self.body, bg=self.palette.border, height=1).pack(fill="x", pady=self.metrics.px(8))

    def section_title(self, text: str) -> tk.Label:
        label = tk.Label(
            self.body,
            text=text,
            bg=self.palette.window_bg,
            fg=self.palette.text,
            font=self.fonts.h3,
            anchor="w",
        )
        label.pack(fill="x", pady=(self.metrics.px(6), self.metrics.px(3)))
        return label

    def hline(self, master: tk.Misc | None = None) -> tk.Frame:
        line = tk.Frame(master or self.body, bg=self.palette.border, height=1)
        line.pack(fill="x", pady=self.metrics.px(6))
        return line

    def label(self, text: str, *, muted: bool = False, wrap: int | None = None, master: tk.Misc | None = None) -> tk.Label:
        label = tk.Label(
            master or self.body,
            text=text,
            bg=self.palette.window_bg,
            fg=self.palette.muted if muted else self.palette.text,
            font=self.fonts.small if muted else self.fonts.base,
            anchor="w",
            justify="left",
            wraplength=wrap or 0,
        )
        return label

    # ── 显示 / 关闭 ───────────────────────────────────────
    def center(self) -> None:
        self.update_idletasks()
        width = max(self.winfo_reqwidth(), self.metrics.px(360))
        height = max(self.winfo_reqheight(), self.metrics.px(220))
        parent = self.app.root
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
        except Exception:
            px = py = 0
            pw = self.winfo_screenwidth()
            ph = self.winfo_screenheight()
        x = px + max(0, (pw - width) // 2)
        y = py + max(0, (ph - height) // 3)
        x = max(0, min(x, max(0, self.winfo_screenwidth() - width)))
        y = max(0, min(y, max(0, self.winfo_screenheight() - height)))
        self.geometry(f"+{x}+{y}")
        self._centered = True

    def show(self) -> None:
        """显示窗口（截图模式用，不进入 modal 循环）。"""
        self.deiconify()
        self.center()
        self.lift()
        self.update_idletasks()
        self.update()

    def show_modal(self) -> Any:
        self.deiconify()
        self.center()
        try:
            self.grab_set()
        except Exception:
            pass
        self.focus_set()
        try:
            self.wait_window(self)
        except Exception:
            pass
        return self.result

    def _on_close(self) -> None:
        # 注意：不要在这里清空 self.result —— 各「确定/跳过」处理器先写入 result 再调用本方法；
        # 真正的取消路径由调用方显式把 result 置为 None（或保持初始 None）。
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def set_busy(self, busy: bool) -> None:
        for button in self._buttons:
            try:
                button.configure(state="disabled" if busy else "normal")
            except Exception:
                pass


# ── 分辨率选择对话框（B1 / B6）────────────────────────────
@dataclass
class SizeResolution:
    """分辨率选择结果。"""

    size: Any = None
    remember: bool = True
    apply_all: bool = False
    skip: bool = False
    skip_all: bool = False

    @property
    def skipped(self) -> bool:
        return self.skip or self.skip_all


class ResolutionDialog(BaseDialog):
    """未知尺寸块的分辨率选择窗口：候选列表 + 实时预览 + 自定义输入。"""

    def __init__(
        self,
        app: Any,
        *,
        block_index: int,
        decompressed_len: int,
        candidates: list[Any],
        preview_provider: Callable[[Any], Image.Image | None] | None = None,
        same_size_count: int = 0,
        remembered_choice: Any = None,
        subject: str | None = None,
        allowed_depths: tuple[int, ...] = (2, 4),
        default_depth: int = 4,
    ) -> None:
        super().__init__(app, app.tr.t("size_dialog.title"))
        self.block_index = block_index
        self.decompressed_len = int(decompressed_len)
        self.candidates = list(candidates)
        self.preview_provider = preview_provider
        self.same_size_count = max(0, int(same_size_count) - 1) if same_size_count else 0
        self.subject = subject
        self.allowed_depths = tuple(allowed_depths) or (2, 4)
        self.default_depth = default_depth if default_depth in self.allowed_depths else self.allowed_depths[0]
        self.remembered_choice = remembered_choice
        self._preview_photo: Any = None
        self._checker_photo: Any = None
        self._preview_cache: dict[str, Any] = {}
        self._current_size: Any = None
        self._built = False
        self._build()
        self.show()
        self._fit_geometry()

    # ── 构建 ─────────────────────────────────────────────
    def _fit_geometry(self) -> None:
        m = self.metrics
        width = m.px(880)
        height = m.px(560)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        width = min(width, max(m.px(620), screen_w - m.px(20)))
        height = min(height, max(m.px(400), screen_h - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.minsize(m.px(600), m.px(380))
        self.center()

    def _build(self) -> None:
        if self._built:
            return
        self._built = True
        m = self.metrics
        palette = self.palette
        tr = self.app.tr

        header = tk.Frame(self.body, bg=palette.window_bg)
        header.pack(fill="x")
        tk.Label(
            header,
            text=tr.t("size_dialog.header"),
            bg=palette.warning if hasattr(palette, "warning") else palette.accent,
            fg="#ffffff",
            font=self.fonts.h3,
            padx=m.px(10),
            pady=m.px(4),
        ).pack(side="left")
        subject_text = self.subject or tr.t(
            "size_dialog.block_info", index=self.block_index + 1, bytes=f"{self.decompressed_len:,}"
        )
        tk.Label(
            header,
            text=subject_text,
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
        ).pack(side="left", padx=(m.px(10), 0))

        subtitle = tk.Label(
            self.body,
            text=tr.t("size_dialog.subheader", bytes=f"{self.decompressed_len:,}"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(820),
        )
        subtitle.pack(fill="x", pady=(m.px(6), m.px(8)))

        content = tk.Frame(self.body, bg=palette.window_bg)
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=3)
        content.grid_columnconfigure(1, weight=2)
        content.grid_rowconfigure(0, weight=1)

        # 左：候选列表
        left = tk.Frame(content, bg=palette.window_bg)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, m.px(10)))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        tk.Label(
            left,
            text=tr.t("size_dialog.candidates"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, m.px(4)))
        tree_frame = tk.Frame(left, bg=palette.border, highlightthickness=0)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        self.tree = tb.Treeview(
            tree_frame,
            columns=("size", "confidence", "reason"),
            show="headings",
            selectmode="browse",
            bootstyle="primary",
            height=7,
        )
        self.tree.heading("size", text=tr.t("size_dialog.col_size"))
        self.tree.heading("confidence", text=tr.t("size_dialog.col_confidence"))
        self.tree.heading("reason", text=tr.t("size_dialog.col_reason"))
        self.tree.column("size", width=m.px(120), anchor="center", stretch=False)
        self.tree.column("confidence", width=m.px(80), anchor="center", stretch=False)
        self.tree.column("reason", width=m.px(230), anchor="w", stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = tb.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._fill_candidates()

        if not self.candidates:
            tk.Label(
                left,
                text=tr.t("size_dialog.no_candidates"),
                bg=palette.window_bg,
                fg=palette.warning,
                font=self.fonts.small,
                anchor="w",
                justify="left",
                wraplength=m.px(400),
            ).grid(row=2, column=0, sticky="w", pady=(m.px(6), 0))

        # 右：自定义输入 + 预览
        right = tk.Frame(content, bg=palette.window_bg)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        custom = tk.Frame(right, bg=palette.window_bg)
        custom.grid(row=0, column=0, sticky="ew")
        tk.Label(
            custom,
            text=tr.t("size_dialog.custom"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, m.px(4)))
        self.width_var = tk.StringVar(value="")
        self.height_var = tk.StringVar(value="")
        self.bpp_var = tk.StringVar(value="4")
        for col, (label_key, var, width) in enumerate(
            (
                ("size_dialog.width", self.width_var, 7),
                ("size_dialog.height", self.height_var, 7),
            )
        ):
            tk.Label(
                custom,
                text=tr.t(label_key),
                bg=palette.window_bg,
                fg=palette.muted,
                font=self.fonts.small,
            ).grid(row=1, column=col * 2, sticky="w", padx=(0, m.px(4)))
            entry = tb.Spinbox(
                custom,
                textvariable=var,
                from_=1,
                to=8192,
                width=width,
                font=self.fonts.base,
            )
            entry.grid(row=1, column=col * 2 + 1, sticky="w", padx=(0, m.px(10)))
        tk.Label(
            custom,
            text=tr.t("size_dialog.bpp"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
        ).grid(row=1, column=4, sticky="w", padx=(0, m.px(4)))
        depth_labels = []
        for depth in self.allowed_depths:
            depth_labels.append(tr.t("size_dialog.bpp_2") if depth == 2 else tr.t("size_dialog.bpp_4"))
        self.bpp_combo = tb.Combobox(
            custom,
            textvariable=self.bpp_var,
            values=tuple(depth_labels),
            state="readonly",
            width=14,
            font=self.fonts.small,
        )
        self.bpp_combo.grid(row=1, column=5, sticky="w")
        try:
            self.bpp_combo.current(self.allowed_depths.index(self.default_depth))
        except ValueError:
            self.bpp_combo.current(0)
        self.bpp_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_custom_changed())
        for var in (self.width_var, self.height_var):
            var.trace_add("write", lambda *_: self._on_custom_changed())

        self.custom_button = tb.Button(
            custom,
            text=tr.t("size_dialog.validate"),
            bootstyle="outline-secondary",
            command=self._on_custom_changed,
        )
        self.custom_button.grid(row=2, column=0, columnspan=3, sticky="w", pady=(m.px(6), 0))

        self.message_label = tk.Label(
            right,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(300),
        )
        self.message_label.grid(row=1, column=0, sticky="ew", pady=(m.px(4), m.px(6)))

        preview_frame = tk.Frame(right, bg=palette.border)
        preview_frame.grid(row=2, column=0, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)
        self.preview_canvas = tk.Canvas(
            preview_frame,
            bg=palette.preview_bg,
            highlightthickness=0,
            height=m.px(210),
        )
        self.preview_canvas.pack(fill="both", expand=True)
        self.preview_canvas.bind("<Configure>", lambda _e: self._redraw_preview())
        self.preview_caption = tk.Label(
            right,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        )
        self.preview_caption.grid(row=3, column=0, sticky="w", pady=(m.px(3), 0))

        # 选项
        options = tk.Frame(self.body, bg=palette.window_bg)
        options.pack(fill="x", pady=(m.px(8), 0))
        self.apply_all_var = tk.BooleanVar(value=self.same_size_count > 0)
        self.remember_var = tk.BooleanVar(value=True)
        apply_text = tr.t("size_dialog.apply_all")
        if self.same_size_count > 0:
            apply_text += f"（{tr.t('size_dialog.apply_all_hint', count=self.same_size_count)}）"
        self.apply_all_check = tb.Checkbutton(
            options,
            text=apply_text,
            variable=self.apply_all_var,
            bootstyle="round-toggle",
        )
        self.apply_all_check.pack(side="left")
        self.remember_check = tb.Checkbutton(
            options,
            text=tr.t("size_dialog.remember"),
            variable=self.remember_var,
            bootstyle="round-toggle",
        )
        self.remember_check.pack(side="left", padx=(m.px(18), 0))
        Tooltip(self.remember_check, tr.t("size_dialog.remember_hint"))

        # 按钮
        self.add_button(tr.t("size_dialog.cancel_all"), self._on_skip_all, bootstyle="outline-secondary")
        self.add_button(tr.t("size_dialog.cancel"), self._on_skip, bootstyle="outline-secondary")
        self.apply_button = self.add_button(
            tr.t("size_dialog.ok"),
            self._on_apply,
            bootstyle="primary",
            default=True,
        )
        self.bind("<Return>", lambda _e: self._on_apply())

        # 初始化选择
        if self.remembered_choice is not None:
            self._set_size(self.remembered_choice, from_candidate=False)
        if self.candidates:
            self.tree.selection_set(self.tree.get_children()[0])
            first = self.candidates[0]
            self._set_size(getattr(first, "size", first), from_candidate=True)
        else:
            self._on_custom_changed()

    def _fill_candidates(self) -> None:
        tr = self.app.tr
        for candidate in self.candidates:
            size = getattr(candidate, "size", candidate)
            score = getattr(candidate, "score", None)
            reason = getattr(candidate, "reason", "")
            confidence = f"{round(float(score) * 100)}%" if score is not None else "—"
            self.tree.insert(
                "",
                "end",
                values=(session_mod.size_to_text(size), confidence, reason),
                iid=str(len(self.tree.get_children())),
            )

    # ── 交互 ─────────────────────────────────────────────
    def _on_tree_select(self, _event: tk.Event | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if 0 <= index < len(self.candidates):
            candidate = self.candidates[index]
            self._set_size(getattr(candidate, "size", candidate), from_candidate=True)

    def _on_custom_changed(self) -> None:
        width = _to_int(self.width_var.get())
        height = _to_int(self.height_var.get())
        depth_index = self.bpp_combo.current()
        if depth_index < 0 or depth_index >= len(self.allowed_depths):
            depth_index = 0
        bpp = self.allowed_depths[depth_index]
        if width is None or height is None or width <= 0 or height <= 0:
            self._disable_apply(self.app.tr.t("size_dialog.invalid"))
            self._current_size = None
            return
        try:
            size = session_mod.size_from_parts(width, height, bpp)
        except Exception:
            self._disable_apply(self.app.tr.t("size_dialog.invalid"))
            self._current_size = None
            return
        self._set_size(size, from_candidate=False)

    def _set_size(self, size: Any, *, from_candidate: bool) -> None:
        self._current_size = size
        key = session_mod.size_key(size)
        matches = key == self.decompressed_len
        if matches:
            self.message_label.configure(
                text=self.app.tr.t("size_dialog.bytes_exact", need=f"{key:,}"),
                fg=self.palette.success,
            )
            self._enable_apply()
        else:
            self.message_label.configure(
                text=self.app.tr.t(
                    "size_dialog.bytes_mismatch",
                    need=f"{key:,}",
                    have=f"{self.decompressed_len:,}",
                ),
                fg=self.palette.danger,
            )
            self._disable_apply()
        if from_candidate:
            try:
                width = getattr(size, "width", "")
                height = getattr(size, "height", "")
                depth = getattr(size, "color_depth", 4)
                self.width_var.set(str(width))
                self.height_var.set(str(height))
                try:
                    self.bpp_combo.current(self.allowed_depths.index(int(depth)))
                except (ValueError, TypeError):
                    self.bpp_combo.current(0)
            except Exception:
                pass
        self._redraw_preview()

    def _enable_apply(self) -> None:
        try:
            self.apply_button.configure(state="normal")
        except Exception:
            pass

    def _disable_apply(self, message: str = "") -> None:
        try:
            self.apply_button.configure(state="disabled")
        except Exception:
            pass
        if message:
            self.message_label.configure(text=message, fg=self.palette.danger)

    # ── 预览 ─────────────────────────────────────────────
    def _redraw_preview(self) -> None:
        canvas = self.preview_canvas
        size = self._current_size
        try:
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
        except Exception:
            return
        if width <= 2 or height <= 2:
            return
        canvas.delete("all")
        palette = self.palette
        m = self.metrics
        if size is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text=self.app.tr.t("size_dialog.preview_placeholder"),
                fill=palette.muted,
                font=(self.fonts.family, int(round(self.fonts.small_pt))),
            )
            self.preview_caption.configure(text="")
            return
        cache_key = f"{getattr(size, 'width', 0)}x{getattr(size, 'height', 0)}x{session_mod.size_key(size)}"
        image = self._preview_cache.get(cache_key)
        if image is None and self.preview_provider is not None:
            try:
                image = self.preview_provider(size)
            except Exception:
                image = None
            if image is not None:
                if image.mode != "RGBA":
                    image = image.convert("RGBA")
                self._preview_cache[cache_key] = image
        if image is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text=self.app.tr.t("size_dialog.preview_placeholder"),
                fill=palette.muted,
                font=(self.fonts.family, int(round(self.fonts.small_pt))),
            )
            self.preview_caption.configure(text="")
            return
        pad = m.px(10)
        scale = fit_scale(image.size, (max(1, width - pad * 2), max(1, height - pad * 2)))
        display = scaled_size(image.size, scale)
        offset = ((width - display[0]) // 2, (height - display[1]) // 2)
        checker = make_checkerboard(display[0], display[1], palette.checker_a, palette.checker_b, max(4, m.px(8)))
        self._checker_photo = to_photo(self, checker)
        resized = image.resize(display, getattr(Image, "Resampling", Image).LANCZOS)
        self._preview_photo = to_photo(self, resized)
        canvas.create_image(offset[0], offset[1], anchor="nw", image=self._checker_photo)
        canvas.create_image(offset[0], offset[1], anchor="nw", image=self._preview_photo)
        canvas.create_rectangle(
            offset[0] - 1,
            offset[1] - 1,
            offset[0] + display[0] + 1,
            offset[1] + display[1] + 1,
            outline=palette.border,
        )
        percent = round(display[0] / max(1, image.width) * 100)
        self.preview_caption.configure(
            text=f"{session_mod.size_to_text(size, compact=False)} · {image.size[0]}×{image.size[1]}px · 预览 {percent}%"
        )

    # ── 结果 ─────────────────────────────────────────────
    def _on_apply(self) -> None:
        if self._current_size is None:
            return
        if session_mod.size_key(self._current_size) != self.decompressed_len:
            self._disable_apply(
                self.app.tr.t("size_dialog.bytes_mismatch")
                if False
                else self.app.tr.t("size_dialog.invalid")
            )
            return
        self.result = SizeResolution(
            size=self._current_size,
            remember=bool(self.remember_var.get()),
            apply_all=bool(self.apply_all_var.get()),
            skip=False,
            skip_all=False,
        )
        self._on_close()

    def _on_skip(self) -> None:
        self.result = SizeResolution(skip=True)
        self._on_close()

    def _on_skip_all(self) -> None:
        self.result = SizeResolution(skip=True, skip_all=True)
        self._on_close()


def _to_int(text: str) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


# ── 替换图片对话框（B3）────────────────────────────────────
@dataclass
class ReplaceChoice:
    image: Image.Image | None = None
    fit: str = "stretch"


class ReplaceImageDialog(BaseDialog):
    """选择本地图片并预览按目标位深/尺寸转换后的效果。"""

    def __init__(
        self,
        app: Any,
        *,
        block_index: int,
        target_size: Any,
        current_image: Image.Image | None = None,
        frame_hint: str | None = None,
    ) -> None:
        super().__init__(app, app.tr.t("replace.title"))
        self.block_index = block_index
        self.target_size = target_size
        self.current_image = current_image
        self.frame_hint = frame_hint
        self.source_image: Image.Image | None = None
        self.source_path: Path | None = None
        self._original_photo: Any = None
        self._preview_photo: Any = None
        self._checker_photo: Any = None
        self._build()
        self.show()
        self._fit_geometry()

    def _fit_geometry(self) -> None:
        m = self.metrics
        width = min(m.px(820), max(m.px(560), self.winfo_screenwidth() - m.px(20)))
        height = min(m.px(520), max(m.px(360), self.winfo_screenheight() - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.minsize(m.px(520), m.px(340))
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr

        header = tk.Frame(self.body, bg=palette.window_bg)
        header.pack(fill="x")
        tk.Label(
            header,
            text=tr.t("replace.title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text=(
                tr.t(
                    "replace.target",
                    index=self.block_index + 1,
                    size=session_mod.size_to_text(self.target_size, compact=False),
                )
                + (f" · {self.frame_hint}" if self.frame_hint else "")
            ),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
        ).pack(side="left", padx=(m.px(10), 0))

        chooser = tk.Frame(self.body, bg=palette.window_bg)
        chooser.pack(fill="x", pady=(m.px(8), m.px(2)))
        self.choose_button = tb.Button(
            chooser,
            text=tr.t("replace.choose"),
            bootstyle="primary",
            command=self.choose_file,
        )
        self.choose_button.pack(side="left")
        self.source_label = tk.Label(
            chooser,
            text=tr.t("replace.choose_hint"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        )
        self.source_label.pack(side="left", padx=(m.px(10), 0))

        fit_frame = tk.Frame(self.body, bg=palette.window_bg)
        fit_frame.pack(fill="x", pady=(m.px(4), m.px(6)))
        tk.Label(
            fit_frame,
            text=tr.t("replace.fit"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
        ).pack(side="left", padx=(0, m.px(10)))
        self.fit_var = tk.StringVar(value=self.app.settings.default_fit)
        for value, label_key in session_mod.FIT_LABEL_KEYS.items():
            tb.Radiobutton(
                fit_frame,
                text=tr.t(label_key),
                value=value,
                variable=self.fit_var,
                bootstyle="primary",
                command=self._update_preview,
            ).pack(side="left", padx=(0, m.px(12)))

        previews = tk.Frame(self.body, bg=palette.window_bg)
        previews.pack(fill="both", expand=True)
        previews.grid_columnconfigure(0, weight=1)
        previews.grid_columnconfigure(1, weight=1)
        previews.grid_rowconfigure(1, weight=1)
        tk.Label(
            previews,
            text=tr.t("replace.preview_original"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            previews,
            text=tr.t("replace.preview_new"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(m.px(8), 0))
        self.original_canvas = tk.Canvas(
            previews, bg=palette.preview_bg, highlightthickness=0, height=m.px(200)
        )
        self.original_canvas.grid(row=1, column=0, sticky="nsew", pady=(m.px(3), 0))
        self.new_canvas = tk.Canvas(
            previews, bg=palette.preview_bg, highlightthickness=0, height=m.px(200)
        )
        self.new_canvas.grid(row=1, column=1, sticky="nsew", pady=(m.px(3), 0), padx=(m.px(8), 0))
        for canvas in (self.original_canvas, self.new_canvas):
            canvas.bind("<Configure>", self._on_preview_configure)
        self._draw_original()

        self.add_button(tr.t("replace.cancel"), self._on_cancel, bootstyle="outline-secondary")
        self.apply_button = self.add_button(
            tr.t("replace.ok"), self._on_apply, bootstyle="primary", default=True
        )
        try:
            self.apply_button.configure(state="disabled")
        except Exception:
            pass

    # ── 选择文件 ─────────────────────────────────────────
    def choose_file(self) -> None:
        initial = self.app.settings.last_open_dir or str(Path.home())
        path = filedialog.askopenfilename(
            parent=self,
            title=self.app.tr.t("replace.choose"),
            initialdir=initial,
            filetypes=[
                (self.app.tr.t("open.filter_image"), "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
                (self.app.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str | Path) -> None:
        source = Path(path)
        try:
            with Image.open(source) as image:
                image.load()
                self.source_image = image.copy()
        except Exception as exc:
            self.source_label.configure(
                text=self.app.tr.t("replace.load_failed", error=exc),
                fg=self.palette.danger,
            )
            self.source_image = None
            self._draw_original()
            return
        self.source_path = source
        self.source_label.configure(
            text=self.app.tr.t(
                "replace.source_info",
                name=source.name,
                width=self.source_image.width,
                height=self.source_image.height,
            ),
            fg=self.palette.text,
        )
        try:
            self.apply_button.configure(state="normal")
        except Exception:
            pass
        self._update_preview()

    # ── 预览 ─────────────────────────────────────────────
    def _on_preview_configure(self, _event: tk.Event | None = None) -> None:
        self._draw_original()
        self._update_preview()

    def _draw_original(self) -> None:
        self._draw_image_in_canvas(self.original_canvas, self.current_image)

    def _update_preview(self) -> None:
        if self.source_image is None:
            self._draw_image_in_canvas(self.new_canvas, None, placeholder=self.app.tr.t("replace.no_image"))
            return
        image = self._convert_preview()
        self._draw_image_in_canvas(self.new_canvas, image)

    def _convert_preview(self) -> Image.Image | None:
        try:
            core = core_bridge.get_core()
            fit = self.fit_var.get() or "stretch"
            pixels = core.image_to_pixels(self.source_image, self.target_size, fit=fit)
            return core.pixels_to_image(pixels, self.target_size)
        except Exception:
            return self.source_image

    def _draw_image_in_canvas(
        self,
        canvas: tk.Canvas,
        image: Image.Image | None,
        *,
        placeholder: str | None = None,
    ) -> None:
        try:
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
        except Exception:
            return
        if width <= 2:
            return
        canvas.delete("all")
        palette = self.palette
        m = self.metrics
        if image is None:
            canvas.create_text(
                width / 2,
                height / 2,
                text=placeholder or "",
                fill=palette.muted,
                font=(self.fonts.family, int(round(self.fonts.small_pt))),
            )
            return
        pad = m.px(8)
        scale = fit_scale(image.size, (max(1, width - pad * 2), max(1, height - pad * 2)))
        display = scaled_size(image.size, scale)
        offset = ((width - display[0]) // 2, (height - display[1]) // 2)
        checker = make_checkerboard(display[0], display[1], palette.checker_a, palette.checker_b, max(4, m.px(8)))
        photo_ref = to_photo(self, checker)
        resized = image.resize(display, getattr(Image, "Resampling", Image).LANCZOS)
        photo = to_photo(self, resized)
        if canvas is self.original_canvas:
            self._checker_photo = photo_ref
            self._original_photo = photo
        else:
            self._preview_photo = photo
        canvas.create_image(offset[0], offset[1], anchor="nw", image=photo_ref)
        canvas.create_image(offset[0], offset[1], anchor="nw", image=photo)
        canvas.create_rectangle(
            offset[0] - 1,
            offset[1] - 1,
            offset[0] + display[0] + 1,
            offset[1] + display[1] + 1,
            outline=palette.border,
        )

    # ── 结果 ─────────────────────────────────────────────
    def _on_apply(self) -> None:
        if self.source_image is None:
            return
        self.result = ReplaceChoice(image=self.source_image, fit=self.fit_var.get() or "stretch")
        self._on_close()

    def _on_cancel(self) -> None:
        self.result = None
        self._on_close()


# ── 错误对话框（B8）────────────────────────────────────────
class ErrorDialog(BaseDialog):
    """中文可读错误 + 可选建议 + 可展开/复制的技术详情。"""

    def __init__(
        self,
        app: Any,
        *,
        title: str | None = None,
        message: str,
        hint: str | None = None,
        traceback_text: str = "",
        context: str | None = None,
    ) -> None:
        super().__init__(app, title or app.tr.t("error.title"), resizable=(True, True))
        self.message = message
        self.hint = hint
        self.traceback_text = traceback_text
        self.context = context
        self._details_visible = False
        self._build()
        self.show()
        self._fit_geometry()

    def _fit_geometry(self) -> None:
        m = self.metrics
        width = min(m.px(720), max(m.px(460), self.winfo_screenwidth() - m.px(20)))
        self.update_idletasks()
        wanted = max(m.px(240), min(self.winfo_reqheight() + m.px(8), m.px(560)))
        height = min(wanted, max(m.px(280), self.winfo_screenheight() - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr

        head = tk.Frame(self.body, bg=palette.window_bg)
        head.pack(fill="x")
        badge = tk.Label(
            head,
            text="!",
            bg=palette.danger,
            fg="#ffffff",
            font=self.fonts.h2,
            width=2,
            pady=m.px(2),
        )
        badge.pack(side="left")
        text_frame = tk.Frame(head, bg=palette.window_bg)
        text_frame.pack(side="left", fill="x", expand=True, padx=(m.px(10), 0))
        tk.Label(
            text_frame,
            text=tr.t("error.summary"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            text_frame,
            text=self.message,
            bg=palette.window_bg,
            fg=palette.danger,
            font=self.fonts.base,
            anchor="w",
            justify="left",
            wraplength=m.px(600),
        ).pack(fill="x", pady=(m.px(2), 0))

        if self.hint:
            tk.Label(
                self.body,
                text=tr.t("error.hint", hint=self.hint),
                bg=palette.window_bg,
                fg=palette.muted,
                font=self.fonts.small,
                anchor="w",
                justify="left",
                wraplength=m.px(600),
            ).pack(fill="x", pady=(m.px(8), 0))

        if self.context:
            tk.Label(
                self.body,
                text=self.context,
                bg=palette.window_bg,
                fg=palette.muted,
                font=self.fonts.tiny,
                anchor="w",
            ).pack(fill="x", pady=(m.px(4), 0))

        # 详情区
        toggle_row = tk.Frame(self.body, bg=palette.window_bg)
        toggle_row.pack(fill="x", pady=(m.px(10), m.px(4)))
        self.details_link = LinkButton(
            toggle_row,
            "▸ " + tr.t("error.details"),
            self.toggle_details,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.window_bg,
            font=self.fonts.small,
        )
        self.details_link.pack(side="left")
        self.copy_link = LinkButton(
            toggle_row,
            tr.t("error.copy"),
            self.copy_details,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.window_bg,
            font=self.fonts.small,
        )
        self.copy_link.pack(side="right")
        self.save_link = LinkButton(
            toggle_row,
            tr.t("error.save_log"),
            self.save_log,
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.window_bg,
            font=self.fonts.small,
        )
        self.save_link.pack(side="right", padx=(0, m.px(12)))

        self.details_frame = tk.Frame(self.body, bg=palette.log_bg)
        self.details_text = tk.Text(
            self.details_frame,
            height=8,
            bg=palette.log_bg,
            fg=palette.log_fg,
            font=self.fonts.mono,
            wrap="word",
            bd=0,
            relief="flat",
            padx=m.px(8),
            pady=m.px(6),
            state="disabled",
        )
        scroll = tb.Scrollbar(self.details_frame, orient="vertical", command=self.details_text.yview)
        self.details_text.configure(yscrollcommand=scroll.set)
        self.details_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.details_text.configure(state="normal")
        self.details_text.insert(
            "1.0",
            self.traceback_text or f"{self.message}\n\n（没有更多技术细节）",
        )
        self.details_text.configure(state="disabled")
        self._details_frame_packed = False
        if self.traceback_text:
            self.toggle_details()  # 有技术详情时默认展开，方便用户直接复制

        self.add_button(tr.t("error.close"), self._on_close, bootstyle="primary", default=True)

    def toggle_details(self) -> None:
        self._details_visible = not self._details_visible
        if self._details_visible:
            self.details_frame.pack(fill="both", expand=True, pady=(m := self.metrics.px(4), 0))
            self.details_link.set_text("▾ " + self.app.tr.t("error.details"))
        else:
            self.details_frame.pack_forget()
            self.details_link.set_text("▸ " + self.app.tr.t("error.details"))

    def copy_details(self) -> None:
        content = self.traceback_text or self.message
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self.app.set_status(self.app.tr.t("error.copied"), "success")
        except Exception:
            pass

    def save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title=self.app.tr.t("error.save_log"),
            defaultextension=".txt",
            initialfile="logobuilder-error.txt",
            filetypes=[("文本文件", "*.txt"), (self.app.tr.t("open.filter_all"), "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(self.traceback_text or self.message, encoding="utf-8")
            self.app.set_status(self.app.tr.t("log.saved", path=path), "success")
        except OSError as exc:
            self.app.log("ERROR", f"save error log failed: {exc}")


# ── 通用成功对话框 ─────────────────────────────────────────
class SuccessDialog(BaseDialog):
    def __init__(
        self,
        app: Any,
        *,
        title: str,
        path: Path,
        lines: list[str] | None = None,
    ) -> None:
        super().__init__(app, title)
        palette = self.palette
        m = self.metrics
        tr = app.tr
        head = tk.Frame(self.body, bg=palette.window_bg)
        head.pack(fill="x")
        tk.Label(
            head,
            text="✓",
            bg=palette.success,
            fg="#ffffff",
            font=self.fonts.h2,
            width=2,
            pady=m.px(2),
        ).pack(side="left")
        tk.Label(
            head,
            text=title,
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(side="left", padx=(m.px(10), 0))
        tk.Label(
            self.body,
            text=tr.t("export.success_body", path=str(path)),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.base,
            anchor="w",
            justify="left",
            wraplength=m.px(560),
        ).pack(fill="x", pady=(m.px(10), m.px(2)))
        for line in lines or []:
            tk.Label(
                self.body,
                text=line,
                bg=palette.window_bg,
                fg=palette.muted,
                font=self.fonts.small,
                anchor="w",
            ).pack(fill="x")
        self.add_button(tr.t("export.close"), self._on_close, bootstyle="secondary")
        self.add_button(
            tr.t("export.open_folder"),
            lambda: self.app.reveal_path(path),
            bootstyle="primary",
            default=True,
        )
        self.show()
        self._fit_geometry()

    def _fit_geometry(self) -> None:
        self.geometry(f"{self.metrics.px(520)}x+0+0".split("+")[0] if False else "")
        self.update_idletasks()
        width = max(self.metrics.px(480), self.winfo_reqwidth())
        self.geometry(f"{width}x{self.winfo_reqheight()}")
        self.center()


# ── 设置对话框 ─────────────────────────────────────────────
class SettingsDialog(BaseDialog):
    def __init__(self, app: Any) -> None:
        super().__init__(app, app.tr.t("settings.title"))
        self.settings = app.settings
        self._build()
        self.show()
        self._fit_geometry()

    def _fit_geometry(self) -> None:
        self.update_idletasks()
        height = max(self.metrics.px(360), min(self.metrics.px(520), self.winfo_reqheight() + self.metrics.px(16)))
        self.geometry(f"{self.metrics.px(620)}x{height}")
        self.minsize(self.metrics.px(520), self.metrics.px(340))
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr

        # 图像
        self.section_title(tr.t("settings.image_defaults"))
        fit_row = tk.Frame(self.body, bg=palette.window_bg)
        fit_row.pack(fill="x", padx=m.px(2))
        tk.Label(
            fit_row,
            text=tr.t("settings.default_fit"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            width=12,
            anchor="w",
        ).pack(side="left")
        self.fit_var = tk.StringVar(value=self.settings.default_fit)
        for value, label_key in session_mod.FIT_LABEL_KEYS.items():
            tb.Radiobutton(
                fit_row,
                text=tr.t(label_key),
                value=value,
                variable=self.fit_var,
                bootstyle="primary",
            ).pack(side="left", padx=(0, m.px(8)))

        # 导出
        self.section_title(tr.t("settings.export"))
        padding_row = tk.Frame(self.body, bg=palette.window_bg)
        padding_row.pack(fill="x", padx=m.px(2))
        tk.Label(
            padding_row,
            text=tr.t("settings.padding_byte"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            width=12,
            anchor="w",
        ).pack(side="left")
        self.padding_var = tk.IntVar(value=self.settings.padding_byte)
        tb.Radiobutton(
            padding_row,
            text=tr.t("settings.padding_00"),
            value=0,
            variable=self.padding_var,
            bootstyle="primary",
        ).pack(side="left")
        tb.Radiobutton(
            padding_row,
            text=tr.t("settings.padding_ff"),
            value=255,
            variable=self.padding_var,
            bootstyle="primary",
        ).pack(side="left", padx=(m.px(10), 0))

        options = tk.Frame(self.body, bg=palette.window_bg)
        options.pack(fill="x", padx=m.px(2), pady=(m.px(10), 0))
        self.geometry_var = tk.BooleanVar(value=self.settings.remember_geometry)
        tb.Checkbutton(
            options,
            text=tr.t("settings.remember_geometry"),
            variable=self.geometry_var,
            bootstyle="round-toggle",
        ).pack(anchor="w")
        self.log_var = tk.BooleanVar(value=self.settings.show_log_on_error)
        tb.Checkbutton(
            options,
            text=tr.t("settings.show_log"),
            variable=self.log_var,
            bootstyle="round-toggle",
        ).pack(anchor="w", pady=(m.px(4), 0))

        # 分辨率记忆
        self.section_title(tr.t("settings.sizes"))
        tk.Label(
            self.body,
            text=tr.t("settings.sizes_desc"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(560),
        ).pack(fill="x", padx=m.px(2))
        size_buttons = tk.Frame(self.body, bg=palette.window_bg)
        size_buttons.pack(fill="x", padx=m.px(2), pady=(m.px(4), 0))
        tb.Button(
            size_buttons,
            text=tr.t("settings.clear_sizes"),
            bootstyle="outline-danger",
            command=self._clear_sizes,
        ).pack(side="left")
        self.sizes_status = tk.Label(
            size_buttons,
            text="",
            bg=palette.window_bg,
            fg=palette.success,
            font=self.fonts.small,
        )
        self.sizes_status.pack(side="left", padx=(m.px(10), 0))
        tk.Label(
            self.body,
            text=tr.t("settings.config_dir", path=self.app.config_dir),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        ).pack(fill="x", padx=m.px(2), pady=(m.px(8), 0))

        self.add_button(tr.t("settings.restore_defaults"), self._restore_defaults, bootstyle="outline-secondary")
        self.add_button(tr.t("settings.cancel"), self._on_cancel, bootstyle="outline-secondary")
        self.add_button(tr.t("settings.ok"), self._on_ok, bootstyle="primary", default=True)

    def _clear_sizes(self) -> None:
        from tkinter import messagebox

        confirm = messagebox.askyesno(
            self.app.tr.t("settings.clear_sizes"),
            self.app.tr.t("settings.clear_sizes_confirm"),
            parent=self,
        )
        if not confirm:
            return
        path = session_mod.clear_remembered_sizes()
        self.sizes_status.configure(
            text=self.app.tr.t("settings.clear_sizes_done") if path else ""
        )
        self.app.log("INFO", f"remembered sizes cleared: {path}")

    def _restore_defaults(self) -> None:
        from .settings import UiSettings

        defaults = UiSettings()
        self.fit_var.set(defaults.default_fit)
        self.padding_var.set(defaults.padding_byte)
        self.geometry_var.set(defaults.remember_geometry)
        self.log_var.set(defaults.show_log_on_error)

    def _on_ok(self) -> None:
        settings = self.settings
        settings.default_fit = self.fit_var.get()
        settings.padding_byte = int(self.padding_var.get())
        settings.remember_geometry = bool(self.geometry_var.get())
        settings.show_log_on_error = bool(self.log_var.get())
        self.result = settings
        self._on_close()

    def _on_cancel(self) -> None:
        self.result = None
        self._on_close()


# ── 帮助 / 关于 ────────────────────────────────────────────
class HelpDialog(BaseDialog):
    def __init__(self, app: Any) -> None:
        super().__init__(app, app.tr.t("help.title"), resizable=(True, True))
        self._build()
        self.show()
        self.geometry(f"{self.metrics.px(660)}x{self.metrics.px(540)}")
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        notebook = tb.Notebook(self.body, bootstyle="secondary")
        notebook.pack(fill="both", expand=True)
        for title_key, text_key in (
            ("help.usage_title", "help.usage"),
            ("help.shortcuts_title", "help.shortcuts"),
            ("help.tips_title", "help.tips"),
        ):
            frame = tk.Frame(notebook, bg=palette.surface)
            notebook.add(frame, text=tr.t(title_key))
            text = tk.Text(
                frame,
                wrap="word",
                bg=palette.surface,
                fg=palette.text,
                font=self.fonts.base,
                bd=0,
                relief="flat",
                padx=m.px(12),
                pady=m.px(10),
                spacing1=m.px(2),
                spacing3=m.px(4),
            )
            text.insert("1.0", tr.t(text_key))
            text.configure(state="disabled")
            scroll = tb.Scrollbar(frame, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")
        self.add_button(tr.t("help.close"), self._on_close, bootstyle="primary", default=True)


class AboutDialog(BaseDialog):
    def __init__(self, app: Any) -> None:
        super().__init__(app, app.tr.t("about.title"))
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        import platform as _platform
        import sys as _sys

        header = tk.Frame(self.body, bg=palette.window_bg)
        header.pack(fill="x")
        canvas = tk.Canvas(
            header,
            width=m.px(48),
            height=m.px(48),
            bg=palette.window_bg,
            highlightthickness=0,
        )
        canvas.pack(side="left")
        canvas.create_rectangle(0, 0, m.px(44), m.px(44), fill=palette.accent, outline="")
        canvas.create_text(
            m.px(22),
            m.px(22),
            text="LB",
            fill="#ffffff",
            font=(self.fonts.family, int(round(self.fonts.h2_pt)), "bold"),
        )
        title_frame = tk.Frame(header, bg=palette.window_bg)
        title_frame.pack(side="left", padx=(m.px(12), 0))
        tk.Label(
            title_frame,
            text="亿方联发科工具箱 · InspireMTKTools",
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.title,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_frame,
            text=tr.t("about.version", version=getattr(self.app, "toolkit_version", "3.0.0"))
            + " · "
            + tr.t(
                "about.python",
                version=_platform.python_version(),
                pillow=_pillow_version(),
            )
            + f" · core {self.app.core_version}",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self.body,
            text=tr.t("about.body"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.base,
            anchor="w",
            justify="left",
            wraplength=m.px(520),
        ).pack(fill="x", pady=(m.px(12), 0))
        self.add_button(tr.t("about.close"), self._on_close, bootstyle="primary", default=True)
        self.show()
        self.update_idletasks()
        self.geometry(f"{max(m.px(560), self.winfo_reqwidth())}x{self.winfo_reqheight()}")
        self.center()


def _pillow_version() -> str:
    try:
        from PIL import __version__ as pillow_version

        return pillow_version
    except Exception:
        return "?"


# ── 第二屏 boot_logo ───────────────────────────────────────
class BootLogoDialog(BaseDialog):
    def __init__(self, app: Any) -> None:
        super().__init__(app, app.tr.t("bootlogo.title"))
        self._build()
        self.show()
        self.update_idletasks()
        self.geometry(f"{self.metrics.px(560)}x{self.winfo_reqheight()}")
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        tk.Label(
            self.body,
            text=tr.t("bootlogo.title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            self.body,
            text=tr.t("bootlogo.desc"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(480),
        ).pack(fill="x", pady=(m.px(6), m.px(12)))
        buttons = tk.Frame(self.body, bg=palette.window_bg)
        buttons.pack(fill="x")
        tb.Button(
            buttons,
            text=tr.t("bootlogo.from_image"),
            bootstyle="primary",
            command=lambda: self._run(self.app.bootlogo_from_image),
        ).pack(side="left", padx=(0, m.px(8)))
        tb.Button(
            buttons,
            text=tr.t("bootlogo.from_block"),
            bootstyle="secondary",
            command=lambda: self._run(self.app.bootlogo_from_block),
        ).pack(side="left", padx=(0, m.px(8)))
        tb.Button(
            buttons,
            text=tr.t("bootlogo.open"),
            bootstyle="outline-secondary",
            command=lambda: self._run(self.app.bootlogo_open_preview),
        ).pack(side="left")
        self.add_button(tr.t("common.close"), self._on_close, bootstyle="outline-secondary")

    def _run(self, callback: Callable[[], Any]) -> None:
        self._on_close()
        try:
            callback()
        except Exception as exc:  # 由 app 内统一处理，这里兜一层
            self.app.show_error(exc)


# ── 导出 logo.img 选项对话框 ───────────────────────────────
@dataclass
class ExportImgChoice:
    path: Path | None = None
    pad_target: int = 0
    fill_byte: int = 0x00


class ExportImgDialog(BaseDialog):
    """选择 logo.img 输出路径与分区对齐选项（0x00/0xFF 填充）。"""

    def __init__(
        self,
        app: Any,
        *,
        default_path: Path,
        actual_size: int | None = None,
        suggested_target: int | None = None,
        fill_byte: int = 0x00,
    ) -> None:
        super().__init__(app, app.tr.t("export.img_title"))
        self.actual_size = actual_size
        self.suggested_target = suggested_target or actual_size or 0
        self._default_path = Path(default_path)
        self._build(fill_byte)
        self.show()
        self.update_idletasks()
        self.geometry(f"{self.metrics.px(640)}x{self.winfo_reqheight()}")
        self.center()

    def _build(self, fill_byte: int) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr

        tk.Label(
            self.body,
            text=tr.t("export.img_title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(fill="x")

        path_row = tk.Frame(self.body, bg=palette.window_bg)
        path_row.pack(fill="x", pady=(m.px(10), m.px(4)))
        tk.Label(
            path_row,
            text=tr.t("export.save_dialog"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            width=10,
            anchor="w",
        ).pack(side="left")
        self.path_var = tk.StringVar(value=str(self._default_path))
        tb.Entry(path_row, textvariable=self.path_var, font=self.fonts.small).pack(
            side="left", fill="x", expand=True
        )
        tb.Button(
            path_row,
            text=tr.t("common.browse"),
            bootstyle="outline-secondary",
            command=self._browse,
        ).pack(side="left", padx=(m.px(6), 0))

        hint = tk.Label(
            self.body,
            text=tr.t("export.pad_hint"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(580),
        )
        hint.pack(fill="x", pady=(m.px(4), m.px(8)))

        pad_row = tk.Frame(self.body, bg=palette.window_bg)
        pad_row.pack(fill="x")
        self.pad_var = tk.BooleanVar(value=bool(self.suggested_target))
        tb.Checkbutton(
            pad_row,
            text=tr.t("export.pad_to"),
            variable=self.pad_var,
            bootstyle="round-toggle",
        ).pack(side="left")
        self.target_var = tk.StringVar(value=str(self.suggested_target or ""))
        self.target_spin = tb.Spinbox(
            pad_row,
            textvariable=self.target_var,
            from_=0,
            to=1024 * 1024 * 1024,
            width=14,
            font=self.fonts.small,
        )
        self.target_spin.pack(side="left", padx=(m.px(10), 0))

        fill_row = tk.Frame(self.body, bg=palette.window_bg)
        fill_row.pack(fill="x", pady=(m.px(6), 0))
        tk.Label(
            fill_row,
            text=tr.t("export.pad_byte"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            width=10,
            anchor="w",
        ).pack(side="left")
        self.fill_var = tk.IntVar(value=fill_byte)
        tb.Radiobutton(
            fill_row,
            text=tr.t("settings.padding_00"),
            value=0,
            variable=self.fill_var,
            bootstyle="primary",
        ).pack(side="left")
        tb.Radiobutton(
            fill_row,
            text=tr.t("settings.padding_ff"),
            value=255,
            variable=self.fill_var,
            bootstyle="primary",
        ).pack(side="left", padx=(m.px(10), 0))

        self.size_label = tk.Label(
            self.body,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
            justify="left",
            wraplength=m.px(580),
        )
        self.size_label.pack(fill="x", pady=(m.px(8), 0))
        self._update_size_label()
        self.target_var.trace_add("write", lambda *_: self._update_size_label())

        self.add_button(tr.t("common.cancel"), self._on_cancel, bootstyle="outline-secondary")
        self.add_button(tr.t("common.ok"), self._on_ok, bootstyle="primary", default=True)

    def _update_size_label(self) -> None:
        from .core_bridge import human_size

        parts = []
        if self.actual_size:
            parts.append(self.app.tr.t("export.size_info", size=f"{human_size(self.actual_size)}（{self.actual_size:,} 字节）"))
        target = _to_int(self.target_var.get()) or 0
        if self.pad_var.get() and self.actual_size and target and target < self.actual_size:
            parts.append(
                self.app.tr.t(
                    "export.pad_larger",
                    actual=f"{human_size(self.actual_size)}",
                    target=f"{target:,}",
                )
            )
        self.size_label.configure(text=" ".join(parts))

    def _browse(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title=self.app.tr.t("export.img_title"),
            defaultextension=".img",
            initialfile=Path(self.path_var.get()).name or "logo.img",
            filetypes=[
                ("logo.img", "*.img"),
                (self.app.tr.t("open.filter_logo"), "*.bin;*.img"),
                (self.app.tr.t("open.filter_all"), "*.*"),
            ],
        )
        if path:
            self.path_var.set(path)

    def _on_ok(self) -> None:
        path_text = self.path_var.get().strip()
        if not path_text:
            return
        target = _to_int(self.target_var.get()) or 0
        self.result = ExportImgChoice(
            path=Path(path_text),
            pad_target=target if self.pad_var.get() else 0,
            fill_byte=int(self.fill_var.get()),
        )
        self._on_close()

    def _on_cancel(self) -> None:
        self.result = None
        self._on_close()


# ── 图片预览对话框（boot_logo / PNG 预览）──────────────────
class ImagePreviewDialog(BaseDialog):
    """只读图片预览（用于 boot_logo 等裸数据文件）。"""

    def __init__(self, app: Any, *, title: str, image: Image.Image, caption: str = "") -> None:
        super().__init__(app, title, resizable=(True, True))
        self.image = image
        m = self.metrics
        palette = self.palette
        self._photo: Any = None
        self._checker: Any = None
        tk.Label(
            self.body,
            text=title,
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(fill="x")
        if caption:
            tk.Label(
                self.body,
                text=caption,
                bg=palette.window_bg,
                fg=palette.muted,
                font=self.fonts.small,
                anchor="w",
            ).pack(fill="x", pady=(m.px(2), m.px(6)))
        self.canvas = tk.Canvas(
            self.body,
            bg=palette.preview_bg,
            highlightthickness=0,
            height=m.px(300),
            width=m.px(420),
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self._draw())
        self.add_button(app.tr.t("common.close"), self._on_close, bootstyle="primary", default=True)
        self.show()
        width = min(m.px(560), max(m.px(360), self.winfo_screenwidth() - m.px(20)))
        height = min(m.px(460), max(m.px(260), self.winfo_screenheight() - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.center()
        self._draw()

    def _draw(self) -> None:
        canvas = self.canvas
        try:
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
        except Exception:
            return
        if width <= 2:
            return
        canvas.delete("all")
        palette = self.palette
        m = self.metrics
        pad = m.px(12)
        scale = fit_scale(self.image.size, (max(1, width - pad * 2), max(1, height - pad * 2)))
        display = scaled_size(self.image.size, scale)
        offset = ((width - display[0]) // 2, (height - display[1]) // 2)
        checker = make_checkerboard(display[0], display[1], palette.checker_a, palette.checker_b, max(4, m.px(8)))
        self._checker = to_photo(self, checker)
        if self.image.mode not in ("RGB", "RGBA"):
            image = self.image.convert("RGBA")
        else:
            image = self.image
        resized = image.resize(display, getattr(Image, "Resampling", Image).LANCZOS)
        self._photo = to_photo(self, resized)
        canvas.create_image(offset[0], offset[1], anchor="nw", image=self._checker)
        canvas.create_image(offset[0], offset[1], anchor="nw", image=self._photo)
        canvas.create_rectangle(
            offset[0] - 1,
            offset[1] - 1,
            offset[0] + display[0] + 1,
            offset[1] + display[1] + 1,
            outline=palette.border,
        )


# ── 简单确认对话框（native messagebox 的轻封装）─────────────
def ask_yes_no(parent: Any, title: str, message: str) -> bool:
    from tkinter import messagebox

    return bool(messagebox.askyesno(title, message, parent=parent))


def show_info(parent: Any, title: str, message: str) -> None:
    from tkinter import messagebox

    messagebox.showinfo(title, message, parent=parent)


def show_warning(parent: Any, title: str, message: str) -> None:
    from tkinter import messagebox

    messagebox.showwarning(title, message, parent=parent)
