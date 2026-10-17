"""Static exchange evaluation (SEE) and pin-aware attacker sets.

SEE simulates the capture sequence on one square, least valuable attacker
first, letting either side stop when continuing would lose material. X-rays are
handled naturally: each capture removes the capturing piece from its origin, so
sliders behind it start attacking. Absolutely pinned pieces may only capture
along their pin line, and a king may not capture onto a defended square.

Known limits (by design of SEE): ignores checks, zwischenzugs, relative pins,
promotions and en passant.
"""

from __future__ import annotations

import chess

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 100,
}


def value(piece_type: chess.PieceType) -> int:
    return PIECE_VALUES[piece_type]


def effective_attackers(board: chess.Board, color: chess.Color, square: chess.Square) -> chess.SquareSet:
    """Pieces of `color` attacking `square`, excluding absolutely pinned pieces
    that cannot reach it without leaving the pin line."""
    result = chess.SquareSet()
    for sq in board.attackers(color, square):
        if board.is_pinned(color, sq) and square not in board.pin(color, sq):
            continue
        result.add(sq)
    return result


def least_valuable_attacker(board: chess.Board, color: chess.Color, square: chess.Square) -> chess.Square | None:
    attackers = effective_attackers(board, color, square)
    if not attackers:
        return None
    return min(attackers, key=lambda sq: value(board.piece_type_at(sq)))


def _exchange_gains(board: chess.Board, square: chess.Square, side: chess.Color, standing_value: int) -> list[int]:
    """Material won at each capture of the sequence on `square`, `side` first."""
    b = board.copy(stack=False)
    gains: list[int] = []
    color = side
    while True:
        att = least_valuable_attacker(b, color, square)
        if att is None:
            break
        piece = b.piece_at(att)
        b.remove_piece_at(att)
        if piece.piece_type == chess.KING and effective_attackers(b, not color, square):
            break  # king cannot capture onto a defended square
        gains.append(standing_value)
        b.set_piece_at(square, piece)
        standing_value = value(piece.piece_type)
        color = not color
    return gains


def _resolve(gains: list[int]) -> int:
    """Back up the exchange: each side may decline to continue."""
    score = 0
    for g in reversed(gains):
        score = max(0, g - score)
    return score


def see(board: chess.Board, square: chess.Square, side: chess.Color) -> int:
    """Material `side` wins by initiating captures on the piece at `square`.

    Returns 0 if the square is empty, holds a `side` piece, or capturing does
    not pay. A positive value means the piece is en prise.
    """
    target = board.piece_at(square)
    if target is None or target.color == side:
        return 0
    return _resolve(_exchange_gains(board, square, side, value(target.piece_type)))


def occupation_loss(board: chess.Board, square: chess.Square, color: chess.Color) -> int | None:
    """If `color` moved its cheapest attacker onto `square`, how much material
    would the opponent win there? None if `color` does not attack the square.

    This is the basis of "safe control": a side controls a square when it can
    put a piece there without losing it.
    """
    att = least_valuable_attacker(board, color, square)
    if att is None:
        return None
    b = board.copy(stack=False)
    piece = b.piece_at(att)
    b.remove_piece_at(att)
    if piece.piece_type == chess.KING and effective_attackers(b, not color, square):
        return value(chess.KING)
    b.set_piece_at(square, piece)
    return see(b, square, not color)
