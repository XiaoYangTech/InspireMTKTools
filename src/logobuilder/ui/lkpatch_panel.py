"""LK 修补面板（亿方联科工具箱 / InspireMTKTools 第二工作区）。

功能：
* 添加 LK 镜像（多选文件 / 选择文件夹自动识别 lk*.img、lk*.bin、lk2.*、lk_a.*、lk_b.*）；
* 后台扫描：解析 MTK LK 头、架构、警告文本、内核参数、候选补丁点；
* 明细：文件信息、候选补丁表（类别/偏移/置信度/证据）、警告文本、内核参数保护列表、说明；
* 选项：Orange/Red 警告+等待、dm-verity 警告+等待、同时清空警告文本；输出到新文件或覆盖原文件（自动备份）；
* 预览补丁（dry-run，不写文件）/ 应用补丁（写入前备份 + 后置校验）/ 校验结果 / 还原备份 / 刷写说明；
* A/B 双槽提醒；所有操作复用主窗口后台线程、日志面板和中文错误对话框。

本模块只调用 ``logobuilder.core.lkpatch`` 公共 API（见 docs/spec_lkpatch.md §4），
不实现任何字节级格式/补丁逻辑；核心缺失时面板显示降级提示并禁用按钮。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

import tkinter as tk

import ttkbootstrap as tb

from . import core_bridge
from .dialogs import BaseDialog
from .imaging import platform_reveal_command
from .theme import Metrics, Palette, UiFonts
from .widgets import LinkButton, Tooltip

# LK 文件识别规则（文件名，不含路径；大小写不敏感）
LK_FILE_PATTERNS = (
    "lk*.img",
    "lk*.bin",
    "lk*.img",
    "lk*.bin",
    "lk2.*",
    "lk_a.*",
    "lk_b.*",
)


# ── 数据模型 ───────────────────────────────────────────────
@dataclass
class LkFileEntry:
    """一个已扫描（或扫描失败）的 LK 镜像。"""

    path: Path
    image: Any = None
    report: Any = None
    arch: str = ""
    verdict: str = "unsupported"
    sha256: str = ""
    error: str | None = None
    scanned: bool = False

    @property
    def findings(self) -> list[Any]:
        if self.report is None:
            return []
        return list(getattr(self.report, "findings", []) or [])

    @property
    def key(self) -> str:
        return str(self.path)


@dataclass
class LkPatchResult:
    """一次应用/校验的结果（面板内保存，供校验/还原使用）。"""

    output: Path | None = None
    backup: Path | None = None
    changes: list[str] = field(default_factory=list)
    verify: dict[str, Any] = field(default_factory=dict)
    rescan: LkFileEntry | None = None


class LkPatchRefused(RuntimeError):
    """后置校验不通过时不写文件。"""


# ── 纯函数（可在工作线程执行）────────────────────────────
def is_lk_filename(name: str) -> bool:
    lowered = name.lower()
    if not (lowered.endswith(".img") or lowered.endswith(".bin")):
        return False
    stem = Path(lowered).stem
    if stem.startswith("lk"):
        return True
    return lowered in {"lk2", "lk_a", "lk_b"} or stem in {"lk2", "lk_a", "lk_b"}


def collect_lk_files(folder: str | Path) -> list[Path]:
    """列出文件夹里符合 LK 命名规则的镜像（仅顶层，按名称排序）。"""
    directory = Path(folder)
    if not directory.is_dir():
        return []
    result: list[Path] = []
    try:
        for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
            if child.is_file() and is_lk_filename(child.name):
                result.append(child)
    except OSError:
        return []
    return result


def scan_one(path: str | Path, lkpatch: Any) -> LkFileEntry:
    """读取并扫描单个 LK 镜像（工作线程调用）。"""
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        return LkFileEntry(path=file_path, error=f"无法读取文件：{exc}", verdict="error")
    try:
        image = lkpatch.parse_lk_image(data, file_path)
        arch = lkpatch.detect_arch(image)
        report = lkpatch.scan(image)
        sha256 = str(getattr(image, "sha256", "") or "")
        return LkFileEntry(
            path=file_path,
            image=image,
            report=report,
            arch=str(arch or getattr(report, "arch", "") or "unknown"),
            verdict=str(getattr(report, "verdict", "unsupported") or "unsupported"),
            sha256=sha256,
            scanned=True,
        )
    except Exception as exc:  # noqa: BLE001 - 单文件失败不影响其它文件
        return LkFileEntry(path=file_path, error=str(exc) or type(exc).__name__, verdict="error")


def scan_paths(paths: Iterable[str | Path], lkpatch: Any, ctx: Any = None, tr: Any = None) -> list[LkFileEntry]:
    """批量扫描（工作线程调用）；``ctx`` 用于进度/取消。"""
    items = [Path(p) for p in paths]
    entries: list[LkFileEntry] = []
    total = max(1, len(items))
    for index, path in enumerate(items, start=1):
        if ctx is not None:
            ctx.check_cancel()
            message = (
                tr.t("lk.scan_progress", index=index, total=total, name=path.name)
                if tr is not None
                else f"{index}/{total}: {path.name}"
            )
            ctx.progress(index / total, message)
        entries.append(scan_one(path, lkpatch))
    return entries


def dry_run_entry(
    entry: LkFileEntry,
    categories: list[str],
    clear_text: bool,
    lkpatch: Any,
) -> dict[str, Any]:
    """dry-run：计算补丁结果但不写文件（工作线程调用）。"""
    if entry.image is None or entry.report is None:
        raise ValueError("该文件尚未成功扫描")
    new_data, changes = lkpatch.apply_patches(
        entry.image,
        entry.report,
        categories=list(categories),
        clear_warning_text=bool(clear_text),
    )
    verify = lkpatch.verify_patch(entry.image, new_data, entry.report)
    return {
        "changes": [str(item) for item in changes],
        "verify": dict(verify or {}),
        "size": len(new_data),
        "new_data": bytes(new_data),
    }


def apply_entry(
    entry: LkFileEntry,
    categories: list[str],
    clear_text: bool,
    inplace: bool,
    backup_dir: str | Path | None,
    lkpatch: Any,
) -> LkPatchResult:
    """应用补丁：后置校验 → 备份 → 写盘 → 重新扫描输出文件（工作线程调用）。"""
    dry = dry_run_entry(entry, categories, clear_text, lkpatch)
    verify = dry["verify"]
    if not verify.get("patch_ok"):
        raise LkPatchRefused("后置校验未通过，已拒绝写入文件")
    for flag, label in (
        ("size_same", "文件大小"),
        ("header_same", "头部 0x200"),
        ("guard_counts_same", "内核参数计数"),
    ):
        if flag in verify and not verify.get(flag):
            raise LkPatchRefused(f"{label}校验未通过，已拒绝写入文件")
    backup = lkpatch.save_backup(entry.image, backup_dir)
    output = Path(entry.path) if inplace else Path(lkpatch.patched_path(entry.path))
    _atomic_write(output, dry["new_data"])
    rescan = scan_one(output, lkpatch)
    return LkPatchResult(
        output=output,
        backup=Path(backup) if backup else None,
        changes=dry["changes"],
        verify=verify,
        rescan=rescan,
    )


def verify_output(
    entry: LkFileEntry,
    output_path: str | Path,
    lkpatch: Any,
) -> dict[str, Any]:
    """校验一个已写入的补丁文件（工作线程调用）。"""
    if entry.image is None or entry.report is None:
        raise ValueError("该文件尚未成功扫描")
    path = Path(output_path)
    data = path.read_bytes()
    verify = dict(lkpatch.verify_patch(entry.image, data, entry.report) or {})
    verify["output"] = str(path)
    return verify


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


# ── 文案 / 展示辅助 ────────────────────────────────────────
def arch_label(tr: Any, arch: str) -> str:
    return {
        "arm32-thumb": tr.t("lk.arch_arm32"),
        "aarch64": tr.t("lk.arch_aarch64"),
    }.get(str(arch), tr.t("lk.arch_unknown"))


def verdict_label(tr: Any, verdict: str, error: str | None = None) -> str:
    if error:
        return tr.t("lk.verdict_error")
    text = str(verdict)
    if text in ("supported-arm32", "supported-arm32-thumb") or text.startswith("supported-arm32"):
        return tr.t("lk.verdict_supported_arm32")
    if text.startswith("supported-aarch64"):
        return tr.t("lk.verdict_supported_aarch64")
    if text == "already-patched":
        return tr.t("lk.verdict_already")
    return tr.t("lk.verdict_unsupported")


def category_label(tr: Any, category: str) -> str:
    return {
        "orange_state": tr.t("lk.category_orange_state"),
        "red_state": tr.t("lk.category_red_state"),
        "dm_verity": tr.t("lk.category_dm_verity"),
        "warning_text": tr.t("lk.category_warning_text"),
    }.get(str(category), str(category))


def hex_offset(value: Any) -> str:
    try:
        return f"0x{int(value):08X}"
    except (TypeError, ValueError):
        return "—"


def human_size(num: Any) -> str:
    return core_bridge.human_size(num)


def _short(text: Any, limit: int = 96) -> str:
    value = str(text or "").replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _verify_flag(tr: Any, value: Any) -> str:
    return tr.t("lk.yes") if value else tr.t("lk.no")


def format_ranges(value: Any, *, limit: int = 8, one_line: bool = False) -> str:
    """把 core 返回的差异区间（[(start,end), ...] 或字符串）格式化成人读形式。"""
    if value is None:
        return "—"
    items: list[str] = []
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    items.append(f"0x{int(item[0]):08X}-0x{int(item[1]):08X}")
                except (TypeError, ValueError):
                    items.append(str(item))
            else:
                items.append(str(item))
    else:
        items = [str(value)]
    if not items:
        return "—"
    if len(items) > limit:
        items = items[:limit] + ["…"]
    return ("、".join(items) if one_line else "\n".join(items))


def _join_lines(value: Any, *, limit: int = 6) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        text = str(value)
        items = text.splitlines() if "\n" in text else [text]
    if len(items) > limit:
        items = items[:limit] + ["…"]
    return "\n".join(items) if items else "—"


# ── 面板 ───────────────────────────────────────────────────
class LkPatchPanel(tk.Frame):
    """LK 修补主面板（放在主窗口 Notebook 的「LK 修补」页）。"""

    def __init__(self, master: tk.Misc, app: Any) -> None:
        palette: Palette = app.palette
        super().__init__(master, bg=palette.window_bg)
        self.app = app
        self.palette = palette
        self.metrics: Metrics = app.metrics
        self.fonts: UiFonts = app.fonts
        self.entries: dict[str, LkFileEntry] = {}
        self.order: list[str] = []
        self.selected_key: str | None = None
        self.last_results: dict[str, LkPatchResult] = {}
        self._busy = False
        self._building = True
        lk_ready, lk_error = core_bridge.lkpatch_status()
        self.core_ready = lk_ready
        self.core_error = lk_error
        self._build()
        self._building = False
        self.refresh_actions()

    # ── 取 core ──────────────────────────────────────────
    def _lk(self) -> Any:
        return core_bridge.get_lkpatch()

    # ── 构建界面 ─────────────────────────────────────────
    def _build(self) -> None:
        palette = self.palette
        m = self.metrics
        tr = self.app.tr
        root = tk.Frame(self, bg=palette.window_bg)
        root.pack(fill="both", expand=True, padx=m.pad, pady=(m.px(8), m.px(6)))

        # 标题行
        head = tk.Frame(root, bg=palette.window_bg)
        head.pack(fill="x")
        tk.Label(
            head,
            text=tr.t("lk.title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h2,
            anchor="w",
        ).pack(side="left")
        self.core_badge = tk.Label(
            head,
            text=tr.t("lk.core_ready") if self.core_ready else tr.t("lk.core_missing", error=self.core_error or ""),
            bg=palette.tag_ok_bg if self.core_ready else palette.tag_unknown_bg,
            fg=palette.tag_ok_fg if self.core_ready else palette.tag_unknown_fg,
            font=self.fonts.tiny,
            padx=m.px(8),
            pady=m.px(2),
        )
        self.core_badge.pack(side="left", padx=(m.px(10), 0))
        tk.Label(
            head,
            text=tr.t("lk.risk_short"),
            bg=palette.window_bg,
            fg=palette.warning,
            font=self.fonts.small,
            anchor="e",
            justify="right",
            wraplength=m.px(430),
        ).pack(side="right")

        desc = tk.Label(
            root,
            text=tr.t("lk.desc"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(1100),
        )
        desc.pack(fill="x", pady=(m.px(2), m.px(6)))

        # 工具栏
        tools = tk.Frame(root, bg=palette.window_bg)
        tools.pack(fill="x", pady=(0, m.px(6)))
        self.add_files_button = tb.Button(
            tools,
            text=tr.t("lk.add_files"),
            bootstyle="primary",
            command=self.add_files_dialog,
        )
        self.add_files_button.pack(side="left")
        self.add_folder_button = tb.Button(
            tools,
            text=tr.t("lk.add_folder"),
            bootstyle="outline-secondary",
            command=self.add_folder_dialog,
        )
        self.add_folder_button.pack(side="left", padx=(m.px(6), 0))
        self.rescan_button = tb.Button(
            tools,
            text=tr.t("lk.scan"),
            bootstyle="outline-secondary",
            command=self.rescan_all,
        )
        self.rescan_button.pack(side="left", padx=(m.px(6), 0))
        self.clear_button = tb.Button(
            tools,
            text=tr.t("lk.clear"),
            bootstyle="outline-secondary",
            command=self.clear_files,
        )
        self.clear_button.pack(side="left", padx=(m.px(6), 0))
        self.flash_button = tb.Button(
            tools,
            text=tr.t("lk.flash_guide"),
            bootstyle="outline-secondary",
            command=self.show_flash_guide,
        )
        self.flash_button.pack(side="right")

        # 主体：左右分栏
        body = tk.PanedWindow(
            root,
            orient="horizontal",
            bg=palette.border,
            sashwidth=m.px(6),
            sashrelief="flat",
            bd=0,
        )
        self.body = body  # 在 _build 末尾最后 pack，确保底部选项/操作行不被裁切

        # 左：文件表 + A/B 提醒
        left = tk.Frame(body, bg=palette.window_bg)
        body.add(left, minsize=m.px(420), width=m.px(560))
        files_head = tk.Frame(left, bg=palette.window_bg)
        files_head.pack(fill="x", pady=(0, m.px(3)))
        self.files_title = tk.Label(
            files_head,
            text=tr.t("lk.files_title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        )
        self.files_title.pack(side="left")
        self.files_count = tk.Label(
            files_head,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
        )
        self.files_count.pack(side="right")

        tree_wrap = tk.Frame(left, bg=palette.border, highlightthickness=0)
        tree_wrap.pack(fill="both", expand=True)
        self.file_tree = tb.Treeview(
            tree_wrap,
            columns=("file", "size", "arch", "verdict", "sha"),
            show="headings",
            selectmode="browse",
            bootstyle="primary",
            height=6,
        )
        for column, key, width, anchor in (
            ("file", "lk.col_file", 125, "w"),
            ("size", "lk.col_size", 66, "e"),
            ("arch", "lk.col_arch", 92, "center"),
            ("verdict", "lk.col_verdict", 168, "w"),
            ("sha", "lk.col_sha", 72, "center"),
        ):
            self.file_tree.heading(column, text=tr.t(key))
            self.file_tree.column(column, width=m.px(width), anchor=anchor, stretch=column == "file")
        self.file_tree.pack(side="left", fill="both", expand=True)
        table_scroll = tb.Scrollbar(tree_wrap, orient="vertical", command=self.file_tree.yview)
        table_scroll.pack(side="right", fill="y")
        self.file_tree.configure(yscrollcommand=table_scroll.set)
        self.file_tree.tag_configure("ok", foreground=palette.tag_ok_fg, background=palette.tag_ok_bg)
        self.file_tree.tag_configure("warn", foreground=palette.tag_mod_fg, background=palette.tag_mod_bg)
        self.file_tree.tag_configure("muted", foreground=palette.muted)
        self.file_tree.tag_configure("error", foreground=palette.tag_unknown_fg, background=palette.tag_unknown_bg)
        self.file_tree.bind("<<TreeviewSelect>>", self._on_file_select)

        self.empty_hint = tk.Label(
            left,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(520),
        )
        self.empty_hint.pack(fill="x", pady=(m.px(4), 0))
        self.ab_label = tk.Label(
            left,
            text="",
            bg=palette.tag_mod_bg,
            fg=palette.tag_mod_fg,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(520),
            padx=m.px(8),
            pady=m.px(3),
        )
        self.ab_label.pack(fill="x", pady=(m.px(4), 0))
        self._set_ab_reminder([])

        # 右：详情
        right = tk.Frame(body, bg=palette.window_bg)
        body.add(right, minsize=m.px(400))

        info_wrap = tk.Frame(right, bg=palette.surface, highlightthickness=1, highlightbackground=palette.border)
        info_wrap.pack(fill="x", pady=(0, m.px(4)))
        self.detail_title = tk.Label(
            info_wrap,
            text=tr.t("lk.detail_title"),
            bg=palette.surface,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        )
        self.detail_title.grid(row=0, column=0, columnspan=6, sticky="w", padx=m.px(8), pady=(m.px(2), m.px(1)))
        self._info_vars: dict[str, tk.StringVar] = {}

        def _info_label(row: int, col: int, key: str, label_key: str, *, columnspan: int = 1) -> tk.StringVar:
            tk.Label(
                info_wrap,
                text=tr.t(label_key),
                bg=palette.surface,
                fg=palette.muted,
                font=self.fonts.tiny,
                anchor="w",
            ).grid(row=row, column=col, sticky="w", padx=(m.px(8), m.px(3)), pady=0)
            var = tk.StringVar(value="—")
            tk.Label(
                info_wrap,
                textvariable=var,
                bg=palette.surface,
                fg=palette.text,
                font=self.fonts.tiny,
                anchor="w",
                justify="left",
                wraplength=m.px(190),
            ).grid(
                row=row,
                column=col + 1,
                columnspan=columnspan,
                sticky="w",
                padx=(0, m.px(10)),
                pady=0,
            )
            self._info_vars[key] = var
            return var

        # 第 1 行：标题 + 路径（长路径自然截断，节省纵向空间）
        path_var = tk.StringVar(value="—")
        tk.Label(
            info_wrap,
            textvariable=path_var,
            bg=palette.surface,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
            justify="left",
        ).grid(row=0, column=1, columnspan=5, sticky="w", padx=(0, m.px(8)), pady=0)
        self._info_vars["path"] = path_var

        # 第 2 行：大小 / 分区名 / 架构
        _info_label(1, 0, "size", "lk.detail_size")
        _info_label(1, 2, "name", "lk.detail_name")
        _info_label(1, 4, "arch", "lk.detail_arch")
        # 第 3 行：Magic 头 / 扩展 magic / 结论
        _info_label(2, 0, "header", "lk.detail_header")
        _info_label(2, 2, "ext", "lk.detail_ext")
        _info_label(2, 4, "verdict", "lk.detail_verdict")
        # 第 4 行：容器类型 + SHA-256（同一行显示）
        _info_label(3, 0, "container", "lk.detail_container")
        for col in (1, 3, 5):
            info_wrap.grid_columnconfigure(col, weight=1)

        self.sha_label = tk.Label(
            info_wrap,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        )
        self.sha_label.grid(row=3, column=2, columnspan=4, sticky="w", padx=(0, m.px(8)), pady=0)

        # 候选补丁表
        findings_head = tk.Frame(right, bg=palette.window_bg)
        findings_head.pack(fill="x")
        self.findings_title = tk.Label(
            findings_head,
            text=tr.t("lk.findings_title"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        )
        self.findings_title.pack(side="left")
        self.findings_count = tk.Label(
            findings_head,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
        )
        self.findings_count.pack(side="right")
        findings_wrap = tk.Frame(right, bg=palette.border)
        self._findings_wrap = findings_wrap
        self.findings_tree = tb.Treeview(
            findings_wrap,
            columns=("category", "offset", "confidence", "evidence"),
            show="headings",
            selectmode="browse",
            bootstyle="primary",
            height=3,
        )
        for column, key, width, anchor in (
            ("category", "lk.col_category", 150, "w"),
            ("offset", "lk.col_offset", 90, "center"),
            ("confidence", "lk.col_confidence", 65, "center"),
            ("evidence", "lk.col_evidence", 240, "w"),
        ):
            self.findings_tree.heading(column, text=tr.t(key))
            self.findings_tree.column(column, width=m.px(width), anchor=anchor, stretch=column == "evidence")
        self.findings_tree.pack(side="left", fill="both", expand=True)
        findings_scroll = tb.Scrollbar(findings_wrap, orient="vertical", command=self.findings_tree.yview)
        findings_scroll.pack(side="right", fill="y")
        self.findings_tree.configure(yscrollcommand=findings_scroll.set)
        self.findings_tree.tag_configure("high", foreground=palette.tag_ok_fg)
        self.findings_tree.tag_configure("low", foreground=palette.tag_mod_fg)

        # 警告文本 / 内核参数
        lists = tk.Frame(right, bg=palette.window_bg)
        self._lists_frame = lists
        lists.grid_columnconfigure(0, weight=1)
        lists.grid_columnconfigure(1, weight=1)
        self.warning_list = self._make_listbox_block(lists, 0, "lk.warnings_title", height=3)
        self.guard_list = self._make_listbox_block(lists, 1, "lk.guard_title", height=3)
        self.notes_label = tk.Label(
            right,
            text="",
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
            justify="left",
            wraplength=m.px(520),
        )
        self.notes_label.pack(fill="x", pady=(m.px(2), 0))

        # 右栏布局：警告文本/内核参数列表与说明固定在底部，候选补丁表自适应剩余高度
        self.notes_label.pack_forget()
        self._lists_frame.pack_forget()
        self._findings_wrap.pack_forget()
        self.notes_label.pack(side="bottom", fill="x", pady=(m.px(2), 0))
        self._lists_frame.pack(side="bottom", fill="x")
        self._findings_wrap.pack(fill="both", expand=True, pady=(m.px(2), m.px(4)))

        # 选项行
        options = tk.Frame(root, bg=palette.surface, highlightthickness=1, highlightbackground=palette.border)
        self.options_frame = options
        opt_head = tk.Label(
            options,
            text=tr.t("lk.options_title"),
            bg=palette.surface,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        )
        opt_head.grid(row=0, column=0, sticky="w", padx=(m.px(8), m.px(8)), pady=(m.px(4), 0))
        self.orange_var = tk.BooleanVar(value=True)
        self.dm_var = tk.BooleanVar(value=True)
        self.clear_text_var = tk.BooleanVar(value=False)
        self.orange_check = tb.Checkbutton(
            options,
            text=tr.t("lk.opt_orange"),
            variable=self.orange_var,
            bootstyle="round-toggle",
        )
        self.orange_check.grid(row=0, column=1, sticky="w", padx=(0, m.px(10)), pady=(m.px(4), 0))
        Tooltip(self.orange_check, tr.t("lk.opt_orange_hint"))
        self.dm_check = tb.Checkbutton(
            options,
            text=tr.t("lk.opt_dmverity"),
            variable=self.dm_var,
            bootstyle="round-toggle",
        )
        self.dm_check.grid(row=0, column=2, sticky="w", padx=(0, m.px(10)), pady=(m.px(4), 0))
        Tooltip(self.dm_check, tr.t("lk.opt_dmverity_hint"))
        self.clear_text_check = tb.Checkbutton(
            options,
            text=tr.t("lk.opt_clear_text"),
            variable=self.clear_text_var,
            bootstyle="round-toggle",
        )
        self.clear_text_check.grid(row=0, column=3, sticky="w", pady=(m.px(4), 0))
        Tooltip(self.clear_text_check, tr.t("lk.opt_clear_text_hint"))

        mode_label = tk.Label(
            options,
            text=tr.t("lk.output_mode"),
            bg=palette.surface,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="e",
        )
        mode_label.grid(row=0, column=4, sticky="e", padx=(m.px(10), m.px(4)), pady=(m.px(4), 0))
        self.output_mode = tk.StringVar(value="new")
        self.new_radio = tb.Radiobutton(
            options,
            text=tr.t("lk.output_new"),
            value="new",
            variable=self.output_mode,
            bootstyle="primary",
        )
        self.new_radio.grid(row=0, column=5, sticky="w", pady=(m.px(4), 0))
        self.inplace_radio = tb.Radiobutton(
            options,
            text=tr.t("lk.output_inplace"),
            value="inplace",
            variable=self.output_mode,
            bootstyle="primary",
        )
        self.inplace_radio.grid(row=0, column=6, sticky="w", padx=(0, m.px(10)), pady=(m.px(4), 0))
        options.grid_columnconfigure(3, weight=1)
        self.backup_hint = tk.Label(
            options,
            text="",
            bg=palette.surface,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        )
        self.backup_hint.grid(row=1, column=0, columnspan=7, sticky="w", padx=m.px(8), pady=(0, m.px(4)))

        # 操作行
        actions = tk.Frame(root, bg=palette.window_bg)
        self.actions_frame = actions
        self.preview_button = tb.Button(
            actions,
            text=tr.t("lk.preview"),
            bootstyle="outline-primary",
            command=self.preview_patch,
        )
        self.preview_button.pack(side="left")
        self.apply_button = tb.Button(
            actions,
            text=tr.t("lk.apply"),
            bootstyle="primary",
            command=self.apply_patch,
        )
        self.apply_button.pack(side="left", padx=(m.px(6), 0))
        self.verify_button = tb.Button(
            actions,
            text=tr.t("lk.verify"),
            bootstyle="secondary",
            command=self.verify_patch,
        )
        self.verify_button.pack(side="left", padx=(m.px(6), 0))
        self.restore_button = tb.Button(
            actions,
            text=tr.t("lk.restore"),
            bootstyle="outline-secondary",
            command=self.restore_backup,
        )
        self.restore_button.pack(side="left", padx=(m.px(6), 0))
        self.status_label = tk.Label(
            actions,
            text=tr.t("lk.sample_hint"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="e",
            justify="right",
            wraplength=m.px(650),
        )
        self.status_label.pack(side="right")

        # 先贴底放置操作行/选项行，再让主体填满剩余空间（小窗口下也不会裁切按钮）
        self.actions_frame.pack(side="bottom", fill="x", pady=(m.px(6), 0))
        self.options_frame.pack(side="bottom", fill="x", pady=(m.px(6), 0))
        self.body.pack(side="top", fill="both", expand=True)
        self._update_empty_state()

    def _make_listbox_block(self, master: tk.Misc, column: int, title_key: str, *, height: int = 3) -> tk.Listbox:
        palette = self.palette
        m = self.metrics
        frame = tk.Frame(master, bg=palette.window_bg)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, m.px(4)) if column == 0 else (m.px(4), 0))
        tk.Label(
            frame,
            text=self.app.tr.t(title_key),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.tiny,
            anchor="w",
        ).pack(fill="x")
        listbox = tk.Listbox(
            frame,
            height=height,
            bg=palette.surface_alt,
            fg=palette.text,
            font=self.fonts.tiny,
            bd=0,
            highlightthickness=1,
            highlightbackground=palette.border,
            selectbackground=palette.accent,
            activestyle="none",
        )
        listbox.pack(fill="both", expand=True)
        return listbox

    # ── 状态 / 文案 ──────────────────────────────────────
    def set_status(self, text: str, level: str = "normal") -> None:
        colors = {
            "normal": self.palette.muted,
            "success": self.palette.success,
            "warning": self.palette.warning,
            "error": self.palette.danger,
        }
        try:
            self.status_label.configure(text=text, fg=colors.get(level, self.palette.muted))
        except Exception:
            pass

    def _update_empty_state(self) -> None:
        tr = self.app.tr
        if not self.core_ready:
            self.empty_hint.configure(
                text=tr.t("lk.core_missing", error=self.core_error or ""),
                fg=self.palette.danger,
            )
        elif not self.order:
            self.empty_hint.configure(
                text=tr.t("lk.no_files_hint"),
                fg=self.palette.muted,
            )
        else:
            self.empty_hint.configure(text="")
        self.files_count.configure(
            text=f"{len(self.entries)}" if self.entries else ""
        )

    def _set_ab_reminder(self, entries: list[LkFileEntry]) -> None:
        tr = self.app.tr
        try:
            if len(entries) > 1:
                names = "、".join(entry.path.name for entry in entries)
                self.ab_label.configure(text=tr.t("lk.ab_reminder", count=len(entries)) + "  " + names)
                self.ab_label.pack(fill="x", pady=(self.metrics.px(4), 0))
            else:
                self.ab_label.configure(text="")
                self.ab_label.pack_forget()
        except Exception:
            pass

    # ── 文件添加 / 扫描 ──────────────────────────────────
    def add_files_dialog(self) -> None:
        if self._busy:
            return
        from tkinter import filedialog

        paths = filedialog.askopenfilenames(
            parent=self.app.root,
            title=self.app.tr.t("lk.files_title_dialog"),
            initialdir=str(self.app.settings.last_open_dir or Path.home()),
            filetypes=[
                (self.app.tr.t("lk.filter_lk"), "*.img;*.bin"),
                (self.app.tr.t("lk.filter_all"), "*.*"),
            ],
        )
        if paths:
            self.add_paths([Path(p) for p in paths])

    def add_folder_dialog(self) -> None:
        if self._busy:
            return
        from tkinter import filedialog

        folder = filedialog.askdirectory(
            parent=self.app.root,
            title=self.app.tr.t("lk.folder_title"),
            initialdir=str(self.app.settings.last_open_dir or Path.home()),
            mustexist=True,
        )
        if not folder:
            return
        self.add_paths(collect_lk_files(folder))

    def add_paths(self, paths: Iterable[str | Path], *, auto_scan: bool = True) -> list[Path]:
        """加入文件到列表（默认自动扫描）。返回新增的路径列表。"""
        added: list[Path] = []
        for path in paths:
            p = Path(path)
            key = str(p)
            if key in self.entries:
                continue
            entry = LkFileEntry(path=p)
            self.entries[key] = entry
            self.order.append(key)
            self._insert_row(entry)
            added.append(p)
        if added:
            self.app.log("INFO", self.app.tr.t("lk.file_added", count=len(added)))
            self._update_empty_state()
        if added and auto_scan:
            self.scan_files(added, merge=True)
        return added

    def _insert_row(self, entry: LkFileEntry) -> None:
        tr = self.app.tr
        tag = self._row_tag(entry)
        sha_short = (entry.sha256 or "")[:8]
        size_text = human_size(entry.path.stat().st_size) if entry.path.exists() else "—"
        self.file_tree.insert(
            "",
            "end",
            iid=entry.key,
            values=(
                entry.path.name,
                size_text,
                arch_label(tr, entry.arch) if entry.scanned else "—",
                verdict_label(tr, entry.verdict, entry.error)
                if (entry.scanned or entry.error)
                else tr.t("common.loading"),
                sha_short,
            ),
            tags=(tag,),
        )

    def _row_tag(self, entry: LkFileEntry) -> str:
        if entry.error:
            return "error"
        verdict = str(entry.verdict)
        if verdict == "already-patched" or verdict.startswith("supported"):
            return "ok"
        return "muted"

    def _update_row(self, entry: LkFileEntry) -> None:
        tr = self.app.tr
        if not self.file_tree.exists(entry.key):
            return
        try:
            size_text = human_size(entry.path.stat().st_size) if entry.path.exists() else "—"
        except OSError:
            size_text = "—"
        self.file_tree.item(
            entry.key,
            values=(
                entry.path.name,
                size_text,
                arch_label(tr, entry.arch) if entry.scanned or entry.error else "—",
                verdict_label(tr, entry.verdict, entry.error) if (entry.scanned or entry.error) else tr.t("common.loading"),
                (entry.sha256 or "")[:8],
            ),
            tags=(self._row_tag(entry),),
        )

    def rescan_all(self) -> None:
        if self._busy:
            return
        paths = [self.entries[key].path for key in self.order]
        if not paths:
            self.set_status(self.app.tr.t("lk.scan_none"), "warning")
            return
        self.scan_files(paths, merge=True)

    def scan_files(self, paths: Iterable[str | Path], *, merge: bool = True) -> None:
        """后台扫描文件列表。"""
        if self._busy:
            self.set_status(self.app.tr.t("lk.busy"), "warning")
            return
        items = [Path(p) for p in paths]
        if not items:
            self.set_status(self.app.tr.t("lk.scan_none"), "warning")
            return
        self._set_busy(True)
        tr = self.app.tr
        lkpatch = self._lk()

        def work(ctx: Any) -> list[LkFileEntry]:
            return scan_paths(items, lkpatch, ctx=ctx, tr=tr)

        def done(entries: list[LkFileEntry]) -> None:
            self._ingest_entries(entries)
            self._set_busy(False)

        def failed(info: dict[str, Any]) -> None:
            self._set_busy(False)
            self.app._show_error_data(info)

        self.app._run_task(work, title=tr.t("lk.scanning"), on_done=done, on_error=failed)

    def _ingest_entries(self, entries: list[LkFileEntry]) -> None:
        """把扫描结果合并进表格/详情（主线程或测试同步调用）。"""
        tr = self.app.tr
        for entry in entries:
            self.entries[entry.key] = entry
            if entry.key not in self.order:
                self.order.append(entry.key)
            if not self.file_tree.exists(entry.key):
                self._insert_row(entry)
            else:
                self._update_row(entry)
        self._set_ab_reminder([self.entries[key] for key in self.order])
        self._update_empty_state()
        first_ok = next((e for e in entries if e.error is None), None)
        if first_ok is not None:
            self.select_path(first_ok.key)
        elif entries:
            self.select_path(entries[0].key)
        self.set_status(tr.t("lk.scan_done", count=len(entries)), "success")
        for entry in entries:
            self.app.log(
                "WARNING" if entry.error else "INFO",
                tr.t(
                    "lk.log_scan_item",
                    name=entry.path.name,
                    verdict=verdict_label(tr, entry.verdict, entry.error),
                    arch=arch_label(tr, entry.arch),
                    findings=len(entry.findings),
                ),
            )
        self.refresh_actions()
        try:
            self.app._update_status_info()
        except Exception:
            pass

    def scan_sync(self, paths: Iterable[str | Path]) -> list[LkFileEntry]:
        """同步扫描（测试/截图用，避开后台线程）。"""
        entries = scan_paths(paths, self._lk(), ctx=None, tr=self.app.tr)
        self._ingest_entries(entries)
        return entries

    def clear_files(self) -> None:
        if self._busy:
            return
        for key in list(self.order):
            if self.file_tree.exists(key):
                self.file_tree.delete(key)
        self.entries.clear()
        self.order.clear()
        self.selected_key = None
        self.last_results.clear()
        try:
            self.app._update_status_info()
        except Exception:
            pass
        self._refresh_detail(None)
        self._set_ab_reminder([])
        self._update_empty_state()
        self.refresh_actions()
        self.set_status(self.app.tr.t("lk.no_files_hint"), "normal")

    # ── 选择 / 明细 ──────────────────────────────────────
    def _on_file_select(self, _event: tk.Event | None = None) -> None:
        selection = self.file_tree.selection()
        if selection:
            self.select_path(selection[0], select_in_tree=False)

    def select_path(self, key: str, *, select_in_tree: bool = True) -> None:
        if key not in self.entries:
            return
        self.selected_key = key
        if select_in_tree and self.file_tree.exists(key):
            try:
                self.file_tree.selection_set(key)
                self.file_tree.see(key)
            except Exception:
                pass
        self._refresh_detail(self.entries.get(key))
        self.refresh_actions()

    def selected_entry(self) -> LkFileEntry | None:
        if self.selected_key is None:
            return None
        return self.entries.get(self.selected_key)

    def selected_categories(self) -> list[str]:
        categories: list[str] = []
        if self.orange_var.get():
            categories.append("orange_state")
            categories.append("red_state")
        if self.dm_var.get():
            categories.append("dm_verity")
        return categories

    def _refresh_detail(self, entry: LkFileEntry | None) -> None:
        tr = self.app.tr
        for key, var in self._info_vars.items():
            var.set("—")
        self.sha_label.configure(text="")
        self.findings_tree.delete(*self.findings_tree.get_children())
        self.warning_list.delete(0, "end")
        self.guard_list.delete(0, "end")
        self.notes_label.configure(text="")
        self.findings_count.configure(text="")
        if entry is None:
            return
        report = entry.report
        image = entry.image
        self._info_vars["path"].set(_short(str(entry.path), 64))
        try:
            size_text = human_size(entry.path.stat().st_size)
        except OSError:
            size_text = "—"
        self._info_vars["size"].set(size_text)
        if entry.error:
            self._info_vars["verdict"].set(tr.t("lk.unsupported", reason=entry.error))
            return
        header = getattr(image, "header", None)
        is_container = bool(getattr(image, "is_container", header is not None))
        if header:
            magic = int.from_bytes(header[0:4], "little") if len(header) >= 4 else 0
            declared = int.from_bytes(header[4:8], "little") if len(header) >= 8 else 0
            self._info_vars["header"].set(f"0x{magic:08X} · {declared:,} B")
            ext = int.from_bytes(header[0x30:0x34], "little") if len(header) >= 0x34 else 0
            self._info_vars["ext"].set(f"0x{ext:08X}")
        else:
            self._info_vars["header"].set("—")
            self._info_vars["ext"].set("—")
        self._info_vars["name"].set(str(getattr(image, "name", "") or "—"))
        self._info_vars["arch"].set(arch_label(tr, entry.arch))
        self._info_vars["verdict"].set(verdict_label(tr, entry.verdict, entry.error))
        self._info_vars["container"].set(
            tr.t("lk.container_yes") if is_container else tr.t("lk.container_no")
        )
        self.sha_label.configure(text=tr.t("lk.detail_sha") + "：" + (entry.sha256 or "—"))
        findings = entry.findings
        self.findings_count.configure(text=tr.t("lk.findings_count", count=len(findings)))
        for finding in findings:
            category = str(getattr(finding, "category", ""))
            confidence = float(getattr(finding, "confidence", 0) or 0)
            evidence = getattr(finding, "evidence", []) or []
            summary = _short(evidence[0]) if evidence else ""
            if len(evidence) > 1:
                summary += " " + tr.t("lk.evidence_more", count=len(evidence) - 1)
            self.findings_tree.insert(
                "",
                "end",
                values=(
                    category_label(tr, category),
                    hex_offset(getattr(finding, "offset", 0)),
                    tr.t("lk.confidence", value=round(confidence * 100)),
                    summary,
                ),
                tags=("high" if confidence >= 0.8 else "low",),
            )
        warnings = list(getattr(report, "warnings", []) or [])
        for warning in warnings[:40]:
            self.warning_list.insert(
                "end",
                f"{hex_offset(getattr(warning, 'offset', 0))}  {_short(getattr(warning, 'text', ''), 56)}",
            )
        guard_params = dict(getattr(report, "guard_params", {}) or {})
        for key_name, values in list(guard_params.items())[:20]:
            count = len(values) if isinstance(values, (list, tuple)) else values
            self.guard_list.insert("end", f"{key_name} × {count}")
        notes = list(getattr(report, "notes", []) or [])
        if notes:
            self.notes_label.configure(text=tr.t("lk.notes_title") + "：" + " · ".join(_short(note) for note in notes[:3]))
        if not findings:
            self.findings_count.configure(text=tr.t("lk.no_findings"))

    # ── 选项可用性 / 按钮状态 ────────────────────────────
    def refresh_actions(self) -> None:
        if self._building:
            return
        tr = self.app.tr
        entry = self.selected_entry()
        has_entry = entry is not None and entry.error is None and entry.report is not None
        eligible = has_entry and bool(entry.findings) if entry is not None else False
        result = self.last_results.get(self.selected_key) if self.selected_key else None
        has_output = bool(
            result and result.output and result.output.exists()
        ) or bool(
            entry is not None and self._patched_path_exists(entry.path)
        )
        inplace = bool(self.orange_var.get() or self.dm_var.get() or self.clear_text_var.get())
        state = "normal" if (eligible and inplace) else "disabled"
        for button, enabled in (
            (self.preview_button, eligible and inplace),
            (self.apply_button, eligible and inplace),
            (self.verify_button, has_entry and has_output),
            (self.restore_button, has_entry),
        ):
            try:
                button.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        # 类别勾选框按扫描结果启用
        categories = {str(getattr(f, "category", "")) for f in (entry.findings if entry else [])}
        for widget, enabled in (
            (self.orange_check, bool({"orange_state", "red_state"} & categories)),
            (self.dm_check, "dm_verity" in categories),
        ):
            try:
                widget.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        try:
            self.clear_text_check.configure(state="normal" if categories else "disabled")
        except Exception:
            pass
        if entry is not None:
            patched = self._patched_path(entry.path)
            self.backup_hint.configure(
                text=tr.t("lk.backup_dir", path=str(entry.path.parent))
                + "  ·  "
                + (tr.t("lk.output_new_hint") if self.output_mode.get() != "inplace" else tr.t("lk.output_inplace_hint"))
                + f"  ({patched.name})"
            )
        else:
            self.backup_hint.configure(text="")
        if self._busy:
            self._set_busy(True)

    def _patched_path(self, path: Path) -> Path:
        try:
            lkpatch = core_bridge.try_lkpatch()
            if lkpatch is not None:
                return Path(lkpatch.patched_path(path))
        except Exception:
            pass
        suffix = path.suffix or ".img"
        return path.with_name(path.stem + "_patched" + suffix)

    def _patched_path_exists(self, path: Path) -> bool:
        try:
            return self._patched_path(path).exists()
        except Exception:
            return False

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        state = "disabled" if busy else "normal"
        for widget in (
            self.add_files_button,
            self.add_folder_button,
            self.rescan_button,
            self.clear_button,
            self.preview_button,
            self.apply_button,
            self.verify_button,
            self.restore_button,
            self.flash_button,
            self.file_tree,
        ):
            try:
                widget.configure(state=state)
            except Exception:
                pass
        if not busy:
            self.refresh_actions()

    # ── 预览补丁（dry-run）──────────────────────────────
    def _ensure_core(self) -> bool:
        if not self.core_ready:
            self.app._show_error_data(
                {
                    "message": self.app.tr.t("lk.core_missing", error=self.core_error or ""),
                    "hint": "请更新到包含 LK 修补功能的核心库版本。",
                    "traceback": "",
                }
            )
            return False
        try:
            self._lk()
            return True
        except Exception as exc:
            self.app.show_error(exc)
            return False

    def _require_entry(self) -> LkFileEntry | None:
        entry = self.selected_entry()
        if entry is None or entry.error is not None:
            self.set_status(self.app.tr.t("lk.select_first"), "warning")
            return None
        if not entry.findings:
            self.set_status(self.app.tr.t("lk.no_findings"), "warning")
            return None
        return entry

    def _run_sync(self, func: Callable[[], Any], *, title: str, on_done: Callable[[Any], None]) -> None:
        """后台执行并复用主窗口的 busy/日志/错误处理。"""
        self._set_busy(True)

        def failed(info: dict[str, Any]) -> None:
            self._set_busy(False)
            self.app._show_error_data(info)

        def done(result: Any) -> None:
            self._set_busy(False)
            try:
                on_done(result)
            except Exception as exc:  # noqa: BLE001
                self.app.show_error(exc)

        self.app._run_task(lambda ctx: func(), title=title, on_done=done, on_error=failed)

    def dry_run_sync(self) -> dict[str, Any]:
        """同步 dry-run（测试/截图用）：返回 changes/verify/new_data。"""
        entry = self._require_entry()
        if entry is None:
            raise RuntimeError("no entry")
        return dry_run_entry(entry, self.selected_categories(), bool(self.clear_text_var.get()), self._lk())

    def preview_patch(self) -> None:
        if self._busy or not self._ensure_core():
            return
        entry = self._require_entry()
        if entry is None:
            return
        categories = self.selected_categories()
        if not categories and not self.clear_text_var.get():
            self.set_status(self.app.tr.t("lk.categories_none"), "warning")
            return
        tr = self.app.tr
        self._run_sync(
            lambda: dry_run_entry(entry, categories, bool(self.clear_text_var.get()), self._lk()),
            title=tr.t("lk.preview_title"),
            on_done=lambda result: self._show_preview_dialog(entry, result),
        )

    def _show_preview_dialog(self, entry: LkFileEntry, result: dict[str, Any]) -> None:
        tr = self.app.tr
        for change in result["changes"]:
            self.app.log("INFO", "dry-run " + change)
        verify = result["verify"]
        self.app.log(
            "INFO" if verify.get("patch_ok") else "WARNING",
            tr.t(
                "lk.log_dryrun",
                changes=len(result["changes"]),
                ok=tr.t("lk.preview_ok") if verify.get("patch_ok") else tr.t("lk.preview_fail", reason="patch_ok=false"),
            ),
        )
        dialog = LkPreviewDialog(self.app, panel=self, entry=entry, result=result)
        outcome = dialog.show_modal()
        if outcome == "apply":
            self.apply_patch()

    # ── 应用补丁 ─────────────────────────────────────────
    def apply_patch(self) -> None:
        if self._busy or not self._ensure_core():
            return
        entry = self._require_entry()
        if entry is None:
            return
        categories = self.selected_categories()
        if not categories and not self.clear_text_var.get():
            self.set_status(self.app.tr.t("lk.categories_none"), "warning")
            return
        tr = self.app.tr
        inplace = self.output_mode.get() == "inplace"
        if self.app.interactive:
            from tkinter import messagebox

            body_key = "lk.apply_inplace_body" if inplace else "lk.apply_confirm_body"
            if not messagebox.askyesno(
                tr.t("lk.apply_confirm_title"),
                tr.t(body_key, path=str(entry.path)),
                parent=self.app.root,
            ):
                return
        lkpatch = self._lk()

        def work(ctx: Any) -> LkPatchResult:
            ctx.progress(None, tr.t("lk.applying"))
            return apply_entry(
                entry,
                categories,
                bool(self.clear_text_var.get()),
                inplace,
                None,
                lkpatch,
            )

        self._run_sync(work, title=tr.t("lk.applying"), on_done=lambda result: self._on_apply_done(entry, result))

    def apply_sync(self) -> LkPatchResult:
        """同步应用补丁（测试/截图用，不弹确认框）。"""
        entry = self._require_entry()
        if entry is None:
            raise RuntimeError("no entry")
        result = apply_entry(
            entry,
            self.selected_categories(),
            bool(self.clear_text_var.get()),
            self.output_mode.get() == "inplace",
            None,
            self._lk(),
        )
        self._on_apply_done(entry, result)
        return result

    def _on_apply_done(self, entry: LkFileEntry, result: LkPatchResult) -> None:
        tr = self.app.tr
        self.last_results[entry.key] = result
        # 保留原始扫描结果（校验/还原/重复应用都基于原始镜像）
        if self.selected_key == entry.key:
            self._refresh_detail(entry)
        self._update_row(entry)
        message = tr.t("lk.apply_done", path=str(result.output))
        self.app.log("INFO", tr.t("lk.log_applied", path=str(result.output), backup=str(result.backup or "—")))
        verify = result.verify or {}
        self.app.log(
            "INFO" if verify.get("patch_ok") else "WARNING",
            tr.t(
                "lk.log_verified",
                ok=tr.t("lk.verify_ok") if verify.get("patch_ok") else tr.t("lk.verify_fail"),
                ranges=_join_lines(verify.get("changed_ranges"), limit=8),
            ),
        )
        self.set_status(message, "success")
        self.app.set_status(message, "success")
        self.refresh_actions()
        try:
            self.app._update_status_info()
        except Exception:
            pass
        if self.app.interactive:
            LkVerifyDialog(
                self.app,
                entry=result.rescan or entry,
                verify=verify,
                backup=result.backup,
            ).show_modal()

    # ── 校验 ─────────────────────────────────────────────
    def verify_sync(self) -> dict[str, Any]:
        entry = self.selected_entry()
        if entry is None:
            raise RuntimeError("no entry")
        result = self.last_results.get(entry.key)
        output = result.output if (result and result.output and result.output.exists()) else self._patched_path(entry.path)
        return verify_output(entry, output, self._lk())

    def verify_patch(self) -> None:
        if self._busy or not self._ensure_core():
            return
        entry = self.selected_entry()
        if entry is None or entry.error is not None:
            self.set_status(self.app.tr.t("lk.select_first"), "warning")
            return
        result = self.last_results.get(entry.key)
        output = result.output if (result and result.output and result.output.exists()) else self._patched_path(entry.path)
        if not output.exists():
            self.set_status(self.app.tr.t("lk.verify_missing"), "warning")
            return
        tr = self.app.tr
        self._run_sync(
            lambda: verify_output(entry, output, self._lk()),
            title=tr.t("lk.verifying"),
            on_done=lambda verify: self._on_verify_done(entry, verify),
        )

    def _on_verify_done(self, entry: LkFileEntry, verify: dict[str, Any]) -> None:
        tr = self.app.tr
        self.set_status(
            tr.t("lk.verify_ok") if verify.get("patch_ok") else tr.t("lk.verify_fail"),
            "success" if verify.get("patch_ok") else "error",
        )
        self.app.log(
            "INFO" if verify.get("patch_ok") else "WARNING",
            tr.t("lk.log_verified", ok=tr.t("lk.verify_ok") if verify.get("patch_ok") else tr.t("lk.verify_fail"), ranges=_join_lines(verify.get("changed_ranges"), limit=8)),
        )
        if self.app.interactive:
            LkVerifyDialog(self.app, entry=entry, verify=verify, backup=None).show_modal()

    # ── 还原备份 ─────────────────────────────────────────
    def restore_sync(self, backup: Path, target: Path) -> dict[str, Any]:
        lkpatch = self._lk()
        result = lkpatch.restore_from_backup(backup, target)
        restored = min(3, len(self.order)) if self.order else 0
        # 重新扫描目标文件（如果它在列表中）
        entry = self.entries.get(str(target))
        if entry is not None:
            self.entries[entry.key] = scan_one(target, lkpatch)
            self._update_row(self.entries[entry.key])
            if self.selected_key == entry.key:
                self._refresh_detail(self.entries[entry.key])
        self.last_results.pop(str(target), None)
        return {"target": Path(result) if result else target, "scanned": restored}

    def restore_backup(self) -> None:
        if self._busy or not self._ensure_core():
            return
        from tkinter import filedialog, messagebox

        tr = self.app.tr
        entry = self.selected_entry()
        initial_dir = entry.path.parent if entry else Path.home()
        backup = filedialog.askopenfilename(
            parent=self.app.root,
            title=tr.t("lk.restore_select"),
            initialdir=str(initial_dir),
            initialfile="lk_original_backup.img",
            filetypes=[(tr.t("lk.filter_lk"), "*.img;*.bin"), (tr.t("lk.filter_all"), "*.*")],
        )
        if not backup:
            return
        target = entry.path if entry is not None else None
        if target is None:
            selected = filedialog.askopenfilename(
                parent=self.app.root,
                title=tr.t("lk.restore_target"),
                initialdir=str(initial_dir),
                filetypes=[(tr.t("lk.filter_lk"), "*.img;*.bin"), (tr.t("lk.filter_all"), "*.*")],
            )
            if not selected:
                return
            target = Path(selected)
        if not messagebox.askyesno(
            tr.t("lk.restore"),
            tr.t("lk.restore_confirm", backup=str(backup), target=str(target)),
            parent=self.app.root,
        ):
            return
        self._run_sync(
            lambda: self.restore_sync(Path(backup), Path(target)),
            title=tr.t("lk.restore"),
            on_done=lambda result: self._on_restore_done(result),
        )

    def _on_restore_done(self, result: dict[str, Any]) -> None:
        tr = self.app.tr
        message = tr.t("lk.restore_done", target=str(result["target"]))
        self.app.log("INFO", tr.t("lk.log_restored", target=str(result["target"])))
        self.set_status(message, "success")
        self.app.set_status(message, "success")
        self.refresh_actions()
        try:
            self.app._update_status_info()
        except Exception:
            pass

    # ── 刷写说明 ─────────────────────────────────────────
    def show_flash_guide(self) -> None:
        entries = [self.entries[key] for key in self.order]
        LkFlashGuideDialog(self.app, entries=entries).show_modal()

    # ── 重建（主题/语言切换）状态保留 ────────────────────
    def snapshot_state(self) -> dict[str, Any]:
        return {
            "paths": [self.entries[key].path for key in self.order],
            "entries": dict(self.entries),
            "selected": self.selected_key,
            "orange": bool(self.orange_var.get()),
            "dm": bool(self.dm_var.get()),
            "clear_text": bool(self.clear_text_var.get()),
            "output_mode": self.output_mode.get(),
            "last_results": dict(self.last_results),
        }

    def restore_state(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        paths = state.get("paths") or []
        entries = state.get("entries") or {}
        self.entries = dict(entries)
        self.order = [str(p) for p in paths if str(p) in self.entries]
        for key in self.order:
            entry = self.entries[key]
            if self.file_tree.exists(key):
                continue
            self._insert_row(entry)
            self._update_row(entry)
        self.orange_var.set(bool(state.get("orange", True)))
        self.dm_var.set(bool(state.get("dm", True)))
        self.clear_text_var.set(bool(state.get("clear_text", False)))
        self.output_mode.set(state.get("output_mode", "new"))
        self.last_results = dict(state.get("last_results") or {})
        self._set_ab_reminder([self.entries[key] for key in self.order])
        self._update_empty_state()
        selected = state.get("selected")
        if selected and selected in self.entries:
            self.select_path(str(selected))
        elif self.order:
            self.select_path(self.order[0])
        self.refresh_actions()

    def apply_theme(self) -> None:
        """主题切换由主窗口整体重建 UI 完成，这里无需处理（保留接口给测试）。"""
        return


# ── 预览对话框 ─────────────────────────────────────────────
class LkPreviewDialog(BaseDialog):
    """dry-run 补丁预览：变更列表 + 补丁前后反汇编。"""

    def __init__(self, app: Any, *, panel: LkPatchPanel, entry: LkFileEntry, result: dict[str, Any]) -> None:
        super().__init__(app, app.tr.t("lk.preview_title"), resizable=(True, True))
        self.panel = panel
        self.entry = entry
        self.result_data = result
        self._build()
        self.show()
        self.update_idletasks()
        m = self.metrics
        width = min(m.px(980), max(m.px(720), self.winfo_screenwidth() - m.px(20)))
        height = min(m.px(640), max(m.px(460), self.winfo_screenheight() - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.minsize(m.px(680), m.px(420))
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        verify = self.result_data.get("verify") or {}
        ranges = format_ranges(verify.get("changed_ranges"), limit=6, one_line=True)
        summary = tr.t(
            "lk.preview_summary",
            name=self.entry.path.name,
            arch=arch_label(tr, self.entry.arch),
            changes=len(self.result_data.get("changes") or []),
            ranges=ranges,
        )
        tk.Label(
            self.body,
            text=summary,
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(900),
        ).pack(fill="x")
        ok = bool(verify.get("patch_ok"))
        tk.Label(
            self.body,
            text=tr.t("lk.preview_ok") if ok else tr.t("lk.preview_fail", reason=_short(verify.get("error") or "patch_ok=false")),
            bg=palette.window_bg,
            fg=palette.success if ok else palette.danger,
            font=self.fonts.h3,
            anchor="w",
        ).pack(fill="x", pady=(m.px(4), m.px(6)))

        changes_head = tk.Frame(self.body, bg=palette.window_bg)
        changes_head.pack(fill="x")
        tk.Label(
            changes_head,
            text=tr.t("lk.preview_changes"),
            bg=palette.window_bg,
            fg=palette.text,
            font=self.fonts.h3,
            anchor="w",
        ).pack(side="left")
        changes_wrap = tk.Frame(self.body, bg=palette.border)
        changes_wrap.pack(fill="x", pady=(m.px(2), m.px(6)))
        tree = ttk_treeview(changes_wrap, app=self.app, columns=("category", "offset", "detail"))
        tree.heading("category", text=tr.t("lk.col_category"))
        tree.heading("offset", text=tr.t("lk.col_offset"))
        tree.heading("detail", text=tr.t("lk.col_change"))
        tree.column("category", width=m.px(170), anchor="w", stretch=False)
        tree.column("offset", width=m.px(100), anchor="center", stretch=False)
        tree.column("detail", width=m.px(640), anchor="w", stretch=True)
        import re as _re

        offset_re = _re.compile(r"0x[0-9a-fA-F]+")
        for change in self.result_data.get("changes") or []:
            change_text = str(change)
            match = offset_re.search(change_text)
            offset_text = match.group(0) if match else "—"
            category_text = change_text.split("@", 1)[0].strip()
            detail_text = change_text
            if ":" in change_text:
                parts = change_text.rsplit(":", 1)
                if "->" in parts[-1]:
                    detail_text = parts[-1].strip()
            tree.insert("", "end", values=(category_text, offset_text, detail_text))
        tree.configure(height=max(3, min(6, len(self.result_data.get("changes") or []) or 1)))
        tree.pack(fill="x")
        scroll = tb.Scrollbar(changes_wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)

        disasm = tk.Frame(self.body, bg=palette.window_bg)
        disasm.pack(fill="both", expand=True)
        disasm.grid_columnconfigure(0, weight=1)
        disasm.grid_columnconfigure(1, weight=1)
        disasm.grid_rowconfigure(1, weight=1)
        tk.Label(
            disasm,
            text=tr.t("lk.preview_before"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            disasm,
            text=tr.t("lk.preview_after"),
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(m.px(8), 0))
        before_text = _join_lines(verify.get("disasm_before"), limit=20)
        if before_text == "—":
            before_text = _join_lines(
                [line for finding in self.entry.findings for line in (getattr(finding, "evidence", []) or [])],
                limit=20,
            )
        self._make_text_panel(disasm, 0, before_text)
        self._make_text_panel(disasm, 1, _join_lines(verify.get("disasm_after"), limit=20))

        self.add_button(tr.t("lk.preview_close"), self._on_close, bootstyle="outline-secondary")
        self.add_button(
            tr.t("lk.preview_apply"),
            self._on_apply,
            bootstyle="success",
            default=True,
        )

    def _make_text_panel(self, master: tk.Misc, column: int, content: str) -> None:
        m = self.metrics
        palette = self.palette
        wrap = tk.Frame(master, bg=palette.border)
        wrap.grid(
            row=1,
            column=column,
            sticky="nsew",
            padx=(0, m.px(8)) if column == 0 else (m.px(8), 0),
            pady=(m.px(2), 0),
        )
        text = tk.Text(
            wrap,
            height=10,
            bg=palette.log_bg,
            fg=palette.log_fg,
            font=self.fonts.mono,
            wrap="none",
            bd=0,
            relief="flat",
            padx=m.px(8),
            pady=m.px(4),
        )
        text.insert("1.0", content or "—")
        text.configure(state="disabled")
        vertical = tb.Scrollbar(wrap, orient="vertical", command=text.yview)
        horizontal = tb.Scrollbar(wrap, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        text.pack(side="left", fill="both", expand=True)
        vertical.pack(side="right", fill="y")

    def _on_apply(self) -> None:
        self.result = "apply"
        self._on_close()


# ── 校验对话框 ─────────────────────────────────────────────
class LkVerifyDialog(BaseDialog):
    """补丁校验结果（PASS/FAIL + 各项校验值）。"""

    def __init__(
        self,
        app: Any,
        *,
        entry: LkFileEntry,
        verify: dict[str, Any],
        backup: Path | None = None,
    ) -> None:
        super().__init__(app, app.tr.t("lk.verify_title"), resizable=(True, True))
        self.entry = entry
        self.verify = verify or {}
        self.backup = backup
        self._build()
        self.show()
        self.update_idletasks()
        m = self.metrics
        width = min(m.px(760), max(m.px(520), self.winfo_screenwidth() - m.px(20)))
        wanted = max(m.px(320), min(m.px(600), self.winfo_reqheight() + m.px(24)))
        self.geometry(f"{width}x{wanted}")
        self.center()

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        verify = self.verify
        ok = bool(verify.get("patch_ok"))
        head = tk.Frame(self.body, bg=palette.window_bg)
        head.pack(fill="x")
        tk.Label(
            head,
            text="✓" if ok else "!",
            bg=palette.success if ok else palette.danger,
            fg="#ffffff",
            font=self.fonts.h2,
            width=2,
            pady=m.px(2),
        ).pack(side="left")
        tk.Label(
            head,
            text=tr.t("lk.verify_ok") if ok else tr.t("lk.verify_fail"),
            bg=palette.window_bg,
            fg=palette.success if ok else palette.danger,
            font=self.fonts.h2,
            anchor="w",
        ).pack(side="left", padx=(m.px(10), 0))
        tk.Label(
            head,
            text=self.entry.path.name,
            bg=palette.window_bg,
            fg=palette.muted,
            font=self.fonts.small,
            anchor="w",
        ).pack(side="left", padx=(m.px(10), 0))

        info = tk.Frame(self.body, bg=palette.surface, highlightthickness=1, highlightbackground=palette.border)
        info.pack(fill="x", pady=(m.px(8), m.px(6)))
        residual = verify.get("residual_warnings")
        if isinstance(residual, int):
            residual_text = tr.t("lk.verify_residual_count", count=residual)
        else:
            residual_text = _join_lines(residual, limit=6)
        rows = (
            ("lk.verify_size", _verify_flag(tr, verify.get("size_same")), False),
            ("lk.verify_header", _verify_flag(tr, verify.get("header_same")), False),
            ("lk.verify_guard", _verify_flag(tr, verify.get("guard_counts_same")), False),
            ("lk.verify_ranges", format_ranges(verify.get("changed_ranges"), limit=8), True),
            ("lk.verify_disasm", _join_lines(verify.get("disasm_after"), limit=8), True),
            ("lk.verify_residual", residual_text, True),
        )
        for row_index, (key, value, monospace) in enumerate(rows):
            tk.Label(
                info,
                text=tr.t(key) + "：",
                bg=palette.surface,
                fg=palette.muted,
                font=self.fonts.small,
                anchor="w",
                width=16,
                justify="left",
            ).grid(row=row_index, column=0, sticky="nw", padx=(m.px(8), m.px(4)), pady=(m.px(3), 0))
            tk.Label(
                info,
                text=str(value),
                bg=palette.surface,
                fg=palette.text,
                font=self.fonts.mono if monospace else self.fonts.small,
                anchor="w",
                justify="left",
                wraplength=m.px(560),
            ).grid(row=row_index, column=1, sticky="nw", padx=(0, m.px(8)), pady=(m.px(3), 0))
        info.grid_columnconfigure(1, weight=1)
        extra_row = len(rows)
        if verify.get("output"):
            tk.Label(
                info,
                text=str(verify["output"]),
                bg=palette.surface,
                fg=palette.muted,
                font=self.fonts.tiny,
                anchor="w",
            ).grid(row=extra_row, column=0, columnspan=2, sticky="w", padx=m.px(8), pady=(m.px(3), 0))
            extra_row += 1
        if self.backup:
            tk.Label(
                info,
                text=tr.t("lk.apply_backup", path=str(self.backup)),
                bg=palette.surface,
                fg=palette.muted,
                font=self.fonts.tiny,
                anchor="w",
            ).grid(row=extra_row, column=0, columnspan=2, sticky="w", padx=m.px(8), pady=(m.px(2), m.px(4)))
        self.add_button(tr.t("lk.verify_close"), self._on_close, bootstyle="primary", default=True)


# ── 刷写说明对话框 ─────────────────────────────────────────
class LkFlashGuideDialog(BaseDialog):
    """LK 刷写说明：fastboot / SP Flash Tool / A/B 双槽 / 前置条件 / 风险。"""

    def __init__(self, app: Any, *, entries: list[LkFileEntry] | None = None) -> None:
        super().__init__(app, app.tr.t("lk.flash_title"), resizable=(True, True))
        self.entries = entries or []
        self._build()
        self.show()
        self.update_idletasks()
        m = self.metrics
        width = min(m.px(820), max(m.px(600), self.winfo_screenwidth() - m.px(20)))
        height = min(m.px(700), max(m.px(480), self.winfo_screenheight() - m.px(40)))
        self.geometry(f"{width}x{height}")
        self.minsize(m.px(560), m.px(420))
        self.center()

    def _commands(self) -> tuple[list[str], bool]:
        """返回 (fastboot 命令列表, 是否 A/B 双槽)。"""
        commands: list[str] = []
        ab = len(self.entries) > 1
        for entry in self.entries:
            stem = entry.path.stem.lower()
            if stem in ("lk_a", "lk_b"):
                slot = stem
                ab = True
            elif stem in ("lk2",):
                slot = "lk2"
                ab = True
            elif stem == "lk":
                slot = "lk"
            else:
                slot = "lk"
            output = entry.path if "patched" in stem else entry.path.with_name(entry.path.stem + "_patched" + (entry.path.suffix or ".img"))
            commands.append(f"fastboot flash {slot} {output.name}")
        if not commands:
            commands.append("fastboot flash lk lk_patched.img")
        return commands, ab

    def _build(self) -> None:
        m = self.metrics
        palette = self.palette
        tr = self.app.tr
        commands, ab = self._commands()

        tk.Label(
            self.body,
            text=tr.t("lk.flash_warn"),
            bg=palette.tag_unknown_bg,
            fg=palette.tag_unknown_fg,
            font=self.fonts.small,
            anchor="w",
            justify="left",
            wraplength=m.px(760),
            padx=m.px(8),
            pady=m.px(6),
        ).pack(fill="x")

        def section(title_key: str, body_text: str, *, color: str | None = None) -> None:
            frame = tk.Frame(self.body, bg=palette.window_bg)
            frame.pack(fill="x", pady=(m.px(8), 0))
            tk.Label(
                frame,
                text=tr.t(title_key),
                bg=palette.window_bg,
                fg=color or palette.text,
                font=self.fonts.h3,
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                frame,
                text=body_text,
                bg=palette.surface_alt,
                fg=palette.text,
                font=self.fonts.small,
                anchor="w",
                justify="left",
                wraplength=m.px(740),
                padx=m.px(8),
                pady=m.px(6),
            ).pack(fill="x")

        section("lk.flash_fastboot_title", tr.t("lk.flash_fastboot_body", commands="\n".join(commands)))
        section("lk.flash_spflash_title", tr.t("lk.flash_spflash_body"))
        if ab:
            files = "、".join(entry.path.name for entry in self.entries)
            section("lk.flash_ab_title", tr.t("lk.flash_ab_body", count=len(self.entries), files=files), color=palette.warning)
        else:
            section("lk.flash_ab_title", tr.t("lk.flash_ab_none"), color=palette.warning)
        section("lk.flash_prereq_title", tr.t("lk.flash_prereq_body"))

        self.add_button(tr.t("lk.flash_close"), self._on_close, bootstyle="primary", default=True)
        self.add_button(tr.t("lk.flash_copy"), lambda: self._copy(commands), bootstyle="outline-secondary")

    def _copy(self, commands: list[str]) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append("\n".join(commands))
            self.app.set_status(self.app.tr.t("lk.flash_copied"), "success")
        except Exception:
            pass


def ttk_treeview(master: tk.Misc, *, app: Any, columns: tuple[str, ...]) -> Any:
    """创建与面板风格一致的 Treeview（供预览对话框使用）。"""
    return tb.Treeview(
        master,
        columns=columns,
        show="headings",
        selectmode="browse",
        bootstyle="primary",
        height=3,
    )
