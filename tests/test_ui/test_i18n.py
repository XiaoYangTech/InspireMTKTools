"""i18n 测试：中文单语目录 + 使用完整性（无英文文案/无语言切换）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from logobuilder.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    STRINGS,
    Translator,
    available_languages,
    language_name,
)

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
USED_KEY_RE = re.compile(r"""\.t\(\s*["']([A-Za-z0-9_.]+)["']""")
ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "src" / "logobuilder" / "ui"
APP_FILE = ROOT / "app.py"

CJK_START = chr(0x4E00)
CJK_END = chr(0x9FFF)


def _has_chinese(text: str) -> bool:
    return any(CJK_START <= char <= CJK_END for char in text)


def test_only_chinese_language_supported():
    """产品要求：只保留中文（不再有英文/语言切换）。"""
    assert available_languages() == {"zh_CN": "简体中文"}
    assert set(LANGUAGES) == {"zh_CN"}
    assert DEFAULT_LANGUAGE == "zh_CN"
    assert language_name("zh_CN") == "简体中文"
    assert language_name("en") == "en"  # 未知代码原样返回


def test_catalog_entries_are_plain_zh_strings():
    """catalog 现在是扁平 ``key -> 中文文本`` 字典（没有 en 段/嵌套语言结构）。"""
    assert STRINGS, "catalog 不能为空"
    bad = [key for key, value in STRINGS.items() if not isinstance(value, str) or not value.strip()]
    assert not bad, f"文案必须是非空字符串：{bad[:10]}"
    # 中文界面文案应绝大多数包含中文字符；纯技术串（版本号/架构名/SHA-256/占位符格式）允许保留
    non_chinese = [key for key, value in STRINGS.items() if not _has_chinese(value)]
    coverage = 1.0 - len(non_chinese) / max(1, len(STRINGS))
    assert coverage >= 0.95, f"中文覆盖率过低（{coverage:.1%}）：{non_chinese[:10]}"
    # 主要 UI 标签必须为中文
    for key in (
        "toolbar.open_image",
        "toolbar.replace_image",
        "menu.file",
        "menu.help.help",
        "settings.title",
        "help.title",
        "about.title",
        "lk.title",
        "preview.title",
        "blocks.title",
    ):
        assert _has_chinese(STRINGS[key]), f"{key} 应为中文：{STRINGS[key]!r}"


def test_placeholders_are_well_formed():
    problems = []
    for key, text in STRINGS.items():
        if text.count("{") != text.count("}"):
            problems.append((key, text))
            continue
        if PLACEHOLDER_RE.search(text) is None and ("{" in text or "}" in text):
            if "{{" not in text and "}}" not in text:
                problems.append((key, text))
    assert not problems, f"占位符格式异常：{problems[:10]}"


def test_translator_basic_and_fallback():
    tr = Translator()
    assert tr.language == "zh_CN"
    assert tr.t("toolbar.open_image") == "打开镜像"
    assert tr.t("blocks.count", count=3) == "共 3 块"
    assert tr.t("no.such.key") == "no.such.key"  # 缺失 key 回退 key，不抛异常
    assert tr.t("blocks.count", count=3, unknown=1) == "共 3 块"
    assert tr.has("toolbar.open_image") and not tr.has("no.such.key")


def test_translator_rejects_english_switch():
    tr = Translator()
    with pytest.raises(ValueError):
        tr.language = "en"
    # 构造时传入旧语言参数统一回退中文（单语产品）
    assert Translator("en").language == "zh_CN"
    assert Translator("klingon").language == "zh_CN"


def _used_keys() -> set[str]:
    keys: set[str] = set()
    for path in list(UI_DIR.glob("*.py")) + [APP_FILE]:
        text = path.read_text(encoding="utf-8")
        keys.update(USED_KEY_RE.findall(text))
    return keys


def test_all_used_keys_exist_in_catalog():
    used = _used_keys()
    assert len(used) > 200, f"发现的 key 太少（{len(used)}），正则可能失效"
    missing = sorted(key for key in used if key not in STRINGS)
    assert not missing, f"界面使用了未定义的 i18n key：{missing}"


def test_no_removed_theme_or_language_keys():
    """深色模式/语言切换相关文案应已从 catalog 删除（界面也无入口）。"""
    removed = {
        "app.theme_light",
        "app.theme_dark",
        "app.theme_switch_to_dark",
        "app.theme_switch_to_light",
        "app.language",
        "app.lang_zh",
        "app.lang_en",
        "menu.view.theme",
        "settings.theme",
        "settings.theme_light",
        "settings.theme_dark",
        "settings.theme_system",
        "settings.language",
    }
    assert removed.isdisjoint(STRINGS), f"仍存在已移除的文案 key：{sorted(removed & set(STRINGS))}"


def test_brand_strings_chinese_and_brand_kept():
    tr = Translator()
    brand = tr.t("app.title")
    assert "亿方联发科工具箱" in brand
    assert "InspireMTKTools" in brand  # 品牌英文名作为产品名保留
    assert "3.0.0" in brand
    assert tr.t("app.short_title") == "亿方联发科工具箱"
