"""左侧块列表：可滚动的卡片列表（缩略图 + 序号 + 尺寸 + 类型 + 状态）。

* 缩略图渐进式生成（先出卡片，再后台补齐图片），大镜像打开时界面不卡；
* 单击选中、双击替换图片、右键菜单；支持按序号/尺寸/类型过滤；
* 卡片外观随主题调色板重绘（主题切换时整体重建 UI）。
"""

from __future__ import annotations

import tkinter as tk
from typing import Any

import ttkbootstrap as tb

from .imaging import make_placeholder, make_thumbnail, to_photo
from .session import BlockView, UiSession, size_to_text
from .theme import Metrics, Palette, UiFonts
from .widgets import Tooltip

FILTER_PLACEHOLDER: dict[str, str] = {
    "zh_CN": "搜索序号 / 尺寸 / 类型…",
    "en": "Filter index / size / type…",
}


class BlockCard(tk.Frame):
    """单个块卡片。"""

    def __init__(self, master: tk.Misc, panel: "BlockListPanel", view: BlockView) -> None:
        self.panel = panel
        self.app = panel.app
        self.view = view
        self.palette: Palette = panel.palette
        self.metrics: Metrics = panel.metrics
        self.fonts: UiFonts = panel.fonts
        super().__init__(
            master,
            bg=self.palette.surface,
            highlightthickness=1,
            highlightbackground=self.palette.surface,
            highlightcolor=self.palette.accent,
        )
        self._selected = False
        self._hovered = False
        self._thumb_photo: Any = None
        self._paint_widgets: list[tk.Widget] = []
        m = self.metrics
        self.configure(height=m.px(122 if view.is_multi_frame else 78))
        self.grid_propagate(False)
        self.grid_columnconfigure(2, weight=1)

        # 左侧强调条
        self.bar = tk.Frame(self, bg=self.palette.surface, width=m.px(3))
        self.bar.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, m.px(2)))

        # 缩略图
        thumb = m.thumb_size
        self.thumb = tk.Label(
            self,
            bg=self.palette.surface_alt,
            width=thumb,
            height=thumb,
            bd=0,
            highlightthickness=0,
        )
        self.thumb.grid(row=0, column=1, rowspan=3, padx=(m.px(6), m.px(10)), pady=m.px(10))

        # 文字：标题 / 类型 / 大小
        self.title_label = tk.Label(
            self,
            text=self._title(),
            bg=self.palette.surface,
            fg=self.palette.text,
            font=self.fonts.bold,
            anchor="w",
            justify="left",
        )
        self.title_label.grid(row=0, column=2, sticky="w", pady=(m.px(10), 0))
        # 双帧块的尺寸文字较长：允许按卡片宽度换行（高度在 update_from_view 中调整）
        self.title_label.configure(justify="left")
        try:
            self.bind("<Configure>", self._on_card_configure, add="+")
        except Exception:
            pass

        self.kind_label = tk.Label(
            self,
            text=self._kind_text(),
            bg=self.palette.surface,
            fg=self.palette.muted,
            font=self.fonts.small,
            anchor="w",
        )
        self.kind_label.grid(row=1, column=2, sticky="w", pady=(m.px(1), 0))

        self.size_label = tk.Label(
            self,
            text=self._size_text(),
            bg=self.palette.surface,
            fg=self.palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        )
        self.size_label.grid(row=2, column=2, sticky="w", pady=(0, m.px(10)))

        # 右侧状态标签
        self.status_label = tk.Label(
            self,
            text=self._status_text(),
            bg=self.palette.badge_bg,
            fg=self.palette.badge_fg,
            font=self.fonts.tiny,
            padx=m.px(7),
            pady=m.px(2),
        )
        self.status_label.grid(row=0, column=3, rowspan=3, padx=(m.px(6), m.px(10)), sticky="e")
        self._apply_status_color()

        self._paint_widgets = [
            self,
            self.title_label,
            self.kind_label,
            self.size_label,
            self.bar,
        ]
        self._bind_events(self)
        for child in (self.bar, self.thumb, self.title_label, self.kind_label, self.size_label, self.status_label):
            self._bind_events(child)
        Tooltip(self, self.app.tr.t("blocks.item_tooltip"))
        self._apply_bg()

    # ── 文本 ─────────────────────────────────────────────
    def _title(self) -> str:
        tr = self.app.tr
        view = self.view
        if view.size is not None:
            size_text = size_to_text(view.size)
            if view.is_multi_frame:
                size_text = tr.t(
                    "blocks.size_with_frames",
                    size=size_text,
                    count=view.frame_count,
                    frame_width=view.frame_width,
                    frame_height=view.frame_height,
                )
            return f"#{view.index + 1}   {size_text}"
        return f"#{view.index + 1}"

    def _kind_text(self) -> str:
        tr = self.app.tr
        if self.view.is_unknown:
            return tr.t("blocks.kind_unknown")
        if self.view.is_binary:
            return tr.t("blocks.kind_binary")
        return tr.t("blocks.kind_image")

    def _size_text(self) -> str:
        from .core_bridge import human_size

        parts = [self._kind_text()]
        if self.view.decompressed_len:
            parts.append(f"{human_size(self.view.decompressed_len)}")
        if self.view.modified:
            parts.append(self.app.tr.t("blocks.status_modified"))
        return " · ".join(parts)

    def _status_text(self) -> str:
        tr = self.app.tr
        status = self.view.status
        return {
            "ok": tr.t("blocks.status_ok"),
            "modified": tr.t("blocks.status_modified"),
            "unknown": tr.t("blocks.status_unknown"),
            "binary": tr.t("blocks.status_binary"),
        }.get(status, tr.t("blocks.status_ok"))

    def _apply_status_color(self) -> None:
        palette = self.palette
        status = self.view.status
        mapping = {
            "ok": (palette.tag_ok_bg, palette.tag_ok_fg),
            "modified": (palette.tag_mod_bg, palette.tag_mod_fg),
            "unknown": (palette.tag_unknown_bg, palette.tag_unknown_fg),
            "binary": (palette.tag_binary_bg, palette.tag_binary_fg),
        }
        bg, fg = mapping.get(status, (palette.badge_bg, palette.badge_fg))
        self.status_label.configure(bg=bg, fg=fg)

    def _on_card_configure(self, event: Any = None) -> None:
        try:
            width = event.width if event is not None else self.winfo_width()
        except Exception:
            width = self.winfo_width()
        wrap = max(self.metrics.px(120), int(width) - self.metrics.px(112))
        try:
            self.title_label.configure(wraplength=wrap)
        except Exception:
            pass

    def update_from_view(self) -> None:
        self.title_label.configure(text=self._title())
        # 双帧块标题需要两行，卡片高度略增；单帧块保持原高度
        height = self.metrics.px(122 if self.view.is_multi_frame else 78)
        try:
            self.configure(height=height)
        except Exception:
            pass
        self._on_card_configure()
        self.kind_label.configure(text=self._kind_text())
        self.size_label.configure(text=self._size_text())
        self.status_label.configure(text=self._status_text())
        self._apply_status_color()

    # ── 缩略图 ───────────────────────────────────────────
    def set_thumbnail(self, photo: Any) -> None:
        self._thumb_photo = photo
        self.thumb.configure(image=photo, text="", width=0, height=0)
        self.thumb.image = photo  # type: ignore[attr-defined]

    # ── 交互 ─────────────────────────────────────────────
    def _bind_events(self, widget: tk.Widget) -> None:
        widget.bind("<Button-1>", self._on_click)
        widget.bind("<Double-Button-1>", self._on_double)
        widget.bind("<Button-3>", self._on_context)
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_click(self, _event: tk.Event) -> str:
        self.panel.select(self.view.index)
        return "break"

    def _on_double(self, _event: tk.Event) -> str:
        self.panel.activate(self.view.index)
        return "break"

    def _on_context(self, event: tk.Event) -> str:
        self.panel.select(self.view.index)
        self.panel.show_context_menu(event, self.view.index)
        return "break"

    def _on_enter(self, _event: tk.Event) -> None:
        self._hovered = True
        self._apply_bg()

    def _on_leave(self, _event: tk.Event) -> None:
        # 指针移到卡片内其他子控件时不算离开
        self._hovered = False
        self.after(30, self._check_leave)

    def _check_leave(self) -> None:
        try:
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
        except Exception:
            widget = None
        if widget is not None and (widget is self or str(widget).startswith(str(self))):
            self._hovered = True
        self._apply_bg()

    def set_selected(self, selected: bool) -> None:
        if selected == self._selected:
            return
        self._selected = selected
        self._apply_bg()

    def _apply_bg(self) -> None:
        if self._selected:
            bg = self.palette.accent_soft
            bar = self.palette.accent
            border = self.palette.accent
        elif self._hovered:
            bg = self.palette.surface_hover
            bar = self.palette.surface_hover
            border = self.palette.border
        else:
            bg = self.palette.surface
            bar = self.palette.surface
            border = self.palette.surface
        try:
            self.configure(bg=bg, highlightbackground=border)
            for widget in self._paint_widgets:
                if widget is self:
                    continue
                if widget is self.bar:
                    widget.configure(bg=bar)
                else:
                    widget.configure(bg=bg)
            self.thumb.configure(bg=self.palette.surface_alt)
        except Exception:
            pass


class BlockListPanel(tk.Frame):
    """块列表面板（含标题、过滤框、滚动区、空状态）。"""

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
        m = self.metrics
        self.session: UiSession | None = None
        self.selected_index: int = -1
        self.cards: dict[int, BlockCard] = {}
        self._thumb_cache: dict[tuple[int, int, str], Any] = {}
        self._thumb_queue: list[int] = []
        self._thumb_after: str | None = None
        self._filter_text = ""
        self._scroll_bindings: list[str] = []

        # 标题行
        header = tk.Frame(self, bg=palette.surface)
        header.pack(fill="x", padx=m.px(12), pady=(m.px(10), m.px(4)))
        self.title_label = tk.Label(
            header,
            text=app.tr.t("blocks.title"),
            bg=palette.surface,
            fg=palette.text,
            font=fonts.h2,
        )
        self.title_label.pack(side="left")
        self.count_label = tk.Label(
            header,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=fonts.small,
        )
        self.count_label.pack(side="right")

        # 过滤框
        filter_frame = tk.Frame(self, bg=palette.surface)
        filter_frame.pack(fill="x", padx=m.px(12), pady=(0, m.px(8)))
        self.filter_var = tk.StringVar(value="")
        self.filter_entry = tb.Entry(
            filter_frame,
            textvariable=self.filter_var,
            font=fonts.base,
            bootstyle="secondary",
        )
        self.filter_entry.pack(fill="x")
        self._placeholder_text = FILTER_PLACEHOLDER.get(app.tr.language, FILTER_PLACEHOLDER["zh_CN"])
        self.filter_entry.insert(0, self._placeholder_text)
        self.filter_entry.configure(foreground=palette.muted)
        self.filter_entry.bind("<FocusIn>", self._on_filter_focus_in)
        self.filter_entry.bind("<FocusOut>", self._on_filter_focus_out)
        self.filter_var.trace_add("write", lambda *_: self._on_filter_changed())
        self._placeholder_active = True

        # 滚动区
        body = tk.Frame(self, bg=palette.surface)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(
            body,
            bg=palette.surface,
            highlightthickness=0,
            bd=0,
            width=1,
            height=1,
        )
        self.scrollbar = tb.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, bg=palette.surface)
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # 空状态
        self.empty_frame = tk.Frame(self.inner, bg=palette.surface)
        self.empty_title = tk.Label(
            self.empty_frame,
            text=app.tr.t("blocks.empty_title"),
            bg=palette.surface,
            fg=palette.text,
            font=app.fonts.h3,
        )
        self.empty_title.pack(pady=(m.px(30), m.px(6)))
        self.empty_hint = tk.Label(
            self.empty_frame,
            text=app.tr.t("blocks.empty_hint"),
            bg=palette.surface,
            fg=palette.muted,
            font=app.fonts.small,
            wraplength=m.px(260),
            justify="center",
        )
        self.empty_hint.pack(padx=m.px(18))
        self._show_empty(self.app.tr.t("blocks.empty_title"), self.app.tr.t("blocks.empty_hint"))

        self._bind_mousewheel()

    # ── 数据 ─────────────────────────────────────────────
    def set_session(self, session: UiSession | None, *, select: int | None = 0) -> None:
        self.session = session
        self._thumb_cache.clear()
        self._thumb_queue.clear()
        self.selected_index = -1
        self._rebuild()
        if session is not None and session.count:
            target = 0 if select is None else min(max(0, select), session.count - 1)
            self.select(target, notify=False)
            self.app.on_block_selected(target)
        else:
            self.app.on_block_selected(-1)

    def refresh(self, *, preserve_selection: bool = True) -> None:
        selection = self.selected_index if preserve_selection else 0
        self._thumb_cache.clear()
        self._rebuild()
        if self.session is not None and selection >= 0:
            self.select(selection, notify=False)

    def update_block(self, index: int) -> None:
        """单块数据变化（替换图片 / 识别尺寸）后刷新卡片与缩略图。"""
        card = self.cards.get(index)
        if card is None:
            return
        card.update_from_view()
        self._thumb_cache.pop((index, self._session_revision(), ""), None)
        self._queue_thumb(index)
        self._update_counts()

    def _session_revision(self) -> int:
        return self.session.revision if self.session is not None else 0

    # ── 构建卡片 ─────────────────────────────────────────
    def _visible_blocks(self) -> list[BlockView]:
        if self.session is None:
            return []
        needle = self._filter_text.strip().lower()
        blocks = self.session.blocks
        if not needle:
            return list(blocks)
        result = []
        for view in blocks:
            haystack = " ".join(
                [
                    str(view.index + 1),
                    f"#{view.index + 1}",
                    view.kind,
                    (
                        self._title()
                        if view.is_multi_frame
                        else (size_to_text(view.size) if view.size is not None else "")
                    ),
                    str(view.decompressed_len or ""),
                    view.status,
                ]
            ).lower()
            if needle in haystack:
                result.append(view)
        return result

    def _rebuild(self) -> None:
        for card in self.cards.values():
            card.destroy()
        self.cards.clear()
        self._thumb_queue.clear()
        for child in self.inner.winfo_children():
            if child is not self.empty_frame:
                child.destroy()
        blocks = self._visible_blocks()
        if not blocks:
            if self.session is None:
                self._show_empty(self.app.tr.t("blocks.empty_title"), self.app.tr.t("blocks.empty_hint"))
            else:
                self._show_empty(self.app.tr.t("blocks.no_match"), "")
            self._update_counts()
            return
        self._hide_empty()
        for view in blocks:
            card = BlockCard(self.inner, self, view)
            card.pack(fill="x", padx=self.metrics.px(8), pady=self.metrics.px(3))
            self._bind_card_wheel(card)
            self.cards[view.index] = card
        self._update_counts()
        for index in self.cards:
            self._queue_thumb(index)
        self._pump_thumbs()
        # 恢复选中态
        if self.selected_index in self.cards:
            self.cards[self.selected_index].set_selected(True)

    def _show_empty(self, title: str, hint: str) -> None:
        self.empty_title.configure(text=title)
        self.empty_hint.configure(text=hint)
        self.empty_frame.pack_forget()
        self.empty_frame.pack(fill="x", pady=self.metrics.px(8))

    def _hide_empty(self) -> None:
        self.empty_frame.pack_forget()

    def _update_counts(self) -> None:
        total = self.session.count if self.session is not None else 0
        shown = len(self.cards)
        tr = self.app.tr
        if self.session is None:
            self.count_label.configure(text="")
        elif shown != total:
            self.count_label.configure(text=f"{shown}/{total}")
        else:
            self.count_label.configure(text=tr.t("blocks.count", count=total))

    # ── 缩略图（渐进式）──────────────────────────────────
    def _queue_thumb(self, index: int) -> None:
        if index not in self._thumb_queue:
            self._thumb_queue.append(index)
        if self._thumb_after is None:
            self._thumb_after = self.after(1, self._pump_thumbs)

    def _pump_thumbs(self) -> None:
        self._thumb_after = None
        session = self.session
        if session is None:
            return
        processed = 0
        while self._thumb_queue and processed < 6:
            index = self._thumb_queue.pop(0)
            processed += 1
            card = self.cards.get(index)
            view = session.block(index)
            if card is None or view is None:
                continue
            key = (index, session.revision, view.kind)
            photo = self._thumb_cache.get(key)
            if photo is None:
                photo = self._make_thumb(view)
                self._thumb_cache[key] = photo
            card.set_thumbnail(photo)
        if self._thumb_queue:
            self._thumb_after = self.after(1, self._pump_thumbs)

    def _make_thumb(self, view: BlockView) -> Any:
        size = self.metrics.thumb_size
        if view.is_image:
            image = self.session.image(view.index) if self.session is not None else None
            if image is not None:
                thumb = make_thumbnail(image, size, bg=self.palette.surface_alt, pad=2)
                return to_photo(self, thumb)
            return to_photo(
                self,
                make_placeholder(size, "ERR", bg=self.palette.surface_alt, fg=self.palette.danger),
            )
        if view.is_unknown:
            return to_photo(
                self,
                make_placeholder(size, "?", bg=self.palette.surface_alt, fg=self.palette.warning),
            )
        return to_photo(
            self,
            make_placeholder(size, "BIN", bg=self.palette.surface_alt, fg=self.palette.muted),
        )

    # ── 选中 / 激活 ──────────────────────────────────────
    def select(self, index: int, *, notify: bool = True) -> None:
        if self.session is None or index < 0 or index >= self.session.count:
            return
        previous = self.selected_index
        self.selected_index = index
        if previous in self.cards:
            self.cards[previous].set_selected(False)
        card = self.cards.get(index)
        if card is not None:
            card.set_selected(True)
            self._scroll_into_view(card)
        if notify:
            self.app.on_block_selected(index)

    def activate(self, index: int) -> None:
        view = self.session.block(index) if self.session is not None else None
        if view is None:
            return
        if view.is_image or view.is_unknown:
            self.app.replace_image_dialog(index)
        elif view.is_binary:
            self.app.set_status(self.app.tr.t("blocks.binary_hint"), "info")

    def show_context_menu(self, event: tk.Event, index: int) -> None:
        view = self.session.block(index) if self.session is not None else None
        if view is None:
            return
        palette = self.palette
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=palette.surface,
            fg=palette.text,
            activebackground=palette.accent,
            activeforeground="#ffffff",
            font=(self.fonts.family, int(round(self.fonts.base_pt))),
            bd=0,
        )
        tr = self.app.tr
        menu.add_command(
            label=tr.t("blocks.ctx_replace"),
            command=lambda: self.app.replace_image_dialog(index),
            state="normal" if view.is_image else "disabled",
        )
        menu.add_command(
            label=tr.t("blocks.ctx_restore"),
            command=lambda: self.app.restore_image(index),
            state="normal" if self.session.can_restore(index) else "disabled",
        )
        menu.add_command(
            label=tr.t("blocks.ctx_detect"),
            command=lambda: self.app.detect_size_for_block(index),
            state="normal" if view.is_unknown else "disabled",
        )
        menu.add_separator()
        menu.add_command(
            label=tr.t("blocks.ctx_export_png"),
            command=lambda: self.app.export_block_png(index),
            state="normal" if view.is_image else "disabled",
        )
        menu.add_command(
            label=tr.t("blocks.ctx_copy_info"),
            command=lambda: self.app.copy_block_info(index),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            self.after(50, menu.destroy)

    # ── 滚动 ─────────────────────────────────────────────
    def _on_inner_configure(self, _event: tk.Event | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._inner_id, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        self.scrollbar.set(first, last)
        # 内容不足一屏时隐藏滚动条会闪，保持显示但可禁用（ttk 不易禁用，直接显示）

    def _scroll_into_view(self, card: tk.Widget) -> None:
        try:
            self.inner.update_idletasks()
            # 首次布局完成前（canvas 高度还是 1）不要滚动，否则会把列表滚到中间
            if not self.canvas.winfo_ismapped() or self.canvas.winfo_height() < self.metrics.px(80):
                return
            y = card.winfo_y()
            height = card.winfo_height()
            total = max(1, self.inner.winfo_height())
            view_height = max(1, self.canvas.winfo_height())
            if total <= view_height:
                return
            top = y - self.metrics.px(6)
            bottom = y + height + self.metrics.px(6)
            current_top = self.canvas.canvasy(0)
            current_bottom = current_top + view_height
            if top < current_top:
                self.canvas.yview_moveto(max(0.0, top / total))
            elif bottom > current_bottom:
                self.canvas.yview_moveto(max(0.0, (bottom - view_height) / total))
        except Exception:
            pass

    def _bind_mousewheel(self) -> None:
        self.canvas.bind("<Enter>", self._bind_all_wheel)
        self.canvas.bind("<Leave>", self._unbind_all_wheel)

    def _bind_card_wheel(self, card: tk.Widget) -> None:
        card.bind("<Enter>", self._bind_all_wheel, add="+")
        card.bind("<Leave>", self._unbind_all_wheel, add="+")

    def _bind_all_wheel(self, _event: tk.Event | None = None) -> None:
        if self._scroll_bindings:
            return
        self._scroll_bindings = ["<MouseWheel>", "<Button-4>", "<Button-5>"]
        try:
            self.bind_all("<MouseWheel>", self._on_mousewheel)
            self.bind_all("<Button-4>", self._on_mousewheel)
            self.bind_all("<Button-5>", self._on_mousewheel)
        except Exception:
            self._scroll_bindings = []

    def _unbind_all_wheel(self, _event: tk.Event | None = None) -> None:
        if not self._scroll_bindings:
            return
        try:
            self.unbind_all("<MouseWheel>")
            self.unbind_all("<Button-4>")
            self.unbind_all("<Button-5>")
        except Exception:
            pass
        self._scroll_bindings = []

    def _on_mousewheel(self, event: tk.Event) -> str:
        try:
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -int(event.delta / 120) if event.delta else 0
            if delta:
                self.canvas.yview_scroll(delta * 3, "units")
        except Exception:
            pass
        return "break"

    # ── 过滤框占位符 ─────────────────────────────────────
    def _on_filter_focus_in(self, _event: tk.Event | None = None) -> None:
        if self._placeholder_active:
            self.filter_entry.delete(0, "end")
            self.filter_entry.configure(foreground=self.palette.text)
            self._placeholder_active = False

    def _on_filter_focus_out(self, _event: tk.Event | None = None) -> None:
        if not self.filter_entry.get():
            self._placeholder_active = True
            self.filter_entry.insert(0, self._placeholder_text)
            self.filter_entry.configure(foreground=self.palette.muted)

    def _on_filter_changed(self) -> None:
        if self._placeholder_active:
            return
        text = self.filter_var.get()
        if text == self._filter_text:
            return
        self._filter_text = text
        self._rebuild()

    def set_filter(self, text: str) -> None:
        """测试/快捷键用：直接设置过滤文本。"""
        self._on_filter_focus_in()
        self.filter_entry.delete(0, "end")
        self.filter_entry.insert(0, text)
        self.filter_var.set(text)

    # ── 焦点辅助 ─────────────────────────────────────────
    def focus_list(self) -> None:
        try:
            self.canvas.focus_set()
        except Exception:
            pass

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0.0)
