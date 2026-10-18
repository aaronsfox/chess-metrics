"""Command line: `chessmetrics records|structure|analyse ...`."""

from __future__ import annotations

import argparse
import json
import sys

import chess

from .engine import DEFAULT_STOCKFISH, AnalysisCache, EngineAnalyzer, SearchLimit
from .human import EngineSoftmaxModel
from .pgn import pgn_to_records, read_games, write_csv
from .structure import snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chessmetrics")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("records", help="normalise a PGN into per-move CSV records")
    p.add_argument("pgn")

    p = sub.add_parser("structure", help="structural metrics for a FEN")
    p.add_argument("fen", nargs="?", default=chess.STARTING_FEN)

    p = sub.add_parser("analyse", help="engine + difficulty + EPA report for each game in a PGN")
    p.add_argument("pgn")
    p.add_argument("--engine", default=DEFAULT_STOCKFISH)
    p.add_argument("--depth", type=int, default=12)
    p.add_argument("--cache", default="analysis_cache.sqlite")
    p.add_argument("--skip-plies", type=int, default=0)
    p.add_argument("--human-model", default="maia3-5m",
                   help='"softmax" (placeholder baseline) or a Maia-3 alias such as maia3-5m')
    p.add_argument("--maia-checkpoint", help="local Maia-3 checkpoint instead of downloading")

    args = parser.parse_args(argv)
    if args.cmd == "records":
        n = write_csv(pgn_to_records(args.pgn), sys.stdout)
        print(f"{n} records", file=sys.stderr)
    elif args.cmd == "structure":
        print(json.dumps(snapshot(chess.Board(args.fen)), indent=2))
    elif args.cmd == "analyse":
        from .report import analyse_game

        if args.human_model == "softmax":
            model = EngineSoftmaxModel()
        else:
            from .maia import Maia3Model

            model = Maia3Model(args.human_model, checkpoint_path=args.maia_checkpoint)
        cache = AnalysisCache(args.cache)
        with EngineAnalyzer(args.engine, SearchLimit(depth=args.depth), cache) as analyzer, open(args.pgn) as f:
            for game in read_games(f):
                report = analyse_game(game, analyzer, model, skip_plies=args.skip_plies)
                report["human_model"] = getattr(model, "model_id", args.human_model)
                print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
