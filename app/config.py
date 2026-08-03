"""Konfigurasi & manajemen API key yang aman.

Urutan sumber API key (yang pertama ketemu dipakai):
  1. Environment variable OPENROUTER_API_KEY
  2. File .env di root project (dimuat via python-dotenv)
  3. config.json lokal (ditulis lewat UI saat user paste key)

Key TIDAK PERNAH di-hardcode dan hanya dipakai di header Authorization.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv selalu ada via requirements
    load_dotenv = None

# Lokasi config & .env:
#  - dev: root project (app/config.py -> project/).
#  - frozen (.exe): di sebelah executable agar writable & persisten (bukan _MEIPASS
#    yang ephemeral/read-only). Letakkan .env di samping .exe bila perlu.
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_APP_TITLE = "LLM Chess Arena"
DEFAULT_APP_URL = "https://github.com/local/llm-chess-arena"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# Muat .env sekali saat modul diimpor (tidak meng-override env yang sudah ada).
if load_dotenv is not None and ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


def _read_config_file() -> dict:
    """Baca config.json lokal; kembalikan dict kosong bila tidak ada/rusak."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_api_key() -> Optional[str]:
    """Kembalikan API key OpenRouter dari sumber prioritas, atau None bila tak ada."""
    env_key = os.environ.get("OPENROUTER_API_KEY")
    if env_key and env_key.strip():
        return env_key.strip()

    file_key = _read_config_file().get("openrouter_api_key")
    if isinstance(file_key, str) and file_key.strip():
        return file_key.strip()

    return None


def has_api_key() -> bool:
    return get_api_key() is not None


def _write_config(data: dict) -> None:
    """Tulis config.json secara atomik (tmp lalu replace) agar tak korup saat crash."""
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    tmp.replace(CONFIG_PATH)


def save_api_key(api_key: str) -> None:
    """Simpan API key ke config.json lokal (ber-gitignore). Raises ValueError bila kosong."""
    if not api_key or not api_key.strip():
        raise ValueError("API key tidak boleh kosong.")
    data = _read_config_file()
    data["openrouter_api_key"] = api_key.strip()
    _write_config(data)


def get_base_url() -> str:
    """Base URL provider LLM. Prioritas: env OPENROUTER_BASE_URL -> config.json -> default OpenRouter."""
    env_url = os.environ.get("OPENROUTER_BASE_URL")
    if env_url and env_url.strip():
        return env_url.strip().rstrip("/")
    file_url = _read_config_file().get("base_url")
    if isinstance(file_url, str) and file_url.strip():
        return file_url.strip().rstrip("/")
    return DEFAULT_BASE_URL


def save_settings(api_key: Optional[str], base_url: Optional[str]) -> None:
    """Simpan pengaturan secara permanen ke config.json.

    - api_key: bila None/kosong -> key lama dipertahankan; bila diisi -> diganti.
    - base_url: bila None -> tidak diubah; bila "" -> reset ke default OpenRouter;
      selain itu disimpan (wajib http:// atau https://). Raises ValueError bila invalid.
    """
    data = _read_config_file()
    if api_key is not None and api_key.strip():
        data["openrouter_api_key"] = api_key.strip()
    if base_url is not None:
        url = base_url.strip().rstrip("/")
        if not url:
            data.pop("base_url", None)  # kosong -> pakai default
        elif not url.startswith(("http://", "https://")):
            raise ValueError("Base URL harus diawali http:// atau https://")
        else:
            data["base_url"] = url
    _write_config(data)


def get_app_title() -> str:
    return os.environ.get("OPENROUTER_APP_TITLE", DEFAULT_APP_TITLE)


def get_app_url() -> str:
    return os.environ.get("OPENROUTER_APP_URL", DEFAULT_APP_URL)
