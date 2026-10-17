"""Tension, loose pieces and overloaded defenders, built on SEE.

Duties a defender can carry (in the order the build plan lists them):

1. ``defends``  - without it, a friendly piece would become en prise
   (SEE > 0 for the opponent). Latent if the piece is not attacked yet.
2. ``mate``     - without it, the opponent would have mate in one.
3. ``blockade`` - it stands on the stop square of an enemy passed pawn.

A piece is *overloaded* when it has two or more duties and at least one is
under attack right now. Expect false positives: SEE ignores checks and
zwischenzugs, so tune against engine-found tactics before going on air.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from .control import passed_pawns
from .see import effective_attackers, see, value


@dataclass(frozen=True)
class EnPrise:
    square: chess.Square
    piece: chess.Piece
    gain: int  # material the opponent wins by capturing


def en_prise(board: chess.Board, color: chess.Color) -> list[EnPrise]:
    """`color`'s pieces that the opponent can win by static exchange."""
    out = []
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        gain = see(board, sq, not color)
        if gain > 0:
            out.append(EnPrise(sq, piece, gain))
    return sorted(out, key=lambda e: -e.gain)


def loose_pieces(board: chess.Board, color: chess.Color) -> list[chess.Square]:
    """Non-king pieces with no effective defender."""
    return [
        sq
        for sq, piece in board.piece_map().items()
        if piece.color == color and piece.piece_type != chess.KING and not effective_attackers(board, color, sq)
    ]


@dataclass(frozen=True)
class Duty:
    kind: str  # "defends" | "mate" | "blockade"
    target: chess.Square  # the defended piece, the mating square, or the pawn blockaded
    stake: float  # material at risk if the duty is abandoned
    under_attack: bool


@dataclass
class Load:
    square: chess.Square
    piece: chess.Piece
    duties: list[Duty] = field(default_factory=list)

    @property
    def score(self) -> float:
        return sum(d.stake for d in self.duties)

    @property
    def overloaded(self) -> bool:
        return len(self.duties) >= 2 and any(d.under_attack for d in self.duties)


MATE_STAKE = 50.0  # material-equivalent stake for guarding against mate


def _without(board: chess.Board, square: chess.Square) -> chess.Board:
    b = board.copy(stack=False)
    b.remove_piece_at(square)
    return b


def _mate_in_one_squares(board: chess.Board, attacker: chess.Color) -> set[chess.Square]:
    b = board.copy(stack=False)
    b.turn = attacker
    b.ep_square = None
    if b.was_into_check() or not b.is_valid():
        return set()  # the side not to move would be in check: not a legal position
    mates = set()
    for move in b.legal_moves:
        b.push(move)
        if b.is_checkmate():
            mates.add(move.to_square)
        b.pop()
    return mates


def piece_loads(board: chess.Board, color: chess.Color) -> list[Load]:
    """Duties carried by each of `color`'s non-king pieces (only pieces with duties)."""
    enemy = not color
    baseline_mates = _mate_in_one_squares(board, enemy)
    stop_targets = {}
    forward = -8 if enemy == chess.BLACK else 8
    for p in passed_pawns(board, enemy):
        if 0 <= p + forward < 64:
            stop_targets[p + forward] = p

    loads = []
    for sq, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        load = Load(sq, piece)
        reduced = _without(board, sq)

        # 1. defends: friendly pieces whose SEE flips to losing without this piece
        for tsq in board.attacks(sq):
            target = board.piece_at(tsq)
            if target is None or target.color != color or target.piece_type == chess.KING:
                continue
            if see(board, tsq, enemy) > 0:
                continue  # already lost; this piece is not holding it
            lost = see(reduced, tsq, enemy)
            if lost > 0:
                attacked = bool(effective_attackers(board, enemy, tsq))
                load.duties.append(Duty("defends", tsq, float(lost), attacked))
            elif not effective_attackers(board, enemy, tsq) and len(effective_attackers(board, color, tsq)) == 1:
                # sole defender of an unattacked piece: a latent duty
                load.duties.append(Duty("defends", tsq, float(value(target.piece_type)), False))

        # 2. mate: mates in one that only exist without this piece
        for msq in _mate_in_one_squares(reduced, enemy) - baseline_mates:
            load.duties.append(Duty("mate", msq, MATE_STAKE, bool(effective_attackers(board, enemy, msq))))

        # 3. blockade: standing on an enemy passer's stop square
        if sq in stop_targets:
            pawn = stop_targets[sq]
            rank_from_promo = 7 - chess.square_rank(pawn) if enemy == chess.WHITE else chess.square_rank(pawn)
            stake = 1.0 + 3.0 / max(rank_from_promo, 1)  # closer to promotion, higher stake
            load.duties.append(Duty("blockade", pawn, stake, bool(effective_attackers(board, enemy, sq))))

        if load.duties:
            loads.append(load)
    return sorted(loads, key=lambda l: -l.score)


def overloaded(board: chess.Board, color: chess.Color) -> list[Load]:
    return [l for l in piece_loads(board, color) if l.overloaded]


def describe(load: Load) -> str:
    """Plain-language caption, e.g. "Black's knight on d7 is guarding e5 and the back rank"."""
    side = "White" if load.piece.color == chess.WHITE else "Black"
    name = chess.piece_name(load.piece.piece_type)
    parts = []
    for d in load.duties:
        if d.kind == "defends":
            parts.append(chess.square_name(d.target))
        elif d.kind == "mate":
            parts.append(f"against mate on {chess.square_name(d.target)}")
        else:
            parts.append(f"the blockade of the {chess.square_name(d.target)} pawn")
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{side}'s {name} on {chess.square_name(load.square)} is guarding {joined}"
