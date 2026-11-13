"""界面设置持久化（窗口几何、主题、语言、导出偏好、最近目录）。

* 默认位置：``%APPDATA%\\LogoBuilder\\ui_settings.json``（Windows）、
  ``~/Library/Application Support/LogoBuilder``（macOS）、``~/.config/LogoBuilder``（Linux）；
* 可用环境变量 ``LOGOBUILDER_CONFIG_DIR`` 覆盖（测试/便携版用）；
* 读取失败时静默回退默认值，绝不因为配置损坏导致程序无法启动。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR_NAME = "LogoBuilder"
SETTINGS_FILENAME = "ui_settings.json"


def config_dir() -> Path:
    """平台相关的配置目录（不存在时创建）。"""
    override = os.environ.get("LOGOBUILDER_CONFIG_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = Path(base) / APP_DIR_NAME
    elif sys.platform == "darwin":
        path = Path(os.path.expanduser("~/Library/Application Support")) / APP_DIR_NAME
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        path = Path(base) / APP_DIR_NAME
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


@dataclass
class UiSettings:
    """可持久化的界面设置（全部有安全默认值）。

    产品为中文单语、单一 litera 浅色主题，因此不再持久化语言/主题字段；
    旧配置文件里的 ``language`` / ``theme`` 会被读取时自动忽略。
    """

    default_fit: str = "stretch"  # stretch | contain | cover
    padding_byte: int = 0x00  # 0x00 | 0xFF
    pad_enabled: bool = False
    pad_target: int = 0
    remember_geometry: bool = True
    geometry: str = ""
    paned_sash: int = 0
    show_log_on_error: bool = True
    last_open_dir: str = ""
    last_save_dir: str = ""
    log_level: str = "INFO"
    drop_hint_shown: bool = False

    # ── 读写 ─────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path | None = None) -> "UiSettings":
        target = path or settings_path()
        try:
            data = json.loads(Path(target).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        payload = {key: value for key, value in data.items() if key in known}
        settings = cls()
        for key, value in payload.items():
            default = getattr(settings, key)
            # 类型不匹配时忽略（配置被手改坏也能启动）
            if isinstance(default, bool) and isinstance(value, bool):
                setattr(settings, key, value)
            elif isinstance(default, int) and isinstance(value, int) and not isinstance(value, bool):
                setattr(settings, key, value)
            elif isinstance(default, str) and isinstance(value, str):
                setattr(settings, key, value)
        return settings

    def save(self, path: Path | None = None) -> Path:
        target = path or settings_path()
        data = asdict(self)
        try:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            tmp = Path(str(target) + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except OSError:
            pass
        return Path(target)

    def as_dict(self) -> dict:
        return asdict(self)

    def reset(self) -> None:
        """恢复默认值（就地修改，保留对象身份）。"""
        defaults = UiSettings()
        for f in fields(self):
            setattr(self, f.name, getattr(defaults, f.name))
