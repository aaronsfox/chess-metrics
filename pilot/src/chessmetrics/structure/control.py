"""Square control, space and key squares.

A side *controls* a square when it can safely hold it, decided by static
exchange rather than by counting attackers:

- Empty or enemy-occupied square: the side's cheapest attacker could stand
  there without losing material.
- Own-occupied square: the opponent cannot win material by capturing there.

When both sides could safely occupy an empty square, the side whose cheapest
attacker is lower in value controls it (pawn control beats queen control).
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .see import effective_attackers, least_valuable_attacker, occupation_loss, see, value

CENTER = chess.SquareSet([chess.D4, chess.E4, chess.D5, chess.E5])
EXTENDED_CENTER = chess.SquareSet(chess.BB_CENTER) | chess.SquareSet(
    [chess.C3, chess.D3, chess.E3, chess.F3, chess.C4, chess.F4, chess.C5, chess.F5,
     chess.C6, chess.D6, chess.E6, chess.F6]
)


@dataclass(frozen=True)
class SquareControl:
    square: chess.Square
    controller: chess.Color | None
    white_attackers: int
    black_attackers: int
    white_cheapest: int | None  # value of the cheapest effective attacker
    black_cheapest: int | None


def _cheapest(board: chess.Board, color: chess.Color, square: chess.Square) -> int | None:
    sq = least_valuable_attacker(board, color, square)
    return None if sq is None else value(board.piece_type_at(sq))


def square_control(board: chess.Board, square: chess.Square) -> SquareControl:
    occupant = board.piece_at(square)
    wc, bc = _cheapest(board, chess.WHITE, square), _cheapest(board, chess.BLACK, square)
    if occupant is not None:
        holder = occupant.color
        controller = holder if see(board, square, not holder) <= 0 else not holder
    else:
        safe = {c: occupation_loss(board, square, c) == 0 for c in chess.COLORS}
        if safe[chess.WHITE] and safe[chess.BLACK]:
            controller = chess.WHITE if wc < bc else chess.BLACK if bc < wc else None
        elif safe[chess.WHITE]:
            controller = chess.WHITE
        elif safe[chess.BLACK]:
            controller = chess.BLACK
        else:
            controller = None
    return SquareControl(
        square,
        controller,
        len(effective_attackers(board, chess.WHITE, square)),
        len(effective_attackers(board, chess.BLACK, square)),
        wc,
        bc,
    )


def control_map(board: chess.Board) -> dict[chess.Square, SquareControl]:
    return {sq: square_control(board, sq) for sq in chess.SQUARES}


def opponent_half(color: chess.Color) -> chess.SquareSet:
    ranks = range(4, 8) if color == chess.WHITE else range(0, 4)
    return chess.SquareSet([sq for sq in chess.SQUARES if chess.square_rank(sq) in ranks])


def pawn_attacks(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    result = chess.SquareSet()
    for sq in board.pieces(chess.PAWN, color):
        result |= board.attacks(sq)
    return result


def space(board: chess.Board, color: chess.Color, cmap: dict[chess.Square, SquareControl] | None = None) -> int:
    """Safely controlled squares in the opponent's half not hit by enemy pawns.

    Squares the side merely occupies without attacking are not counted.
    """
    cmap = cmap or control_map(board)
    enemy_pawns = pawn_attacks(board, not color)
    count = 0
    for sq in opponent_half(color):
        c = cmap[sq]
        attacks = c.white_attackers if color == chess.WHITE else c.black_attackers
        if c.controller == color and attacks > 0 and sq not in enemy_pawns:
            count += 1
    return count


def outposts(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    """Pawn-supported squares on the 5th/6th rank (from `color`'s side) that no
    enemy pawn can ever attack."""
    result = chess.SquareSet()
    enemy_pawns = board.pieces(chess.PAWN, not color)
    own_pawn_attacks = pawn_attacks(board, color)
    forward = 1 if color == chess.WHITE else -1
    ranks = (4, 5) if color == chess.WHITE else (3, 2)
    for sq in own_pawn_attacks:
        f, r = chess.square_file(sq), chess.square_rank(sq)
        if r not in ranks:
            continue
        # an enemy pawn on an adjacent file that has not yet passed sq's rank can
        # still advance to attack it
        if not any(abs(chess.square_file(p) - f) == 1 and (chess.square_rank(p) - r) * forward > 0
                   for p in enemy_pawns):
            result.add(sq)
    return result


def king_zone(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    """King square, its neighbours, and one more rank toward the enemy."""
    king = board.king(color)
    if king is None:
        return chess.SquareSet()
    zone = chess.SquareSet(chess.BB_KING_ATTACKS[king]) | chess.SquareSet.from_square(king)
    forward = 1 if color == chess.WHITE else -1
    kf, kr = chess.square_file(king), chess.square_rank(king)
    r = kr + 2 * forward
    if 0 <= r <= 7:
        for f in (kf - 1, kf, kf + 1):
            if 0 <= f <= 7:
                zone.add(chess.square(f, r))
    return zone


def passed_pawns(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    result = chess.SquareSet()
    enemy = board.pieces(chess.PAWN, not color)
    forward = 1 if color == chess.WHITE else -1
    for p in board.pieces(chess.PAWN, color):
        pf, pr = chess.square_file(p), chess.square_rank(p)
        if not any(abs(chess.square_file(e) - pf) <= 1 and (chess.square_rank(e) - pr) * forward > 0 for e in enemy):
            result.add(p)
    return result


def stop_squares(board: chess.Board, color: chess.Color) -> chess.SquareSet:
    """The square directly in front of each of `color`'s passed pawns."""
    forward = 8 if color == chess.WHITE else -8
    return chess.SquareSet([p + forward for p in passed_pawns(board, color) if 0 <= p + forward < 64])


# Weights for key-square ownership. A judgement call: publish and keep fixed.
KEY_WEIGHTS = {"center": 2.0, "extended_center": 1.0, "king_zone": 1.5, "outpost": 1.0, "passer_stop": 1.5}


@dataclass(frozen=True)
class KeySquares:
    weights: dict[chess.Square, float]  # combined weight per key square
    owned: dict[chess.Color, float]  # weighted sum of controlled key squares


def key_squares(board: chess.Board, cmap: dict[chess.Square, SquareControl] | None = None) -> KeySquares:
    cmap = cmap or control_map(board)
    weights: dict[chess.Square, float] = {}

    def add(squares, w):
        for sq in squares:
            weights[sq] = weights.get(sq, 0.0) + w

    add(CENTER, KEY_WEIGHTS["center"])
    add(EXTENDED_CENTER - CENTER, KEY_WEIGHTS["extended_center"])
    for color in chess.COLORS:
        add(king_zone(board, color), KEY_WEIGHTS["king_zone"])
        add(outposts(board, color), KEY_WEIGHTS["outpost"])
        add(stop_squares(board, color), KEY_WEIGHTS["passer_stop"])
    owned = {chess.WHITE: 0.0, chess.BLACK: 0.0}
    for sq, w in weights.items():
        ctrl = cmap[sq].controller
        if ctrl is not None:
            owned[ctrl] += w
    return KeySquares(weights, owned)
