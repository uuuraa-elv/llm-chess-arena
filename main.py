"""Entry point LLM Chess Arena (aplikasi desktop)."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

# Impor QtWebEngineWidgets sebelum membuat QApplication agar inisialisasi WebEngine benar.
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

from app.ui.main_window import MainWindow


def _run_selftest(app: QApplication, window: MainWindow) -> int:
    """Self-check diagnostik: pastikan papan ter-render & QWebChannel siap.

    Diaktifkan via env ARENA_SELFTEST=1. Hasil ditulis ke file (env
    ARENA_SELFTEST_OUT, default di TEMP) karena build windowed tak punya stdout.
    Return 0 bila render OK, selain itu 1. Tidak berjalan saat penggunaan normal.
    """
    out_path = os.environ.get("ARENA_SELFTEST_OUT") or str(
        Path(tempfile.gettempdir()) / "arena_selftest.txt"
    )
    state = {"ok": False, "raw": ""}

    def probe() -> None:
        js = (
            "(function(){var c=document.querySelector('.fighter-card');"
            "return JSON.stringify({sq:document.querySelectorAll('.square').length,"
            "qwc:typeof QWebChannel,pieces:document.querySelectorAll('.piece').length,"
            "co:c?getComputedStyle(c).overflow:'none',"
            "think:!!document.querySelector('.commentary #thinklist'),"
            "snd:!!document.querySelector('#sound-toggle'),"
            "eb:!!document.querySelector('#evalbar .eb-track'),"
            "pause:!!document.querySelector('#pause-btn'),"
            "rb:!!document.querySelector('#resume-banner')});})()"
        )

        def cb(result) -> None:
            state["raw"] = str(result)
            try:
                import json
                data = json.loads(result)
                state["ok"] = (
                    data.get("sq") == 64
                    and data.get("qwc") == "function"
                    and "visible" in str(data.get("co", ""))  # dropdown tak terpotong
                    and data.get("think") is True               # panel penalaran ada
                    and data.get("snd") is True                 # toggle suara ada
                    and data.get("eb") is True                  # eval bar ada
                    and data.get("pause") is True               # tombol pause ada
                    and data.get("rb") is True                  # banner resume ada
                )
            except (ValueError, TypeError):
                state["ok"] = False
            try:
                with open(out_path, "w", encoding="utf-8") as fh:
                    fh.write(("OK " if state["ok"] else "FAIL ") + state["raw"])
            except OSError:
                pass

        window.view.page().runJavaScript(js, cb)

    QTimer.singleShot(4000, probe)
    QTimer.singleShot(6000, app.quit)
    app.exec()
    return 0 if state["ok"] else 1


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LLM Chess Arena")
    app.setOrganizationName("LLM Chess Arena")

    window = MainWindow()
    window.show()

    if os.environ.get("ARENA_SELFTEST"):
        return _run_selftest(app, window)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
