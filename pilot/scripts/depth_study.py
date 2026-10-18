"""How does search depth affect EPA noise and good-enough sets?

    python scripts/depth_study.py tests/fixtures/broadcast/olympiad2026_open_2700.pgn \
        --game 2 --depths 12 16 20 --scan-depth 10 --threads 4

Each depth gets a fresh engine (no hash carry-over) and a tiered analysis:
Maia-3 candidates (p >= 1%) + played move + scan top 5 at that depth, the rest
at --scan-depth. The deepest depth is the reference. Per depth it reports:

- drift: mean |E_best(next position) - E_played(this search)|, mover's view.
  How much consecutive searches disagree; chained EPA books this as EPA.
- chained concessions on held moves: sum of negative chained EPA on moves in
  the reference good-enough set (the old report's spurious "conceded").
- G agreement vs reference: share of played moves whose in-G verdict matches,
  and mean Jaccard of G over moves searched deeply at both depths.
- find-rate / P(blunder) mean absolute difference vs reference.
- seconds per position.
"""

from __future__ import annotations

import argparse
import json
import time

import chess
import chess.pgn

from chessmetrics.difficulty import difficulty, good_enough
from chessmetrics.engine import AnalysisCache, EngineAnalyzer, SearchLimit
from chessmetrics.human import PlayerContext
from chessmetrics.maia import Maia3Model


def load_game(path: str, index: int) -> chess.pgn.Game:
    with open(path) as f:
        for _ in range(index):
            chess.pgn.skip_game(f)
        return chess.pgn.read_game(f)


def run_depth(game, probs_per_ply, depth, scan_depth, threads, cache_path):
    cache = AnalysisCache(cache_path)
    rows = []
    with EngineAnalyzer(limit=SearchLimit(depth=depth), cache=cache, threads=threads, hash_mb=512) as eng:
        board = game.board()
        t0 = time.time()
        for ply, node in enumerate(game.mainline()):
            probs = probs_per_ply[ply]
            cand = {m for m, p in probs.items() if p >= 0.01} | {node.move}
            a = eng.analyse_tiered(board, cand, SearchLimit(depth=scan_depth))
            d = difficulty(a, probs)
            mover = board.turn
            rows.append({
                "ply": ply,
                "mover": mover,
                "e_best": a.best.expected(mover),
                "e_played": a.by_move()[node.move].expected(mover),
                "good": {m.uci() for m in good_enough(a)},
                "deep": {m.move.uci() for m in a.moves if m.deep},
                "played": node.move.uci(),
                "find": d.find_rate,
                "p_blunder": d.p_blunder,
            })
            board.push(node.move)
        final = eng.evaluate(board)
        seconds = (time.time() - t0) / len(rows)
    for i, r in enumerate(rows):
        if i + 1 < len(rows):
            nxt_best_for_mover = 1 - rows[i + 1]["e_best"]  # standard points: E_mover = 1 - E_opponent
        else:
            nxt_best_for_mover = final.pov(r["mover"])[0] + 0.5 * final.draw
        r["e_next"] = nxt_best_for_mover
    return rows, seconds


def summarise(rows, ref, skip, seconds):
    use = [(r, q) for r, q in zip(rows, ref) if r["ply"] >= skip]
    n = len(use)
    drift = sum(abs(r["e_next"] - r["e_played"]) for r, _ in use) / n
    chained_conc = sum(min(0.0, r["e_next"] - r["e_best"]) for r, q in use if q["played"] in q["good"])
    single_conc = sum(min(0.0, r["e_played"] - r["e_best"]) for r, q in use if q["played"] in q["good"])
    agree = sum((r["played"] in r["good"]) == (q["played"] in q["good"]) for r, q in use) / n
    jac = []
    for r, q in use:
        both = r["deep"] & q["deep"]
        a, b = r["good"] & both, q["good"] & both
        if a | b:
            jac.append(len(a & b) / len(a | b))
    return {
        "s_per_pos": seconds,
        "drift": drift,
        "chained_conc_on_held": chained_conc,
        "single_conc_on_held": single_conc,
        "g_played_agree": agree,
        "g_jaccard": sum(jac) / len(jac),
        "find_mad": sum(abs(r["find"] - q["find"]) for r, q in use) / n,
        "pbl_mad": sum(abs(r["p_blunder"] - q["p_blunder"]) for r, q in use) / n,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("pgn")
    ap.add_argument("--game", type=int, default=0)
    ap.add_argument("--depths", type=int, nargs="+", default=[12, 16, 20])
    ap.add_argument("--scan-depth", type=int, default=10)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--skip-plies", type=int, default=16)
    ap.add_argument("--cache", default=":memory:")
    ap.add_argument("--out", help="write per-depth summaries as JSON")
    args = ap.parse_args(argv)

    game = load_game(args.pgn, args.game)
    h = game.headers
    elo = {chess.WHITE: float(h["WhiteElo"]), chess.BLACK: float(h["BlackElo"])}
    model = Maia3Model("maia3-5m")
    board, probs_per_ply = game.board(), []
    for node in game.mainline():
        probs_per_ply.append(model.move_probs(board, PlayerContext(elo[board.turn], elo[not board.turn])))
        board.push(node.move)
    print(f"{h['White']} - {h['Black']} {h['Result']}: {len(probs_per_ply)} plies", flush=True)

    runs = {}
    for depth in sorted(args.depths):
        runs[depth] = run_depth(game, probs_per_ply, depth, args.scan_depth, args.threads, args.cache)
        print(f"depth {depth}: {runs[depth][1]:.1f}s/position", flush=True)
    ref = runs[max(runs)][0]
    out = {d: summarise(rows, ref, args.skip_plies, secs) for d, (rows, secs) in runs.items()}
    cols = ["s_per_pos", "drift", "chained_conc_on_held", "single_conc_on_held", "g_played_agree", "g_jaccard",
            "find_mad", "pbl_mad"]
    print(f"\nref depth {max(runs)}; plies >= {args.skip_plies}")
    print("depth " + " ".join(f"{c:>20}" for c in cols))
    for d, s in out.items():
        print(f"{d:>5} " + " ".join(f"{s[c]:>20.3f}" for c in cols))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
