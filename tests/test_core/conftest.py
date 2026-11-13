"""pytest 共享配置与 fixtures。

* 把 ``src`` 加入 ``sys.path``（仓库未安装为包，测试直接跑源码）；
* 隔离用户尺寸配置目录，避免读写真实 ``%APPDATA%``；
* 提供真实样本（logo.bin / lk.bin）的只读 fixture。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from helpers import LK_BIN, LOGO_BIN  # noqa: E402  (paths only)


@pytest.fixture(scope="session", autouse=True)
def isolated_config_dir(tmp_path_factory):
    """把用户配置目录指向临时目录，防止污染真实用户环境。"""
    config_dir = tmp_path_factory.mktemp("logobuilder-config")
    previous = os.environ.get("LOGOBUILDER_CONFIG_DIR")
    os.environ["LOGOBUILDER_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    if previous is None:
        os.environ.pop("LOGOBUILDER_CONFIG_DIR", None)
    else:
        os.environ["LOGOBUILDER_CONFIG_DIR"] = previous


@pytest.fixture(scope="session")
def real_logo_bytes() -> bytes:
    return LOGO_BIN.read_bytes()


@pytest.fixture(scope="session")
def real_lk_bytes() -> bytes:
    return LK_BIN.read_bytes()


@pytest.fixture()
def real_logo_container(real_logo_bytes):
    from logobuilder.core import LogoContainer

    return LogoContainer.parse(real_logo_bytes, path=LOGO_BIN)


@pytest.fixture()
def real_lk_container(real_lk_bytes):
    from logobuilder.core import LogoContainer

    return LogoContainer.parse(real_lk_bytes, path=LK_BIN)


@pytest.fixture()
def default_db():
    from logobuilder.core import SizeDB

    return SizeDB.default()
