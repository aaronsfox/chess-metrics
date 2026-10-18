from pathlib import Path

import chess
import pytest

from chessmetrics import epa
from chessmetrics.difficulty import difficulty, good_enough
from chessmetrics.engine import MoveEval, PositionAnalysis, SearchLimit
from chessmetrics.human import EngineSoftmaxModel, PlayerContext
from chessmetrics.pgn import TimeControl, pgn_to_records
from chessmetrics.points import WDL, PointsFunction

FIXTURES = Path(__file__).parent / "fixtures"


# --- points -----------------------------------------------------------------

def test_wdl_pov_and_permille():
    w = WDL.from_permille(600, 300, 100, pov=chess.BLACK)
    assert w == WDL(0.1, 0.3, 0.6)
    assert w.pov(chess.BLACK) == (0.6, 0.3, 0.1)


def test_points_functions():
    drawn = WDL(0, 1, 0)
    assert PointsFunction.standard().expected(drawn, chess.WHITE) == 0.5
    arm = PointsFunction.armageddon()
    assert arm.expected(drawn, chess.WHITE) == 0.0
    assert arm.expected(drawn, chess.BLACK) == 1.0
    assert PointsFunction.must_win(chess.BLACK).expected(drawn, chess.BLACK) == 0.0


# --- pgn --------------------------------------------------------------------

@pytest.mark.parametrize("header, base, inc, cat, bonuses", [
    ("180+2", 180, 2, "blitz", ()),
    ("600", 600, 0, "rapid", ()),
    ("40/5400+30:1800+30", 5400, 30, "classical", ((40, 1800),)),
    ("40/7200:20/3600:900", 7200, 0, "classical", ((40, 3600), (60, 900))),
    ("-", None, 0, "unknown", ()),
])
def test_time_control(header, base, inc, cat, bonuses):
    tc = TimeControl.parse(header)
    assert (tc.base, tc.increment, tc.category, tc.bonuses) == (base, inc, cat, bonuses)


def test_records_clocks():
    recs = list(pgn_to_records(FIXTURES / "scholars.pgn"))
    game = [r for r in recs if r.game_id == "scholars#0"]
    assert [r.san for r in game] == ["e4", "e5", "Bc4", "Nc6", "Qh5", "Nf6", "Qxf7#"]
    bc4 = game[2]
    assert (bc4.clock_before, bc4.clock_after, bc4.time_spent) == (180, 178, 4)
    nf6 = game[5]
    assert nf6.opp_clock == 175 and nf6.time_spent == 9
    untimed = [r for r in recs if r.game_id == "scholars#1"]
    assert untimed[0].clock_before == 5400 and untimed[0].time_spent is None


# --- difficulty -------------------------------------------------------------

def _analysis(fen, evals):
    """evals: list of (uci, white-pov WDL tuple), any order."""
    moves = tuple(MoveEval(chess.Move.from_uci(u), None, None, WDL(*w)) for u, w in evals)
    board = chess.Board(fen)
    moves = tuple(sorted(moves, key=lambda m: -m.expected(board.turn)))
    return PositionAnalysis(fen, "test", SearchLimit(), moves)


START = chess.STARTING_FEN


def test_find_rate_and_pressure():
    a = _analysis(START, [
        ("e2e4", (0.30, 0.60, 0.10)),  # E = 0.60
        ("d2d4", (0.29, 0.61, 0.10)),  # E = 0.595, within tau
        ("g2g4", (0.10, 0.40, 0.50)),  # E = 0.30, blunder
    ])
    assert good_enough(a) == {chess.Move.from_uci("e2e4"), chess.Move.from_uci("d2d4")}
    probs = {chess.Move.from_uci("e2e4"): 0.5, chess.Move.from_uci("d2d4"): 0.3, chess.Move.from_uci("g2g4"): 0.2}
    d = difficulty(a, probs)
    assert d.find_rate == pytest.approx(0.8)
    assert d.p_blunder == pytest.approx(0.2)
    assert d.expected_loss == pytest.approx(0.3 * 0.005 + 0.2 * 0.30)
    assert d.good_moves == 2 and d.coverage == pytest.approx(1.0)


def test_difficulty_renormalises_partial_coverage():
    a = _analysis(START, [("e2e4", (0.3, 0.6, 0.1)), ("g2g4", (0.1, 0.4, 0.5))])
    probs = {chess.Move.from_uci("e2e4"): 0.3, chess.Move.from_uci("g2g4"): 0.1, chess.Move.from_uci("a2a3"): 0.6}
    d = difficulty(a, probs)
    assert d.coverage == pytest.approx(0.4)
    assert d.find_rate == pytest.approx(0.75)


def test_softmax_model_sharpens_with_rating_and_flattens_on_low_clock():
    a = _analysis(START, [("e2e4", (0.3, 0.6, 0.1)), ("g2g4", (0.2, 0.6, 0.2))])
    m, b, e4 = EngineSoftmaxModel(), chess.Board(), chess.Move.from_uci("e2e4")
    weak = m.move_probs(b, PlayerContext(1500, 1500), a)[e4]
    strong = m.move_probs(b, PlayerContext(2700, 2700), a)[e4]
    rushed = m.move_probs(b, PlayerContext(2700, 2700, clock=3), a)[e4]
    assert weak < rushed < strong


# --- epa --------------------------------------------------------------------

def test_epa_telescopes_and_ledger():
    W, B = chess.WHITE, chess.BLACK
    wdls = [WDL(0.3, 0.5, 0.2), WDL(0.35, 0.5, 0.15), WDL(0.6, 0.3, 0.1), WDL(0.55, 0.35, 0.1), WDL(1, 0, 0)]
    plies = epa.plies_from_wdls([W, B, W, B], ["a", "b", "c", "d"], wdls)
    res = epa.compute(plies)
    assert sum(r.epa for r in res if r.mover == W) + sum(-r.epa for r in res if r.mover == B) == pytest.approx(
        1.0 - 0.55)
    for color in (W, B):
        led = epa.ledger(plies, color)
        start = PointsFunction().expected(wdls[0], color)
        end = PointsFunction().expected(wdls[-1], color)
        assert led.net == pytest.approx(end - start)
    assert [s.san for s in epa.swing_moments(res, k=2)] == ["b", "d"]


def test_missed_points_after_gift():
    W, B = chess.WHITE, chess.BLACK
    # Black's move hands White 0.3; White's reply only keeps 0.1 of it although best kept all
    wdls = [WDL(0.3, 0.4, 0.3), WDL(0.6, 0.4, 0.0), WDL(0.4, 0.4, 0.2)]
    plies = epa.plies_from_wdls([B, W], ["gift", "meh"], wdls, best_after=[None, WDL(0.6, 0.4, 0.0)])
    res = epa.compute(plies)
    assert res[1].missed == pytest.approx(0.2)
    assert epa.ledger(plies, W).missed == pytest.approx(0.2)


def test_armageddon_epa_differs_from_standard():
    W = chess.WHITE
    plies = epa.plies_from_wdls([W], ["x"], [WDL(0.4, 0.2, 0.4), WDL(0.4, 0.4, 0.2)])
    assert epa.compute(plies)[0].epa == pytest.approx(0.1)
    # converting Black wins into draws gains White nothing in Armageddon
    assert epa.compute(plies, PointsFunction.armageddon())[0].epa == pytest.approx(0.0)


def test_broadcast_fixture_clocks():
    """Real elite OTB games (Olympiad 2026, both players 2700+, 90/40+30+30s)."""
    recs = list(pgn_to_records(FIXTURES / "broadcast" / "olympiad2026_open_2700.pgn"))
    assert len({r.game_id for r in recs}) == 25
    # relay noise is confined to the first move; the move-40 bonus is absorbed
    assert {r.ply // 2 + 1 for r in recs if r.clock_anomaly} <= {1}
    assert all(r.time_spent is not None for r in recs if not r.clock_anomaly)
    assert all(r.time_category == "classical" for r in recs)
