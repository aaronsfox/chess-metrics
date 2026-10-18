"""Baseline evaluation of a human move model on played moves (build plan step 1).

    python scripts/eval_human_model.py tests/fixtures/broadcast/olympiad2026_open_2700.pgn \
        --skip-plies 16 --elo-offsets 0 200 400 800

Reports top-1 move-match rate, mean log-likelihood of the played move, and
calibration (predicted probability vs how often moves with that probability
were actually played). `--elo-offsets` re-runs with ratings shifted, a quick
probe of whether the model's rating conditioning fits elite play;
`--temperatures` does the same for temperature scaling (build plan step 3).
Fitting and scoring on the same games is exploratory only: hold out by event
before fixing a value.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

import chess

from chessmetrics.human import PlayerContext
from chessmetrics.maia import Maia3Model
from chessmetrics.pgn import read_games

BINS = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0001]


@dataclass
class Sample:
    board: chess.Board  # with move stack, for history
    played: chess.Move
    rating: float
    opp_rating: float


def collect(path: str, skip_plies: int) -> list[Sample]:
    out = []
    with open(path) as f:
        for game in read_games(f):
            h = game.headers
            elo = {chess.WHITE: float(h.get("WhiteElo", 1500)), chess.BLACK: float(h.get("BlackElo", 1500))}
            board = game.board()
            for node in game.mainline():
                if board.ply() >= skip_plies and board.legal_moves.count() > 1:
                    out.append(Sample(board.copy(), node.move, elo[board.turn], elo[not board.turn]))
                board.push(node.move)
    return out


def evaluate(model: Maia3Model, samples: list[Sample], offset: float, batch: int = 64,
             temperature: float = 1.0) -> dict:
    model.clock_temperature = (lambda ctx: temperature) if temperature != 1.0 else None
    top1 = ll = uniform_ll = 0.0
    bins = [[0.0, 0, 0] for _ in BINS[:-1]]  # sum of predicted p, count, played
    for i in range(0, len(samples), batch):
        chunk = samples[i:i + batch]
        ctxs = [PlayerContext(s.rating + offset, s.opp_rating + offset) for s in chunk]
        for s, pred in zip(chunk, model.predict_batch([s.board for s in chunk], ctxs)):
            probs = pred.probs
            top1 += max(probs, key=probs.get) == s.played
            ll += math.log(max(probs[s.played], 1e-12))
            uniform_ll += -math.log(len(probs))
            for move, p in probs.items():
                b = next(k for k in range(len(BINS) - 1) if BINS[k] <= p < BINS[k + 1])
                bins[b][0] += p
                bins[b][1] += 1
                bins[b][2] += move == s.played
    n = len(samples)
    return {
        "offset": offset,
        "temperature": temperature,
        "n": n,
        "top1": top1 / n,
        "mean_ll": ll / n,
        "uniform_ll": uniform_ll / n,
        "calibration": [(BINS[k], BINS[k + 1], s / c, played / c, c) for k, (s, c, played) in enumerate(bins) if c],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pgn")
    parser.add_argument("--model", default="maia3-5m")
    parser.add_argument("--skip-plies", type=int, default=16, help="exclude likely opening preparation")
    parser.add_argument("--elo-offsets", type=float, nargs="+", default=[0.0])
    parser.add_argument("--temperatures", type=float, nargs="+", default=[1.0])
    args = parser.parse_args(argv)

    model = Maia3Model(args.model)
    samples = collect(args.pgn, args.skip_plies)
    print(f"{args.model}: {len(samples)} positions (ply >= {args.skip_plies}, >1 legal move)")
    results = [evaluate(model, samples, off, temperature=t) for off in args.elo_offsets for t in args.temperatures]
    print(f"{'elo offset':>10} {'temp':>5} {'top-1':>7} {'mean LL':>8} {'uniform LL':>10}")
    for r in results:
        print(f"{r['offset']:>+10.0f} {r['temperature']:>5.2f} {r['top1']:>7.3f} {r['mean_ll']:>8.3f} "
              f"{r['uniform_ll']:>10.3f}")
    for r in (results[0], max(results, key=lambda r: r["mean_ll"])):
        print(f"\ncalibration at offset {r['offset']:+.0f}, T={r['temperature']:.2f} (predicted -> observed, n moves):")
        for lo, hi, pred, obs, c in r["calibration"]:
            print(f"  [{lo:.2f}, {min(hi, 1):.2f})  {pred:.3f} -> {obs:.3f}   n={c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
