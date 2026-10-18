"""Expected points added (EPA), swing moments and the seized/gifted/missed ledger.

Works on any sequence of position WDLs: engine WDL today, human-aware win
probability (HWP) once that model exists. EPA for a move is the change in the
mover's expected points across it.

Two ways to build the plies:

- `plies_from_wdls`: one WDL per position; each move's "after" is the next
  position's "before". Natural for a model like HWP that scores positions.
- `plies_from_searches`: each move is scored within one engine search, played
  move vs best move. Separate searches of consecutive positions disagree
  (horizon effects), and chaining them books that disagreement as EPA on moves
  that held. Here it is kept apart as `drift` instead.

Either way a player's ledger telescopes: net = E(final) - E(start).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import chess

from .points import STANDARD, WDL, PointsFunction

GIFT_THRESHOLD = 0.10  # an opponent move that hands you this many points counts as an error


@dataclass(frozen=True)
class PlyInput:
    mover: chess.Color
    san: str
    wdl_before: WDL
    wdl_after: WDL
    # Optional extras:
    wdl_after_frozen_clock: WDL | None = None  # after the move, clock reset to its pre-move value
    wdl_best_after: WDL | None = None  # after the best available move instead


@dataclass(frozen=True)
class PlyEPA:
    ply: int
    mover: chess.Color
    san: str
    e_before: float
    e_after: float
    epa: float
    move_epa: float | None  # decision quality, clock held fixed
    clock_epa: float | None  # cost of the time spent
    missed: float | None  # unrealised gain after an opponent error


def compute(plies: Sequence[PlyInput], points: PointsFunction = STANDARD) -> list[PlyEPA]:
    out: list[PlyEPA] = []
    prev_gift = 0.0  # points the previous (opponent) move handed to this mover
    for i, p in enumerate(plies):
        e0, e1 = points.expected(p.wdl_before, p.mover), points.expected(p.wdl_after, p.mover)
        move_epa = clock_epa = missed = None
        if p.wdl_after_frozen_clock is not None:
            ef = points.expected(p.wdl_after_frozen_clock, p.mover)
            move_epa, clock_epa = ef - e0, e1 - ef
        if p.wdl_best_after is not None and prev_gift > GIFT_THRESHOLD:
            missed = max(0.0, points.expected(p.wdl_best_after, p.mover) - e1)
        out.append(PlyEPA(i, p.mover, p.san, e0, e1, e1 - e0, move_epa, clock_epa, missed))
        # how much this move changed the *opponent's* expected points
        opp = not p.mover
        prev_gift = points.expected(p.wdl_after, opp) - points.expected(p.wdl_before, opp)
    return out


def plies_from_searches(movers: Sequence[chess.Color], sans: Sequence[str], best: Sequence[WDL],
                        played: Sequence[WDL]) -> list[PlyInput]:
    """Plies scored within each position's own search: before = best move, after = played move."""
    return [PlyInput(m, s, b, p, wdl_best_after=b) for m, s, b, p in zip(movers, sans, best, played)]


def plies_from_wdls(movers: Sequence[chess.Color], sans: Sequence[str], wdls: Sequence[WDL],
                    best_after: Sequence[WDL | None] | None = None) -> list[PlyInput]:
    """Build PlyInputs from n moves and n+1 position WDLs (start, after move 1, ...)."""
    if len(wdls) != len(movers) + 1:
        raise ValueError("need one more WDL than moves")
    best_after = best_after or [None] * len(movers)
    return [PlyInput(movers[i], sans[i], wdls[i], wdls[i + 1], wdl_best_after=best_after[i])
            for i in range(len(movers))]


@dataclass(frozen=True)
class Ledger:
    color: chess.Color
    seized: float  # positive EPA from own moves
    conceded: float  # negative EPA from own moves (<= 0)
    gifted: float  # points opponent moves handed over (>= 0)
    taken: float  # points opponent moves took away (<= 0)
    missed: float  # gains left on the table after opponent errors (>= 0)
    drift: float = 0.0  # re-evaluation between consecutive searches (0 for chained WDLs)

    @property
    def net(self) -> float:
        """Equals E(final) - E(start) for this player."""
        return self.seized + self.conceded + self.gifted + self.taken + self.drift


def ledger(plies: Sequence[PlyInput], color: chess.Color, points: PointsFunction = STANDARD,
           final: WDL | None = None) -> Ledger:
    """`final` is the WDL after the last move; needed for drift when plies come from searches."""
    seized = conceded = gifted = taken = missed = drift = 0.0
    nexts = [q.wdl_before for q in plies[1:]] + [final if final is not None else plies[-1].wdl_after]
    for p, nxt in zip(plies, nexts):
        drift += points.expected(nxt, color) - points.expected(p.wdl_after, color)
    for p, r in zip(plies, compute(plies, points)):
        if p.mover == color:
            seized += max(r.epa, 0.0)
            conceded += min(r.epa, 0.0)
            missed += r.missed or 0.0
        else:
            delta = points.expected(p.wdl_after, color) - points.expected(p.wdl_before, color)
            gifted += max(delta, 0.0)
            taken += min(delta, 0.0)
    return Ledger(color, seized, conceded, gifted, taken, missed, drift)


def swing_moments(results: Sequence[PlyEPA], k: int = 3) -> list[PlyEPA]:
    """The k moves with the largest absolute EPA, in game order."""
    top = sorted(results, key=lambda r: -abs(r.epa))[:k]
    return sorted(top, key=lambda r: r.ply)
