"""Human move-prediction interface: P_human(move | position, rating, clock).

HWP, find rate and pressure all read from a model implementing `HumanMoveModel`.
The production model will be a Maia-2 adapter with clock conditioning; until
then `EngineSoftmaxModel` is a transparent placeholder baseline so the rest of
the pipeline can be built and tested end to end. Do not put its numbers on air.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import chess

from .engine import PositionAnalysis
from .points import STANDARD


@dataclass(frozen=True)
class PlayerContext:
    rating: float
    opp_rating: float
    clock: float | None = None  # mover's seconds remaining
    opp_clock: float | None = None
    increment: float = 0.0
    base: float | None = None  # starting time, for clock-fraction features


class HumanMoveModel(Protocol):
    def move_probs(
        self, board: chess.Board, ctx: PlayerContext, analysis: PositionAnalysis | None = None
    ) -> dict[chess.Move, float]:
        """Probability for each legal move; values sum to 1."""
        ...


class UniformModel:
    """Every legal move equally likely. A floor baseline for evaluation."""

    def move_probs(self, board, ctx, analysis=None):
        moves = list(board.legal_moves)
        return {m: 1.0 / len(moves) for m in moves}


@dataclass
class EngineSoftmaxModel:
    """Softmax over engine expected points with a rating- and clock-dependent
    temperature. Stronger players and more time -> sharper distribution.

    Placeholder only: it cannot capture *which* wrong moves humans like.
    """

    temp_at_1500: float = 0.08
    temp_at_2800: float = 0.015
    low_clock_seconds: float = 30.0  # below this, temperature rises
    low_clock_factor: float = 2.0  # max temperature multiplier at zero clock

    def temperature(self, ctx: PlayerContext) -> float:
        # log-linear interpolation in rating, clamped
        t = min(max((ctx.rating - 1500) / 1300, -0.5), 1.2)
        temp = math.exp(math.log(self.temp_at_1500) * (1 - t) + math.log(self.temp_at_2800) * t)
        if ctx.clock is not None and ctx.clock < self.low_clock_seconds:
            frac = 1 - max(ctx.clock, 0.0) / self.low_clock_seconds
            temp *= 1 + (self.low_clock_factor - 1) * frac
        return temp

    def move_probs(self, board, ctx, analysis=None):
        if analysis is None:
            raise ValueError("EngineSoftmaxModel needs the engine analysis of the position")
        es = analysis.expected_by_move(STANDARD)
        temp = self.temperature(ctx)
        top = max(es.values())
        weights = {m: math.exp((e - top) / temp) for m, e in es.items()}
        z = sum(weights.values())
        return {m: w / z for m, w in weights.items()}
