# Chess Prompt Quality (Bundle C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Naikkan kekuatan main LLM di LLM Chess Arena dengan memperkaya prompt (SAN + riwayat PGN + konteks pertandingan + metode CCT), tanpa mengubah arsitektur, aturan, atau format I/O JSON.

**Architecture:** Tambah 2 helper baca-saja di `ChessGame` (daftar SAN + movetext), perkaya 3 prompt di `llm_player.py` (CCT + framing menang), dan teruskan riwayat + skor dari `match_engine` ke `request_move`. Output JSON `{"reasoning","move","decision"}` dan jalur parsing/UI/threading TIDAK berubah.

**Tech Stack:** Python 3.14, python-chess, PyQt6 (tak disentuh di plan ini), pytest. Venv di `.venv\Scripts\python.exe`.

## Global Constraints

- **Project BUKAN git repo** → tidak ada langkah `git commit`. "Checkpoint" = jalankan test sampai hijau.
- Jalankan Python via `.venv/Scripts/python.exe` (Windows). pytest: `.venv/Scripts/python.exe -m pytest`.
- **Format output JSON TIDAK berubah:** `{"reasoning","move","decision"}`. Jangan sentuh `parse_response`, `extract_move`, fallback prosa, atau UI/web.
- **60 test existing WAJIB tetap hijau.** Param baru harus punya default agar pemanggilan lama kompatibel.
- Jangan ubah: aturan catur, best-of-2, randomisasi warna, sinyal Qt, board.js/app.js/style.css/index.html, keamanan API key.
- `REASONING_MAX_CHARS` dinaikkan ke `1500` (dari 600).
- Reasoning/teks dinamis ke DOM tetap via `textContent` (tak ada perubahan UI di plan ini).

---

### Task 1: Helper SAN + movetext di ChessGame

**Files:**
- Modify: `app/chess_game.py` (tambah 2 method di kelas `ChessGame`)
- Test: `tests/test_chess_game.py`

**Interfaces:**
- Consumes: `self.board` (chess.Board), `chess.Board` untuk replay.
- Produces:
  - `ChessGame.legal_moves_san(self) -> list[str]`
  - `ChessGame.movetext(self) -> str`  (movetext SAN, mis. `"1. e4 e5 2. Nf3"`, `""` bila kosong; TIDAK mengubah state board)

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan di akhir `tests/test_chess_game.py` (pastikan ada `import chess` dan `from app.chess_game import ChessGame` di atas; tambahkan import bila belum ada):

```python
def test_legal_moves_san_start_position():
    g = ChessGame()
    san = g.legal_moves_san()
    assert "Nf3" in san
    assert "e4" in san
    assert len(san) == 20  # 16 langkah pion + 4 langkah kuda


def test_movetext_empty_at_start():
    g = ChessGame()
    assert g.movetext() == ""


def test_movetext_builds_pgn_movetext():
    g = ChessGame()
    g.push(chess.Move.from_uci("e2e4"))
    assert g.movetext() == "1. e4"
    g.push(chess.Move.from_uci("e7e5"))
    assert g.movetext() == "1. e4 e5"
    g.push(chess.Move.from_uci("g1f3"))
    assert g.movetext() == "1. e4 e5 2. Nf3"


def test_movetext_does_not_mutate_board():
    g = ChessGame()
    g.push(chess.Move.from_uci("e2e4"))
    before_fen = g.fen
    before_turn = g.turn
    _ = g.movetext()
    assert g.fen == before_fen
    assert g.turn == before_turn
```

- [ ] **Step 2: Jalankan test untuk memastikan GAGAL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_chess_game.py -q -k "legal_moves_san or movetext"`
Expected: FAIL dengan `AttributeError: 'ChessGame' object has no attribute 'legal_moves_san'` (dan `movetext`).

- [ ] **Step 3: Implementasi minimal**

Di `app/chess_game.py`, tambahkan dua method ini di dalam kelas `ChessGame` (mis. tepat setelah method `legal_moves_uci`):

```python
    def legal_moves_san(self) -> list[str]:
        return [self.board.san(m) for m in self.board.legal_moves]

    def movetext(self) -> str:
        """Movetext SAN game berjalan, mis. '1. e4 e5 2. Nf3'. '' bila belum ada langkah.

        Pakai papan replay terpisah agar tidak mengubah state board ini.
        """
        replay = chess.Board()
        parts: list[str] = []
        for i, move in enumerate(self.board.move_stack):
            san = replay.san(move)
            if i % 2 == 0:
                parts.append(f"{i // 2 + 1}. {san}")
            else:
                parts.append(san)
            replay.push(move)
        return " ".join(parts)
```

- [ ] **Step 4: Jalankan test untuk memastikan LULUS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_chess_game.py -q -k "legal_moves_san or movetext"`
Expected: PASS (4 passed).

- [ ] **Step 5: Checkpoint (regresi)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_chess_game.py -q`
Expected: semua test chess_game hijau. (Tidak ada git commit — project bukan repo.)

---

### Task 2: Perkaya prompt di llm_player (CCT + SAN + riwayat + framing)

**Files:**
- Modify: `app/llm_player.py` (`REASONING_MAX_CHARS`, `system_prompt`, `user_prompt`, `self_eval_prompt`, `request_move`)
- Test: `tests/test_llm_player.py`

**Interfaces:**
- Consumes: `ChessGame.legal_moves_san()`, `ChessGame.movetext()` (Task 1), `_board_ascii(game)` (existing).
- Produces:
  - `system_prompt(color: bool) -> str` (CCT + framing menang)
  - `user_prompt(game, last_opponent_move, history: str = "", match_context: str = "") -> str`
  - `self_eval_prompt(game, move) -> str` (diperkuat)
  - `request_move(..., history: str = "", match_context: str = "")` (param baru, default kosong → kompatibel mundur)

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan di akhir `tests/test_llm_player.py`:

```python
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
    assert "Position (FEN):" in p  # FEN tetap ada (dipakai test match_engine)


def test_user_prompt_omits_history_when_empty():
    g = ChessGame()
    p = llm_player.user_prompt(g, None)
    assert "Game so far" not in p


def test_system_prompt_has_cct_and_win_framing():
    s = llm_player.system_prompt(chess.WHITE).lower()
    assert "checks" in s and "captures" in s and "threats" in s
    assert "win" in s


def test_self_eval_prompt_asks_for_alternative():
    g = ChessGame()
    sp = llm_player.self_eval_prompt(g, chess.Move.from_uci("e2e4")).lower()
    assert "alternative" in sp
    assert "decision" in sp
```

- [ ] **Step 2: Jalankan test untuk memastikan GAGAL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_player.py -q -k "history_and_match or omits_history or cct_and_win or asks_for_alternative"`
Expected: FAIL (mis. `AssertionError` karena prompt belum memuat string baru / `user_prompt()` belum menerima kwargs `history`/`match_context`).

- [ ] **Step 3: Implementasi**

3a. Di `app/llm_player.py`, naikkan konstanta:

```python
REASONING_MAX_CHARS = 1500
```

3b. Ganti seluruh `system_prompt` dengan:

```python
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
```

3c. Ganti seluruh `user_prompt` dengan (signature baru):

```python
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
```

3d. Ganti seluruh `self_eval_prompt` dengan:

```python
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
```

3e. Update awal `request_move` agar menerima & meneruskan param baru. Ubah signature dan baris pembentukan `messages`:

```python
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
```

dan di body-nya, ganti pembentukan `messages` menjadi:

```python
    messages = [
        {"role": "system", "content": system_prompt(color)},
        {"role": "user", "content": user_prompt(game, last_opponent_move, history, match_context)},
    ]
```

(Sisa body `request_move` TIDAK berubah.)

- [ ] **Step 4: Jalankan test baru untuk memastikan LULUS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_player.py -q`
Expected: PASS (semua test llm_player hijau, termasuk 4 baru dan parsing/self-eval lama).

- [ ] **Step 5: Checkpoint (regresi penuh sejauh ini)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_llm_player.py tests/test_chess_game.py -q`
Expected: semua hijau.

---

### Task 3: Teruskan riwayat + konteks pertandingan dari match_engine

**Files:**
- Modify: `app/match_engine.py` (method `_play_game`)
- Test: `tests/test_match_engine.py`

**Interfaces:**
- Consumes: `ChessGame.movetext()` (Task 1), `request_move(..., history=, match_context=)` (Task 2), `WINS_NEEDED`, `self.score_a/score_b`, `white_is_a`.
- Produces: prompt yang dikirim ke `chat_fn` kini memuat baris `Match (best of N): ...` dan (setelah langkah pertama) `Game so far (SAN): ...`. Payload board TIDAK berubah.

- [ ] **Step 1: Tulis test yang gagal**

Tambahkan di akhir `tests/test_match_engine.py`:

```python
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
    assert any("Match (best of 2):" in p for p in prompts)
    # Setelah langkah pertama, prompt berikutnya memuat riwayat.
    assert any("Game so far (SAN):" in p for p in prompts)
```

- [ ] **Step 2: Jalankan test untuk memastikan GAGAL**

Run: `.venv/Scripts/python.exe -m pytest tests/test_match_engine.py -q -k "injects_match_context"`
Expected: FAIL (`assert any("Match (best of 2):" ...)` gagal — engine belum menyuntik konteks).

- [ ] **Step 3: Implementasi**

Di `app/match_engine.py`, method `_play_game`:

3a. Ubah deklarasi `last_move` agar ada juga varian SAN. Cari:

```python
        game = ChessGame()
        last_move: Optional[str] = None
```

ganti menjadi:

```python
        game = ChessGame()
        last_move: Optional[str] = None       # UCI untuk payload board.js
        last_move_san: Optional[str] = None   # SAN untuk prompt (lebih natural)
```

3b. Tepat SEBELUM pemanggilan `outcome = llm_player.request_move(...)`, bangun konteks dan ubah pemanggilannya. Cari blok:

```python
            t0 = time.monotonic()
            outcome = llm_player.request_move(
                game, mover_model, turn, last_move,
                api_key=self.api_key, chat_fn=self._chat_fn,
            )
```

ganti menjadi:

```python
            mover_is_a = (turn == chess.WHITE) == white_is_a
            your = self.score_a if mover_is_a else self.score_b
            opp = self.score_b if mover_is_a else self.score_a
            match_context = f"Match (best of {WINS_NEEDED}): you {your} - {opp} opponent."

            t0 = time.monotonic()
            outcome = llm_player.request_move(
                game, mover_model, turn, last_move_san,
                api_key=self.api_key, chat_fn=self._chat_fn,
                history=game.movetext(), match_context=match_context,
            )
```

3c. Setelah `game.push`, simpan SAN langkah terakhir. Cari:

```python
            san = game.push(outcome.move)
            last_move = outcome.move.uci()
```

ganti menjadi:

```python
            san = game.push(outcome.move)
            last_move = outcome.move.uci()
            last_move_san = san
```

- [ ] **Step 4: Jalankan test untuk memastikan LULUS**

Run: `.venv/Scripts/python.exe -m pytest tests/test_match_engine.py -q`
Expected: PASS (semua test match_engine hijau, termasuk yang baru dan regresi payload).

- [ ] **Step 5: Checkpoint (suite penuh)**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: semua test hijau (60 lama + ~9 baru).

---

### Task 4: Verifikasi end-to-end + rebuild .exe

**Files:**
- Verifikasi saja (tidak ubah kode). Output: dist exe ter-rebuild.

**Interfaces:**
- Consumes: hasil Task 1-3.

- [ ] **Step 1: Suite penuh hijau**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: PASS, 0 failed.

- [ ] **Step 2: Self-test desktop (render + panel + format tak berubah)**

Run (Git Bash):
```bash
cd /c/Users/fuadn/Downloads/Project/llm-chess-arena && \
ARENA_SELFTEST=1 ARENA_SELFTEST_OUT=/tmp/arena_selftest.txt \
timeout 30 .venv/Scripts/python.exe main.py >/dev/null 2>&1; cat /tmp/arena_selftest.txt
```
Expected: `OK {"sq":64,"qwc":"function","pieces":0,"co":"visible","think":true,"snd":true}`

- [ ] **Step 3: (Opsional) verifikasi prompt nyata berisi konteks baru**

Run:
```bash
cd /c/Users/fuadn/Downloads/Project/llm-chess-arena && .venv/Scripts/python.exe -c "
import chess
from app.chess_game import ChessGame
from app import llm_player
g=ChessGame(); g.push(chess.Move.from_uci('e2e4'))
p=llm_player.user_prompt(g,'e4',history=g.movetext(),match_context='Match (best of 2): you 1 - 0 opponent.')
print('HISTORY' if 'Game so far (SAN): 1. e4' in p else 'NO-HISTORY')
print('SAN' if 'legal moves (SAN)' in p else 'NO-SAN')
print('CCT' if all(k in llm_player.system_prompt(chess.WHITE).lower() for k in ('checks','captures','threats')) else 'NO-CCT')
"
```
Expected: `HISTORY`, `SAN`, `CCT`.

- [ ] **Step 4: Rebuild .exe (PyInstaller mem-bundle kode Python baru)**

Run (PowerShell):
```powershell
Set-Location "C:\Users\fuadn\Downloads\Project\llm-chess-arena"; .\build.ps1; .\build.ps1 -OneFile
```
Expected: keduanya `Build complete!` exit 0. Hasil: `dist\LLMChessArena\LLMChessArena.exe` + `dist\LLMChessArena-portable.exe`.

> Catatan: pastikan TIDAK ada `_harness.html` atau file scaffolding di `app/ui/web/` sebelum build (kalau ada, hapus — akan ikut ter-bundle).

- [ ] **Step 5: Self-test exe**

Run (Git Bash):
```bash
cd /c/Users/fuadn/Downloads/Project/llm-chess-arena && \
ARENA_SELFTEST=1 ARENA_SELFTEST_OUT="C:\\Users\\fuadn\\Downloads\\Project\\llm-chess-arena\\_exe_selftest.txt" \
timeout 60 ./dist/LLMChessArena-portable.exe >/dev/null 2>&1; sleep 1; cat _exe_selftest.txt; rm -f _exe_selftest.txt
```
Expected: `OK {"sq":64,...,"think":true,"snd":true}`

---

## Self-Review (penulis plan)

**1. Spec coverage:**
- A.1 daftar SAN → Task 2 (`legal_moves_san` di Task 1, dipakai `user_prompt`). ✓
- A.2 riwayat movetext → Task 1 (`movetext`) + Task 3 (kirim). ✓
- A.3 langkah lawan SAN → Task 3 (`last_move_san`). ✓
- A.4 konteks pertandingan → Task 3 (`match_context`). ✓
- B metode CCT + self-eval kuat → Task 2 (`system_prompt`, `user_prompt`, `self_eval_prompt`). ✓
- C framing menang → Task 2 (`system_prompt`). ✓
- D `REASONING_MAX_CHARS=1500` → Task 2 step 3a. ✓
- Testing plan → test di Task 1-3 + verifikasi Task 4. ✓
- Output JSON & parsing tak berubah → tak ada task menyentuhnya. ✓

**2. Placeholder scan:** Tidak ada TBD/TODO; semua step berisi kode/perintah nyata. ✓

**3. Type consistency:** `legal_moves_san()/movetext()` dipakai konsisten; `user_prompt(game,last,history,match_context)` dan `request_move(...,history,match_context)` cocok antar Task 2 & 3; `last_move_san` (SAN) untuk prompt vs `last_move` (UCI) untuk payload dibedakan jelas. ✓

**Catatan kompatibilitas:** Param baru `history`/`match_context` default `""` → semua pemanggilan & test lama tetap jalan. `user_prompt` tetap memuat baris `Position (FEN):` sehingga helper `_fen_from_messages` di test match_engine tetap berfungsi.
