"""可选的拖放支持（依赖 ``tkinterdnd2``）。

没有安装 tkinterdnd2 时必须 **安静降级**：主窗口照常启动，只是拖放不可用。
本模块只在安装时才访问 tkdnd，因此任何 import/初始化失败都被吞掉并返回 ``False``。
"""

from __future__ import annotations

from typing import Any, Callable


def _parse_drop_data(root: Any, data: str) -> list[str]:
    """解析 Tk 的 DND_Files 数据（路径可能带花括号、空格）。"""
    try:
        items = list(root.tk.splitlist(data))
    except Exception:
        items = [data]
    result: list[str] = []
    for item in items:
        item = str(item).strip()
        if item.startswith("{") and item.endswith("}"):
            item = item[1:-1]
        if item:
            result.append(item)
    return result


def enable_dnd(root: Any, on_files: Callable[[list[str]], Any]) -> bool:
    """在 ``root`` 上注册文件拖放。

    返回是否成功启用。启用后把拖入的文件路径列表交给 ``on_files``。
    """
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore[import-not-found]
    except Exception:
        return False  # 未安装可选依赖：静默降级
    try:
        if not getattr(root, "TkdndVersion", None):
            root.TkdndVersion = TkinterDnD._require(root)  # type: ignore[attr-defined]
    except Exception:
        return False
    try:
        targets = [root]
        try:
            targets.append(root.winfo_children()[0])
        except Exception:
            pass

        def _on_drop(event: Any) -> str:
            try:
                paths = _parse_drop_data(root, getattr(event, "data", "") or "")
            except Exception:
                paths = []
            if paths:
                try:
                    on_files(paths)
                except Exception:
                    pass
            return "break"

        for widget in targets:
            try:
                widget.tk.call(
                    "tkdnd::drop_target",
                    "register",
                    widget._w,
                    DND_FILES,
                )
                widget.bind("<<Drop>>", _on_drop)
            except Exception:
                continue
        return True
    except Exception:
        return False


def dnd_available() -> bool:
    """仅检查 Python 包是否可导入（不初始化 Tk）。"""
    try:
        import tkinterdnd2  # type: ignore[import-not-found]  # noqa: F401

        return True
    except Exception:
        return False
