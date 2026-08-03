"""Jembatan QWebChannel antara Python dan UI web.

Meneruskan sinyal MatchEngine ke JS sebagai string JSON, dan mengekspos slot yang
dipanggil dari JS (mulai/stop pertandingan, simpan API key, ambil daftar model).
"""
from __future__ import annotations

import json
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

from . import config, openrouter, session_store
from .match_engine import MatchEngine


class _ModelFetcher(QThread):
    """Ambil daftar model OpenRouter di thread terpisah agar UI tak freeze."""

    done = pyqtSignal(object)

    def run(self) -> None:
        try:
            models = openrouter.list_models()
            self.done.emit({"models": models, "error": None})
        except Exception as exc:  # noqa: BLE001 - laporkan semua error ke UI
            self.done.emit({"models": [], "error": str(exc)})


class Bridge(QObject):
    """Objek yang diregister ke QWebChannel sebagai `bridge` di sisi JS."""

    sig_board = pyqtSignal(str)
    sig_state = pyqtSignal(str)
    sig_log = pyqtSignal(str)
    sig_game_over = pyqtSignal(str)
    sig_match_over = pyqtSignal(str)
    sig_models = pyqtSignal(str)
    sig_api_status = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._engine: Optional[MatchEngine] = None
        self._fetcher: Optional[_ModelFetcher] = None

    # ------------------------------------------------------- dipanggil dari JS
    @pyqtSlot()
    def ready(self) -> None:
        """Dipanggil JS setelah halaman & channel siap."""
        self._emit_api_status()
        self.request_models()

    @pyqtSlot()
    def request_models(self) -> None:
        if self._fetcher is not None and self._fetcher.isRunning():
            return
        self.sig_models.emit(json.dumps({"loading": True}))
        fetcher = _ModelFetcher()
        fetcher.done.connect(self._on_models)
        self._fetcher = fetcher
        fetcher.start()

    def _launch_engine(self, model_a: str, model_b: str, resume=None) -> None:
        engine = MatchEngine(model_a, model_b, resume=resume)
        engine.sig_board.connect(lambda d: self._forward("board", d))
        engine.sig_state.connect(lambda d: self._forward("state", d))
        engine.sig_log.connect(lambda d: self._forward("log", d))
        engine.sig_game_over.connect(lambda d: self._forward("game_over", d))
        engine.sig_match_over.connect(lambda d: self._forward("match_over", d))
        self._engine = engine
        engine.start()

    @pyqtSlot(str, str)
    def start_match(self, model_a: str, model_b: str) -> None:
        if self._engine is not None and self._engine.isRunning():
            self._forward("log", {"level": "warn", "message": "Pertandingan sudah berjalan."})
            return
        model_a = (model_a or "").strip()
        model_b = (model_b or "").strip()
        if not model_a or not model_b:
            self._forward("log", {"level": "error", "message": "Pilih kedua model dulu."})
            self._forward("match_over", {"aborted": True, "reason": "invalid_models"})
            return
        session_store.clear_session()  # pertandingan baru -> buang simpanan lama
        self._launch_engine(model_a, model_b)

    @pyqtSlot(result=str)
    def get_saved_session(self) -> str:
        """Ringkasan pertandingan tersimpan untuk banner setup (tanpa log besar)."""
        s = session_store.load_session()
        if not s:
            return json.dumps({"has": False})
        return json.dumps({
            "has": True,
            "model_a": s.get("model_a"),
            "model_b": s.get("model_b"),
            "score_a": s.get("score_a", 0),
            "score_b": s.get("score_b", 0),
            "draws": s.get("draws", 0),
            "game_index": s.get("game_index", 1),
        })

    @pyqtSlot()
    def resume_saved_match(self) -> None:
        if self._engine is not None and self._engine.isRunning():
            self._forward("log", {"level": "warn", "message": "Pertandingan sudah berjalan."})
            return
        s = session_store.load_session()
        if not s:
            self._forward("log", {"level": "error", "message": "Tidak ada pertandingan tersimpan."})
            self._forward("match_over", {"aborted": True, "reason": "no_saved_session"})
            return
        self._launch_engine(s["model_a"], s["model_b"], resume=s)

    @pyqtSlot()
    def clear_saved_session(self) -> None:
        session_store.clear_session()

    @pyqtSlot()
    def stop_match(self) -> None:
        if self._engine is not None and self._engine.isRunning():
            self._engine.requestInterruption()
            self._engine.resume()  # lepas jeda agar loop bisa mendeteksi interupsi
            self._forward("log", {"level": "info", "message": "Menghentikan pertandingan..."})

    @pyqtSlot()
    def pause_match(self) -> None:
        if self._engine is not None and self._engine.isRunning():
            self._engine.pause()

    @pyqtSlot()
    def resume_match(self) -> None:
        if self._engine is not None and self._engine.isRunning():
            self._engine.resume()

    @pyqtSlot(str, result=str)
    def save_api_key(self, key: str) -> str:
        try:
            config.save_api_key(key)
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        self._emit_api_status()
        return json.dumps({"ok": True})

    @pyqtSlot(result=str)
    def api_status(self) -> str:
        return json.dumps({"has_key": config.has_api_key()})

    @pyqtSlot(result=str)
    def get_settings(self) -> str:
        """Pengaturan saat ini untuk panel Settings (key tidak pernah dikirim balik)."""
        return json.dumps({
            "has_key": config.has_api_key(),
            "base_url": config.get_base_url(),
            "default_base_url": config.DEFAULT_BASE_URL,
        })

    @pyqtSlot(str, str, result=str)
    def save_settings(self, api_key: str, base_url: str) -> str:
        """Simpan API key (bila diisi) & base URL secara permanen ke config.json."""
        try:
            config.save_settings(api_key, base_url)
        except ValueError as exc:
            return json.dumps({"ok": False, "error": str(exc)})
        self._emit_api_status()
        return json.dumps({"ok": True, "base_url": config.get_base_url()})

    # ----------------------------------------------------------------- internal
    def _on_models(self, payload: dict) -> None:
        self.sig_models.emit(json.dumps(payload))

    def _emit_api_status(self) -> None:
        self.sig_api_status.emit(json.dumps({"has_key": config.has_api_key()}))

    def _forward(self, channel: str, data: dict) -> None:
        message = json.dumps(data)
        signal = {
            "board": self.sig_board,
            "state": self.sig_state,
            "log": self.sig_log,
            "game_over": self.sig_game_over,
            "match_over": self.sig_match_over,
        }[channel]
        signal.emit(message)

    def shutdown(self) -> None:
        """Hentikan thread dengan rapi saat window ditutup.

        blockSignals(True) mencegah worker meng-emit ke objek Qt/JS yang sudah
        dihancurkan bila penutupan terjadi saat panggilan HTTP masih berjalan.
        """
        if self._engine is not None and self._engine.isRunning():
            self._engine.blockSignals(True)
            self._engine.requestInterruption()
            self._engine.wait(5000)
        if self._fetcher is not None and self._fetcher.isRunning():
            self._fetcher.blockSignals(True)
            self._fetcher.wait(3000)
