"""窗口截图（用于 ``python app.py --screenshot``）。

Windows 下优先用 Pillow ``ImageGrab`` 截取屏幕；当窗口比屏幕还大（小屏幕/远程桌面常见）时，
通过把窗口移动到不同位置分块截图再拼接，保证拿到 **完整** 的窗口画面，而不是被裁掉一半。
非 Windows 平台退化为普通 ``ImageGrab.grab`` / 系统截图命令，失败时抛可读异常。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageGrab


class ScreenshotError(RuntimeError):
    """截图失败（环境不支持/无显示）。"""


def _sleep(root: Any, seconds: float = 0.12) -> None:
    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass
    time.sleep(seconds)


# ── Windows 原生接口 ───────────────────────────────────────
def _win_dpi_aware() -> None:  # pragma: no cover - 仅 Windows
    # 截图前确保进程是 DPI 感知的，否则坐标系会与 ImageGrab 不一致
    try:
        import ctypes

        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


def _toplevel_hwnd(window: Any) -> int:  # pragma: no cover - 仅 Windows
    """取得窗口对应的顶层 HWND。

    注意不能用 ``GetParent`` 一路向上：Tk 的 ``Toplevel`` 对话框带 owner（transient），
    ``GetParent`` 会跨过 owner 指回主窗口，导致截错窗口。``wm_frame()`` 直接返回
    该 toplevel 自己的窗口句柄。
    """
    import ctypes

    try:
        frame = window.winfo_toplevel().wm_frame()
        if frame:
            return int(str(frame), 16)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    hwnd = int(window.winfo_id())
    for _ in range(8):
        parent = user32.GetParent(hwnd)
        if not parent:
            break
        # 遇到 owner 关系（父窗口标题是自己的 owner 而非 wrapper）时停止
        try:
            import ctypes.wintypes as wt

            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(parent, buf, 256)
            if buf.value and buf.value != str(window.winfo_toplevel().title()):
                break
        except Exception:
            pass
        hwnd = parent
    return hwnd


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:  # pragma: no cover
    """返回窗口可见外框（不含 DWM 阴影）。

    直接用 ``GetWindowRect`` 会把不可见的窗口阴影也算进去（截图底部/右侧多出灰边），
    因此优先用 ``DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)``。
    """
    import ctypes
    import ctypes.wintypes as wt

    try:
        bounds = wt.RECT()
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 9, ctypes.byref(bounds), ctypes.sizeof(bounds)
        )
        if hr == 0 and bounds.right > bounds.left and bounds.bottom > bounds.top:
            return bounds.left, bounds.top, bounds.right, bounds.bottom
    except Exception:
        pass
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _virtual_screen() -> tuple[int, int, int, int]:  # pragma: no cover
    import ctypes

    user32 = ctypes.windll.user32
    vx = user32.GetSystemMetrics(76)
    vy = user32.GetSystemMetrics(77)
    vw = user32.GetSystemMetrics(78)
    vh = user32.GetSystemMetrics(79)
    if vw <= 0 or vh <= 0:
        vw, vh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        vx = vy = 0
    return vx, vy, vw, vh


def _work_area() -> tuple[int, int, int, int]:  # pragma: no cover
    """返回主显示器的工作区（去掉任务栏），避免截图拼进任务栏像素。"""
    import ctypes
    import ctypes.wintypes as wt

    rect = wt.RECT()
    try:
        ok = ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        if ok and rect.right > rect.left and rect.bottom > rect.top:
            return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    user32 = ctypes.windll.user32
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def _grab_screen() -> Image.Image:  # pragma: no cover
    try:
        return ImageGrab.grab(all_screens=True)
    except TypeError:
        return ImageGrab.grab()


def _grab_region(region: tuple[int, int, int, int]) -> Image.Image:  # pragma: no cover
    """截取指定屏幕矩形（region 为屏幕坐标）；失败时退回整屏裁剪。"""
    try:
        return ImageGrab.grab(bbox=region, all_screens=False)
    except Exception:
        pass
    full = _grab_screen()
    left, top, right, bottom = region
    if full.width <= 1 or full.height <= 1:
        return full
    # all_screens 的图片原点即虚拟屏幕原点
    vx, vy, _vw, _vh = _virtual_screen()
    return full.crop((left - vx, top - vy, right - vx, bottom - vy))


def _client_rect(hwnd: int) -> tuple[int, int, int, int]:  # pragma: no cover
    """返回客户区在屏幕坐标系中的矩形 ``(left, top, right, bottom)``。"""
    import ctypes
    import ctypes.wintypes as wt

    user32 = ctypes.windll.user32
    rect = wt.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ScreenshotError("无法读取窗口客户区尺寸")
    point = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return point.x, point.y, point.x + rect.right, point.y + rect.bottom


def _tile_starts(length: int, visible: int) -> list[int]:
    """返回一组目标起始坐标（0..length），使长 ``visible`` 的窗口能覆盖整个目标。"""
    length = max(0, int(length))
    visible = max(1, int(visible))
    if length <= visible:
        return [0]
    overlap = max(2, visible // 20)
    starts = [0]
    while True:
        next_start = starts[-1] + visible - overlap
        if next_start + visible >= length:
            next_start = length - visible
        if next_start <= starts[-1]:
            next_start = starts[-1] + 1
        starts.append(next_start)
        if starts[-1] + visible >= length or len(starts) > 64:
            break
    return starts


def _capture_windows(window: Any, *, include_titlebar: bool = True) -> Image.Image:  # pragma: no cover
    """截取窗口；窗口大于工作区时按目标坐标分块移动窗口再拼接。

    * ``include_titlebar=False`` 时只截客户区（推荐：没有系统标题栏/阴影/任务栏干扰）；
    * 分块目标按「客户区坐标」计算，保证最后一块能覆盖到窗口底部。
    """
    _win_dpi_aware()
    hwnd = _toplevel_hwnd(window)
    outer = _window_rect(hwnd)
    client = _client_rect(hwnd)
    if include_titlebar:
        target_rect = outer
        left_off = 0
        top_off = 0
    else:
        target_rect = client
        left_off = client[0] - outer[0]
        top_off = client[1] - outer[1]
    width = target_rect[2] - target_rect[0]
    height = target_rect[3] - target_rect[1]
    if width <= 0 or height <= 0:
        raise ScreenshotError("窗口尺寸无效（可能尚未显示）")

    # 用工作区（去掉任务栏）计算分块：任务栏永远浮在窗口之上，拼进截图会很难看
    wa_left, wa_top, wa_right, wa_bottom = _work_area()
    vw, vh = max(1, wa_right - wa_left), max(1, wa_bottom - wa_top)
    result = Image.new("RGB", (width, height), (24, 26, 30))

    # 目标区域完整落在工作区内时直接截取，避免移动窗口
    if (
        target_rect[0] >= wa_left
        and target_rect[1] >= wa_top
        and target_rect[2] <= wa_right
        and target_rect[3] <= wa_bottom
    ):
        try:
            return _grab_region(target_rect).convert("RGB")
        except Exception as exc:
            raise ScreenshotError(f"截图失败：{exc}") from exc

    try:
        original_topmost = bool(window.attributes("-topmost"))
    except Exception:
        original_topmost = False
    try:
        geometry = window.winfo_geometry()
    except Exception:
        geometry = ""
    try:
        try:
            window.attributes("-topmost", True)
            window.lift()
        except Exception:
            pass
        _sleep(window)
        for ty in _tile_starts(height, vh):
            for tx in _tile_starts(width, vw):
                left = wa_left - tx - left_off
                top = wa_top - ty - top_off
                try:
                    window.wm_geometry(f"+{left}+{top}")
                except Exception:
                    continue
                _sleep(window, 0.16)
                current = _window_rect(hwnd) if include_titlebar else _client_rect(hwnd)
                inter_left = max(current[0], wa_left)
                inter_top = max(current[1], wa_top)
                inter_right = min(current[2], wa_right)
                inter_bottom = min(current[3], wa_bottom)
                if inter_right <= inter_left or inter_bottom <= inter_top:
                    continue
                screen = _grab_region((inter_left, inter_top, inter_right, inter_bottom))
                result.paste(screen, (inter_left - current[0], inter_top - current[1]))
    finally:
        try:
            if geometry:
                window.wm_geometry(geometry)
        except Exception:
            pass
        try:
            window.attributes("-topmost", original_topmost)
        except Exception:
            pass
        _sleep(window, 0.12)
    return result


def _tile_offsets(length: int, screen_length: int) -> list[int]:
    """返回一组窗口偏移（<=0），使屏幕能覆盖整个窗口。"""
    if length <= screen_length:
        return [0]
    overlap = max(2, screen_length // 20)
    offsets = [0]
    while -offsets[-1] + screen_length < length:
        candidate = offsets[-1] - (screen_length - overlap)
        candidate = max(-(length - screen_length), candidate)
        offsets.append(candidate)
    return offsets


def _crop_client_area(window: Any, hwnd: int, image: Image.Image) -> Image.Image:  # pragma: no cover
    import ctypes
    import ctypes.wintypes as wt

    rect = wt.RECT()
    ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wt.POINT(0, 0)
    ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(point))
    wl, wt_, _wr, _wb = _window_rect(hwnd)
    x = point.x - wl
    y = point.y - wt_
    return image.crop((x, y, x + rect.right, y + rect.bottom))


# ── 非 Windows 退化路径 ───────────────────────────────────
def _capture_generic(window: Any, *, include_titlebar: bool = True) -> Image.Image:
    window.update_idletasks()
    window.update()
    time.sleep(0.2)
    window.update()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    w, h = window.winfo_width(), window.winfo_height()
    if w <= 1 or h <= 1:
        raise ScreenshotError("窗口尺寸无效（可能尚未显示）")
    try:
        base = ImageGrab.grab()
        if base.width > w and base.height > h:
            return base.crop((x, y, x + w, y + h)).convert("RGB")
        return base.convert("RGB")
    except Exception as exc:  # pragma: no cover - 依赖环境
        pass
    if sys.platform == "darwin":  # pragma: no cover
        out = Path("/tmp/logobuilder-shot.png")
        subprocess.run(["screencapture", "-x", "-R", f"{x},{y},{w},{h}", str(out)], check=True)
        return Image.open(out).convert("RGB")
    if sys.platform.startswith("linux"):  # pragma: no cover
        for command in (
            ["gnome-screenshot", "-f", "/tmp/logobuilder-shot.png"],
            ["scrot", "-o", "/tmp/logobuilder-shot.png"],
            ["import", "-window", "root", "/tmp/logobuilder-shot.png"],
        ):
            try:
                subprocess.run(command, check=True, timeout=10)
                return Image.open("/tmp/logobuilder-shot.png").convert("RGB")
            except Exception:
                continue
    raise ScreenshotError("当前平台无法截图（需要可用的显示环境）")


# ── 对外接口 ───────────────────────────────────────────────
def capture_window(window: Any, *, include_titlebar: bool = True) -> Image.Image:
    """截取整个窗口（含超屏拼接），返回 PIL.Image。"""
    if sys.platform == "win32":
        try:
            return _capture_windows(window, include_titlebar=include_titlebar)
        except ScreenshotError:
            raise
        except Exception as exc:  # pragma: no cover - 仅 Windows 环境
            raise ScreenshotError(f"Windows 截图失败：{exc}") from exc
    return _capture_generic(window, include_titlebar=include_titlebar)


def save_window_screenshot(
    window: Any,
    path: str | Path,
    *,
    include_titlebar: bool = True,
) -> Path:
    """把窗口截图保存为 PNG，返回实际文件路径。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image = capture_window(window, include_titlebar=include_titlebar)
    image.save(target, "PNG")
    return target


def wait_ui(root: Any, milliseconds: int = 120) -> None:
    """在无 mainloop 的截图模式下让 Tk 处理若干事件。"""
    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass
    time.sleep(max(0.0, milliseconds / 1000.0))
    try:
        root.update_idletasks()
        root.update()
    except Exception:
        pass
