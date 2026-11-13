"""核心库（``logobuilder.core``）导入桥。

UI 与核心解耦：
* 核心库存在 → 正常导入；
* 核心库缺失/损坏 → 窗口仍可启动，顶部显示提示条，相关操作给出可读错误（不崩溃）；
* 测试可用 :func:`install_core` 注入桩实现（仅测试代码使用，``src`` 内不含假实现）。
"""

from __future__ import annotations

import traceback
from types import ModuleType
from typing import Any

# 公共 API 中 UI 直接依赖的名字（见 docs/spec_api.md）
REQUIRED_ATTRS: tuple[str, ...] = (
    "LogoSize",
    "SizeDB",
    "LogoContainer",
    "ImageBlock",
    "BinaryBlock",
    "LogoProject",
    "image_to_pixels",
    "pixels_to_image",
    "make_update_zip",
    "read_boot_logo",
    "write_boot_logo",
)


class CoreUnavailable(RuntimeError):
    """核心库不可用（未安装/导入失败）。"""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint


_core: ModuleType | None = None
_core_error: str | None = None
_missing: tuple[str, ...] = ()
_loaded = False

# LK 修补模块（logobuilder.core.lkpatch）——独立于 logo 核心，允许缺失时界面降级
LK_REQUIRED_ATTRS: tuple[str, ...] = (
    "parse_lk_image",
    "detect_arch",
    "scan",
    "apply_patches",
    "verify_patch",
    "save_backup",
    "restore_from_backup",
    "patched_path",
    "backup_path",
)
_lkpatch: ModuleType | None = None
_lkpatch_error: str | None = None
_lkpatch_missing: tuple[str, ...] = ()
_lkpatch_loaded = False


def load_core(*, force: bool = False) -> ModuleType | None:
    """尝试导入 ``logobuilder.core``；失败时记录错误并返回 ``None``。"""
    global _core, _core_error, _missing, _loaded
    if _loaded and not force:
        return _core
    _loaded = True
    _core = None
    _core_error = None
    _missing = ()
    try:
        import logobuilder.core as core  # type: ignore[import-not-found]
    except Exception as exc:  # 核心可能还在并行开发中
        _core_error = f"{type(exc).__name__}: {exc}"
        return None
    _core = core
    _missing = tuple(name for name in REQUIRED_ATTRS if not hasattr(core, name))
    return core


def load_lkpatch(*, force: bool = False) -> ModuleType | None:
    """导入 ``logobuilder.core.lkpatch``；缺失时返回 ``None``（LK 面板降级显示）。"""
    global _lkpatch, _lkpatch_error, _lkpatch_missing, _lkpatch_loaded
    if _lkpatch_loaded and not force:
        return _lkpatch
    _lkpatch_loaded = True
    _lkpatch = None
    _lkpatch_error = None
    _lkpatch_missing = ()
    module: ModuleType | None = None
    try:
        from logobuilder.core import lkpatch as module  # type: ignore[attr-defined]
    except Exception as exc:
        try:
            import importlib

            module = importlib.import_module("logobuilder.core.lkpatch")
        except Exception:
            _lkpatch_error = f"{type(exc).__name__}: {exc}"
            return None
    _lkpatch = module
    _lkpatch_missing = tuple(
        name for name in LK_REQUIRED_ATTRS if not hasattr(module, name)
    )
    return module


def install_lkpatch(module: ModuleType | None) -> None:
    """注入 LK 修补模块（仅测试/自检使用）。"""
    global _lkpatch, _lkpatch_error, _lkpatch_missing, _lkpatch_loaded
    _lkpatch = module
    _lkpatch_error = None if module is not None else "not installed"
    _lkpatch_missing = (
        ()
        if module is None
        else tuple(name for name in LK_REQUIRED_ATTRS if not hasattr(module, name))
    )
    _lkpatch_loaded = True


def get_lkpatch() -> ModuleType:
    """取 LK 修补模块；不可用时抛 :class:`CoreUnavailable`（中文提示）。"""
    module = load_lkpatch()
    if module is None:
        raise CoreUnavailable(
            "LK 修补核心模块（logobuilder.core.lkpatch）不可用。",
            hint="请更新到包含 LK 修补功能的核心库版本。",
        )
    if _lkpatch_missing:
        raise CoreUnavailable(
            "LK 修补核心模块缺少必要接口：" + ", ".join(_lkpatch_missing),
            hint="核心库与界面版本不匹配，请更新到配套版本。",
        )
    return module


def try_lkpatch() -> ModuleType | None:
    """尽量取 LK 修补模块，但不抛异常。"""
    try:
        return get_lkpatch()
    except CoreUnavailable:
        return None


def lkpatch_status() -> tuple[bool, str | None]:
    """返回 ``(LK 修补是否可用, 错误描述)``，供界面显示状态提示。"""
    load_lkpatch()
    if _lkpatch is None:
        return False, _lkpatch_error or "未找到 LK 修补核心模块"
    if _lkpatch_missing:
        return False, "缺少接口：" + ", ".join(_lkpatch_missing)
    return True, None


def install_core(module: ModuleType | None) -> None:
    """注入核心模块（仅测试/自检使用）。"""
    global _core, _core_error, _loaded, _missing
    _core = module
    _core_error = None if module is not None else "not installed"
    _missing = () if module is None else tuple(
        name for name in REQUIRED_ATTRS if not hasattr(module, name)
    )
    _loaded = True


def get_core() -> ModuleType:
    """取核心模块；不可用时抛 :class:`CoreUnavailable`（带中文提示）。"""
    core = load_core()
    if core is None:
        raise CoreUnavailable(
            "核心模块 logobuilder.core 不可用。",
            hint="请确认已安装依赖并在仓库根目录运行；详情见日志面板。",
        )
    if _missing:
        raise CoreUnavailable(
            "核心模块缺少必要接口：" + ", ".join(_missing),
            hint="核心库版本与界面不匹配，请更新到配套版本。",
        )
    return core


def try_core() -> ModuleType | None:
    """尽量取核心模块，但不抛异常。"""
    try:
        return get_core()
    except CoreUnavailable:
        return None


def core_status() -> tuple[bool, str | None]:
    """返回 ``(是否可用, 错误描述)``，供启动时展示提示条。"""
    load_core()
    if _core is None:
        return False, _core_error or "未找到核心模块"
    if _missing:
        return False, "缺少接口：" + ", ".join(_missing)
    return True, None


def core_version() -> str:
    core = try_core()
    version = getattr(core, "__version__", None) if core else None
    return str(version or "unknown")


def describe_exception(exc: BaseException) -> tuple[str, str | None, str]:
    """把异常整理为 ``(可读信息, 建议, 完整 traceback)``。"""
    message = str(exc) or type(exc).__name__
    hint = getattr(exc, "hint", None)
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return message, (str(hint) if hint else None), tb


def human_size(num: int | float | None) -> str:
    """字节数转人类可读文本（中文单位）。"""
    if num is None:
        return "—"
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "—"
    if value < 1024:
        return f"{int(value)} B"
    units = ("KB", "MB", "GB", "TB")
    for unit in units:
        value /= 1024.0
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def format_error(exc: BaseException) -> dict[str, Any]:
    """错误对话框使用的数据结构。"""
    message, hint, tb = describe_exception(exc)
    return {
        "message": message,
        "hint": hint,
        "traceback": tb,
        "type": type(exc).__name__,
    }
