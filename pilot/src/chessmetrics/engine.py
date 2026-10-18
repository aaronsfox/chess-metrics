"""Engine analysis pipeline: evaluate every plausible move, not just the best one.

Each analysed move gets a centipawn score (mover's view) and a WDL (White's
view, like every WDL in the package). Results are cached in SQLite keyed by position + engine version +
search limit + candidate set, so re-running a dataset only pays for new positions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Sequence

import chess
import chess.engine

from .points import STANDARD, WDL, PointsFunction

DEFAULT_STOCKFISH = "/usr/games/stockfish"
# Used only when the engine does not report WDL itself.
FALLBACK_WDL_MODEL = "sf16"


@dataclass(frozen=True)
class SearchLimit:
    """A fixed search budget, so evaluations are comparable across positions."""

    depth: int | None = 14
    nodes: int | None = None

    def to_engine(self) -> chess.engine.Limit:
        return chess.engine.Limit(depth=self.depth, nodes=self.nodes)

    def key(self) -> str:
        return f"d{self.depth}-n{self.nodes}"


@dataclass(frozen=True)
class MoveEval:
    """Engine verdict on one candidate move. cp and mate are from the mover's view."""

    move: chess.Move
    cp: int | None  # None when the score is a forced mate
    mate: int | None  # moves to mate; positive = mover mates
    wdl: WDL  # White's point of view, like every WDL in the package
    deep: bool = True  # False for moves only covered by a tiered analysis's shallow scan

    def expected(self, mover: chess.Color, points: PointsFunction = STANDARD) -> float:
        return points.expected(self.wdl, mover)

    def to_json(self) -> dict:
        return {
            "move": self.move.uci(),
            "cp": self.cp,
            "mate": self.mate,
            "wdl": [self.wdl.white, self.wdl.draw, self.wdl.black],
            "deep": self.deep,
        }

    @classmethod
    def from_json(cls, d: dict) -> MoveEval:
        return cls(chess.Move.from_uci(d["move"]), d["cp"], d["mate"], WDL(*d["wdl"]), d.get("deep", True))


@dataclass(frozen=True)
class PositionAnalysis:
    fen: str
    engine_id: str
    limit: SearchLimit
    moves: tuple[MoveEval, ...]  # deep moves best first, then shallow-only moves best first
    scan_limit: SearchLimit | None = None  # set for tiered analyses

    @property
    def mover(self) -> chess.Color:
        return chess.Board(self.fen).turn

    @property
    def best(self) -> MoveEval:
        """Best deeply searched move. Shallow-only moves never count as best."""
        return self.moves[0]

    def by_move(self) -> dict[chess.Move, MoveEval]:
        return {m.move: m for m in self.moves}

    def expected_by_move(self, points: PointsFunction = STANDARD) -> dict[chess.Move, float]:
        mover = self.mover
        return {m.move: m.expected(mover, points) for m in self.moves}


def position_key(board: chess.Board) -> str:
    """FEN without the fullmove number, which never affects the evaluation."""
    return " ".join(board.fen().split(" ")[:5])


class AnalysisCache:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS analysis ("
            " position TEXT, engine TEXT, lim TEXT, candidates TEXT, moves TEXT,"
            " PRIMARY KEY (position, engine, lim, candidates))"
        )

    def get(self, position: str, engine: str, lim: str, candidates: str) -> list[MoveEval] | None:
        row = self._db.execute(
            "SELECT moves FROM analysis WHERE position=? AND engine=? AND lim=? AND candidates=?",
            (position, engine, lim, candidates),
        ).fetchone()
        return None if row is None else [MoveEval.from_json(d) for d in json.loads(row[0])]

    def put(self, position: str, engine: str, lim: str, candidates: str, moves: Sequence[MoveEval]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO analysis VALUES (?, ?, ?, ?, ?)",
            (position, engine, lim, candidates, json.dumps([m.to_json() for m in moves])),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()


def _candidates_key(board: chess.Board, candidates: Iterable[chess.Move] | None) -> str:
    if candidates is None:
        return "all"
    ucis = sorted(m.uci() for m in candidates)
    if len(ucis) == board.legal_moves.count():
        return "all"
    return hashlib.sha1(",".join(ucis).encode()).hexdigest()[:16]


class EngineAnalyzer:
    """Wraps a UCI engine for MultiPV analysis over a candidate set of moves.

    Use as a context manager, or call close() when done.
    """

    def __init__(
        self,
        path: str = DEFAULT_STOCKFISH,
        limit: SearchLimit = SearchLimit(),
        cache: AnalysisCache | None = None,
        threads: int = 1,
        hash_mb: int = 64,
    ) -> None:
        self.limit = limit
        self.cache = cache or AnalysisCache()
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        options = {"Threads": threads, "Hash": hash_mb}
        if "UCI_ShowWDL" in self._engine.options:
            options["UCI_ShowWDL"] = True
        self._engine.configure(options)
        self.engine_id = self._engine.id.get("name", Path(path).name)

    def __enter__(self) -> EngineAnalyzer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._engine.quit()

    def analyse(
        self,
        board: chess.Board,
        candidates: Iterable[chess.Move] | None = None,
        limit: SearchLimit | None = None,
    ) -> PositionAnalysis:
        """Evaluate `candidates` (default: every legal move), best first."""
        if board.is_game_over():
            raise ValueError("cannot analyse moves in a finished position")
        limit = limit or self.limit
        cand = list(board.legal_moves) if candidates is None else list(candidates)
        pos, ckey = position_key(board), _candidates_key(board, cand)
        cached = self.cache.get(pos, self.engine_id, limit.key(), ckey)
        if cached is not None:
            return PositionAnalysis(board.fen(), self.engine_id, limit, tuple(cached))

        infos = self._engine.analyse(
            board,
            limit.to_engine(),
            multipv=len(cand),
            root_moves=cand,
            info=chess.engine.INFO_SCORE | chess.engine.INFO_PV,
        )
        moves = [self._to_move_eval(board, info) for info in infos if info.get("pv")]
        moves.sort(key=lambda m: -m.expected(board.turn))
        self.cache.put(pos, self.engine_id, limit.key(), ckey, moves)
        return PositionAnalysis(board.fen(), self.engine_id, limit, tuple(moves))

    def analyse_tiered(
        self,
        board: chess.Board,
        candidates: Iterable[chess.Move],
        scan_limit: SearchLimit = SearchLimit(depth=10),
        scan_top_n: int = 5,
    ) -> PositionAnalysis:
        """Deep search on the plausible moves, shallow scan for the rest.

        Every legal move is scanned at `scan_limit`; `candidates` plus the scan's
        `scan_top_n` best moves are then searched at the analyzer's limit. The
        shallow-only moves are kept so that low-probability moves can still be
        classified as good or bad, but they never count as the best move.
        """
        scan = self.analyse(board, None, scan_limit)
        deep_set = {m.move for m in scan.moves[:scan_top_n]} | set(candidates)
        deep = self.analyse(board, sorted(deep_set, key=chess.Move.uci))
        searched = {m.move for m in deep.moves}
        shallow = tuple(replace(m, deep=False) for m in scan.moves if m.move not in searched)
        return PositionAnalysis(board.fen(), self.engine_id, self.limit, deep.moves + shallow, scan_limit)

    def evaluate(self, board: chess.Board) -> WDL:
        """WDL of a position itself (single PV). Terminal positions are scored exactly."""
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            return WDL.from_result(outcome.result())
        pos = position_key(board)
        cached = self.cache.get(pos, self.engine_id, self.limit.key(), "best")
        if cached is None:
            info = self._engine.analyse(board, self.limit.to_engine())
            cached = [self._to_move_eval(board, info)]
            self.cache.put(pos, self.engine_id, self.limit.key(), "best", cached)
        return cached[0].wdl

    @staticmethod
    def _to_move_eval(board: chess.Board, info: chess.engine.InfoDict) -> MoveEval:
        score = info["score"].pov(board.turn)
        wdl_info = info.get("wdl")
        if wdl_info is not None:
            w = wdl_info.pov(board.turn)
        else:
            w = score.wdl(model=FALLBACK_WDL_MODEL, ply=board.ply())
        return MoveEval(
            move=info["pv"][0],
            cp=score.score(),
            mate=score.mate(),
            wdl=WDL.from_permille(w.wins, w.draws, w.losses, pov=board.turn),
        )
