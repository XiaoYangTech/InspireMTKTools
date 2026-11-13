"""错误体系测试。"""

from __future__ import annotations

import pytest

from logobuilder.core import (
    CorruptContainer,
    ExportError,
    LogoBuilderError,
    NotLogoFile,
    ProjectError,
    UnknownImageSize,
)


def test_hierarchy():
    for subclass in (NotLogoFile, CorruptContainer, UnknownImageSize, ProjectError, ExportError):
        assert issubclass(subclass, LogoBuilderError)
        assert issubclass(subclass, Exception)


def test_message_and_hint():
    error = LogoBuilderError("出错了", hint="试试重启", context={"offset": 3})
    assert error.message == "出错了"
    assert error.hint == "试试重启"
    assert error.context == {"offset": 3}
    text = str(error)
    assert "出错了" in text and "试试重启" in text


def test_default_hint_used():
    error = NotLogoFile("magic 不符")
    assert error.hint is not None
    assert "logo.bin" in error.hint


def test_explicit_none_hint_keeps_default():
    error = CorruptContainer("损坏", hint=None)
    assert error.hint == CorruptContainer.default_hint


def test_to_dict():
    payload = ExportError("写失败", hint="检查权限", context={"path": "x"}).to_dict()
    assert payload == {
        "type": "ExportError",
        "message": "写失败",
        "hint": "检查权限",
        "context": {"path": "x"},
    }


def test_catchable_as_base_exception():
    with pytest.raises(LogoBuilderError):
        raise ProjectError("项目坏了")
