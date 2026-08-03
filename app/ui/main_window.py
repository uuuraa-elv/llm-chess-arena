"""Window utama: QWebEngineView memuat UI web + QWebChannel ke Bridge."""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QFileDialog, QMainWindow
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView

from ..bridge import Bridge


def _default_save_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.is_dir() else Path.home()

# Saat dibekukan PyInstaller, aset web ada di bundle (sys._MEIPASS); saat dev,
# di sebelah modul ini.
if getattr(sys, "frozen", False):
    _base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    WEB_DIR = _base / "app" / "ui" / "web"
else:
    WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LLM Chess Arena")
        self.resize(1320, 880)
        self.setMinimumSize(1040, 720)

        self.view = QWebEngineView(self)
        self.setCentralWidget(self.view)

        self.bridge = Bridge(self)
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        # Ekspor reasoning (Blob download dari JS) -> dialog simpan file.
        self.view.page().profile().downloadRequested.connect(self._on_download)

        self.view.load(QUrl.fromLocalFile(str(INDEX_HTML)))

    def _on_download(self, download) -> None:
        """Tangani permintaan unduhan (ekspor penalaran TXT/JSON)."""
        suggested = download.downloadFileName() or "reasoning.txt"
        start = str(_default_save_dir() / suggested)
        path, _ = QFileDialog.getSaveFileName(self, "Simpan ekspor penalaran", start)
        if not path:
            download.cancel()
            return
        target = Path(path)
        download.setDownloadDirectory(str(target.parent))
        download.setDownloadFileName(target.name)
        download.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - override Qt
        self.bridge.shutdown()
        super().closeEvent(event)
