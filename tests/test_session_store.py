"""Test persistensi sesi (save/load/clear + validasi)."""
from app import session_store


def test_roundtrip_save_load_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "SESSION_PATH", tmp_path / "session.json")
    assert session_store.load_session() is None  # belum ada

    session_store.save_session({
        "version": session_store.SESSION_VERSION,
        "model_a": "A", "model_b": "B",
        "score_a": 1, "score_b": 0, "moves": ["e2e4"],
    })
    loaded = session_store.load_session()
    assert loaded is not None
    assert loaded["model_a"] == "A" and loaded["score_a"] == 1
    assert loaded["moves"] == ["e2e4"]

    session_store.clear_session()
    assert session_store.load_session() is None


def test_load_rejects_wrong_version(tmp_path, monkeypatch):
    p = tmp_path / "session.json"
    monkeypatch.setattr(session_store, "SESSION_PATH", p)
    p.write_text('{"version": 999, "model_a": "A", "model_b": "B"}', encoding="utf-8")
    assert session_store.load_session() is None


def test_load_rejects_missing_models(tmp_path, monkeypatch):
    p = tmp_path / "session.json"
    monkeypatch.setattr(session_store, "SESSION_PATH", p)
    p.write_text('{"version": 1, "model_a": "", "model_b": "B"}', encoding="utf-8")
    assert session_store.load_session() is None


def test_load_rejects_corrupt_json(tmp_path, monkeypatch):
    p = tmp_path / "session.json"
    monkeypatch.setattr(session_store, "SESSION_PATH", p)
    p.write_text("{not valid json", encoding="utf-8")
    assert session_store.load_session() is None
