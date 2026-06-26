"""Entry-point shim for the ``fbsat59`` console script.

``pyproject.toml`` declares::

    [project.scripts]
    fbsat59 = "fbsat59.main:main"

This module delegates to ``src/main.py``, which contains all startup logic.
``src/`` is on ``sys.path`` for both editable and regular installs
(``[tool.setuptools.packages.find] where = ["src"]``), so ``import main``
resolves to ``src/main.py`` at runtime.
"""

from __future__ import annotations

import importlib
import os
import sys


def main() -> int:
    """Launch FBSAT59."""
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)
    _main_mod = importlib.import_module("main")
    return int(_main_mod.main())
