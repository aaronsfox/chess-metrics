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

    @classmethod
    def parse(cls, header: str | None) -> TimeControl:
        """Parse PGN TimeControl, e.g. "180+2", "600", or FIDE "40/5400+30:1800+30".

        For multi-stage controls only the first stage's base is kept; the
        increment is taken from the first stage that declares one.
        """
        if not header or header in ("-", "?"):
            return cls(None, 0.0)
        stages = header.split(":")
        first = stages[0].split("/")[-1]
        base, _, inc = first.partition("+")
        if not inc:
            inc = next((s.partition("+")[2] for s in stages[1:] if "+" in s), "0")
        return cls(float(base), float(inc))

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
    time_spent: float | None
    result: str
    event: str


def _elo(headers: chess.pgn.Headers, key: str) -> int | None:
    value = headers.get(key, "")
    return int(value) if re.fullmatch(r"\d+", value) else None


def game_records(game: chess.pgn.Game, game_id: str | None = None) -> list[MoveRecord]:
    h = game.headers
    tc = TimeControl.parse(h.get("TimeControl"))
    gid = game_id or h.get("Site") or h.get("GameId") or "unknown"
    # last known clock per colour, seeded with the starting time
    clocks: dict[chess.Color, float | None] = {chess.WHITE: tc.base, chess.BLACK: tc.base}
    records = []
    board = game.board()
    for ply, node in enumerate(game.mainline()):
        mover = board.turn
        before, opp = clocks[mover], clocks[not mover]
        after = node.clock()
        spent = None
        if before is not None and after is not None:
            spent = max(0.0, before - after + tc.increment)
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
            yield from game_records(game, game_id=game.headers.get("Site") or f"{Path(path).stem}#{i}")


def write_csv(records: Iterator[MoveRecord], out: TextIO) -> int:
    writer = csv.DictWriter(out, fieldnames=[f.name for f in fields(MoveRecord)])
    writer.writeheader()
    n = 0
    for r in records:
        writer.writerow(asdict(r))
        n += 1
    return n
