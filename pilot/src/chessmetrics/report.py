"""End-to-end game report: engine pass, difficulty, EPA and accuracy over expected."""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.pgn

from . import epa
from .difficulty import BETA, TAU, difficulty, good_enough
from .engine import EngineAnalyzer, SearchLimit
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
    scan_limit: SearchLimit | None = SearchLimit(depth=10),
    min_prob: float = 0.01,
) -> dict:
    """Analyse every move of `game`.

    With `scan_limit` set (the default), each position gets a tiered analysis:
    the moves the human model rates at `min_prob` or above, plus the played move,
    are searched at the analyzer's limit; the rest only at `scan_limit`. Pass
    `scan_limit=None` to search every legal move at full depth. Models that need
    the engine analysis to produce probabilities always get the full search.

    EPA is scored within each position's own search (played vs best move), with
    re-evaluation between searches reported separately as `drift`.

    `skip_plies` excludes the first N half-moves from accuracy figures, standing
    in for "exclude moves until a player leaves known theory" until an opening
    book is wired in.
    """
    h = game.headers
    tc = TimeControl.parse(h.get("TimeControl"))
    elo = {chess.WHITE: _elo(h, "WhiteElo", 1500), chess.BLACK: _elo(h, "BlackElo", 1500)}
    clocks: dict[chess.Color, float | None] = {chess.WHITE: tc.base, chess.BLACK: tc.base}

    tiered = scan_limit is not None and not getattr(model, "needs_analysis", False)
    board = game.board()
    rows: list[MoveRow] = []
    best_wdls: list[WDL] = []
    played_wdls: list[WDL] = []
    movers, sans = [], []
    for ply, node in enumerate(game.mainline()):
        mover = board.turn
        ctx = PlayerContext(elo[mover], elo[not mover], clocks[mover], clocks[not mover], tc.increment, tc.base)
        if tiered:
            probs = model.move_probs(board, ctx)
            candidates = {m for m, p in probs.items() if p >= min_prob} | {node.move}
            analysis = analyzer.analyse_tiered(board, candidates, scan_limit)
        else:
            analysis = analyzer.analyse(board)
            probs = model.move_probs(board, ctx, analysis)
        d = difficulty(analysis, probs, tau, beta, points)
        g = good_enough(analysis, tau, points)

        best_wdls.append(analysis.best.wdl)
        played_wdls.append(analysis.by_move()[node.move].wdl)
        movers.append(mover)
        sans.append(board.san(node.move))
        rows.append(MoveRow(ply, "white" if mover else "black", sans[-1], points.expected(analysis.best.wdl, mover),
                            0.0, node.move in g, d.find_rate, d.p_blunder, d.expected_loss, clocks[mover]))
        if node.clock() is not None:
            clocks[mover] = node.clock()
        board.push(node.move)
    final = analyzer.evaluate(board)

    plies = epa.plies_from_searches(movers, sans, best_wdls, played_wdls)
    results = epa.compute(plies, points)
    for row, r in zip(rows, results):
        row.epa = r.epa

    summary = {}
    for color in chess.COLORS:
        name = "white" if color else "black"
        own = [r for r in rows if r.mover == name and r.ply >= skip_plies]
        led = epa.ledger(plies, color, points, final=final)
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
            "drift": led.drift,
            "net": led.net,
        }

    return {
        "white": h.get("White", "?"),
        "black": h.get("Black", "?"),
        "result": h.get("Result", "*"),
        "engine": analyzer.engine_id,
        "limit": analyzer.limit.key(),
        "scan_limit": scan_limit.key() if tiered else None,
        "tau": tau,
        "beta": beta,
        "moves": [vars(r) for r in rows],
        "swings": [{"ply": s.ply, "san": s.san, "epa": s.epa} for s in epa.swing_moments(results)],
        "summary": summary,
    }
