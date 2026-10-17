"""Win/draw/loss probabilities and the configurable points function.

Everything downstream is expressed in expected points from a given side's
perspective: E = P(win) * win_value + P(draw) * draw_value + P(loss) * loss_value.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess


@dataclass(frozen=True)
class WDL:
    """Outcome probabilities from White's point of view. Fields sum to 1."""

    white: float
    draw: float
    black: float

    def __post_init__(self) -> None:
        total = self.white + self.draw + self.black
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"WDL must sum to 1, got {total}")

    @classmethod
    def from_permille(cls, wins: int, draws: int, losses: int, pov: chess.Color = chess.WHITE) -> WDL:
        """Build from an engine's permille WDL, given from `pov`'s point of view."""
        total = wins + draws + losses
        w, d, l = wins / total, draws / total, losses / total
        return cls(w, d, l) if pov == chess.WHITE else cls(l, d, w)

    @classmethod
    def from_result(cls, result: str) -> WDL:
        """Degenerate WDL for a finished game ("1-0", "0-1", "1/2-1/2")."""
        return {
            "1-0": cls(1.0, 0.0, 0.0),
            "0-1": cls(0.0, 0.0, 1.0),
            "1/2-1/2": cls(0.0, 1.0, 0.0),
        }[result]

    def pov(self, color: chess.Color) -> tuple[float, float, float]:
        """(win, draw, loss) from `color`'s point of view."""
        if color == chess.WHITE:
            return self.white, self.draw, self.black
        return self.black, self.draw, self.white


@dataclass(frozen=True)
class PointsFunction:
    """Maps outcomes to points for each side.

    The default is standard scoring. Use the constructors for event-specific
    contexts; a must-win situation is modelled by lowering one side's draw value.
    """

    draw_white: float = 0.5
    draw_black: float = 0.5
    win: float = 1.0
    loss: float = 0.0

    @classmethod
    def standard(cls) -> PointsFunction:
        return cls()

    @classmethod
    def armageddon(cls) -> PointsFunction:
        """Draw counts as a win for Black."""
        return cls(draw_white=0.0, draw_black=1.0)

    @classmethod
    def must_win(cls, color: chess.Color, draw_value: float = 0.0) -> PointsFunction:
        if color == chess.WHITE:
            return cls(draw_white=draw_value)
        return cls(draw_black=draw_value)

    def draw_value(self, color: chess.Color) -> float:
        return self.draw_white if color == chess.WHITE else self.draw_black

    def expected(self, wdl: WDL, color: chess.Color) -> float:
        w, d, l = wdl.pov(color)
        return w * self.win + d * self.draw_value(color) + l * self.loss


STANDARD = PointsFunction.standard()
