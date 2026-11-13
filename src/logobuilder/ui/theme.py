"""主题、配色、字体与高 DPI 适配（单一 litera 浅色主题）。

职责：
* :func:`enable_high_dpi` —— 在创建 Tk root **之前**开启 Windows DPI 感知（无需 Windows 时安全跳过）；
* :func:`detect_scale` —— 由 ``root.winfo_fpixels("1i")`` 计算显示缩放（96 DPI 为 1.0）；
* :class:`Metrics` —— 所有像素尺寸都经过 ``scale`` 换算，布局只用 grid/pack，不写死像素；
* :class:`UiFonts` —— 统一的中文字体族（Windows 优先微软雅黑 UI），字号用磅值（pt），
  由 Tk 的 ``tk scaling`` 自动随 DPI 放大；
* :class:`Palette` / :data:`LITERA_PALETTE` —— Bootswatch **litera** 视觉语言
  （白底 #ffffff、文字 #343a40、主色 #4582ec、边框 #dee2e6、success #02b875、
  warning #f0ad4e、danger #d9534f），供 tk 自绘控件（顶栏、卡片、日志、对话框）使用；
* :class:`ThemeManager` —— 固定使用 ttkbootstrap ``litera`` 主题（无深色模式、无主题切换），
  负责 ttk 样式字体/间距缩放。

只在 GUI 层使用；core 库不 import 本模块。
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
import warnings
from dataclasses import dataclass
from typing import Any

try:  # ttkbootstrap 是 GUI 的硬依赖，导入失败时给出明确错误
    import ttkbootstrap as tb
    from ttkbootstrap import Style
except Exception as exc:  # pragma: no cover - 环境缺依赖时才会走到
    raise SystemExit(
        "缺少依赖 ttkbootstrap，请先执行：pip install ttkbootstrap Pillow"
    ) from exc

# ── 设计常量 ────────────────────────────────────────────────
DESIGN_DPI = 96.0
#: ttkbootstrap 2.x 把 Bootswatch 主题名作为 legacy 别名保留；litera 即用户要求的样式。
LIGHT_THEME = "litera"
_THEME_FALLBACKS = ("litera", "flatly", "bootstrap-light", "cosmo")

_FONT_CANDIDATES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Hiragino Sans GB",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "Segoe UI",
    "Helvetica Neue",
    "DejaVu Sans",
)
_MONO_CANDIDATES = (
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "SF Mono",
    "Menlo",
    "DejaVu Sans Mono",
    "Courier New",
)


# ── 高 DPI ─────────────────────────────────────────────────
def enable_high_dpi() -> str:
    """在创建 Tk root 之前调用，开启当前进程的 DPI 感知。

    返回实际采用模式的简短标识；非 Windows 平台返回 ``"native"``。
    该函数可重复调用（已经设置过会安静失败）。
    """
    if sys.platform != "win32":
        return "native"
    # 1) Windows 10 1703+：Per-Monitor Aware V2（-4），显示效果最好
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        ctx = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(ctx):
            return "per-monitor-v2"
    except Exception:
        pass
    # 2) Windows 8.1+：SetProcessDpiAwareness(2) = Per-Monitor Aware
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        if shcore.SetProcessDpiAwareness(2) == 0:  # S_OK
            return "per-monitor"
    except Exception:
        pass
    # 3) 老系统兜底：System DPI Aware
    try:
        if ctypes.windll.user32.SetProcessDPIAware():  # type: ignore[attr-defined]
            return "system"
    except Exception:
        pass
    return "unaware"


def detect_scale(root: tk.Misc) -> tuple[float, float]:
    """返回 ``(scale, dpi)``；scale 以 96 DPI 为 1.0。"""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        dpi = DESIGN_DPI
    if not (48.0 <= dpi <= 960.0):
        dpi = DESIGN_DPI
    return round(dpi / DESIGN_DPI, 3), dpi


def apply_tk_scaling(root: tk.Misc, scale: float) -> None:
    """把设计缩放换算成 Tk 的 ``tk scaling``（每磅像素数 = DPI / 72）。"""
    try:
        root.tk.call("tk", "scaling", float(scale) * DESIGN_DPI / 72.0)
    except Exception:
        pass


# ── 缩放尺寸 ───────────────────────────────────────────────
@dataclass(frozen=True)
class Metrics:
    """把设计稿的像素值按 DPI 缩放；所有 UI 间距/尺寸都应经过这里。"""

    scale: float = 1.0

    def px(self, design_px: float) -> int:
        return max(1, int(round(design_px * self.scale)))

    @property
    def pad(self) -> int:
        return self.px(14)

    @property
    def pad_small(self) -> int:
        return self.px(8)

    @property
    def gap(self) -> int:
        return self.px(8)

    @property
    def gap_small(self) -> int:
        return self.px(5)

    @property
    def header_pad_x(self) -> int:
        return self.px(18)

    @property
    def header_pad_y(self) -> int:
        return self.px(10)

    @property
    def card_height(self) -> int:
        return self.px(78)

    @property
    def thumb_size(self) -> int:
        return self.px(56)

    @property
    def card_radius(self) -> int:
        return self.px(6)

    @property
    def list_width(self) -> int:
        return self.px(336)

    @property
    def list_min_width(self) -> int:
        return self.px(240)

    @property
    def preview_min_height(self) -> int:
        return self.px(240)

    @property
    def info_row_height(self) -> int:
        return self.px(20)

    @property
    def log_height_lines(self) -> int:
        return 9

    @property
    def status_height(self) -> int:
        return self.px(30)

    @property
    def window_min_width(self) -> int:
        return self.px(900)

    @property
    def window_min_height(self) -> int:
        return self.px(560)

    @property
    def window_default_width(self) -> int:
        return self.px(1240)

    @property
    def window_default_height(self) -> int:
        return self.px(820)

    @property
    def toolbar_button_padding(self) -> tuple[int, int]:
        return self.px(12), self.px(6)

    @property
    def button_padding(self) -> tuple[int, int]:
        return self.px(14), self.px(6)

    @property
    def icon_size(self) -> int:
        return self.px(18)


@dataclass(frozen=True)
class UiFonts:
    """统一的字体定义；磅值固定，实际像素大小由 tk scaling 决定。"""

    family: str = "Microsoft YaHei UI"
    mono_family: str = "Consolas"
    base_pt: float = 10.0
    small_pt: float = 9.0
    tiny_pt: float = 8.0
    title_pt: float = 16.0
    h2_pt: float = 12.0
    h3_pt: float = 10.5
    mono_pt: float = 9.0

    @property
    def base(self) -> tuple[str, int]:
        return self.family, int(round(self.base_pt))

    @property
    def small(self) -> tuple[str, int]:
        return self.family, int(round(self.small_pt))

    @property
    def tiny(self) -> tuple[str, int]:
        return self.family, int(round(self.tiny_pt))

    @property
    def bold(self) -> tuple[str, int, str]:
        return self.family, int(round(self.base_pt)), "bold"

    @property
    def h2(self) -> tuple[str, int, str]:
        return self.family, int(round(self.h2_pt)), "bold"

    @property
    def h3(self) -> tuple[str, int, str]:
        return self.family, int(round(self.h3_pt)), "bold"

    @property
    def title(self) -> tuple[str, int, str]:
        return self.family, int(round(self.title_pt)), "bold"

    @property
    def mono(self) -> tuple[str, int]:
        return self.mono_family, int(round(self.mono_pt))

    @property
    def badge(self) -> tuple[str, int, str]:
        return self.family, int(round(self.tiny_pt)), "bold"


def _pick_family(root: tk.Misc, candidates: tuple[str, ...], fallback: str) -> str:
    try:
        available = set(tkfont.families(root))
    except Exception:
        available = set()
    for name in candidates:
        if name in available:
            return name
    return fallback


def build_fonts(root: tk.Misc, base: UiFonts | None = None) -> UiFonts:
    """探测可用中文字体并返回 :class:`UiFonts`（不可变，可安全缓存）。"""
    template = base or UiFonts()
    default_family = "TkDefaultFont"
    try:
        default_family = tkfont.nametofont("TkDefaultFont").actual("family")
    except Exception:
        pass
    family = _pick_family(root, _FONT_CANDIDATES, default_family)
    mono = _pick_family(root, _MONO_CANDIDATES, "Courier New")
    return UiFonts(
        family=family,
        mono_family=mono,
        base_pt=template.base_pt,
        small_pt=template.small_pt,
        tiny_pt=template.tiny_pt,
        title_pt=template.title_pt,
        h2_pt=template.h2_pt,
        h3_pt=template.h3_pt,
        mono_pt=template.mono_pt,
    )


# ── 调色板（litera）────────────────────────────────────────
@dataclass(frozen=True)
class Palette:
    """tk 自绘控件用的 litera 颜色集合（单一浅色主题）。"""

    kind: str = "litera"
    window_bg: str = "#ffffff"
    surface: str = "#ffffff"
    surface_alt: str = "#f8f9fa"
    surface_hover: str = "#f1f3f5"
    border: str = "#dee2e6"
    text: str = "#343a40"
    muted: str = "#6c757d"
    header_bg: str = "#ffffff"
    header_bg_alt: str = "#f1f3f5"
    header_sub: str = "#6c757d"
    accent: str = "#4582ec"
    accent_soft: str = "#e9f0fd"
    success: str = "#02b875"
    warning: str = "#f0ad4e"
    danger: str = "#d9534f"
    preview_bg: str = "#f8f9fa"
    checker_a: str = "#ffffff"
    checker_b: str = "#eef1f4"
    log_bg: str = "#ffffff"
    log_fg: str = "#343a40"
    log_info: str = "#4582ec"
    log_warn: str = "#b7791f"
    log_error: str = "#d9534f"
    badge_bg: str = "#f1f3f5"
    badge_fg: str = "#495057"
    scroll_track: str = "#f1f3f5"
    scroll_thumb: str = "#ced4da"
    shadow: str = "#dee2e6"
    tag_ok_bg: str = "#e6f9f2"
    tag_ok_fg: str = "#087f5b"
    tag_mod_bg: str = "#fff4e3"
    tag_mod_fg: str = "#a56200"
    tag_unknown_bg: str = "#fdecec"
    tag_unknown_fg: str = "#b02a37"
    tag_binary_bg: str = "#f1f3f5"
    tag_binary_fg: str = "#495057"


LITERA_PALETTE = Palette()


# ── 主题管理（固定 litera）─────────────────────────────────
class ThemeManager:
    """管理 ttkbootstrap ``litera`` 主题 + litera 调色板 + ttk 字体/间距缩放。"""

    def __init__(self, root: tb.Window, style: Style | None = None) -> None:
        self.root = root
        self.style: Style = style or getattr(root, "style", None) or Style(root)
        self.palette = LITERA_PALETTE
        self.fonts: UiFonts = build_fonts(root)
        self.metrics = Metrics(scale=detect_scale(root)[0])
        self._apply_theme_name()
        self._configure_styles()

    # ── 主题（固定 litera，无切换）──────────────────────
    def _theme_names(self) -> set[str]:
        try:
            return set(self.style.theme_names())
        except Exception:
            return set()

    def _apply_theme_name(self) -> None:
        """应用 litera；不同 ttkbootstrap 版本下逐级回退（仍是浅色主题）。"""
        names = self._theme_names() | {"litera"}
        for candidate in _THEME_FALLBACKS:
            if candidate not in names:
                continue
            try:
                with warnings.catch_warnings():
                    # ttkbootstrap 2.x 对 Bootswatch legacy 主题名会发 DeprecationWarning，
                    # 这是产品明确要求的 litera 样式，这里安静使用。
                    warnings.simplefilter("ignore", DeprecationWarning)
                    self.style.theme_use(candidate)
                return
            except Exception:
                continue

    # ── 缩放 ─────────────────────────────────────────────
    def apply_scale(self, scale: float, *, fonts: UiFonts | None = None) -> None:
        """应用新的显示缩放：重设 tk scaling、尺寸量与 ttk 样式。"""
        self.metrics = Metrics(scale=float(scale))
        if fonts is not None:
            self.fonts = fonts
        apply_tk_scaling(self.root, float(scale))
        self._configure_styles()

    def refresh_from_root(self) -> None:
        """按当前 root 的实际 DPI 重新计算缩放（启动时调用一次）。"""
        scale, _dpi = detect_scale(self.root)
        self.apply_scale(scale, fonts=build_fonts(self.root, self.fonts))

    # ── ttk 样式 ─────────────────────────────────────────
    def _configure_styles(self) -> None:
        style = self.style
        m = self.metrics
        f = self.fonts
        base = (f.family, int(round(f.base_pt)))
        small = (f.family, int(round(f.small_pt)))
        bold = (f.family, int(round(f.base_pt)), "bold")
        pad = (m.px(12), m.px(5))

        def cfg(name: str, **kw: Any) -> None:
            try:
                style.configure(name, **kw)
            except Exception:
                pass

        cfg("TButton", font=base, padding=pad)
        cfg("TLabel", font=base)
        cfg("TEntry", font=base)
        cfg("TCombobox", font=base)
        cfg("TSpinbox", font=base)
        cfg("TCheckbutton", font=base)
        cfg("TRadiobutton", font=base)
        cfg("TLabelframe.Label", font=bold)
        cfg("Treeview", font=base, rowheight=m.px(28))
        cfg("Treeview.Heading", font=bold)
        cfg("TNotebook.Tab", font=base)
        cfg("Status.TLabel", font=small, background=self.palette.surface, foreground=self.palette.muted)
        cfg("CardTitle.TLabel", font=bold, background=self.palette.surface, foreground=self.palette.text)
        cfg("CardMuted.TLabel", font=small, background=self.palette.surface, foreground=self.palette.muted)
        cfg("Panel.TFrame", background=self.palette.surface)
        cfg("Panel.TLabel", font=base, background=self.palette.surface, foreground=self.palette.text)
        for color in ("primary", "secondary", "success", "info", "warning", "danger", "light", "dark"):
            for suffix in (".TButton", ".Outline.TButton", ".Link.TButton"):
                cfg(f"{color}{suffix}", font=base)
        try:
            self.root.configure(background=self.palette.window_bg)
        except Exception:
            pass
