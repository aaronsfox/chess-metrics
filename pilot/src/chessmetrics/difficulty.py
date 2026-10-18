"""Difficulty layer: good-enough sets, find rate, pressure and only-move chains.

Find rate and pressure are two views of one distribution: the human model's
probability mass split into moves that hold and moves that lose. Move values
use engine WDL (perfect-play value), so difficulty describes the position,
not the clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .engine import EngineAnalyzer, PositionAnalysis
from .human import HumanMoveModel, PlayerContext
from .points import STANDARD, PointsFunction

# Starting values from the build plan. Tune once against blunder data, then freeze.
TAU = 0.04  # good-enough tolerance, expected points
BETA = 0.15  # blunder threshold, expected points


def losses(analysis: PositionAnalysis, points: PointsFunction = STANDARD) -> dict[chess.Move, float]:
    """E_best - E(m) for every analysed move, from the mover's point of view.

    E_best is the best *deeply searched* move. In a tiered analysis a
    shallow-only move can look better than it; its loss is clamped at 0.
    """
    es = analysis.expected_by_move(points)
    best = analysis.best.expected(analysis.mover, points)
    return {m: max(0.0, best - e) for m, e in es.items()}


def good_enough(analysis: PositionAnalysis, tau: float = TAU, points: PointsFunction = STANDARD) -> set[chess.Move]:
    return {m for m, loss in losses(analysis, points).items() if loss <= tau}


@dataclass(frozen=True)
class Difficulty:
    find_rate: float  # P(human plays a move in G)
    p_blunder: float  # P(human plays a move losing more than beta)
    expected_loss: float  # probability-weighted expected-points loss
    good_moves: int  # |G|
    coverage: float  # human probability mass on analysed moves (1.0 if all legal)


def difficulty(
    analysis: PositionAnalysis,
    probs: dict[chess.Move, float],
    tau: float = TAU,
    beta: float = BETA,
    points: PointsFunction = STANDARD,
) -> Difficulty:
    """Combine engine losses with human move probabilities.

    If only a candidate subset was analysed, probabilities are renormalised over
    analysed moves and `coverage` reports how much mass that kept.
    """
    loss = losses(analysis, points)
    coverage = sum(p for m, p in probs.items() if m in loss)
    if coverage <= 0:
        raise ValueError("human model puts no mass on any analysed move")
    norm = {m: probs.get(m, 0.0) / coverage for m in loss}
    return Difficulty(
        find_rate=sum(p for m, p in norm.items() if loss[m] <= tau),
        p_blunder=sum(p for m, p in norm.items() if loss[m] > beta),
        expected_loss=sum(p * loss[m] for m, p in norm.items()),
        good_moves=sum(1 for l in loss.values() if l <= tau),
        coverage=coverage,
    )


@dataclass(frozen=True)
class ChainLink:
    fen: str
    only_move: chess.Move
    find_rate: float


@dataclass(frozen=True)
class OnlyMoveChain:
    links: tuple[ChainLink, ...] = field(default_factory=tuple)

    @property
    def length(self) -> int:
        return len(self.links)

    @property
    def hold_probability(self) -> float:
        """ChainHold = product of find rates along the chain (1.0 if empty)."""
        p = 1.0
        for link in self.links:
            p *= link.find_rate
        return p


def only_move_chain(
    board: chess.Board,
    analyzer: EngineAnalyzer,
    model: HumanMoveModel,
    ctx: PlayerContext,
    max_good: int = 1,
    max_plies: int = 10,
    tau: float = TAU,
    points: PointsFunction = STANDARD,
) -> OnlyMoveChain:
    """Walk the principal line while the defender (side to move now) faces only moves.

    The opponent is assumed to play the engine's best reply, which overstates
    pressure when the opponent is also struggling.
    """
    b = board.copy()
    links: list[ChainLink] = []
    for _ in range(0, max_plies, 2):
        if b.is_game_over():
            break
        analysis = analyzer.analyse(b)
        g = good_enough(analysis, tau, points)
        if len(g) > max_good:
            break
        d = difficulty(analysis, model.move_probs(b, ctx, analysis), tau=tau, points=points)
        links.append(ChainLink(b.fen(), analysis.best.move, d.find_rate))
        b.push(analysis.best.move)
        if b.is_game_over():
            break
        b.push(analyzer.analyse(b).best.move)
    return OnlyMoveChain(tuple(links))
