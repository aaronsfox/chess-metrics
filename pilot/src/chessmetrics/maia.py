"""Maia-3 adapter: the production human move model.

Wraps the Maia-3 inference code (https://github.com/CSSLab/maia3, AGPL-3.0)
in-process rather than over UCI, because the UCI engine only reports up to 20
candidate moves and no policy probabilities, while find rate and pressure need
the full distribution over legal moves.

What the released checkpoints give us, per position:

- ``probs``: P(move) over every legal move, conditioned on both ratings and on
  the last 8 positions of the game.
- ``wdl``: the value head's *human-game* outcome prediction for these ratings.
  A ready-made rating-only HWP baseline; it knows nothing about the clock.
- ``ponder``: the raw output of the think-time head. Units are undocumented,
  so it is exposed for validation work only.

The released weights are not clock-conditioned (``include_time_info=False``).
Clock effects come from `clock_temperature`, the build plan's "lightweight
correction layer"; it defaults to none and must be fitted on Lichess data.

Requires the optional ``maia`` extra (torch + maia3).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

import chess

from .engine import PositionAnalysis
from .human import PlayerContext
from .points import WDL

ClockTemperature = Callable[[PlayerContext], float]


@dataclass(frozen=True)
class MaiaPrediction:
    probs: dict[chess.Move, float]
    wdl: WDL  # White's point of view
    ponder: float


class Maia3Model:
    """`HumanMoveModel` backed by a Maia-3 checkpoint.

    `model` is a Maia-3 alias ("maia3-5m", "maia3-23m", "maia3-79m"). Pass
    `checkpoint_path` to load local weights instead of downloading from
    Hugging Face, or `weights=None` with `random_init=True` for tests that only
    exercise the plumbing.
    """

    def __init__(
        self,
        model: str = "maia3-5m",
        checkpoint_path: str | None = None,
        device: str = "cpu",
        use_history: bool = True,
        clock_temperature: ClockTemperature | None = None,
        random_init: bool = False,
    ) -> None:
        import torch
        from maia3 import dataset, uci, utils
        from maia3.model_registry import resolve_checkpoint_path

        self._torch, self._dataset, self._utils = torch, dataset, utils
        argv = ["--model", model, "--device", device, "--no-use-amp"]
        self.cfg = uci.parse_args(argv)
        if random_init:
            from maia3.models import MAIA3Model

            self.net = MAIA3Model(self.cfg).to(device).eval()
        else:
            self.cfg.checkpoint_path = checkpoint_path or resolve_checkpoint_path(self.cfg.model_spec)
            self.net = uci.load_model(self.cfg)
        self.model_id = self.cfg.model_spec.name if self.cfg.model_spec else model
        self.use_history = use_history
        self.clock_temperature = clock_temperature
        self._moves = utils.get_all_possible_moves()
        self._index = {m: i for i, m in enumerate(self._moves)}

    # -- HumanMoveModel ---------------------------------------------------

    def move_probs(
        self, board: chess.Board, ctx: PlayerContext, analysis: PositionAnalysis | None = None
    ) -> dict[chess.Move, float]:
        return self.predict(board, ctx).probs

    # -- richer API -------------------------------------------------------

    def predict(self, board: chess.Board, ctx: PlayerContext) -> MaiaPrediction:
        return self.predict_batch([board], [ctx])[0]

    def predict_batch(self, boards: Sequence[chess.Board], ctxs: Sequence[PlayerContext]) -> list[MaiaPrediction]:
        """Batched inference. Each board's move stack supplies its history."""
        torch = self._torch
        if not boards:
            return []
        tokens = torch.stack([self._tokens(b) for b in boards]).to(self.cfg.device)
        self_elo = torch.tensor([int(c.rating) for c in ctxs], dtype=torch.long, device=self.cfg.device)
        oppo_elo = torch.tensor([int(c.opp_rating) for c in ctxs], dtype=torch.long, device=self.cfg.device)
        with torch.no_grad():
            logits_move, logits_value, ponder = self.net(tokens, self_elo, oppo_elo)
        out = []
        for i, (board, ctx) in enumerate(zip(boards, ctxs)):
            out.append(MaiaPrediction(
                probs=self._legal_probs(board, logits_move[i].float(), ctx),
                wdl=self._wdl(board, logits_value[i].float()),
                ponder=float(ponder[i]),
            ))
        return out

    # -- internals ----------------------------------------------------------

    def _history(self, board: chess.Board) -> deque:
        """Tokenised positions ending at `board`, oldest first, at most cfg.history."""
        hist: deque = deque(maxlen=self.cfg.history)
        if self.use_history and board.move_stack:
            replay = board.root()
            moves = board.move_stack
            start = max(0, len(moves) - (self.cfg.history - 1))
            for move in moves[:start]:
                replay.push(move)
            hist.append(self._dataset.tokenize_board(replay))
            for move in moves[start:]:
                replay.push(move)
                hist.append(self._dataset.tokenize_board(replay))
        else:
            hist.append(self._dataset.tokenize_board(board))
        return hist

    def _tokens(self, board: chess.Board):
        # time fields are ignored by the released (time-agnostic) checkpoints
        return self._dataset.get_historical_tokens(
            self._history(board), self.cfg, base=0.0, inc=0.0, clk_left_before=0.0, clk_ponder=0.0)

    def _legal_probs(self, board: chess.Board, logits, ctx: PlayerContext) -> dict[chess.Move, float]:
        torch = self._torch
        legal = list(board.legal_moves)
        if not legal:
            return {}
        # the model sees the board from the side to move, so Black's moves are mirrored
        keys = [m.uci() if board.turn == chess.WHITE else self._utils.mirror_move(m.uci()) for m in legal]
        idx = torch.tensor([self._index[k] for k in keys], device=logits.device)
        temp = self.clock_temperature(ctx) if self.clock_temperature else 1.0
        probs = torch.softmax(logits[idx] / temp, dim=-1).tolist()
        return dict(zip(legal, probs))

    @staticmethod
    def _wdl(board: chess.Board, value_logits) -> WDL:
        # value labels are [loss, draw, win] for the side to move
        loss, draw, win = (float(x) for x in value_logits.double().softmax(-1).tolist())
        total = loss + draw + win
        loss, draw, win = loss / total, draw / total, win / total
        if board.turn == chess.WHITE:
            return WDL(win, draw, loss)
        return WDL(loss, draw, win)
