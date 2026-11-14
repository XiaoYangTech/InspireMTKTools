"""右侧预览面板：大图预览（棋盘格透明背景）、块信息、操作按钮。

* 支持「适应窗口 / 1:1 像素」两种缩放；1:1 时可拖动平移；
* 图像缩放结果带缓存，窗口拖动缩放时不会反复做 LANCZOS 重采样；
* 未打开项目时显示欢迎页；二进制块 / 尺寸未知块有对应说明。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

import ttkbootstrap as tb

from .core_bridge import human_size
from .imaging import (
    fit_scale,
    make_checkerboard,
    make_placeholder,
    scaled_size,
    to_photo,
)
from .session import BlockView, UiSession, size_to_text
from .theme import Metrics, Palette, UiFonts
from .widgets import LinkButton, Tooltip


class PreviewPanel(tk.Frame):
    """块预览 + 详情 + 操作区。"""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        metrics: Metrics = app.metrics
        fonts: UiFonts = app.fonts
        super().__init__(
            master,
            bg=palette.surface,
            highlightthickness=1,
            highlightbackground=palette.border,
        )
        self.app = app
        self.palette = palette
        self.metrics = metrics
        self.fonts = fonts
        self.session: UiSession | None = None
        self.index: int = -1
        self.frame_index: int = 0  # 双帧块当前预览/编辑的帧（0 起）
        self.frame_var = tk.IntVar(value=0)
        self.frame_buttons: list[Any] = []
        self.zoom_mode = "fit"  # fit | one
        self._photo: Any = None
        self._checker_photo: Any = None
        self._resize_cache: dict[tuple, Any] = {}
        self._redraw_after: str | None = None
        self._canvas_key: tuple | None = None
        m = self.metrics

        # ── 顶栏 ──────────────────────────────────────────
        top = tk.Frame(self, bg=palette.surface)
        top.pack(fill="x", padx=m.px(12), pady=(m.px(9), m.px(2)))
        self.title_label = tk.Label(
            top,
            text=app.tr.t("preview.title"),
            bg=palette.surface,
            fg=palette.text,
            font=fonts.h2,
        )
        self.title_label.pack(side="left")
        self.block_title = tk.Label(
            top,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.small,
        )
        self.block_title.pack(side="left", padx=(m.px(10), 0))

        self.one_button = LinkButton(
            top,
            app.tr.t("preview.one_to_one"),
            lambda: self.set_zoom("one"),
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface,
            font=fonts.small,
        )
        self.one_button.pack(side="right", padx=(m.px(8), 0))
        self.fit_button = LinkButton(
            top,
            app.tr.t("preview.fit"),
            lambda: self.set_zoom("fit"),
            fg=palette.muted,
            hover_fg=palette.text,
            bg=palette.surface,
            font=fonts.small,
        )
        self.fit_button.pack(side="right")

        # ── 帧选择（左右双帧块才显示）────────────────────
        self.frame_bar = tk.Frame(self, bg=palette.surface)
        self.frame_label = tk.Label(
            self.frame_bar,
            text=app.tr.t("preview.frame_label"),
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.small,
        )
        self.frame_label.pack(side="left", padx=(0, m.px(8)))
        self.frame_buttons_frame = tk.Frame(self.frame_bar, bg=palette.surface)
        self.frame_buttons_frame.pack(side="left")
        self.frame_hint = tk.Label(
            self.frame_bar,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.tiny,
        )
        self.frame_hint.pack(side="left", padx=(m.px(10), 0))

        # ── 操作栏 ────────────────────────────────────────
        actions = tk.Frame(self, bg=palette.surface)
        actions.pack(fill="x", padx=m.px(12), pady=(m.px(4), m.px(6)))
        self.replace_button = tb.Button(
            actions,
            text=app.tr.t("preview.replace"),
            bootstyle="primary",
            command=lambda: self.app.replace_image_dialog(self.index),
            takefocus=True,
        )
        self.replace_button.pack(side="left")
        self.restore_button = tb.Button(
            actions,
            text=app.tr.t("preview.restore"),
            bootstyle="outline-secondary",
            command=lambda: self.app.restore_image(self.index),
            takefocus=True,
        )
        self.restore_button.pack(side="left", padx=(m.px(6), 0))
        self.detect_button = tb.Button(
            actions,
            text=app.tr.t("preview.detect_size"),
            bootstyle="warning-outline",
            command=lambda: self.app.detect_size_for_block(self.index),
            takefocus=True,
        )
        self.detect_button.pack(side="left", padx=(m.px(6), 0))
        self.export_button = tb.Button(
            actions,
            text=app.tr.t("preview.export_png"),
            bootstyle="outline-secondary",
            command=lambda: self.app.export_block_png(self.index),
            takefocus=True,
        )
        self.export_button.pack(side="right")
        Tooltip(self.replace_button, app.tr.t("preview.replace"))
        Tooltip(self.restore_button, app.tr.t("preview.restore"))
        Tooltip(self.detect_button, app.tr.t("preview.detect_size"))
        Tooltip(self.export_button, app.tr.t("preview.export_png"))
        self.zoom_label = tk.Label(
            actions,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.tiny,
        )
        self.zoom_label.pack(side="right", padx=(0, m.px(10)))

        # ── 画布 ──────────────────────────────────────────
        canvas_wrap = tk.Frame(self, bg=palette.preview_bg)
        canvas_wrap.pack(fill="both", expand=True, padx=m.px(12), pady=(0, m.px(8)))
        self.canvas = tk.Canvas(
            canvas_wrap,
            bg=palette.preview_bg,
            highlightthickness=0,
            bd=0,
            width=1,
            height=m.preview_min_height,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)

        # ── 信息区 ────────────────────────────────────────
        self.info_frame = tk.Frame(self, bg=palette.surface)
        self.info_frame.pack(fill="x", padx=m.px(12), pady=(0, m.px(12)))
        self._info_vars: dict[str, tk.StringVar] = {}
        info_defs = (
            ("resolution", "preview.info_resolution"),
            ("format", "preview.info_format"),
            ("kind", "preview.info_kind"),
            ("uncompressed", "preview.info_uncompressed"),
            ("compressed", "preview.info_compressed"),
            ("frame_layout", "preview.info_frame_layout"),
            ("status", "preview.info_status"),
        )
        self.info_tiles: dict[str, tk.Frame] = {}
        for position, (key, label_key) in enumerate(info_defs):
            row, col = divmod(position, 3)
            tile = tk.Frame(
                self.info_frame,
                bg=palette.surface_alt,
                highlightthickness=1,
                highlightbackground=palette.border,
            )
            tile.grid(
                row=row,
                column=col,
                sticky="nsew",
                padx=(0 if col == 0 else m.px(6), 0),
                pady=(0 if row == 0 else m.px(6), 0),
            )
            self.info_frame.grid_columnconfigure(col, weight=1)
            label = tk.Label(
                tile,
                text=app.tr.t(label_key),
                bg=palette.surface_alt,
                fg=palette.muted,
                font=fonts.tiny,
                anchor="w",
            )
            label.pack(fill="x", padx=m.px(8), pady=(m.px(4), 0))
            var = tk.StringVar(value="—")
            value = tk.Label(
                tile,
                textvariable=var,
                bg=palette.surface_alt,
                fg=palette.text,
                font=fonts.small,
                anchor="w",
            )
            value.pack(fill="x", padx=m.px(8), pady=(0, m.px(4)))
            self._info_vars[key] = var
            self.info_tiles[key] = tile

        self._update_action_state(None)

    # ── 对外接口 ─────────────────────────────────────────
    def set_session(self, session: UiSession | None) -> None:
        self.session = session
        self.index = -1
        self.frame_index = 0
        self.frame_var.set(0)
        self._resize_cache.clear()
        self.show_block(-1)

    def show_block(self, index: int) -> None:
        previous = self.index
        self.index = index
        view = self._current_view()
        if index != previous or view is None or not view.is_multi_frame:
            self.frame_index = 0
            self.frame_var.set(0)
        self._update_frame_controls(view)
        self._update_action_state(view)
        self._update_info(view)
        self._schedule_redraw(force=True)

    def set_frame(self, frame: int) -> None:
        """切换到第 ``frame`` 帧（0 起）；切换后立即刷新预览与信息卡。"""
        view = self._current_view()
        if view is None or not view.is_multi_frame:
            self.frame_index = 0
            self.frame_var.set(0)
            self._update_info(view)
            self._schedule_redraw(force=True)
            return
        index = max(0, min(int(frame), view.frame_count - 1))
        if index == self.frame_index:
            return
        self.frame_index = index
        self.frame_var.set(index)
        self._resize_cache.clear()
        self._update_info(view)
        self._schedule_redraw(force=True)

    def reset_frame(self) -> None:
        """恢复原图/切换会话后回到帧 1。"""
        self.frame_index = 0
        self.frame_var.set(0)
        self._update_info(self._current_view())
        self._resize_cache.clear()
        self._schedule_redraw(force=True)

    def _update_frame_controls(self, view: BlockView | None) -> None:
        """双帧块显示「帧选择」；单帧块隐藏。"""
        for child in list(self.frame_buttons_frame.winfo_children()):
            child.destroy()
        self.frame_buttons = []
        if view is None or not view.is_multi_frame:
            try:
                self.frame_bar.pack_forget()
            except Exception:
                pass
            self.frame_hint.configure(text="")
            return
        tr = self.app.tr
        self.frame_index = max(0, min(self.frame_index, view.frame_count - 1))
        self.frame_var.set(self.frame_index)
        for frame in range(view.frame_count):
            button = tb.Radiobutton(
                self.frame_buttons_frame,
                text=tr.t("preview.frame_n", n=frame + 1),
                value=frame,
                variable=self.frame_var,
                bootstyle="primary",
                command=lambda value=frame: self.set_frame(value),
                takefocus=True,
            )
            button.pack(side="left", padx=(0, self.metrics.px(10)))
            self.frame_buttons.append(button)
        self.frame_hint.configure(
            text=tr.t("preview.frame_layout_pair", width=view.frame_width, height=view.frame_height)
            if view.frame_count == 2
            else tr.t(
                "preview.frame_layout_multi",
                count=view.frame_count,
                width=view.frame_width,
                height=view.frame_height,
            )
        )
        self.frame_bar.pack(fill="x", padx=self.metrics.px(12), pady=(self.metrics.px(2), 0), after=self.frame_bar.master.winfo_children()[0])

    def refresh(self) -> None:
        self._resize_cache.clear()
        self._update_action_state(self._current_view())
        self._update_info(self._current_view())
        self._schedule_redraw(force=True)

    def set_zoom(self, mode: str) -> None:
        if mode not in ("fit", "one"):
            mode = "fit"
        self.zoom_mode = mode
        self._update_zoom_links()
        self._resize_cache.clear()
        self._schedule_redraw(force=True)

    def _update_zoom_links(self) -> None:
        palette = self.palette
        active = self.palette.accent
        inactive = palette.muted
        self.fit_button.configure(fg=active if self.zoom_mode == "fit" else inactive)
        self.one_button.configure(fg=active if self.zoom_mode == "one" else inactive)

    # ── 状态 ─────────────────────────────────────────────
    def _current_view(self) -> BlockView | None:
        if self.session is None or self.index < 0:
            return None
        return self.session.block(self.index)

    def _update_action_state(self, view: BlockView | None) -> None:
        tr = self.app.tr
        if view is None:
            for button in (self.replace_button, self.restore_button, self.detect_button, self.export_button):
                button.configure(state="disabled")
            return
        is_image = view.is_image
        can_restore = bool(self.session and self.session.can_restore(view.index))
        self._set_button(self.replace_button, is_image)
        self._set_button(self.restore_button, is_image and can_restore)
        self._set_button(self.export_button, is_image)
        if view.is_unknown:
            self.detect_button.pack(side="left", padx=(self.metrics.px(6), 0), before=self.export_button)
            self._set_button(self.detect_button, True)
        else:
            self.detect_button.pack_forget()

    def _set_button(self, button: Any, enabled: bool) -> None:
        try:
            button.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _update_info(self, view: BlockView | None) -> None:
        tr = self.app.tr
        if view is None:
            for var in self._info_vars.values():
                var.set("—")
            self.block_title.configure(text="")
            return
        self.block_title.configure(text=tr.t("preview.block_title", index=view.index + 1))
        size = view.size
        self._info_vars["resolution"].set(size_to_text(size, compact=False) if size is not None else "—")
        color_depth = getattr(size, "color_depth", getattr(size, "bpp", None)) if size else None
        if color_depth == 2:
            self._info_vars["format"].set("RGB565 · 2 bpp")
        elif color_depth == 4:
            self._info_vars["format"].set("BGRA8888 · 4 bpp")
        else:
            self._info_vars["format"].set("—")
        if view.is_unknown:
            kind = tr.t("blocks.kind_unknown")
        elif view.is_binary:
            kind = tr.t("blocks.kind_binary")
        else:
            kind = tr.t("blocks.kind_image")
        self._info_vars["kind"].set(kind)
        self._info_vars["uncompressed"].set(human_size(view.decompressed_len))
        self._info_vars["compressed"].set(human_size(view.compressed_len))
        status_map = {
            "ok": tr.t("blocks.status_ok"),
            "modified": tr.t("blocks.status_modified"),
            "unknown": tr.t("blocks.status_unknown"),
            "binary": tr.t("blocks.status_binary"),
        }
        if view.is_image and view.is_multi_frame:
            width = view.frame_width
            height = view.frame_height
            if view.frame_count == 2:
                frame_layout = tr.t("preview.frame_layout_pair", width=width, height=height)
            else:
                frame_layout = tr.t(
                    "preview.frame_layout_multi",
                    count=view.frame_count,
                    width=width,
                    height=height,
                )
        else:
            frame_layout = tr.t("preview.frame_layout_single") if view.is_image else "—"
        self._info_vars["frame_layout"].set(frame_layout)
        self._info_vars["status"].set(status_map.get(view.status, "—"))

    # ── 重绘 ─────────────────────────────────────────────
    def _on_canvas_configure(self, _event: tk.Event | None = None) -> None:
        self._schedule_redraw()

    def _schedule_redraw(self, *, force: bool = False) -> None:
        if force:
            self._canvas_key = None
        if self._redraw_after is not None:
            return
        try:
            self._redraw_after = self.after(30, self._do_redraw)
        except Exception:
            self._redraw_after = None

    def _do_redraw(self) -> None:
        self._redraw_after = None
        self._redraw()

    def _redraw(self) -> None:
        canvas = self.canvas
        try:
            width = max(1, canvas.winfo_width())
            height = max(1, canvas.winfo_height())
        except Exception:
            return
        if width <= 2 or height <= 2:
            return
        view = self._current_view()
        revision = self.session.revision if self.session is not None else 0
        key = (width, height, self.index, revision, self.zoom_mode, self.frame_index)
        if key == self._canvas_key:
            return
        self._canvas_key = key
        canvas.delete("all")
        self._photo = None
        self._checker_photo = None
        if self.session is None:
            self._draw_welcome(width, height)
            return
        if view is None:
            self._draw_hint(width, height, self.app.tr.t("preview.no_selection"))
            return
        if view.is_unknown:
            self._draw_unknown(width, height, view)
            return
        if view.is_binary:
            self._draw_binary(width, height, view)
            return
        self._draw_image(width, height, view)

    # ── 各种绘制 ─────────────────────────────────────────
    def _draw_welcome(self, width: int, height: int) -> None:
        canvas = self.canvas
        palette = self.palette
        m = self.metrics
        tr = self.app.tr
        cx, cy = width / 2, height / 2
        icon = m.px(64)
        canvas.create_rectangle(
            cx - icon / 2,
            cy - m.px(96),
            cx + icon / 2,
            cy - m.px(96) + icon,
            fill=palette.accent_soft,
            outline=palette.accent,
            width=max(1, m.px(1)),
        )
        canvas.create_text(
            cx,
            cy - m.px(96) + icon / 2,
            text="LOGO",
            fill=palette.accent,
            font=(self.fonts.family, max(8, int(round(self.fonts.small_pt))), "bold"),
        )
        canvas.create_text(
            cx,
            cy - m.px(8),
            text=tr.t("preview.welcome_title"),
            fill=palette.text,
            font=(self.fonts.family, int(round(self.fonts.title_pt * 0.8)), "bold"),
        )
        canvas.create_text(
            cx,
            cy + m.px(18),
            text=tr.t("preview.welcome_hint"),
            fill=palette.muted,
            font=(self.fonts.family, int(round(self.fonts.base_pt))),
            width=max(m.px(200), min(width - m.px(60), m.px(520))),
            justify="center",
        )
        if self.app.drop_enabled:
            canvas.create_text(
                cx,
                cy + m.px(46),
                text="↧  " + tr.t("dnd.enabled_hint"),
                fill=palette.muted,
                font=(self.fonts.family, int(round(self.fonts.small_pt))),
            )

    def _draw_hint(self, width: int, height: int, text: str) -> None:
        self.canvas.create_text(
            width / 2,
            height / 2,
            text=text,
            fill=self.palette.muted,
            font=(self.fonts.family, int(round(self.fonts.base_pt))),
        )

    def _draw_image(self, width: int, height: int, view: BlockView) -> None:
        canvas = self.canvas
        palette = self.palette
        m = self.metrics
        session = self.session
        assert session is not None
        image = (
            session.frame_image(view.index, self.frame_index)
            if view.is_multi_frame
            else session.image(view.index)
        )
        if image is None:
            self._draw_hint(width, height, view.note or self.app.tr.t("preview.empty_canvas"))
            return
        pad = m.px(16)
        if self.zoom_mode == "fit":
            scale = fit_scale(image.size, (max(1, width - pad * 2), max(1, height - pad * 2)))
            display_size = scaled_size(image.size, scale)
            offset = (max(0, (width - display_size[0]) // 2), max(0, (height - display_size[1]) // 2))
            canvas.configure(scrollregion=(0, 0, width, height))
        else:
            display_size = image.size
            offset = (max(0, (width - display_size[0]) // 2), max(0, (height - display_size[1]) // 2))
            canvas.configure(
                scrollregion=(
                    0,
                    0,
                    max(width, display_size[0] + pad * 2),
                    max(height, display_size[1] + pad * 2),
                )
            )
            if display_size[0] > width or display_size[1] > height:
                canvas.xview_moveto(0)
                canvas.yview_moveto(0)
                offset = (pad, pad)
        cache_key = (view.index, session.revision, self.zoom_mode, display_size, self.frame_index)
        photo = self._resize_cache.get(cache_key)
        if photo is None:
            resized = image.resize(display_size, self._resample())
            photo = to_photo(self, resized)
            self._resize_cache[cache_key] = photo
            if len(self._resize_cache) > 8:
                self._resize_cache.pop(next(iter(self._resize_cache)))
        self._photo = photo
        checker = make_checkerboard(
            max(1, display_size[0]),
            max(1, display_size[1]),
            palette.checker_a,
            palette.checker_b,
            max(4, m.px(10)),
        )
        self._checker_photo = to_photo(self, checker)
        x, y = offset
        # 阴影/边框
        canvas.create_rectangle(
            x - m.px(2),
            y - m.px(2),
            x + display_size[0] + m.px(2),
            y + display_size[1] + m.px(2),
            outline=palette.border,
            width=max(1, m.px(1)),
        )
        canvas.create_image(x, y, anchor="nw", image=self._checker_photo)
        canvas.create_image(x, y, anchor="nw", image=photo)
        percent = round(display_size[0] / max(1, image.width) * 100)
        self.zoom_label.configure(text=self.app.tr.t("preview.zoom_hint", percent=percent))

    def _draw_binary(self, width: int, height: int, view: BlockView) -> None:
        tr = self.app.tr
        cx, cy = width / 2, height / 2
        m = self.metrics
        palette = self.palette
        size = m.px(56)
        image = make_placeholder(size, "BIN", bg=palette.surface_alt, fg=palette.muted)
        self._photo = to_photo(self, image)
        self.canvas.create_image(cx, cy - m.px(44), image=self._photo)
        self.canvas.create_text(
            cx,
            cy + m.px(6),
            text=tr.t("blocks.kind_binary"),
            fill=palette.text,
            font=(self.fonts.family, int(round(self.fonts.h3_pt)), "bold"),
        )
        self.canvas.create_text(
            cx,
            cy + m.px(30),
            text=tr.t("preview.binary_body"),
            fill=palette.muted,
            font=(self.fonts.family, int(round(self.fonts.small_pt))),
            width=max(m.px(220), min(width - m.px(80), m.px(520))),
            justify="center",
        )
        self.zoom_label.configure(text="")

    def _draw_unknown(self, width: int, height: int, view: BlockView) -> None:
        tr = self.app.tr
        cx, cy = width / 2, height / 2
        m = self.metrics
        palette = self.palette
        size = m.px(56)
        image = make_placeholder(size, "?", bg=palette.surface_alt, fg=palette.warning)
        self._photo = to_photo(self, image)
        self.canvas.create_image(cx, cy - m.px(40), image=self._photo)
        self.canvas.create_text(
            cx,
            cy + m.px(8),
            text=tr.t("blocks.kind_unknown"),
            fill=palette.warning,
            font=(self.fonts.family, int(round(self.fonts.h3_pt)), "bold"),
        )
        self.canvas.create_text(
            cx,
            cy + m.px(32),
            text=tr.t("preview.unknown_body"),
            fill=palette.muted,
            font=(self.fonts.family, int(round(self.fonts.small_pt))),
            width=max(m.px(220), min(width - m.px(80), m.px(520))),
            justify="center",
        )
        self.zoom_label.configure(text="")

    # ── 交互 ─────────────────────────────────────────────
    @staticmethod
    def _resample() -> Any:
        from PIL import Image

        return getattr(Image, "Resampling", Image).LANCZOS

    def _on_double_click(self, _event: tk.Event) -> None:
        view = self._current_view()
        if view is not None and (view.is_image or view.is_unknown):
            self.app.replace_image_dialog(view.index)

    def _on_pan_start(self, event: tk.Event) -> None:
        if self.zoom_mode != "one":
            return
        try:
            self.canvas.scan_mark(event.x, event.y)
        except Exception:
            pass

    def _on_pan_move(self, event: tk.Event) -> None:
        if self.zoom_mode != "one":
            return
        try:
            self.canvas.scan_dragto(event.x, event.y, gain=1)
        except Exception:
            pass
