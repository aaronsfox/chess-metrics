"""Command line: `chessmetrics records|structure|analyse ...`."""

from __future__ import annotations

import argparse
import os
import json
import sys

import chess
import chess.pgn

from .engine import DEFAULT_STOCKFISH, WDL_MODELS, AnalysisCache, EngineAnalyzer, SearchLimit
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
    p.add_argument("--depth", type=int, default=18,
                   help="deep search depth (18: see README depth study)")
    p.add_argument("--cache", default="analysis_cache.sqlite")
    p.add_argument("--skip-plies", type=int, default=0)
    p.add_argument("--human-model", default="maia3-5m",
                   help='"softmax" (placeholder baseline) or a Maia-3 alias such as maia3-5m')
    p.add_argument("--maia-checkpoint", help="local Maia-3 checkpoint instead of downloading")
    p.add_argument("--scan-depth", type=int, default=10,
                   help="depth for moves outside the human model's candidates (tiered analysis)")
    p.add_argument("--min-prob", type=float, default=0.01, help="human probability that makes a move a candidate")
    p.add_argument("--full", action="store_true", help="search every legal move at --depth (slow)")
    p.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    p.add_argument("--wdl-model", default="engine", choices=WDL_MODELS,
                   help='"engine" (Stockfish WDL, steep) or a python-chess model, e.g. "lichess" (flatter, no draws)')

    p = sub.add_parser("feed", help="broadcast feed (one JSON frame per position) for one game")
    p.add_argument("pgn")
    p.add_argument("--game", type=int, default=0, help="0-based index of the game in the PGN")
    p.add_argument("--engine", default=DEFAULT_STOCKFISH)
    p.add_argument("--depth", type=int, default=18)
    p.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    p.add_argument("--cache", default="analysis_cache.sqlite")
    p.add_argument("--human-model", default="maia3-5m")
    p.add_argument("-o", "--out", required=True)

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
        scan = None if args.full else SearchLimit(depth=args.scan_depth)
        with EngineAnalyzer(args.engine, SearchLimit(depth=args.depth), cache, threads=args.threads,
                            hash_mb=256, wdl_model=args.wdl_model) as analyzer, open(args.pgn) as f:
            for game in read_games(f):
                report = analyse_game(game, analyzer, model, skip_plies=args.skip_plies,
                                      scan_limit=scan, min_prob=args.min_prob)
                report["human_model"] = getattr(model, "model_id", args.human_model)
                print(json.dumps(report))
    elif args.cmd == "feed":
        from .broadcast import game_feed
        from .maia import Maia3Model

        with open(args.pgn) as f:
            for _ in range(args.game):
                chess.pgn.skip_game(f)
            game = chess.pgn.read_game(f)
        with EngineAnalyzer(args.engine, SearchLimit(depth=args.depth), AnalysisCache(args.cache),
                            threads=args.threads, hash_mb=256) as analyzer:
            feed = game_feed(game, analyzer, Maia3Model(args.human_model))
        with open(args.out, "w") as f:
            json.dump(feed, f, separators=(",", ":"))
        print(f"{len(feed['frames'])} frames -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
