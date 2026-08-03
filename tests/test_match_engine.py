"""Test orkestrasi sesi: scoring best-to-2, remis tidak dihitung, terminasi."""
import random

import chess

from app.chess_game import GameResult
from app.match_engine import MatchEngine, WINS_NEEDED


# Script Fool's mate per-ply: White kalah, Black menang tiap game.
_SCRIPT = {0: "f2f3", 1: "e7e5", 2: "g2g4", 3: "d8h4"}


def _fen_from_messages(messages):
    for m in reversed(messages):
        if m["role"] == "user" and "Position (FEN):" in m["content"]:
            for line in m["content"].splitlines():
                if line.startswith("Position (FEN):"):
                    return line.split("Position (FEN):", 1)[1].strip()
    return chess.STARTING_FEN


def _fools_mate_chat(model, messages, api_key=None, temperature=0.0):
    fen = _fen_from_messages(messages)
    fields = fen.split()
    side, fullmove = fields[1], int(fields[5])
    ply = (fullmove - 1) * 2 + (0 if side == "w" else 1)
    return {"content": "MOVE: " + _SCRIPT[ply], "usage": {}}


def test_tally_draw_increments_not_scored():
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat)
    eng._tally(GameResult(True, None, "stalemate"), "A", "B", True)
    assert eng.draws == 1
    assert eng.score_a == 0 and eng.score_b == 0


def test_tally_white_winner_scores_correct_model():
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat)
    # Model A bermain Putih dan menang.
    eng._tally(GameResult(True, chess.WHITE, "checkmate"), "A", "B", True)
    assert eng.score_a == 1 and eng.score_b == 0


def test_tally_black_winner_scores_correct_model():
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat)
    # Putih = A, Hitam = B; pemenang Hitam -> B yang dapat poin.
    eng._tally(GameResult(True, chess.BLACK, "checkmate"), "A", "B", True)
    assert eng.score_b == 1 and eng.score_a == 0


def test_full_session_terminates_at_two_wins():
    eng = MatchEngine(
        "Model-A", "Model-B", api_key="k",
        chat_fn=_fools_mate_chat, rng=random.Random(7),
    )
    final = {}
    eng.sig_match_over.connect(lambda d: final.update(d))
    eng.run()  # jalankan sinkron di thread test

    assert final.get("phase") == "match_over"
    assert max(eng.score_a, eng.score_b) == WINS_NEEDED
    assert min(eng.score_a, eng.score_b) < WINS_NEEDED
    assert eng.draws == 0
    assert final.get("winner_model") in ("Model-A", "Model-B")
    # Tiap game decisive (tanpa remis) -> sesi selesai dalam <= 3 game.
    assert eng.game_index <= 3


def test_session_without_api_key_aborts():
    eng = MatchEngine("A", "B", api_key="", chat_fn=_fools_mate_chat)
    final = {}
    eng.sig_match_over.connect(lambda d: final.update(d))
    eng.run()
    assert final.get("aborted") is True
    assert final.get("reason") == "no_api_key"


def test_board_and_state_payloads_carry_white_is_a():
    # Saat kedua model identik, UI tak bisa membedakan sisi warna dari nama saja;
    # payload harus membawa white_is_a/thinking_is_a (slot) untuk disambiguasi.
    eng = MatchEngine(
        "Same", "Same", api_key="k",
        chat_fn=_fools_mate_chat, rng=random.Random(7),
    )
    boards, states = [], []
    eng.sig_board.connect(lambda d: boards.append(d))
    eng.sig_state.connect(lambda d: states.append(d))
    eng.run()

    resets = [b for b in boards if b.get("reset")]
    assert resets, "harus ada minimal satu board reset"
    assert all("white_is_a" in b and isinstance(b["white_is_a"], bool) for b in resets)

    thinking = [s for s in states if s.get("phase") == "thinking"]
    assert thinking, "harus ada minimal satu state thinking"
    for s in thinking:
        assert isinstance(s.get("white_is_a"), bool)
        assert isinstance(s.get("thinking_is_a"), bool)
        # thinking_is_a konsisten: sisi yang berpikir = Putih iff white_is_a sama.
        is_white_turn = s["turn_color"] == "White"
        assert s["thinking_is_a"] == (is_white_turn == s["white_is_a"])


def test_board_move_payloads_carry_reasoning_and_timing():
    # Fitur 1: tiap langkah membawa reasoning + waktu berpikir + model untuk panel.
    eng = MatchEngine(
        "Model-A", "Model-B", api_key="k",
        chat_fn=_fools_mate_chat, rng=random.Random(7),
    )
    boards = []
    eng.sig_board.connect(lambda d: boards.append(d))
    eng.run()

    moves = [b for b in boards if not b.get("reset")]
    assert moves, "harus ada minimal satu langkah ter-emit"
    for b in moves:
        assert isinstance(b.get("reasoning"), str)
        assert isinstance(b.get("thinking_ms"), int) and b["thinking_ms"] >= 0
        assert isinstance(b.get("model"), str) and b["model"]
        assert isinstance(b.get("mover_is_a"), bool)
        assert isinstance(b.get("eval_rounds"), int)


def test_engine_injects_match_context_and_history():
    prompts = []

    def capturing_chat(model, messages, api_key=None, temperature=0.0):
        for m in messages:
            if m["role"] == "user" and "Position (FEN):" in m["content"]:
                prompts.append(m["content"])
        return _fools_mate_chat(model, messages, api_key=api_key, temperature=temperature)

    eng = MatchEngine(
        "A", "B", api_key="k", chat_fn=capturing_chat, rng=random.Random(7),
    )
    eng.run()

    assert prompts, "harus ada prompt user ter-capture"
    assert any(f"Match (best of {WINS_NEEDED}):" in p for p in prompts)
    assert any("Game so far (SAN):" in p for p in prompts)


def test_pause_resume_toggle_flag():
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat)
    assert eng._paused is False
    eng.pause()
    assert eng._paused is True
    eng.resume()
    assert eng._paused is False


def test_wait_if_paused_returns_immediately_when_not_paused():
    # Tidak dijeda -> harus kembali seketika (tak nge-block) dan tak emit "paused".
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat)
    states = []
    eng.sig_state.connect(lambda d: states.append(d))
    eng._wait_if_paused()
    assert not any(s.get("phase") == "paused" for s in states)


def test_engine_resume_continues_from_saved_position():
    # Resume di tengah fool's mate (2 langkah sudah dimainkan: f2f3, e7e5).
    resume = {
        "version": 1, "model_a": "A", "model_b": "B",
        "score_a": 0, "score_b": 0, "draws": 0, "game_index": 1,
        "api_calls": 4, "white_is_a": True,
        "moves": ["f2f3", "e7e5"], "log": [],
    }
    eng = MatchEngine(
        "A", "B", api_key="k", chat_fn=_fools_mate_chat,
        rng=random.Random(7), resume=resume,
    )
    boards, final = [], {}
    eng.sig_board.connect(lambda d: boards.append(d))
    eng.sig_match_over.connect(lambda d: final.update(d))
    eng.run()

    resets = [b for b in boards if b.get("reset")]
    assert resets, "harus ada board reset"
    # Reset pertama = resume: bertanda resumed & melanjutkan posisi (bukan dari awal).
    assert resets[0].get("resumed") is True
    assert resets[0]["fen"] != chess.STARTING_FEN
    assert resets[0]["game_index"] == 1
    # Tes meng-inject chat_fn -> tidak boleh menulis ke disk (persist mati).
    assert eng._persist is False
    # Sesi tetap berjalan sampai ada pemenang mutlak.
    assert final.get("phase") == "match_over"
    assert max(eng.score_a, eng.score_b) == WINS_NEEDED


def test_engine_persists_resumable_snapshot(monkeypatch):
    # Paksa persist (meski chat_fn mock) & tangkap snapshot tanpa menyentuh disk.
    from app import session_store
    saved, cleared = [], []
    monkeypatch.setattr(session_store, "save_session", lambda s: saved.append(s))
    monkeypatch.setattr(session_store, "clear_session", lambda: cleared.append(True))
    eng = MatchEngine("A", "B", api_key="k", chat_fn=_fools_mate_chat, rng=random.Random(7))
    eng._persist = True
    eng.run()

    assert saved, "harus ada snapshot tersimpan selama bermain"
    s0 = saved[0]
    for key in ("version", "model_a", "model_b", "score_a", "score_b",
                "game_index", "white_is_a", "moves", "log"):
        assert key in s0
    assert isinstance(s0["moves"], list) and isinstance(s0["log"], list)
    # Sesi selesai (ada pemenang) -> snapshot dihapus.
    assert cleared, "snapshot harus dihapus saat sesi selesai"
