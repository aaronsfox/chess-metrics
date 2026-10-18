"""Parse and normalise games into per-move records.

One record per half-move: the position before the move, both ratings, the time
control, both clocks before the move, the mover's clock after it, and the result.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Iterator, TextIO

import chess
import chess.pgn


@dataclass(frozen=True)
class TimeControl:
    base: float | None  # seconds; None when unknown or untimed
    increment: float
    # (move number, seconds): time added once a player completes that many moves
    bonuses: tuple[tuple[int, float], ...] = ()

    @classmethod
    def parse(cls, header: str | None) -> TimeControl:
        """Parse PGN TimeControl, e.g. "180+2", "600", or FIDE "40/5400+30:1800+30".

        Stages after the first become bonuses. The increment is taken from the
        first stage that declares one (FIDE controls use one increment throughout).
        """
        if not header or header in ("-", "?"):
            return cls(None, 0.0)
        stages = []
        for stage in header.split(":"):
            moves, _, rest = stage.rpartition("/")
            secs, _, inc = rest.partition("+")
            stages.append((int(moves) if moves else None, float(secs), inc))
        increment = next((float(inc) for _, _, inc in stages if inc), 0.0)
        bonuses, done = [], 0
        for (moves, _, _), (_, next_secs, _) in zip(stages, stages[1:]):
            if moves is None:
                break
            done += moves
            bonuses.append((done, next_secs))
        return cls(stages[0][1], increment, tuple(bonuses))

    @property
    def category(self) -> str:
        """Lichess-style class by estimated duration (base + 40 * increment)."""
        if self.base is None:
            return "unknown"
        est = self.base + 40 * self.increment
        if est < 29:
            return "ultrabullet"
        if est < 180:
            return "bullet"
        if est < 480:
            return "blitz"
        if est < 1500:
            return "rapid"
        return "classical"


@dataclass(frozen=True)
class MoveRecord:
    game_id: str
    ply: int  # 0-based half-move index
    fen_before: str
    move_uci: str
    san: str
    mover: str  # "white" | "black"
    white_elo: int | None
    black_elo: int | None
    time_base: float | None
    time_increment: float
    time_category: str
    clock_before: float | None  # mover's clock when the move began, seconds
    clock_after: float | None  # mover's clock after the move (increment included)
    opp_clock: float | None  # opponent's clock while the mover thinks
    time_spent: float | None  # None when clocks are missing or inconsistent
    clock_anomaly: bool  # the clock rose by more than the time control allows
    result: str
    event: str


def _elo(headers: chess.pgn.Headers, key: str) -> int | None:
    value = headers.get(key, "")
    return int(value) if re.fullmatch(r"\d+", value) else None


# Relay clocks are noisy: tolerate this many seconds of unexplained clock gain.
CLOCK_TOLERANCE = 5.0


def game_records(game: chess.pgn.Game, game_id: str | None = None) -> list[MoveRecord]:
    """Per-move records for one game.

    Stage bonuses (e.g. +30 min after move 40) are applied when the clock data
    shows them, at or after the stage's move number: broadcast relays often
    record the bonus a move or two late.
    """
    h = game.headers
    tc = TimeControl.parse(h.get("TimeControl"))
    gid = game_id or h.get("GameURL") or h.get("GameId") or "unknown"
    # last known clock per colour, seeded with the starting time
    clocks: dict[chess.Color, float | None] = {chess.WHITE: tc.base, chess.BLACK: tc.base}
    next_bonus = {chess.WHITE: 0, chess.BLACK: 0}
    records = []
    board = game.board()
    for ply, node in enumerate(game.mainline()):
        mover = board.turn
        before, opp = clocks[mover], clocks[not mover]
        after = node.clock()
        spent, anomaly = None, False
        if before is not None and after is not None:
            raw = before - after + tc.increment
            i = next_bonus[mover]
            if raw < -CLOCK_TOLERANCE and i < len(tc.bonuses) and board.fullmove_number >= tc.bonuses[i][0]:
                raw += tc.bonuses[i][1]
                next_bonus[mover] += 1
            if raw < -CLOCK_TOLERANCE:
                anomaly = True
            else:
                spent = max(0.0, raw)
        records.append(
            MoveRecord(
                game_id=gid,
                ply=ply,
                fen_before=board.fen(),
                move_uci=node.move.uci(),
                san=board.san(node.move),
                mover="white" if mover == chess.WHITE else "black",
                white_elo=_elo(h, "WhiteElo"),
                black_elo=_elo(h, "BlackElo"),
                time_base=tc.base,
                time_increment=tc.increment,
                time_category=tc.category,
                clock_before=before,
                clock_after=after,
                opp_clock=opp,
                time_spent=spent,
                clock_anomaly=anomaly,
                result=h.get("Result", "*"),
                event=h.get("Event", ""),
            )
        )
        if after is not None:
            clocks[mover] = after
        board.push(node.move)
    return records


def read_games(handle: TextIO) -> Iterator[chess.pgn.Game]:
    while (game := chess.pgn.read_game(handle)) is not None:
        yield game


def pgn_to_records(path: str | Path) -> Iterator[MoveRecord]:
    with open(path, encoding="utf-8") as f:
        for i, game in enumerate(read_games(f)):
            # Site is often a venue in OTB PGNs, so it is not a usable id
            yield from game_records(game, game_id=game.headers.get("GameURL") or f"{Path(path).stem}#{i}")


def write_csv(records: Iterator[MoveRecord], out: TextIO) -> int:
    writer = csv.DictWriter(out, fieldnames=[f.name for f in fields(MoveRecord)])
    writer.writeheader()
    n = 0
    for r in records:
        writer.writerow(asdict(r))
        n += 1
    return n
