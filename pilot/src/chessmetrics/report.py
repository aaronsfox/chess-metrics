"""End-to-end game report: engine pass, difficulty, EPA and accuracy over expected."""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.pgn

from . import epa
from .difficulty import BETA, TAU, difficulty, good_enough
from .engine import EngineAnalyzer
from .human import HumanMoveModel, PlayerContext
from .pgn import TimeControl
from .points import STANDARD, WDL, PointsFunction


@dataclass
class MoveRow:
    ply: int
    mover: str
    san: str
    e_before: float  # mover's expected points before the move
    epa: float
    in_good_set: bool
    find_rate: float
    p_blunder: float
    expected_loss: float
    clock: float | None


def _elo(headers: chess.pgn.Headers, key: str, default: float) -> float:
    try:
        return float(headers.get(key, ""))
    except ValueError:
        return default


def analyse_game(
    game: chess.pgn.Game,
    analyzer: EngineAnalyzer,
    model: HumanMoveModel,
    points: PointsFunction = STANDARD,
    tau: float = TAU,
    beta: float = BETA,
    skip_plies: int = 0,
) -> dict:
    """Analyse every move of `game`.

    `skip_plies` excludes the first N half-moves from accuracy figures, standing
    in for "exclude moves until a player leaves known theory" until an opening
    book is wired in.
    """
    h = game.headers
    tc = TimeControl.parse(h.get("TimeControl"))
    elo = {chess.WHITE: _elo(h, "WhiteElo", 1500), chess.BLACK: _elo(h, "BlackElo", 1500)}
    clocks: dict[chess.Color, float | None] = {chess.WHITE: tc.base, chess.BLACK: tc.base}

    board = game.board()
    rows: list[MoveRow] = []
    wdls: list[WDL] = []
    movers, sans, best_after = [], [], []
    for ply, node in enumerate(game.mainline()):
        mover = board.turn
        analysis = analyzer.analyse(board)
        ctx = PlayerContext(elo[mover], elo[not mover], clocks[mover], clocks[not mover], tc.increment, tc.base)
        d = difficulty(analysis, model.move_probs(board, ctx, analysis), tau, beta, points)
        g = good_enough(analysis, tau, points)

        wdls.append(analysis.best.wdl)
        best_after.append(analysis.best.wdl)
        movers.append(mover)
        sans.append(board.san(node.move))
        rows.append(MoveRow(ply, "white" if mover else "black", sans[-1], points.expected(analysis.best.wdl, mover),
                            0.0, node.move in g, d.find_rate, d.p_blunder, d.expected_loss, clocks[mover]))
        if node.clock() is not None:
            clocks[mover] = node.clock()
        board.push(node.move)
    wdls.append(analyzer.evaluate(board))

    plies = epa.plies_from_wdls(movers, sans, wdls, best_after)
    results = epa.compute(plies, points)
    for row, r in zip(rows, results):
        row.epa = r.epa

    summary = {}
    for color in chess.COLORS:
        name = "white" if color else "black"
        own = [r for r in rows if r.mover == name and r.ply >= skip_plies]
        led = epa.ledger(plies, color, points)
        expected = sum(r.find_rate for r in own) / len(own) if own else None
        actual = sum(r.in_good_set for r in own) / len(own) if own else None
        summary[name] = {
            "moves_counted": len(own),
            "expected_accuracy": expected,
            "actual_accuracy": actual,
            "accuracy_over_expected": None if expected is None else actual - expected,
            "seized": led.seized,
            "conceded": led.conceded,
            "gifted": led.gifted,
            "taken": led.taken,
            "missed": led.missed,
            "net": led.net,
        }

    return {
        "white": h.get("White", "?"),
        "black": h.get("Black", "?"),
        "result": h.get("Result", "*"),
        "engine": analyzer.engine_id,
        "limit": analyzer.limit.key(),
        "tau": tau,
        "beta": beta,
        "moves": [vars(r) for r in rows],
        "swings": [{"ply": s.ply, "san": s.san, "epa": s.epa} for s in epa.swing_moments(results)],
        "summary": summary,
    }
