"""核心异常体系。

所有核心库对外抛出的"业务错误"都继承 :class:`LogoBuilderError`，
调用方（GUI/CLI）只需捕获这一个基类即可拿到可读的中文信息与修复建议，
不必再处理 :class:`struct.error` / :class:`zlib.error` / :class:`OSError`
之类的底层异常。

设计要点：

* ``message`` 为中文可读信息（可夹带英文格式名，如 ``magic``）；
* ``hint`` 为可操作的修复建议；
* ``context`` 为结构化上下文（GUI 可展示为详情，测试可断言）。
"""

from __future__ import annotations

from typing import Any


class LogoBuilderError(Exception):
    """所有核心异常的基类。

    Parameters
    ----------
    message:
        中文可读错误信息。
    hint:
        修复建议（可选）。不传时使用子类的 ``default_hint``。
    context:
        结构化上下文，例如 ``{"offset": 520, "expected": 212}``。
    """

    #: 子类可覆盖的默认修复建议。
    default_hint: str | None = None

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.message = str(message)
        self.hint = hint if hint is not None else self.default_hint
        self.context: dict[str, Any] = dict(context or {})
        super().__init__(self._render())

    # -- 展示 ---------------------------------------------------------
    def _render(self) -> str:
        parts = [self.message]
        if self.hint:
            parts.append(f"提示：{self.hint}")
        return " | ".join(parts)

    def __str__(self) -> str:  # pragma: no cover - 与 _render 等价，保留可覆盖性
        return self._render()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r}, hint={self.hint!r}, context={self.context!r})"

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典（GUI 日志/CLI ``--json`` 可用）。"""
        return {
            "type": type(self).__name__,
            "message": self.message,
            "hint": self.hint,
            "context": self.context,
        }


class NotLogoFile(LogoBuilderError):
    """不是 MTK LOGO 镜像（magic 不符）。"""

    default_hint = (
        "请确认选择的是 MTK 机型官方线刷包中的 logo.bin / logo.img；"
        "裸 boot_logo 或其它分区镜像请使用对应功能打开。"
    )


class CorruptContainer(LogoBuilderError):
    """文件头 / 块偏移 / 长度自洽性校验失败（文件损坏或格式不符）。"""

    default_hint = (
        "文件可能已损坏、被截断或不是合法的 MTK LOGO 镜像；"
        "请重新从官方线刷包提取后再试。"
    )


class UnknownImageSize(LogoBuilderError):
    """无法推断图像块尺寸。"""

    default_hint = (
        "可在界面中手动选择分辨率（宽 x 高 x 色深），"
        "或把该分辨率加入内置/用户尺寸表后重试。"
    )


class ProjectError(LogoBuilderError):
    """工程目录 / 工程文件缺失或损坏。"""

    default_hint = "请检查工程目录是否完整（header.bin、project.json、图片/二进制块），或重建工程。"


class ExportError(LogoBuilderError):
    """写盘 / 打包 / 生成 zip 失败。"""

    default_hint = "请检查目标路径是否存在、是否有写权限，以及目标文件是否被其它程序占用。"


__all__ = [
    "LogoBuilderError",
    "NotLogoFile",
    "CorruptContainer",
    "UnknownImageSize",
    "ProjectError",
    "ExportError",
]
