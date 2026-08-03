"""Test wrapper python-chess: parsing langkah & deteksi outcome."""
import chess

from app.chess_game import ChessGame, GameResult


def test_initial_state():
    g = ChessGame()
    assert g.turn == chess.WHITE
    assert g.ply_count == 0
    assert g.fullmove_number == 1
    assert len(g.legal_moves_uci()) == 20


def test_parse_uci_and_san():
    g = ChessGame()
    assert g.parse_move("e2e4").uci() == "e2e4"
    assert g.parse_move("Nf3").uci() == "g1f3"
    assert g.parse_move("  e2e4  ").uci() == "e2e4"


def test_parse_illegal_or_garbage_returns_none():
    g = ChessGame()
    assert g.parse_move("e2e5") is None   # ilegal dari posisi awal
    assert g.parse_move("hello") is None
    assert g.parse_move("") is None


def test_push_returns_san_and_advances():
    g = ChessGame()
    san = g.push(g.parse_move("e2e4"))
    assert san == "e4"
    assert g.turn == chess.BLACK
    assert g.last_move_uci() == "e2e4"
    assert g.ply_count == 1


def test_checkmate_detection_fools_mate():
    g = ChessGame()
    for uci in ["f2f3", "e7e5", "g2g4", "d8h4"]:
        g.push(g.parse_move(uci))
    res = g.result()
    assert res.is_over is True
    assert res.winner_color == chess.BLACK
    assert res.reason == "checkmate"


def test_stalemate_detection():
    g = ChessGame()
    g.board = chess.Board("7k/5Q2/5K2/8/8/8/8/8 b - - 0 1")
    res = g.result()
    assert res.is_over is True
    assert res.winner_color is None
    assert res.reason == "stalemate"


def test_insufficient_material():
    g = ChessGame()
    g.board = chess.Board("8/8/8/4k3/8/8/4K3/8 w - - 0 1")
    res = g.result()
    assert res.is_over is True
    assert res.winner_color is None
    assert res.reason == "insufficient_material"


def test_forfeit_gives_win_to_opponent():
    g = ChessGame()
    g.forfeit(chess.WHITE)
    res = g.result()
    assert res.is_over is True
    assert res.winner_color == chess.BLACK
    assert res.reason == "forfeit"


def test_ply_cap_adjudicates_draw():
    g = ChessGame(max_plies=2)
    g.push(g.parse_move("e2e4"))
    g.push(g.parse_move("e7e5"))
    res = g.result()
    assert res.is_over is True
    assert res.winner_color is None
    assert res.reason == "ply_cap"


def test_san_includes_check_and_mate_annotation():
    # board.san() sudah menyertakan + / # -> UI tidak perlu menambah lagi.
    g = ChessGame()
    for uci in ["e2e4", "f7f6", "d2d4", "g7g5"]:
        g.push(g.parse_move(uci))
    san = g.push(g.parse_move("d1h5"))  # Qh5#
    assert san.endswith("#")


def test_pgn_export_contains_headers():
    g = ChessGame()
    g.push(g.parse_move("e2e4"))
    pgn = g.pgn("Alpha", "Beta")
    assert "Alpha" in pgn and "Beta" in pgn
    assert "1. e4" in pgn


def test_legal_moves_san_start_position():
    g = ChessGame()
    san = g.legal_moves_san()
    assert "Nf3" in san
    assert "e4" in san
    assert len(san) == 20  # 16 pawn moves + 4 knight moves


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
