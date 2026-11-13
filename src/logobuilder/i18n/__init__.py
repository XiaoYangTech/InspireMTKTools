"""轻量 i18n（中文单语）。

产品按需求只保留中文界面（亿方联发科工具箱 / InspireMTKTools），
因此本模块不再维护多语言切换，只提供统一的 ``Translator.t()`` 取词工具，
便于集中管理文案、统一占位符格式化与缺失 key 的兜底。

用法::

    from logobuilder.i18n import Translator

    tr = Translator()
    tr.t("toolbar.open_image")          # -> "打开镜像"
    tr.t("blocks.count", count=51)      # -> "共 51 块"

设计目标：
* 零外部依赖（不依赖 gettext 的 .mo 编译产物）；
* 缺失 key 时返回 key 本身并保持界面可用，绝不抛异常；
* 仍保留 ``Translator.language`` 只读语义（恒为 zh_CN），避免其它模块/测试大面积改动。
"""

from __future__ import annotations

from typing import Any

from .catalog import DEFAULT_LANGUAGE, LANGUAGES, STRINGS

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "STRINGS",
    "Translator",
    "available_languages",
    "language_name",
]


def available_languages() -> dict[str, str]:
    """返回支持的界面语言（当前仅中文）。"""
    return dict(LANGUAGES)


def language_name(code: str) -> str:
    """语言代码对应的显示名；未知代码原样返回。"""
    return LANGUAGES.get(code, code)


class Translator:
    """中文文案查找器（单语，``language`` 恒为 ``zh_CN``）。"""

    def __init__(self, language: str = DEFAULT_LANGUAGE) -> None:
        # 保留参数以兼容既有构造调用；非中文值统一回退中文（单语产品）。
        self._language = DEFAULT_LANGUAGE if language not in LANGUAGES else language

    # ── 语言（只读语义）──────────────────────────────────
    @property
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        if value not in LANGUAGES:
            raise ValueError(f"unsupported language: {value!r}（当前仅支持中文）")
        self._language = value

    @property
    def language_label(self) -> str:
        return language_name(self._language)

    def languages(self) -> dict[str, str]:
        return available_languages()

    # ── 翻译 ──────────────────────────────────────────────
    def t(self, key: str, /, **fmt: Any) -> str:
        """取中文文案；可选 ``fmt`` 用于填充 ``{占位符}``。"""
        text = STRINGS.get(key, key)
        if fmt:
            try:
                return text.format(**fmt)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    # 简写别名，便于 UI 代码书写
    __call__ = t

    def has(self, key: str) -> bool:
        return key in STRINGS
