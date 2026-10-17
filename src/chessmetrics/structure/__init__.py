"""Explanation layer: deterministic board-geometry metrics. No engine needed."""

from __future__ import annotations

import chess

from .control import control_map, key_squares, space
from .king_safety import king_safety
from .tension import describe, en_prise, loose_pieces, overloaded


def snapshot(board: chess.Board) -> dict:
    """All structural metrics for one position, as plain JSON-ready data."""
    cmap = control_map(board)
    keys = key_squares(board, cmap)
    out: dict = {"fen": board.fen(), "control": {}, "sides": {}}
    for sq, c in cmap.items():
        if c.controller is not None:
            out["control"][chess.square_name(sq)] = "white" if c.controller == chess.WHITE else "black"
    for color in chess.COLORS:
        name = "white" if color == chess.WHITE else "black"
        ks = king_safety(board, color)
        out["sides"][name] = {
            "space": space(board, color, cmap),
            "key_squares": round(keys.owned[color], 2),
            "en_prise": [{"square": chess.square_name(e.square), "gain": e.gain} for e in en_prise(board, color)],
            "loose": [chess.square_name(s) for s in loose_pieces(board, color)],
            "overloaded": [describe(l) for l in overloaded(board, color)],
            "king_danger": round(ks.danger, 2),
            "king_attackers": ks.attackers,
        }
    return out
