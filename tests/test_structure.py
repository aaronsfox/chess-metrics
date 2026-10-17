import chess

from chessmetrics.structure import snapshot
from chessmetrics.structure.control import outposts, space, square_control
from chessmetrics.structure.king_safety import king_safety
from chessmetrics.structure.see import effective_attackers, occupation_loss, see
from chessmetrics.structure.tension import describe, en_prise, loose_pieces, overloaded, piece_loads


def B(fen):
    return chess.Board(fen)


# --- SEE --------------------------------------------------------------------

def test_see_undefended_and_defended():
    # white rook on d1 can take an undefended knight on d5
    assert see(B("4k3/8/8/3n4/8/8/8/3RK3 w - - 0 1"), chess.D5, chess.WHITE) == 3
    # knight defended by a pawn: rook capture does not pay
    assert see(B("4k3/8/4p3/3n4/8/8/8/3RK3 w - - 0 1"), chess.D5, chess.WHITE) == 0
    # but a pawn capture of the defended knight wins 3 - 1 = 2
    assert see(B("4k3/8/4p3/3n4/2P5/8/8/4K3 w - - 0 1"), chess.D5, chess.WHITE) == 2


def test_see_xray_through_own_rook():
    # doubled white rooks on d1/d2 vs a knight on d5 defended once by a rook on d8
    board = B("3rk3/8/8/3n4/8/8/3R4/3RK3 w - - 0 1")
    assert see(board, chess.D5, chess.WHITE) == 3  # RxN, RxR, RxR: +3 -5 +5


def test_pinned_attacker_excluded():
    # the black knight on e5 is pinned to its king by Re1; it cannot defend d3? it attacks d3
    board = B("4k3/8/8/4n3/8/3B4/8/4RK2 b - - 0 1")
    assert chess.E5 not in effective_attackers(board, chess.BLACK, chess.D3)
    assert chess.E5 in board.attackers(chess.BLACK, chess.D3)


def test_king_cannot_capture_defended_piece():
    # white king e1 next to a black rook e2 defended by a rook on e8
    board = B("4r1k1/8/8/8/8/8/4r3/4K3 w - - 0 1")
    assert see(board, chess.E2, chess.WHITE) == 0
    board = B("6k1/8/8/8/8/8/4r3/4K3 w - - 0 1")
    assert see(board, chess.E2, chess.WHITE) == 5


# --- control & space ----------------------------------------------------------

def test_control_by_static_exchange_not_attacker_count():
    # d5: white attacks it twice (Nc3, Rd1), black once (c6). Neither side can
    # occupy it safely, so it is contested despite White's extra attacker.
    board = B("4k3/8/2p5/8/8/2N5/8/3RK3 w - - 0 1")
    c = square_control(board, chess.D5)
    assert c.white_attackers == 2 and c.black_attackers == 1
    assert occupation_loss(board, chess.D5, chess.WHITE) > 0
    assert c.controller is None
    # with a second pawn on e6, Black can occupy d5 safely and controls it
    board = B("4k3/8/2p1p3/8/8/2N5/8/3RK3 w - - 0 1")
    assert square_control(board, chess.D5).controller == chess.BLACK


def test_space_counts_safe_squares_in_enemy_half():
    start = chess.Board()
    assert space(start, chess.WHITE) == 0 and space(start, chess.BLACK) == 0
    advanced = B("4k3/8/8/3PP3/8/8/8/4K3 w - - 0 1")  # pawns d5, e5 cover c6 d6 e6 f6
    assert space(advanced, chess.WHITE) == 4


def test_outposts():
    # white pawn e4 supports d5; black pawn on c6 could still kick it, so not an outpost
    assert chess.D5 not in outposts(B("4k3/8/2p5/8/4P3/8/8/4K3 w - - 0 1"), chess.WHITE)
    assert chess.D5 in outposts(B("4k3/8/8/2p5/4P3/8/8/4K3 w - - 0 1"), chess.WHITE)


# --- tension ------------------------------------------------------------------

BACK_RANK_OVERLOAD = "4r1k1/5ppp/8/4n3/8/8/5PPP/R3R1K1 w - - 0 1"


def test_overloaded_back_rank_rook():
    board = B(BACK_RANK_OVERLOAD)
    over = overloaded(board, chess.BLACK)
    assert [l.square for l in over] == [chess.E8]
    kinds = sorted(d.kind for d in over[0].duties)
    assert kinds == ["defends", "mate"]
    text = describe(over[0])
    assert text.startswith("Black's rook on e8 is guarding") and "e5" in text and "mate on a8" in text


def test_no_overload_in_start_position():
    assert overloaded(chess.Board(), chess.WHITE) == []
    assert overloaded(chess.Board(), chess.BLACK) == []


def test_blockade_duty():
    # black knight on d6 blockades the white passed pawn on d5
    board = B("4k3/8/3n4/3P4/8/8/8/4K3 b - - 0 1")
    loads = piece_loads(board, chess.BLACK)
    assert any(d.kind == "blockade" and d.target == chess.D5 for l in loads for d in l.duties)


def test_en_prise_and_loose():
    board = B("6k1/8/8/8/8/8/5PPP/3rR1K1 w - - 0 1")
    assert [(e.square, e.gain) for e in en_prise(board, chess.WHITE)] == [(chess.E1, 5)]
    assert loose_pieces(board, chess.BLACK) == [chess.D1]


# --- king safety ----------------------------------------------------------------

def test_king_safety_orders_exposed_above_castled():
    castled = B("r4rk1/ppp2ppp/8/8/8/8/PPP2PPP/R4RK1 w - - 0 1")
    exposed = B("r4rk1/ppp5/8/7q/8/8/PPP2PPP/R4RK1 w - - 0 1")  # pawnless king, queen nearby
    safe, danger = king_safety(castled, chess.BLACK), king_safety(exposed, chess.BLACK)
    assert danger.danger > safe.danger
    assert danger.files > 0 and danger.shield > 0


def test_snapshot_is_json_ready():
    import json
    json.dumps(snapshot(B(BACK_RANK_OVERLOAD)))
