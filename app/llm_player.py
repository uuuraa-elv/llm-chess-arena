"""Prompt engineering & parsing langkah LLM.

Mode: "nalar lalu langkah final" dengan output JSON terstruktur. Model diminta
membalas {"reasoning": "...", "move": "<uci>"} agar kita bisa menampilkan
penalarannya di UI. Parser tetap robust: bila JSON gagal/ilegal, jatuh balik ke
ekstraksi langkah dari prosa (UCI/SAN) lalu validasi via python-chess. Bila tetap
tak ada langkah legal, lakukan retry dengan pesan koreksi.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import chess

from . import openrouter
from .chess_game import ChessGame

MAX_MOVE_RETRIES = 3
MOVE_TEMPERATURE = 0.4
# Batas atas putaran evaluasi-diri. Modelnya sendiri yang memutuskan berhenti
# ("decision": "final") atau lanjut ("decision": "continue"); cap ini cuma pengaman.
MAX_SELF_EVAL_ROUNDS = 4
# Batas panjang reasoning yang disimpan/dikirim ke UI (hemat token & payload).
REASONING_MAX_CHARS = 1500

# Token UCI: e2e4, e7e8q (promosi opsional). Word-boundary mencegah salah-tangkap
# koordinat di prosa (mis. "bishop on c1 defends d2" -> jangan ambil "c1d2").
_UCI_RE = re.compile(r"(?<!\w)[a-h][1-8][a-h][1-8][qrbnQRBN]?(?!\w)")
# Hapus karakter kontrol agar log respons LLM tetap rapi.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Keputusan model untuk lanjut/berhenti self-eval.
_DECISION_RE = re.compile(r"decision\s*[:=]\s*(continue|final|done|stop)", re.IGNORECASE)


def _wants_continue(text: str) -> bool:
    """True bila model minta satu putaran evaluasi-diri lagi. Default: berhenti."""
    m = _DECISION_RE.search(text or "")
    return bool(m) and m.group(1).lower() == "continue"
# Penanda jawaban final yang sering dipakai model.
_MARKER_RE = re.compile(
    r"(?:final\s+move|my\s+move|best\s+move|i\s+play|i\s+will\s+play|move\s*[:=]|answer\s*[:=]|jawaban|langkah)",
    re.IGNORECASE,
)
# Karakter pembungkus yang dibersihkan dari token SAN.
_STRIP = "()[]{}.,!?;:*\"'`#+ \t\n"


@dataclass
class MoveOutcome:
    """Hasil meminta satu langkah dari LLM."""
    move: Optional[chess.Move]
    illegal_attempts: list[dict[str, Any]] = field(default_factory=list)
    calls: int = 0  # total panggilan API untuk langkah ini (propose + self-eval + retry)
    eval_rounds: int = 0  # berapa kali model self-evaluasi (diputuskan AI sendiri)
    reasoning: str = ""  # penalaran model untuk langkah final (untuk panel UI)

    @property
    def forfeited(self) -> bool:
        return self.move is None


def system_prompt(color: bool) -> str:
    color_name = "White" if color == chess.WHITE else "Black"
    return (
        f"You are a world-class chess engine playing the {color_name} pieces, and you are "
        "playing to WIN the match.\n"
        "Analyze every move with this method, in order:\n"
        "1. FORCING MOVES: list the checks, captures, and threats available to BOTH sides "
        "(yours and your opponent's).\n"
        "2. CANDIDATES: pick 2-3 candidate moves from the legal list.\n"
        "3. REFUTATION: for each candidate, state your opponent's best reply and whether you are "
        "still fine after it (no hanging piece, no fork/pin/skewer/discovered attack/back-rank mate).\n"
        "4. CHOOSE the candidate that is safest and strongest.\n"
        "Match strategy: when you need a win and the position is drifting toward a draw, prefer the "
        "move with the most winning chances instead of simplifying into a draw; when you are ahead "
        "in material, simplify safely. Never resign, never offer a draw.\n"
        "You may be asked to re-evaluate; keep analyzing as many rounds as YOU think are needed.\n"
        "Reply with ONLY a single JSON object, no markdown, no text outside it, in this exact shape:\n"
        '{"reasoning": "<your CCT analysis: forcing moves, candidates, opponent best reply>", "move": "<uci>"}\n'
        "where <uci> is UCI notation (for example e2e4, g1f3, e7e8q for promotion, e1g1 for kingside "
        "castling). Keep \"reasoning\" concise but complete. Never output anything outside the JSON object."
    )


def _board_ascii(game: ChessGame) -> str:
    """Diagram papan 8x8 (sudut pandang Putih) agar model lebih mudah 'melihat' posisi."""
    return str(game.board)


def user_prompt(game: ChessGame, last_opponent_move: Optional[str],
                history: str = "", match_context: str = "") -> str:
    color_name = "White" if game.turn == chess.WHITE else "Black"
    legal_uci = " ".join(game.legal_moves_uci())
    legal_san = " ".join(game.legal_moves_san())
    last = last_opponent_move if last_opponent_move else "(none, this is the opening move)"
    lines: list[str] = []
    if match_context:
        lines.append(match_context)
    lines.append(f"Position (FEN): {game.fen}")
    lines.append(
        "Board (White's view; UPPERCASE = White, lowercase = Black, '.' = empty; "
        "top row = rank 8, bottom row = rank 1, left column = file a):"
    )
    lines.append(_board_ascii(game))
    if history:
        lines.append(f"Game so far (SAN): {history}")
    lines.append("")
    lines.append(f"You are: {color_name}")
    lines.append(f"Move number: {game.fullmove_number}")
    lines.append(f"Opponent's last move: {last}")
    lines.append(f"Your legal moves (SAN): {legal_san}")
    lines.append(f"Your legal moves (UCI): {legal_uci}")
    lines.append("")
    lines.append(
        "Work through the method: (1) list checks/captures/threats for both sides, "
        "(2) give 2-3 candidate moves, (3) for each, the opponent's best reply, (4) choose the best."
    )
    lines.append('Reply with ONLY the JSON object: {"reasoning": "<brief>", "move": "<uci>"}')
    return "\n".join(lines)


def self_eval_prompt(game: ChessGame, move: chess.Move) -> str:
    """Minta model mengkritik langkah usulannya & memutuskan sendiri lanjut/berhenti."""
    legal = " ".join(game.legal_moves_uci())
    return (
        f"You proposed {move.uci()}. Critically re-evaluate it before committing:\n"
        f"- State your opponent's strongest reply; are you still fine after it?\n"
        f"- Does it hang a piece or allow a tactic (fork, pin, skewer, discovered attack, mate)?\n"
        f"- Compare it against at least ONE alternative legal move: is the alternative clearly better "
        f"(a check, a winning capture, a bigger threat, or a safer position)?\n"
        f"Legal moves (UCI): {legal}\n"
        f"If {move.uci()} is still best, keep it; otherwise switch to the better move. "
        "YOU decide how many rounds to keep analyzing.\n"
        'Reply with ONLY a JSON object that adds a "decision" field:\n'
        '{"reasoning": "<brief>", "decision": "continue"|"final", "move": "<uci>"}\n'
        'Use "continue" if you want one more analysis round, or "final" if you are '
        "confident this is your best move."
    )


def correction_prompt(game: ChessGame, bad_text: str) -> str:
    legal = " ".join(game.legal_moves_uci())
    snippet = bad_text.strip()[:160]
    return (
        f"Your previous reply did not contain a legal move (got: \"{snippet}\"). "
        f"You MUST choose one move from this exact list of legal UCI moves: {legal}\n"
        'Reply with ONLY the JSON object: {"reasoning": "<brief>", "move": "<uci>"}'
    )


def extract_move(text: str, game: ChessGame) -> Optional[chess.Move]:
    """Ekstrak langkah legal dari `text` (mungkin verbose).

    Strategi: kumpulkan semua kandidat (UCI via regex + token gaya SAN) beserta
    posisinya, validasi via game.parse_move, lalu ambil kandidat legal TERAKHIR.
    Bila ada penanda jawaban (mis. "MOVE:"), utamakan kandidat setelah penanda
    terakhir. Ini menangkap langkah final yang dinyatakan di akhir penalaran.
    """
    if not text:
        return None
    cleaned = text.replace("`", " ")

    def best_after(offset: int) -> Optional[chess.Move]:
        candidates = _candidate_moves(cleaned, game)
        scoped = [(pos, mv) for pos, mv in candidates if pos >= offset]
        pool = scoped if scoped else candidates
        if not pool:
            return None
        pool.sort(key=lambda pm: pm[0])
        return pool[-1][1]

    markers = list(_MARKER_RE.finditer(cleaned))
    if markers:
        move = best_after(markers[-1].start())
        if move is not None:
            return move
    return best_after(0)


def _candidate_moves(text: str, game: ChessGame) -> list[tuple[int, chess.Move]]:
    """Kembalikan list (offset, Move) untuk semua kandidat legal di `text`."""
    found: list[tuple[int, chess.Move]] = []

    # Sumber 1: token UCI murni (berbasis regex, akurat dengan offset).
    for m in _UCI_RE.finditer(text):
        move = game.parse_move(m.group(0))
        if move is not None:
            found.append((m.start(), move))

    # Sumber 2: token gaya SAN (Nf3, O-O, exd5, e8=Q+). Bersihkan tiap token.
    for m in re.finditer(r"\S+", text):
        token = m.group(0).strip(_STRIP)
        if not token or token.isdigit():
            continue
        # Buang prefiks nomor langkah "12." -> sudah ke-strip; buang "12.Nf3".
        token = re.sub(r"^\d+\.+", "", token)
        if not token:
            continue
        move = game.parse_move(token)
        if move is not None:
            found.append((m.start(), move))

    return found


# ----------------------------- JSON / reasoning -----------------------------

_FENCE_RE = re.compile(r"```[a-zA-Z]*")


def _clip_reasoning(text: str) -> str:
    """Rapikan & potong reasoning agar hemat token/payload."""
    s = _CTRL_RE.sub(" ", (text or "")).strip()
    s = re.sub(r"\s+", " ", s)
    if len(s) > REASONING_MAX_CHARS:
        return s[: REASONING_MAX_CHARS - 1].rstrip() + "…"
    return s


def _extract_json_object(text: str) -> Optional[dict]:
    """Ambil objek JSON PERTAMA dari `text` (toleran code-fence & prosa di sekitar).

    Pakai raw_decode dari '{' pertama (non-greedy) agar JSON valid yang diikuti
    prosa ber-brace (mis. "{...} Note: avoid {bad}") tetap ter-parse, bukan gagal
    karena rfind('}') menelan brace prosa.
    """
    if not text:
        return None
    cleaned = _FENCE_RE.sub(" ", text).replace("```", " ")
    start = cleaned.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# Marker baris langkah/keputusan (di posisi mana pun via \b), dibuang dari reasoning.
_PROSE_DROP_RE = re.compile(r"\b(move|decision|answer|jawaban|langkah)\s*[:=]", re.IGNORECASE)


def _prose_reasoning(text: str) -> str:
    """Reasoning cadangan dari prosa: buang baris berisi marker MOVE/DECISION
    (di awal maupun inline, mis. 'My move: e4'), gabungkan sisanya."""
    if not text:
        return ""
    keep = [ln for ln in text.splitlines() if not _PROSE_DROP_RE.search(ln)]
    return _clip_reasoning(" ".join(keep))


def parse_response(text: str, game: ChessGame):
    """Parse respons LLM -> (move|None, reasoning, decision|None).

    Utamakan JSON {"reasoning","move","decision"}. Bila JSON gagal/ilegal, jatuh
    balik ke parser prosa robust (extract_move) + reasoning dari prosa. `decision`
    dinormalkan ke lowercase ("continue"/"final"/...), None bila tak ada.
    """
    reasoning = ""
    decision: Optional[str] = None

    data = _extract_json_object(text)
    if data is not None:
        raw_move = data.get("move")
        move = game.parse_move(str(raw_move)) if raw_move is not None else None
        r = data.get("reasoning")
        if isinstance(r, str):
            reasoning = r
        d = data.get("decision")
        if isinstance(d, str):
            decision = d.strip().lower()
        if move is not None:
            return move, _clip_reasoning(reasoning), decision

    # Fallback robust: ekstrak langkah dari prosa. Bila JSON ter-parse tapi move-nya
    # ilegal, JANGAN biarkan langkah yang cuma disebut di teks reasoning ikut terpilih
    # (buang dulu nilai reasoning dari teks fallback) -> hindari memainkan langkah
    # bukan-maksud-model; kembalikan None agar memicu retry koreksi.
    fallback_text = text
    if reasoning:
        fallback_text = text.replace(reasoning, " ")
    move = extract_move(fallback_text, game)
    if not reasoning:
        reasoning = _prose_reasoning(text)
    if decision is None and _wants_continue(text):
        decision = "continue"
    return move, _clip_reasoning(reasoning), decision


def _ask_until_legal(game, model, messages, api_key, chat_fn, max_retries, illegal, counter):
    """Panggil model; bila langkah ilegal/tak terbaca, tambah pesan koreksi & ulangi
    sampai `max_retries`. Return (move|None, last_content, reasoning, decision).
    Mencatat percobaan ilegal ke `illegal` & menambah `counter["calls"]`.
    """
    for _ in range(max_retries):
        counter["calls"] += 1
        resp = chat_fn(model, messages, api_key=api_key, temperature=MOVE_TEMPERATURE)
        content = resp.get("content", "") if isinstance(resp, dict) else str(resp)
        move, reasoning, decision = parse_response(content, game)
        if move is not None:
            return move, content, reasoning, decision
        illegal.append({"attempt": len(illegal) + 1, "raw": _CTRL_RE.sub(" ", content.strip())[:200]})
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": correction_prompt(game, content)})
    return None, "", "", None


def request_move(
    game: ChessGame,
    model: str,
    color: bool,
    last_opponent_move: Optional[str],
    api_key: Optional[str] = None,
    chat_fn: Callable[..., dict] = openrouter.chat,
    max_retries: int = MAX_MOVE_RETRIES,
    max_self_eval_rounds: int = MAX_SELF_EVAL_ROUNDS,
    history: str = "",
    match_context: str = "",
) -> MoveOutcome:
    """Minta satu langkah legal dari `model`, dengan iterasi evaluasi-diri yang
    DIPUTUSKAN MODEL SENDIRI (berapa kali menganalisis), dibatasi `max_self_eval_rounds`.

    Alur:
      1. PROPOSE  - model menganalisis taktik & ancaman lalu mengusulkan langkah.
      2. SELF-EVAL - model mengkritik langkahnya sendiri & memilih 'DECISION: continue'
         (lanjut satu putaran) atau 'DECISION: final' (berhenti). Berhenti juga saat cap tercapai.
    Tiap fase punya retry koreksi sampai `max_retries` bila ilegal/tak terbaca.
    Propose gagal total -> forfeit (move None). Self-eval gagal -> pertahankan langkah usulan.
    `chat_fn` di-inject untuk testing. OpenRouterError naik ke pemanggil (ditangani match_engine).
    """
    messages = [
        {"role": "system", "content": system_prompt(color)},
        {"role": "user", "content": user_prompt(game, last_opponent_move, history, match_context)},
    ]
    illegal: list[dict[str, Any]] = []
    counter = {"calls": 0}

    # Fase 1: propose
    move, content, reasoning, _ = _ask_until_legal(
        game, model, messages, api_key, chat_fn, max_retries, illegal, counter
    )
    if move is None:
        return MoveOutcome(move=None, illegal_attempts=illegal, calls=counter["calls"])

    # Fase 2: self-evaluation; model memutuskan sendiri berapa putaran (cap = pengaman).
    rounds = 0
    for _ in range(max(0, max_self_eval_rounds)):
        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": self_eval_prompt(game, move)})
        revised, revised_content, revised_reasoning, decision = _ask_until_legal(
            game, model, messages, api_key, chat_fn, max_retries, illegal, counter
        )
        if revised is None:
            break  # self-eval gagal beri langkah legal -> pertahankan langkah usulan
        move, content, reasoning = revised, revised_content, revised_reasoning
        rounds += 1
        if decision != "continue":
            break  # model memutuskan ini langkah finalnya (atau tak minta lanjut)

    return MoveOutcome(
        move=move,
        illegal_attempts=illegal,
        calls=counter["calls"],
        eval_rounds=rounds,
        reasoning=reasoning,
    )
