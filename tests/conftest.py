"""Fixture pytest: QCoreApplication headless untuk objek Qt (sinyal QThread)."""
import sys
from pathlib import Path

import pytest

# Pastikan root project ada di sys.path agar `import app...` jalan.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app
