"""pytest 会话级引导：确保测试语料（fixtures）存在。

仓库中不提交体积较大的合成测试镜像（`tests/fixtures/generated`、
`tests/fixtures/generated_lk`，约 40MB），由本文件在测试会话开始时按需生成：

* 生成脚本是确定性的（固定 PRNG 种子），同环境产物逐字节一致；
* 已有 `manifest.json` 时仅做快速校验（毫秒级），不会重复生成；
* 生成失败时不静默跳过，而是让后续用例明确失败并提示运行生成脚本。

同时把 `src/` 与 `tests/fixtures/` 加入 `sys.path`，便于未安装包时直接运行测试。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"

for candidate in (ROOT / "src", FIXTURES):
    text = str(candidate)
    if candidate.is_dir() and text not in sys.path:
        sys.path.insert(0, text)


def _ensure_corpora() -> None:
    try:
        import make_fixtures
        import make_lk_fixtures
    except Exception as exc:  # pragma: no cover - 依赖/路径异常时给出可读提示
        print(f"[conftest] 无法导入测试语料生成器：{exc}", file=sys.stderr)
        return
    for name, module in (("Logo 语料", make_fixtures), ("LK 语料", make_lk_fixtures)):
        try:
            module.ensure(quiet=True)
        except Exception as exc:  # pragma: no cover
            print(f"[conftest] 生成{name}失败：{exc}", file=sys.stderr)
            raise


def pytest_sessionstart(session) -> None:  # noqa: ARG001 - pytest hook signature
    _ensure_corpora()
