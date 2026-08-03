"""Worker QThread yang menjalankan satu sesi pertandingan dua LLM.

Sesi = best-to-2-wins (mutlak; remis tidak dihitung & game diulang dengan warna
diacak ulang). Semua komunikasi ke UI lewat sinyal Qt (thread-safe). Tidak ada
jeda artifisial / gerbang konfirmasi (pengaman biaya = minim, pilihan user).
"""
from __future__ import annotations

import random
import time
from typing import Any, Callable, Optional

import chess
from PyQt6.QtCore import QThread, pyqtSignal

from . import config, llm_player, openrouter, session_store
from .chess_game import ChessGame

WINS_NEEDED = 2
MAX_GAMES_SESSION = 25  # backstop defensif agar sesi pasti berakhir


def _color_name(color: bool) -> str:
    return "White" if color == chess.WHITE else "Black"


class MatchEngine(QThread):
    """Menjalankan sesi pertandingan di thread terpisah."""

    sig_board = pyqtSignal(object)      # update papan per langkah
    sig_state = pyqtSignal(object)      # skor / giliran / status
    sig_log = pyqtSignal(object)        # event log (info/warn/error/illegal)
    sig_game_over = pyqtSignal(object)  # ringkasan akhir satu game
    sig_match_over = pyqtSignal(object)  # ringkasan akhir sesi

    def __init__(
        self,
        model_a: str,
        model_b: str,
        api_key: Optional[str] = None,
        chat_fn: Callable[..., dict] = openrouter.chat,
        rng: Optional[random.Random] = None,
        resume: Optional[dict] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.model_a = model_a
        self.model_b = model_b
        self.api_key = api_key or config.get_api_key()
        self._chat_fn = chat_fn
        self._rng = rng or random.Random()
        self.score_a = 0
        self.score_b = 0
        self.draws = 0
        self.game_index = 0
        self.api_calls = 0  # total panggilan LLM (propose + self-eval + retry)
        self._paused = False  # jeda antar-langkah (di-set dari thread UI)
        self._resume = resume  # snapshot untuk melanjutkan game pertama (atau None)
        self._move_log: list[dict] = []  # payload board per-langkah game berjalan (utk restore UI)
        # Persist ke disk hanya untuk run nyata (chat default); tes meng-inject chat_fn.
        self._persist = chat_fn is openrouter.chat
        if resume:
            self.score_a = int(resume.get("score_a", 0))
            self.score_b = int(resume.get("score_b", 0))
            self.draws = int(resume.get("draws", 0))
            self.game_index = int(resume.get("game_index", 0))
            self.api_calls = int(resume.get("api_calls", 0))

    # ------------------------------------------------------------------ jeda
    def pause(self) -> None:
        """Jeda pertandingan (berlaku di sela langkah berikutnya)."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def _wait_if_paused(self) -> None:
        """Bila dijeda, tunggu sampai resume / stop. Tidak memblok langkah berjalan."""
        if not self._paused:
            return
        self._emit_log("info", "Pertandingan dijeda.")
        self.sig_state.emit(self._base_state(status="Dijeda", phase="paused"))
        while self._paused and not self.isInterruptionRequested():
            self.msleep(150)
        if not self.isInterruptionRequested():
            self._emit_log("info", "Pertandingan dilanjutkan.")

    # ------------------------------------------------------------------ helpers
    def _emit_log(self, level: str, message: str, **extra: Any) -> None:
        payload = {"level": level, "message": message}
        payload.update(extra)
        self.sig_log.emit(payload)

    def _base_state(self, **extra: Any) -> dict[str, Any]:
        state = {
            "model_a": self.model_a,
            "model_b": self.model_b,
            "score_a": self.score_a,
            "score_b": self.score_b,
            "draws": self.draws,
            "wins_needed": WINS_NEEDED,
            "game_index": self.game_index,
            "api_calls": self.api_calls,
        }
        state.update(extra)
        return state

    def _save_snapshot(self, game: ChessGame, white_is_a: bool) -> None:
        """Simpan state game berjalan agar bisa di-resume (skor + langkah + log panel)."""
        if not self._persist:
            return
        session_store.save_session({
            "version": session_store.SESSION_VERSION,
            "model_a": self.model_a,
            "model_b": self.model_b,
            "score_a": self.score_a,
            "score_b": self.score_b,
            "draws": self.draws,
            "game_index": self.game_index,
            "api_calls": self.api_calls,
            "white_is_a": white_is_a,
            "moves": [m.uci() for m in game.board.move_stack],
            "log": self._move_log,
        })

    # --------------------------------------------------------------------- run
    def run(self) -> None:
        if not self.api_key:
            self._emit_log("error", "API key OpenRouter belum diset.")
            self.sig_match_over.emit(self._base_state(aborted=True, reason="no_api_key"))
            return
        try:
            self._run_session()
        except openrouter.OpenRouterError as exc:
            self._emit_log("error", f"Pertandingan dihentikan karena error API: {exc}")
            self.sig_match_over.emit(self._base_state(aborted=True, reason="api_error"))
        except Exception as exc:  # pragma: no cover - jaring pengaman terakhir
            self._emit_log("error", f"Error tak terduga: {exc}")
            self.sig_match_over.emit(self._base_state(aborted=True, reason="unexpected"))

    def _run_session(self) -> None:
        self._emit_log("info", f"Sesi dimulai: {self.model_a} vs {self.model_b} (first to {WINS_NEEDED}).")
        self.sig_state.emit(self._base_state(status="Sesi dimulai", phase="start"))

        pending_resume = self._resume  # hanya untuk game pertama
        while self.score_a < WINS_NEEDED and self.score_b < WINS_NEEDED:
            if self.isInterruptionRequested():
                self._emit_log("info", "Pertandingan dihentikan oleh user.")
                self.sig_match_over.emit(self._base_state(aborted=True, reason="stopped"))
                return
            if self.game_index >= MAX_GAMES_SESSION:
                self._emit_log("warn", f"Batas {MAX_GAMES_SESSION} game/sesi tercapai; sesi diakhiri.")
                break

            if pending_resume is not None:
                # Lanjutkan game tersimpan: game_index sudah diset di __init__, JANGAN naikkan.
                white_is_a = bool(pending_resume.get("white_is_a", True))
                resume_arg = pending_resume
                pending_resume = None
            else:
                self.game_index += 1
                white_is_a = self._rng.random() < 0.5
                resume_arg = None
            white_model = self.model_a if white_is_a else self.model_b
            black_model = self.model_b if white_is_a else self.model_a

            self._emit_log(
                "info",
                f"Game {self.game_index}: Putih = {white_model}, Hitam = {black_model}."
                + (" (lanjutan)" if resume_arg else ""),
            )
            interrupted, result = self._play_game(white_model, black_model, white_is_a, resume=resume_arg)
            if interrupted:
                self.sig_match_over.emit(self._base_state(aborted=True, reason="stopped"))
                return

            self._tally(result, white_model, black_model, white_is_a)

        self._emit_match_winner()

    def _play_game(self, white_model: str, black_model: str, white_is_a: bool, resume=None):
        """Mainkan satu game sampai selesai. Return (interrupted, GameResult|None).

        `white_is_a` diteruskan ke payload agar UI bisa membedakan sisi warna saat
        kedua model identik (nama sama -> tak bisa dibedakan dari white_model saja).
        `resume` (opsional) berisi langkah yang sudah dimainkan untuk dilanjutkan.
        """
        game = ChessGame()
        last_move: Optional[str] = None       # UCI untuk payload board.js
        last_move_san: Optional[str] = None   # SAN untuk prompt (lebih natural)
        self._move_log = []

        if resume is not None:
            # Replay langkah tersimpan ke papan (lanjut tanpa mengulang dari awal).
            for uci in resume.get("moves", []):
                try:
                    mv = chess.Move.from_uci(uci)
                except ValueError:
                    break
                if mv not in game.board.legal_moves:
                    break  # save rusak -> hentikan replay, mulai dari posisi yang valid
                last_move_san = game.push(mv)
                last_move = mv.uci()
            self._move_log = list(resume.get("log", []))
            self.sig_board.emit({
                "fen": game.fen,
                "last_move": last_move,
                "san": last_move_san,
                "reset": True,
                "resumed": True,
                "game_index": self.game_index,
                "model_a": self.model_a,
                "model_b": self.model_b,
                "white_model": white_model,
                "black_model": black_model,
                "white_is_a": white_is_a,
                "score_a": self.score_a,
                "score_b": self.score_b,
                "draws": self.draws,
                "api_calls": self.api_calls,
                "restore_log": self._move_log,
            })
        else:
            self.sig_board.emit({
                "fen": game.fen,
                "last_move": None,
                "san": None,
                "reset": True,
                "game_index": self.game_index,
                "white_model": white_model,
                "black_model": black_model,
                "white_is_a": white_is_a,
            })
        self._save_snapshot(game, white_is_a)

        while True:
            if self.isInterruptionRequested():
                return True, None

            self._wait_if_paused()
            if self.isInterruptionRequested():
                return True, None

            result = game.result()
            if result.is_over:
                return False, result

            turn = game.turn
            mover_model = white_model if turn == chess.WHITE else black_model
            self.sig_state.emit(self._base_state(
                status=f"{mover_model} sedang berpikir ({_color_name(turn)})",
                turn_color=_color_name(turn),
                thinking_model=mover_model,
                white_model=white_model,
                black_model=black_model,
                white_is_a=white_is_a,
                thinking_is_a=(turn == chess.WHITE) == white_is_a,
                phase="thinking",
            ))

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
            thinking_ms = int((time.monotonic() - t0) * 1000)
            self.api_calls += outcome.calls

            for att in outcome.illegal_attempts:
                self._emit_log(
                    "illegal",
                    f"{mover_model} ({_color_name(turn)}) memberi langkah ilegal/tak terbaca "
                    f"(percobaan {att['attempt']}): {att['raw']!r}",
                )

            if outcome.forfeited:
                game.forfeit(turn)
                self._emit_log(
                    "warn",
                    f"{mover_model} ({_color_name(turn)}) gagal beri langkah legal setelah "
                    f"{len(outcome.illegal_attempts)} percobaan; forfeit game ini.",
                )
                continue  # loop atas akan mendeteksi game over (forfeit)

            san = game.push(outcome.move)
            last_move = outcome.move.uci()
            last_move_san = san
            board = game.board
            payload = {
                "fen": game.fen,
                "last_move": last_move,
                "san": san,
                "ply": game.ply_count,
                "fullmove": board.fullmove_number,
                "mover": _color_name(turn),
                "mover_is_a": (turn == chess.WHITE) == white_is_a,
                "model": mover_model,
                "check": board.is_check(),
                "checkmate": board.is_checkmate(),
                "white_model": white_model,
                "black_model": black_model,
                "api_calls": self.api_calls,
                "eval_rounds": outcome.eval_rounds,
                "reasoning": outcome.reasoning,
                "thinking_ms": thinking_ms,
            }
            self._move_log.append(payload)
            self.sig_board.emit(payload)
            self._save_snapshot(game, white_is_a)  # persist agar bisa di-resume

    def _tally(self, result, white_model: str, black_model: str, white_is_a: bool) -> None:
        if result is None:
            return
        if result.winner_color is None:
            self.draws += 1
            self._emit_log("info", f"Game {self.game_index} remis ({result.reason}); tidak dihitung, diulang.")
            winner_model = None
        else:
            winner_model = white_model if result.winner_color == chess.WHITE else black_model
            winner_is_a = (winner_model == self.model_a)
            # Tangani kasus kedua model identik: pakai pemetaan warna.
            if self.model_a == self.model_b:
                winner_is_a = (result.winner_color == chess.WHITE) == white_is_a
            if winner_is_a:
                self.score_a += 1
            else:
                self.score_b += 1
            self._emit_log(
                "info",
                f"Game {self.game_index}: {winner_model} menang ({result.reason}). "
                f"Skor {self.model_a} {self.score_a} - {self.score_b} {self.model_b}.",
            )

        self.sig_game_over.emit(self._base_state(
            winner_model=winner_model,
            reason=result.reason,
            white_model=white_model,
            black_model=black_model,
            phase="game_over",
        ))

    def _emit_match_winner(self) -> None:
        if self.score_a >= WINS_NEEDED:
            winner = self.model_a
        elif self.score_b >= WINS_NEEDED:
            winner = self.model_b
        else:
            winner = None  # tercapai lewat backstop tanpa pemenang mutlak
        self._emit_log(
            "info",
            f"Sesi selesai. Pemenang: {winner or 'tidak ada (batas tercapai)'}. "
            f"Skor akhir {self.model_a} {self.score_a} - {self.score_b} {self.model_b}, remis {self.draws}.",
        )
        if self._persist:
            session_store.clear_session()  # sesi tuntas -> tak perlu di-resume
        self.sig_match_over.emit(self._base_state(winner_model=winner, phase="match_over"))
