"""Broadcast feed: one JSON "frame" per position for the graphics layer.

Frame k is the position after k half-moves (frame 0 is the start). Each frame
carries everything a graphic might show for that moment: the board, both
clocks, the engine and human outcome views, the difficulty of the next
decision, the EPA of the move just played, the structural layers (control,
overloaded pieces, the defence network) and any callouts it triggers.

This is the data contract between the metrics service and the graphics. A live
service would push the same frames over a WebSocket as moves arrive.
"""

from __future__ import annotations

import chess
import chess.pgn

from . import epa
from .difficulty import BETA, TAU, difficulty, good_enough
from .engine import EngineAnalyzer, SearchLimit
from .human import PlayerContext
from .maia import Maia3Model
from .pgn import TimeControl
from .points import STANDARD, WDL
from .structure.control import control_map, space
from .structure.king_safety import king_safety
from .structure.tension import describe, en_prise, loose_pieces, piece_loads

FEED_VERSION = 1

# Callout thresholds. Prototype values: tune with producers and viewers.
CALLOUT_FIND_RATE = 0.25  # "only X% find this"
CALLOUT_SWING = 0.15  # |EPA| of a move
CALLOUT_CONVERSION_GAP = 0.15  # engine vs human expected points
CALLOUT_PRIORITY = ["swing", "difficulty", "overload", "conversion"]
CALLOUT_COOLDOWN = {"swing": 0, "difficulty": 6, "overload": 8, "conversion": 20}  # plies


def _pct(x: float) -> str:
    return "under 1%" if x < 0.005 else f"{x * 100:.0f}%"


def _side(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def _wdl(w: WDL) -> list[float]:
    return [round(w.white, 4), round(w.draw, 4), round(w.black, 4)]


def _move_label(board: chess.Board, move: chess.Move) -> str:
    n = board.fullmove_number
    return f"{n}. {board.san(move)}" if board.turn == chess.WHITE else f"{n}... {board.san(move)}"


def _structure(board: chess.Board) -> dict:
    cmap = control_map(board)
    control = {chess.square_name(sq): "w" if c.controller == chess.WHITE else "b"
               for sq, c in cmap.items() if c.controller is not None}
    overloaded, loads = [], {}
    for color in chess.COLORS:
        for load in piece_loads(board, color):
            loads[chess.square_name(load.square)] = round(load.score, 1)
            if load.overloaded:
                overloaded.append({
                    "square": chess.square_name(load.square),
                    "side": _side(color),
                    "duties": [{"kind": d.kind, "target": chess.square_name(d.target), "attacked": d.under_attack}
                               for d in load.duties],
                    "caption": describe(load),
                })
    defends = []
    for sq, piece in board.piece_map().items():
        for target in board.attacks(sq):
            other = board.piece_at(target)
            if other is not None and other.color == piece.color and other.piece_type != chess.KING:
                defends.append([chess.square_name(sq), chess.square_name(target), "w" if piece.color else "b"])
    return {
        "control": control,
        "space": {"white": space(board, chess.WHITE, cmap), "black": space(board, chess.BLACK, cmap)},
        "kingDanger": {_side(c): round(king_safety(board, c).danger, 1) for c in chess.COLORS},
        "enPrise": [{"square": chess.square_name(e.square), "gain": e.gain}
                    for c in chess.COLORS for e in en_prise(board, c)],
        "loose": [chess.square_name(s) for c in chess.COLORS for s in loose_pieces(board, c)],
        "loads": loads,
        "overloaded": overloaded,
        "defends": defends,
    }


def game_feed(
    game: chess.pgn.Game,
    analyzer: EngineAnalyzer,
    model: Maia3Model,
    scan_limit: SearchLimit = SearchLimit(depth=10),
    min_prob: float = 0.01,
    skip_callout_plies: int = 16,
    video: str | None = None,
) -> dict:
    h = game.headers
    tc = TimeControl.parse(h.get("TimeControl"))
    elo = {chess.WHITE: float(h.get("WhiteElo", 1500)), chess.BLACK: float(h.get("BlackElo", 1500))}
    moves = list(game.mainline())

    # Positions 0..N with history, for batched Maia inference.
    boards = [game.board()]
    for node in moves:
        b = boards[-1].copy()
        b.push(node.move)
        boards.append(b)
    live = [b for b in boards if not b.is_game_over()]
    preds = model.predict_batch(live, [PlayerContext(elo[b.turn], elo[not b.turn]) for b in live])
    pred_by_ply = {b.ply(): p for b, p in zip(live, preds)}

    clocks = {chess.WHITE: tc.base, chess.BLACK: tc.base}
    frames, best_wdls, played_wdls, movers, sans = [], [], [], [], []
    for k, board in enumerate(boards):
        frame: dict = {"ply": k, "fen": board.fen(), "toMove": _side(board.turn)}
        if k > 0:
            prev, node = boards[k - 1], moves[k - 1]
            frame["lastMove"] = {"uci": node.move.uci(), "san": prev.san(node.move),
                                 "label": _move_label(prev, node.move), "mover": _side(prev.turn)}
            if node.clock() is not None:
                clocks[prev.turn] = node.clock()
        frame["clocks"] = {"white": clocks[chess.WHITE], "black": clocks[chess.BLACK]}

        if board.is_game_over():
            frame["engine"] = {"wdl": _wdl(analyzer.evaluate(board)), "cp": None}
        else:
            pred = pred_by_ply[k]
            played = moves[k].move if k < len(moves) else None
            cand = {m for m, p in pred.probs.items() if p >= min_prob} | ({played} if played else set())
            analysis = analyzer.analyse_tiered(board, cand, scan_limit)
            g = good_enough(analysis)
            d = difficulty(analysis, pred.probs)
            best = analysis.best
            cp = best.cp if board.turn == chess.WHITE or best.cp is None else -best.cp
            frame["engine"] = {"wdl": _wdl(best.wdl), "cp": cp, "mate": best.mate,
                               "best": {"uci": best.move.uci(), "san": board.san(best.move)}}
            frame["human"] = {"wdl": _wdl(pred.wdl)}
            # unrounded White expected points (engine, human) for the callout rules
            frame["_e"] = (STANDARD.expected(best.wdl, chess.WHITE), STANDARD.expected(pred.wdl, chess.WHITE))
            top = sorted(pred.probs.items(), key=lambda kv: -kv[1])[:3]
            frame["decision"] = {
                "side": _side(board.turn),
                "findRate": round(d.find_rate, 3),
                "pBlunder": round(d.p_blunder, 3),
                "expectedLoss": round(d.expected_loss, 3),
                "goodMoves": d.good_moves,
                "humanTop": [{"uci": m.uci(), "san": board.san(m), "p": round(p, 3), "holds": m in g}
                             for m, p in top],
            }
            if played is not None:
                best_wdls.append(best.wdl)
                played_wdls.append(analysis.by_move()[played].wdl)
                movers.append(board.turn)
                sans.append(board.san(played))
        frame["structure"] = _structure(board)
        frames.append(frame)

    # Maia's value head is scored from the side to move and its bias flips with
    # the turn, so raw values zigzag move to move. Display a two-ply average.
    prev_wdl = None
    for frame in frames:
        human = frame.get("human")
        if human is None:
            continue
        cur = human["wdl"]
        human["wdlSmooth"] = cur if prev_wdl is None else [round((a + b) / 2, 4) for a, b in zip(prev_wdl, cur)]
        prev_wdl = cur

    final = analyzer.evaluate(boards[-1])
    plies = epa.plies_from_searches(movers, sans, best_wdls, played_wdls)
    results = epa.compute(plies)
    for r in results:
        frames[r.ply + 1]["epa"] = {"value": round(r.epa, 3), "mover": _side(r.mover)}

    # Callouts: gather candidates per frame, then schedule at most one per frame
    # by priority, with a per-kind cooldown so the screen is not flooded.
    prev_overloaded: set[str] = set()
    last_fired: dict[str, int] = {}
    for frame in frames:
        k = frame["ply"]
        candidates = []
        e = frame.get("epa")
        if e and abs(e["value"]) >= CALLOUT_SWING:
            candidates.append({"kind": "swing", "title": "Swing moment",
                               "body": f"{frame['lastMove']['label']} cost {e['mover'].title()} "
                                       f"{abs(e['value']):.2f} expected points"})
        dec = frame.get("decision")
        if dec and k >= skip_callout_plies and dec["findRate"] < CALLOUT_FIND_RATE and dec["goodMoves"] >= 1:
            who = elo[chess.WHITE if dec["side"] == "white" else chess.BLACK]
            candidates.append({"kind": "difficulty", "title": ("Under 1% find it" if dec["findRate"] < 0.005
                                         else f"Only {_pct(dec['findRate'])} find it"),
                               "body": f"Maia-3 estimates {_pct(dec['findRate'])} of {who:.0f}-rated players "
                                       f"find a move that holds for {dec['side'].title()} here"})
        current = {o["square"] for o in frame["structure"]["overloaded"]}
        for o in frame["structure"]["overloaded"]:
            attacked = sum(d["attacked"] for d in o["duties"])
            serious = attacked >= 2 or any(d["kind"] == "mate" for d in o["duties"])
            if k >= skip_callout_plies and serious and o["square"] not in prev_overloaded:
                candidates.append({"kind": "overload", "title": "Overloaded piece", "body": o["caption"],
                                   "square": o["square"]})
        prev_overloaded = current
        if "_e" in frame:
            ew, hw = frame.pop("_e")
            leader = "white" if ew >= 0.5 else "black"
            e_lead, h_lead = (ew, hw) if leader == "white" else (1 - ew, 1 - hw)
            if k >= skip_callout_plies and e_lead >= 0.85 and e_lead - h_lead >= CALLOUT_CONVERSION_GAP:
                candidates.append({"kind": "conversion", "title": "Conversion difficulty",
                                   "body": f"Engine: {leader.title()} is winning. Players at this level score "
                                           f"{_pct(h_lead)} from here"})
        frame["callouts"] = []
        for c in sorted(candidates, key=lambda c: CALLOUT_PRIORITY.index(c["kind"])):
            if k - last_fired.get(c["kind"], -10**6) >= CALLOUT_COOLDOWN[c["kind"]]:
                frame["callouts"] = [c]
                last_fired[c["kind"]] = k
                break

    return {
        "version": FEED_VERSION,
        "game": {
            "white": h.get("White", "?"), "black": h.get("Black", "?"),
            "whiteElo": int(elo[chess.WHITE]), "blackElo": int(elo[chess.BLACK]),
            "whiteTeam": h.get("WhiteTeam", ""), "blackTeam": h.get("BlackTeam", ""),
            "event": h.get("BroadcastName") or h.get("Event", ""), "round": h.get("Round", ""),
            "result": h.get("Result", "*"), "timeControl": h.get("TimeControl", ""),
        },
        "sources": {
            "engine": analyzer.engine_id, "depth": analyzer.limit.key(), "scan": scan_limit.key(),
            "humanModel": model.model_id, "tau": TAU, "beta": BETA,
        },
        "swings": [{"ply": r.ply + 1, "label": frames[r.ply + 1]["lastMove"]["label"], "epa": round(r.epa, 3),
                    "mover": _side(r.mover)} for r in epa.swing_moments(results, k=3)],
        "final": _wdl(final),
        "video": {"url": video} if video else None,
        "frames": frames,
    }
