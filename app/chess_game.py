"""Pembungkus python-chess untuk satu game catur.

Menyediakan state papan, daftar langkah legal, deteksi outcome (skakmat/remis),
serta ekspor FEN/PGN/SAN. Tidak tahu apa-apa soal LLM atau Qt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import chess
import chess.pgn

# Backstop defensif: bila satu game melebihi ini, di-adjudikasi remis. Aturan
# remis catur (75-move, fivefold) umumnya menamatkan game jauh sebelum ini.
MAX_PLIES_HARD_CAP = 400


@dataclass(frozen=True)
class GameResult:
    """Hasil satu game. winner_color: chess.WHITE / chess.BLACK / None (remis)."""
    is_over: bool
    winner_color: Optional[bool]
    reason: str  # "checkmate", "stalemate", "insufficient_material", dst.


class ChessGame:
    """State satu game catur tunggal."""

    def __init__(self, max_plies: int = MAX_PLIES_HARD_CAP) -> None:
        self.board = chess.Board()
        self.max_plies = max_plies
        self._forfeit_color: Optional[bool] = None  # sisi yang menyerah/forfeit

    @property
    def turn(self) -> bool:
        """Sisi yang harus jalan (chess.WHITE atau chess.BLACK)."""
        return self.board.turn

    @property
    def fen(self) -> str:
        return self.board.fen()

    @property
    def ply_count(self) -> int:
        return len(self.board.move_stack)

    @property
    def fullmove_number(self) -> int:
        return self.board.fullmove_number

    def legal_moves_uci(self) -> list[str]:
        return [m.uci() for m in self.board.legal_moves]

    def legal_moves_san(self) -> list[str]:
        return [self.board.san(m) for m in self.board.legal_moves]

    def last_move_uci(self) -> Optional[str]:
        if self.board.move_stack:
            return self.board.move_stack[-1].uci()
        return None

    def parse_move(self, text: str) -> Optional[chess.Move]:
        """Coba interpretasi `text` sebagai langkah legal (UCI dulu, lalu SAN).

        Kembalikan chess.Move bila legal, atau None bila tidak valid/ilegal.
        """
        candidate = text.strip()
        if not candidate:
            return None

        # 1) UCI (mis. e2e4, e7e8q)
        try:
            move = chess.Move.from_uci(candidate.lower())
            if move in self.board.legal_moves:
                return move
        except ValueError:
            pass

        # 2) SAN (mis. Nf3, O-O, exd5, e8=Q+)
        try:
            move = self.board.parse_san(candidate)
            if move in self.board.legal_moves:
                return move
        except (ValueError, chess.IllegalMoveError, chess.AmbiguousMoveError):
            pass

        return None

    def push(self, move: chess.Move) -> str:
        """Mainkan `move`. Kembalikan notasi SAN-nya. Raise bila ilegal."""
        if move not in self.board.legal_moves:
            raise ValueError(f"Langkah ilegal: {move.uci()}")
        san = self.board.san(move)
        self.board.push(move)
        return san

    def forfeit(self, color: bool) -> None:
        """Tandai `color` menyerah (mis. gagal beri langkah legal setelah retry)."""
        self._forfeit_color = color

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

    def result(self) -> GameResult:
        """Evaluasi apakah game selesai dan siapa pemenangnya."""
        if self._forfeit_color is not None:
            winner = not self._forfeit_color
            return GameResult(True, winner, "forfeit")

        outcome = self.board.outcome(claim_draw=True)
        if outcome is not None:
            reason = outcome.termination.name.lower()
            return GameResult(True, outcome.winner, reason)

        if self.ply_count >= self.max_plies:
            return GameResult(True, None, "ply_cap")

        return GameResult(False, None, "")

    def pgn(self, white_label: str, black_label: str) -> str:
        """Ekspor PGN dengan label pemain."""
        game = chess.pgn.Game()
        game.headers["Event"] = "LLM Chess Arena"
        game.headers["White"] = white_label
        game.headers["Black"] = black_label
        node = game
        replay = chess.Board()
        for move in self.board.move_stack:
            node = node.add_variation(move)
            replay.push(move)
        res = self.result()
        if res.is_over:
            if res.winner_color is chess.WHITE:
                game.headers["Result"] = "1-0"
            elif res.winner_color is chess.BLACK:
                game.headers["Result"] = "0-1"
            else:
                game.headers["Result"] = "1/2-1/2"
        return str(game)
