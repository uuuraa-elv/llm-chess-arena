"""Persistensi state pertandingan agar bisa di-resume setelah aplikasi ditutup.

Disimpan ke `session.json` di samping `config.json` (writable saat frozen .exe).
Berisi skor sesi + langkah game berjalan + log per-langkah untuk memulihkan panel
UI. Dihapus saat sesi benar-benar selesai (ada pemenang / batas tercapai).
"""
from __future__ import annotations

import json
from typing import Optional

from . import config

SESSION_PATH = config.PROJECT_ROOT / "session.json"
SESSION_VERSION = 1


def save_session(state: dict) -> None:
    """Tulis snapshot secara atomik. Kegagalan I/O ditelan (tak boleh meng-crash worker)."""
    try:
        tmp = SESSION_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        tmp.replace(SESSION_PATH)
    except OSError:
        pass


def load_session() -> Optional[dict]:
    """Muat snapshot valid, atau None bila tidak ada/rusak/versi beda/model kosong."""
    try:
        with open(SESSION_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != SESSION_VERSION:
        return None
    if not data.get("model_a") or not data.get("model_b"):
        return None
    return data


def clear_session() -> None:
    """Hapus snapshot tersimpan (sesi selesai / dibuang)."""
    try:
        SESSION_PATH.unlink()
    except OSError:
        pass
