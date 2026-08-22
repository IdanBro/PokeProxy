from __future__ import annotations

import os
from pathlib import Path


def pytest_configure() -> None:
    os.chdir(Path(__file__).resolve().parent.parent)
