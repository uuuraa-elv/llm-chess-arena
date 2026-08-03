"""Client OpenRouter: ambil daftar model & panggil chat completion.

Menangani rate limit (429) dan error server (5xx) dengan backoff eksponensial,
serta error jaringan/parse secara eksplisit. API key hanya dipakai di header.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

import requests

from . import config

DEFAULT_TIMEOUT = 90  # detik; LLM yang "berpikir" bisa lama
MAX_RETRIES = 4
BASE_BACKOFF = 1.5  # detik


def _models_url(base_url: Optional[str] = None) -> str:
    return (base_url or config.get_base_url()).rstrip("/") + "/models"


def _chat_url(base_url: Optional[str] = None) -> str:
    return (base_url or config.get_base_url()).rstrip("/") + "/chat/completions"


class OpenRouterError(Exception):
    """Error dari OpenRouter (jaringan, HTTP, atau body invalid)."""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Header opsional yang direkomendasikan OpenRouter untuk atribusi.
        "HTTP-Referer": config.get_app_url(),
        "X-Title": config.get_app_title(),
    }


def _retry_after_seconds(resp: requests.Response, attempt: int) -> float:
    """Hitung waktu tunggu: hormati header Retry-After bila ada, jika tidak backoff."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return BASE_BACKOFF * (2 ** attempt)


def list_models(
    api_key: Optional[str] = None,
    timeout: int = 30,
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    base_url: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Ambil daftar model. Mengembalikan list dict {id, name, context_length, pricing}.

    Endpoint /models publik, tapi key tetap dikirim bila tersedia. Retry pada
    429/5xx dengan backoff; raise OpenRouterError pada kegagalan jaringan/parse.
    """
    key = api_key or config.get_api_key()
    headers = _headers(key) if key else {"Content-Type": "application/json"}
    url = _models_url(base_url)

    resp = None
    last_error = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"jaringan: {exc}"
            if attempt < max_retries - 1:
                sleep(BASE_BACKOFF * (2 ** attempt))
                continue
            raise OpenRouterError(f"Gagal terhubung ke provider LLM: {exc}") from exc

        if resp.status_code == 200:
            break
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < max_retries - 1:
            last_error = f"HTTP {resp.status_code}"
            sleep(_retry_after_seconds(resp, attempt))
            continue
        raise OpenRouterError(
            f"Daftar model gagal (HTTP {resp.status_code}): {resp.text[:200]}"
        )

    if resp is None or resp.status_code != 200:
        raise OpenRouterError(f"Daftar model gagal setelah {max_retries} percobaan. Terakhir: {last_error}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise OpenRouterError("Respons daftar model bukan JSON valid.") from exc

    data = payload.get("data")
    if not isinstance(data, list):
        raise OpenRouterError("Format daftar model tidak terduga (tidak ada 'data').")

    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        # Lewati entri tanpa id string non-kosong: id None/kosong akan membuat
        # UI (m.id.toLowerCase()) crash saat filter/preselect.
        if not isinstance(item_id, str) or not item_id.strip():
            continue
        name = item.get("name")
        models.append(
            {
                "id": item_id,
                "name": name if isinstance(name, str) and name.strip() else item_id,
                "context_length": item.get("context_length"),
                "pricing": item.get("pricing") or {},
            }
        )
    models.sort(key=lambda m: str(m["id"]).lower())
    return models


def chat(
    model: str,
    messages: list[dict[str, str]],
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
    base_url: Optional[str] = None,
) -> dict[str, Any]:
    """Panggil chat completion. Kembalikan {"content": str, "usage": dict}.

    Retry pada 429/5xx dengan backoff. Raise OpenRouterError bila gagal permanen.
    Parameter `sleep` di-inject agar bisa di-mock saat test.
    """
    key = api_key or config.get_api_key()
    if not key:
        raise OpenRouterError("API key belum diset.")

    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = _headers(key)
    url = _chat_url(base_url)

    last_error: Optional[str] = None
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"jaringan: {exc}"
            if attempt < max_retries - 1:
                sleep(BASE_BACKOFF * (2 ** attempt))
                continue
            break

        if resp.status_code == 200:
            return _parse_chat_response(resp)

        if resp.status_code == 429 or resp.status_code >= 500:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if attempt < max_retries - 1:
                sleep(_retry_after_seconds(resp, attempt))
                continue
            break

        # 4xx selain 429 = permanen (key salah, model tidak ada, dll.)
        raise OpenRouterError(
            f"Panggilan model gagal (HTTP {resp.status_code}): {resp.text[:300]}"
        )

    raise OpenRouterError(
        f"Panggilan model gagal setelah {max_retries} percobaan. Terakhir: {last_error}"
    )


def _parse_chat_response(resp: requests.Response) -> dict[str, Any]:
    try:
        payload = resp.json()
    except ValueError as exc:
        raise OpenRouterError("Respons chat bukan JSON valid.") from exc

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        # OpenRouter kadang membungkus error provider dalam body 200.
        err = payload.get("error")
        if err:
            raise OpenRouterError(f"Provider error: {err}")
        raise OpenRouterError("Respons chat tidak punya 'choices'.")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise OpenRouterError("Respons chat tidak punya konten teks.")

    return {"content": content, "usage": payload.get("usage") or {}}
