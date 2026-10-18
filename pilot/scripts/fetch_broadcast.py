"""Download Lichess broadcast games (with %clk clocks) as PGN.

    python scripts/fetch_broadcast.py --tour cR0xn6Sw --min-elo 2600 -o tests/fixtures/broadcast/eccc2026.pgn
    python scripts/fetch_broadcast.py --round EEnnGszr -o round1.pgn

With --tour, only finished rounds are downloaded. Unfinished games (Result "*")
are always dropped, and --min-elo keeps games where both players meet it.

Broadcast PGNs usually lack a TimeControl header, which the clock pipeline
needs to know each player's starting time. Pass it with --time-control in PGN
form, e.g. "40/5400+30:1800+30" for 90 min / 40 moves + 30 min, +30 s from move 1.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request

import chess.pgn

API = "https://lichess.org/api"


def _get(url: str, retries: int = 3) -> bytes:
    """GET, one request at a time; on HTTP 429 wait a minute as Lichess asks."""
    req = urllib.request.Request(url, headers={"User-Agent": "chessmetrics-pilot"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == retries:
                raise
            time.sleep(60)
    raise AssertionError("unreachable")


def finished_rounds(tour_id: str) -> list[str]:
    data = json.loads(_get(f"{API}/broadcast/{tour_id}"))
    return [r["id"] for r in data["rounds"] if r.get("finished")]


def round_games(round_id: str) -> list[chess.pgn.Game]:
    text = _get(f"{API}/broadcast/round/{round_id}.pgn").decode("utf-8")
    handle, games = io.StringIO(text), []
    while (game := chess.pgn.read_game(handle)) is not None:
        games.append(game)
    return games


def _elo(game: chess.pgn.Game, key: str) -> int:
    value = game.headers.get(key, "")
    return int(value) if value.isdigit() else 0


def keep(game: chess.pgn.Game, min_elo: int) -> bool:
    if game.headers.get("Result", "*") == "*" or game.errors or game.end().ply() == 0:
        return False
    return min(_elo(game, "WhiteElo"), _elo(game, "BlackElo")) >= min_elo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--tour", help="broadcast tournament id (all finished rounds)")
    src.add_argument("--round", help="single broadcast round id")
    parser.add_argument("--min-elo", type=int, default=0)
    parser.add_argument("--time-control", help="PGN TimeControl to set when the header is missing")
    parser.add_argument("-o", "--out", required=True)
    args = parser.parse_args(argv)

    rounds = finished_rounds(args.tour) if args.tour else [args.round]
    kept = total = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i, rid in enumerate(rounds):
            if i:
                time.sleep(1)
            for game in round_games(rid):
                total += 1
                if keep(game, args.min_elo):
                    if args.time_control and game.headers.get("TimeControl", "?") in ("?", "-", ""):
                        game.headers["TimeControl"] = args.time_control
                    print(game, file=out, end="\n\n")
                    kept += 1
    print(f"{kept}/{total} games from {len(rounds)} round(s) -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
