"""Heuristic king-danger score with an itemised breakdown.

Higher = more dangerous for the king's owner. The weights are heuristic and
must be validated against evaluation before broadcast use; the breakdown is
there so a commentator can say *why* the number is high.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .control import king_zone

ATTACK_WEIGHTS = {chess.PAWN: 1.0, chess.KNIGHT: 2.0, chess.BISHOP: 2.0, chess.ROOK: 3.0, chess.QUEEN: 5.0, chess.KING: 0.0}
DEFENSE_WEIGHTS = {chess.PAWN: 1.0, chess.KNIGHT: 1.5, chess.BISHOP: 1.0, chess.ROOK: 1.0, chess.QUEEN: 1.0, chess.KING: 0.0}
OPEN_FILE_PENALTY = 3.0
HALF_OPEN_FILE_PENALTY = 1.5
MISSING_SHIELD_PENALTY = 1.5
QUEEN_PROXIMITY_MAX = 4.0  # bonus danger when the enemy queen is adjacent, decaying with distance


@dataclass(frozen=True)
class KingSafety:
    color: chess.Color
    attack: float  # weighted enemy attacks on the king zone
    defense: float  # weighted own attacks on the king zone
    files: float  # open / half-open file penalties around the king
    shield: float  # missing pawn-shield penalty
    queen: float  # enemy queen proximity
    attackers: int  # distinct enemy pieces hitting the zone

    @property
    def danger(self) -> float:
        return self.attack - self.defense + self.files + self.shield + self.queen


def _files_around(king: chess.Square) -> list[int]:
    f = chess.square_file(king)
    return [x for x in (f - 1, f, f + 1) if 0 <= x <= 7]


def king_safety(board: chess.Board, color: chess.Color) -> KingSafety:
    king = board.king(color)
    if king is None:
        raise ValueError("no king for that colour")
    enemy = not color
    zone = king_zone(board, color)

    attack, attackers = 0.0, set()
    defense = 0.0
    for sq, piece in board.piece_map().items():
        if piece.piece_type == chess.KING:
            continue
        hits = len(board.attacks(sq) & zone)
        if not hits:
            continue
        if piece.color == enemy:
            attack += ATTACK_WEIGHTS[piece.piece_type] * hits
            attackers.add(sq)
        else:
            defense += DEFENSE_WEIGHTS[piece.piece_type] * hits
    # attacks scale super-linearly with the number of attackers (one piece alone rarely mates)
    attack *= {0: 0.0, 1: 0.5, 2: 1.0}.get(len(attackers), 1.0 + 0.25 * (len(attackers) - 2))

    files = 0.0
    own_pawns, enemy_pawns = board.pieces(chess.PAWN, color), board.pieces(chess.PAWN, enemy)
    for f in _files_around(king):
        mask = chess.SquareSet(chess.BB_FILES[f])
        if not (own_pawns & mask):
            files += OPEN_FILE_PENALTY if not (enemy_pawns & mask) else HALF_OPEN_FILE_PENALTY

    shield = 0.0
    forward = 1 if color == chess.WHITE else -1
    kr = chess.square_rank(king)
    home = kr == (0 if color == chess.WHITE else 7)
    if home:  # the shield only makes sense while the king is on its back rank
        for f in _files_around(king):
            front = [chess.square(f, kr + forward * d) for d in (1, 2)]
            if not any(sq in own_pawns for sq in front):
                shield += MISSING_SHIELD_PENALTY

    queen = 0.0
    for q in board.pieces(chess.QUEEN, enemy):
        dist = chess.square_distance(q, king)
        queen = max(queen, QUEEN_PROXIMITY_MAX * max(0.0, (5 - dist) / 4))

    return KingSafety(color, attack, defense, files, shield, queen, len(attackers))
