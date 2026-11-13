"""pytest bootstrap for the integration/acceptance suite.

Adds ``src/`` (so ``import logobuilder`` works without installing the package)
and ``tests/fixtures/`` (so ``import make_fixtures`` works) to ``sys.path`` for
the whole test session.  Deliberately imports nothing from ``logobuilder`` here
so conftest collection still succeeds before the core library exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

for _candidate in (ROOT / "src", ROOT / "tests" / "fixtures", ROOT / "tests" / "integration"):
    _text = str(_candidate)
    if _candidate.is_dir() and _text not in sys.path:
        sys.path.insert(0, _text)
