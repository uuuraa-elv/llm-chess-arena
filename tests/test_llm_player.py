"""Test prompt-parsing & retry langkah LLM (OpenRouter di-mock)."""
import chess

from app.chess_game import ChessGame
from app import llm_player


def test_extract_simple_uci():
    g = ChessGame()
    assert llm_player.extract_move("MOVE: e2e4", g).uci() == "e2e4"


def test_extract_with_reasoning_then_marker():
    g = ChessGame()
    text = "Let me develop a knight to control the center.\nMOVE: g1f3"
    assert llm_player.extract_move(text, g).uci() == "g1f3"


def test_extract_san_in_prose():
    g = ChessGame()
    assert llm_player.extract_move("I will play Nf3 to develop.", g).uci() == "g1f3"


def test_extract_picks_last_legal_move():
    g = ChessGame()
    text = "e2e4 looks tempting, but after more thought I prefer d2d4."
    assert llm_player.extract_move(text, g).uci() == "d2d4"


def test_extract_marker_overrides_earlier_mentions():
    g = ChessGame()
    text = "Candidates: e2e4, d2d4, g1f3.\nFinal move:\nMOVE: c2c4"
    assert llm_player.extract_move(text, g).uci() == "c2c4"


def test_extract_illegal_returns_none():
    g = ChessGame()
    assert llm_player.extract_move("MOVE: e2e5", g) is None
    assert llm_player.extract_move("blah blah no move here", g) is None


def test_extract_uci_requires_word_boundary():
    g = ChessGame()
    # 'e2e4' tertanam di token alfanumerik lain -> bukan langkah.
    assert llm_player.extract_move("identifier xe2e4y here", g) is None
    # token UCI berdiri sendiri tetap terbaca.
    assert llm_player.extract_move("MOVE: e2e4", g).uci() == "e2e4"


def _fake_chat(responses):
    """Bangun chat_fn palsu yang mengembalikan teks berurutan."""
    box = {"i": 0, "calls": 0}

    def chat(model, messages, api_key=None, temperature=0.0):
        box["calls"] += 1
        idx = min(box["i"], len(responses) - 1)
        text = responses[box["i"]] if box["i"] < len(responses) else responses[-1]
        box["i"] += 1
        return {"content": text, "usage": {}}

    chat.box = box
    return chat


def test_request_move_success_first_try():
    g = ChessGame()
    chat = _fake_chat(["MOVE: e2e4"])
    out = llm_player.request_move(g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=0)
    assert out.move.uci() == "e2e4"
    assert out.illegal_attempts == []
    assert chat.box["calls"] == 1
    assert out.calls == 1


def test_request_move_retry_then_success():
    g = ChessGame()
    chat = _fake_chat(["MOVE: z9z9", "MOVE: e2e4"])
    out = llm_player.request_move(g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=0)
    assert out.move.uci() == "e2e4"
    assert len(out.illegal_attempts) == 1
    assert chat.box["calls"] == 2


def test_request_move_forfeit_after_max_retries():
    g = ChessGame()
    chat = _fake_chat(["total nonsense", "still nonsense", "nope"])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_retries=3, max_self_eval_rounds=0
    )
    assert out.move is None
    assert out.forfeited is True
    assert len(out.illegal_attempts) == 3
    assert chat.box["calls"] == 3


def test_self_eval_can_change_move():
    # Propose e2e4, lalu fase self-eval menggantinya jadi d2d4.
    g = ChessGame()
    chat = _fake_chat(["MOVE: e2e4", "On reflection, d2d4 is safer. MOVE: d2d4"])
    out = llm_player.request_move(g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=1)
    assert out.move.uci() == "d2d4"
    assert chat.box["calls"] == 2
    assert out.calls == 2


def test_self_eval_keeps_confirmed_move():
    g = ChessGame()
    chat = _fake_chat(["MOVE: g1f3", "Still best. MOVE: g1f3"])
    out = llm_player.request_move(g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=1)
    assert out.move.uci() == "g1f3"
    assert out.calls == 2


def test_self_eval_failure_keeps_proposed_move():
    # Self-eval gagal beri langkah legal -> pertahankan langkah usulan (bukan forfeit).
    g = ChessGame()
    chat = _fake_chat(["MOVE: e2e4", "nonsense", "nonsense", "nonsense"])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=1, max_retries=3
    )
    assert out.move.uci() == "e2e4"
    assert out.forfeited is False
    assert out.calls == 4  # 1 propose + 3 self-eval gagal


def test_self_eval_loops_until_ai_decides_final():
    # Model memutuskan sendiri: lanjut, lanjut, lalu final -> 3 putaran self-eval.
    g = ChessGame()
    chat = _fake_chat([
        "MOVE: e2e4",
        "DECISION: continue\nMOVE: d2d4",
        "DECISION: continue\nMOVE: g1f3",
        "DECISION: final\nMOVE: b1c3",
    ])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=5
    )
    assert out.move.uci() == "b1c3"   # langkah final yang diputuskan model
    assert out.eval_rounds == 3
    assert out.calls == 4             # 1 propose + 3 self-eval


def test_self_eval_respects_cap():
    # Model terus minta 'continue', cap menghentikannya.
    g = ChessGame()
    chat = _fake_chat(["MOVE: e2e4", "DECISION: continue\nMOVE: e2e4"])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=2
    )
    assert out.move.uci() == "e2e4"
    assert out.eval_rounds == 2       # dibatasi cap, bukan lebih
    assert out.calls == 3             # 1 propose + 2 self-eval (cap)


# ============================================================
# Fitur 1: respons JSON terstruktur (reasoning + move)
# ============================================================

def test_parse_response_json_move_and_reasoning():
    g = ChessGame()
    txt = '{"reasoning": "Develop and fight for the center.", "move": "e2e4"}'
    move, reasoning, decision = llm_player.parse_response(txt, g)
    assert move.uci() == "e2e4"
    assert "center" in reasoning.lower()
    assert decision is None


def test_parse_response_json_in_code_fence():
    g = ChessGame()
    txt = "Sure:\n```json\n{\"reasoning\": \"Knight out.\", \"move\": \"g1f3\"}\n```"
    move, reasoning, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "g1f3"
    assert reasoning == "Knight out."


def test_parse_response_json_decision_continue():
    g = ChessGame()
    txt = '{"reasoning":"thinking","decision":"continue","move":"d2d4"}'
    move, _, decision = llm_player.parse_response(txt, g)
    assert move.uci() == "d2d4"
    assert decision == "continue"


def test_parse_response_falls_back_to_prose():
    # Tidak ada JSON; parser prosa robust tetap menemukan langkah + reasoning.
    g = ChessGame()
    txt = "I think developing the knight is best here.\nMOVE: g1f3"
    move, reasoning, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "g1f3"
    assert reasoning  # reasoning prosa tertangkap, non-kosong
    assert "MOVE" not in reasoning  # baris MOVE dibuang dari reasoning


def test_parse_response_json_illegal_move_falls_back_to_prose():
    g = ChessGame()
    # Move di JSON ilegal -> fallback ke UCI prosa yang legal.
    txt = '{"reasoning":"x","move":"e2e5"} but actually MOVE: e2e4'
    move, _, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "e2e4"


def test_parse_response_clips_long_reasoning():
    g = ChessGame()
    long_text = "a" * 5000
    txt = '{"reasoning":"' + long_text + '","move":"e2e4"}'
    move, reasoning, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "e2e4"
    assert len(reasoning) <= llm_player.REASONING_MAX_CHARS


def test_request_move_returns_reasoning_from_json():
    g = ChessGame()
    chat = _fake_chat(['{"reasoning":"Open with the king pawn.","move":"e2e4"}'])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=0
    )
    assert out.move.uci() == "e2e4"
    assert "king pawn" in out.reasoning.lower()


def test_request_move_self_eval_json_decision_drives_loop():
    g = ChessGame()
    chat = _fake_chat([
        '{"reasoning":"open center","move":"e2e4"}',
        '{"reasoning":"queen pawn safer","decision":"continue","move":"d2d4"}',
        '{"reasoning":"knight is best","decision":"final","move":"g1f3"}',
    ])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=5
    )
    assert out.move.uci() == "g1f3"
    assert out.eval_rounds == 2
    assert out.reasoning == "knight is best"  # reasoning langkah final


def test_request_move_reasoning_empty_when_absent():
    # Respons tanpa reasoning apa pun -> reasoning kosong (UI tampilkan "tidak ada").
    g = ChessGame()
    chat = _fake_chat(["MOVE: e2e4"])
    out = llm_player.request_move(
        g, "m", chess.WHITE, None, api_key="k", chat_fn=chat, max_self_eval_rounds=0
    )
    assert out.move.uci() == "e2e4"
    assert out.reasoning == ""


def test_parse_response_json_with_trailing_braced_prose():
    # JSON valid diikuti prosa yang memuat brace tidak boleh menggagalkan jalur JSON
    # (reasoning terstruktur harus tetap dipakai, bukan teks JSON mentah).
    g = ChessGame()
    txt = '{"reasoning":"good plan","move":"e2e4"} Note: avoid {bad} idea.'
    move, reasoning, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "e2e4"
    assert reasoning == "good plan"


def test_parse_response_illegal_json_move_does_not_pick_move_from_reasoning():
    # Move JSON ilegal + reasoning menyebut langkah lain: jangan mainkan langkah
    # yang cuma disebut di prosa reasoning -> None (memicu retry koreksi).
    g = ChessGame()
    txt = '{"reasoning":"d2d4 is also fine","move":"e2e5"}'
    move, reasoning, _ = llm_player.parse_response(txt, g)
    assert move is None
    assert reasoning == "d2d4 is also fine"


def test_parse_response_illegal_json_move_still_honors_explicit_marker():
    # Tapi penanda eksplisit di luar reasoning tetap dihormati.
    g = ChessGame()
    txt = '{"reasoning":"x","move":"e2e5"} but actually MOVE: e2e4'
    move, _, _ = llm_player.parse_response(txt, g)
    assert move.uci() == "e2e4"


def test_prose_reasoning_strips_inline_move_marker():
    # Baris "My move: g1f3" harus dibuang dari reasoning meski marker tak di awal baris.
    r = llm_player._prose_reasoning("I will develop a piece.\nMy move: g1f3")
    assert "g1f3" not in r
    assert "develop" in r.lower()


def test_user_prompt_includes_history_and_match_context():
    g = ChessGame()
    g.push(chess.Move.from_uci("e2e4"))
    p = llm_player.user_prompt(
        g, "e4",
        history=g.movetext(),
        match_context="Match (best of 2): you 1 - 0 opponent.",
    )
    assert "Game so far (SAN): 1. e4" in p
    assert "Match (best of 2): you 1 - 0 opponent." in p
    assert "legal moves (san)" in p.lower()
    assert "Position (FEN):" in p


def test_user_prompt_omits_history_when_empty():
    g = ChessGame()
    p = llm_player.user_prompt(g, None)
    assert "Game so far" not in p


def test_system_prompt_has_cct_and_win_framing():
    s = llm_player.system_prompt(chess.WHITE).lower()
    assert "checks" in s and "captures" in s and "threats" in s
    assert "win the match" in s  # framing menang, bukan sekadar substring "win"


def test_self_eval_prompt_asks_for_alternative():
    g = ChessGame()
    sp = llm_player.self_eval_prompt(g, chess.Move.from_uci("e2e4")).lower()
    assert "alternative" in sp
    assert "decision" in sp
