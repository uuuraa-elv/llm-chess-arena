"""Test client OpenRouter: parsing daftar model, ketahanan terhadap data buruk,
backoff retry, dan parsing respons chat. `requests` di-monkeypatch (tanpa jaringan).
"""
import pytest

from app import openrouter


class _FakeResp:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_list_models_skips_items_without_string_id(monkeypatch):
    # Termasuk entri rusak: tanpa id, id None, id kosong, dan id non-string.
    payload = {
        "data": [
            {"id": "anthropic/claude", "name": "Claude"},
            {"name": "no id at all"},
            {"id": None, "name": "null id"},
            {"id": "", "name": "empty id"},
            {"id": 12345, "name": "numeric id"},
            {"id": "openai/gpt", "name": None},
            "not a dict",
        ]
    }
    monkeypatch.setattr(
        openrouter.requests, "get",
        lambda *a, **k: _FakeResp(200, payload),
    )
    models = openrouter.list_models(api_key="k", sleep=lambda s: None)
    ids = [m["id"] for m in models]
    # Hanya dua id string non-kosong yang valid.
    assert ids == ["anthropic/claude", "openai/gpt"]
    # Tidak ada id None/non-string yang lolos (mencegah crash m.id.toLowerCase() di UI).
    assert all(isinstance(m["id"], str) and m["id"] for m in models)
    # name None jatuh balik ke id.
    gpt = next(m for m in models if m["id"] == "openai/gpt")
    assert gpt["name"] == "openai/gpt"


def test_list_models_raises_on_non_list_data(monkeypatch):
    monkeypatch.setattr(
        openrouter.requests, "get",
        lambda *a, **k: _FakeResp(200, {"data": "oops"}),
    )
    with pytest.raises(openrouter.OpenRouterError):
        openrouter.list_models(api_key="k", sleep=lambda s: None)


def test_list_models_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    good = {"data": [{"id": "m/x", "name": "X"}]}

    def fake_get(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResp(429, headers={"Retry-After": "0"})
        return _FakeResp(200, good)

    monkeypatch.setattr(openrouter.requests, "get", fake_get)
    slept = []
    models = openrouter.list_models(api_key="k", sleep=lambda s: slept.append(s))
    assert [m["id"] for m in models] == ["m/x"]
    assert calls["n"] == 2
    assert slept == [0.0]  # menghormati Retry-After


def test_chat_parses_content_and_usage(monkeypatch):
    payload = {
        "choices": [{"message": {"content": "MOVE: e2e4"}}],
        "usage": {"total_tokens": 7},
    }
    monkeypatch.setattr(
        openrouter.requests, "post",
        lambda *a, **k: _FakeResp(200, payload),
    )
    out = openrouter.chat("m", [{"role": "user", "content": "hi"}], api_key="k")
    assert out["content"] == "MOVE: e2e4"
    assert out["usage"] == {"total_tokens": 7}


def test_chat_raises_on_provider_error_in_200_body(monkeypatch):
    payload = {"error": {"message": "rate limited by provider"}}
    monkeypatch.setattr(
        openrouter.requests, "post",
        lambda *a, **k: _FakeResp(200, payload),
    )
    with pytest.raises(openrouter.OpenRouterError):
        openrouter.chat("m", [{"role": "user", "content": "hi"}], api_key="k")


def test_chat_4xx_non_429_is_permanent(monkeypatch):
    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        return _FakeResp(401, text="invalid key")

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    with pytest.raises(openrouter.OpenRouterError):
        openrouter.chat(
            "m", [{"role": "user", "content": "hi"}],
            api_key="k", sleep=lambda s: None,
        )
    # 401 tidak boleh di-retry (permanen).
    assert calls["n"] == 1


def test_chat_network_error_retries_then_raises_without_extra_sleep(monkeypatch):
    import requests

    calls = {"n": 0}

    def fake_post(*a, **k):
        calls["n"] += 1
        raise requests.ConnectionError("down")

    monkeypatch.setattr(openrouter.requests, "post", fake_post)
    slept = []
    with pytest.raises(openrouter.OpenRouterError):
        openrouter.chat(
            "m", [{"role": "user", "content": "hi"}],
            api_key="k", max_retries=3, sleep=lambda s: slept.append(s),
        )
    # 3 percobaan, tapi hanya tidur di antara (2 kali), tidak setelah percobaan terakhir.
    assert calls["n"] == 3
    assert len(slept) == 2


def test_chat_missing_api_key_raises(monkeypatch):
    # Pastikan tidak ada key dari env/config lokal yang menutupi test.
    monkeypatch.setattr(openrouter.config, "get_api_key", lambda: None)
    with pytest.raises(openrouter.OpenRouterError):
        openrouter.chat("m", [{"role": "user", "content": "hi"}], api_key="")
