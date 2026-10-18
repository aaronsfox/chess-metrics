"""Maia-3 adapter tests.

Plumbing tests use randomly initialised weights (needs torch + maia3, no
download). Behaviour tests need real weights: set MAIA3_CHECKPOINT to a local
maia3-5m.pt, or MAIA3_DOWNLOAD=1 to fetch it from Hugging Face.
"""

import math
import os

import chess
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("maia3")

from chessmetrics.human import PlayerContext  # noqa: E402
from chessmetrics.maia import Maia3Model  # noqa: E402

pytestmark = pytest.mark.maia
CTX = PlayerContext(1500, 1500)


def _board(*sans: str) -> chess.Board:
    b = chess.Board()
    for san in sans:
        b.push_san(san)
    return b


@pytest.fixture(scope="module")
def random_model():
    torch.manual_seed(0)
    return Maia3Model("maia3-5m", random_init=True, use_history=False)


def _entropy(probs):
    return -sum(p * math.log(p) for p in probs.values() if p > 0)


@pytest.mark.parametrize("board", [chess.Board(), _board("e4", "c5", "Nf3"), _board("e4", "e5", "Ke2", "Ke7")])
def test_distribution_covers_exactly_the_legal_moves(random_model, board):
    probs = random_model.move_probs(board, CTX)
    assert set(probs) == set(board.legal_moves)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-5)
    assert min(probs.values()) >= 0


@pytest.mark.parametrize("board", [_board("e4", "e5", "Nf3"), _board("d4", "Nf6")])
def test_argmax_matches_maia_uci_path(random_model, board):
    """Our move-index mapping (incl. mirroring for Black) agrees with Maia's own engine."""
    from maia3.uci import Maia3UCIEngine

    eng = Maia3UCIEngine(random_model.cfg)
    eng.model = random_model.net
    eng.temperature = 0
    eng.board = board.copy()
    eng._reset_history()
    theirs, _ = eng.score_moves()
    probs = random_model.move_probs(board, CTX)
    assert max(probs, key=probs.get) == theirs


def test_history_uses_last_eight_positions():
    torch.manual_seed(0)
    m = Maia3Model("maia3-5m", random_init=True, use_history=True)
    from maia3.dataset import tokenize_board

    board = _board("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7")
    hist = list(m._history(board))
    assert len(hist) == m.cfg.history
    assert torch.equal(hist[-1], tokenize_board(board))
    seven_back = board.copy()
    for _ in range(7):
        seven_back.pop()
    assert torch.equal(hist[0], tokenize_board(seven_back))


def test_wdl_is_white_pov(random_model):
    black_to_move = _board("e4")
    pred = random_model.predict(black_to_move, CTX)
    tokens = random_model._tokens(black_to_move).unsqueeze(0)
    elo = torch.tensor([1500])
    _, value, _ = random_model.net(tokens, elo, elo)
    loss, draw, win = value[0].double().softmax(-1).tolist()  # side to move = Black
    assert pred.wdl.black == pytest.approx(win, abs=1e-6)
    assert pred.wdl.white == pytest.approx(loss, abs=1e-6)


def test_clock_temperature_flattens(random_model):
    hot = Maia3Model.__new__(Maia3Model)
    hot.__dict__.update(random_model.__dict__)
    hot.clock_temperature = lambda ctx: 3.0
    board = chess.Board()
    assert _entropy(hot.move_probs(board, CTX)) > _entropy(random_model.move_probs(board, CTX))


def test_batch_matches_single(random_model):
    boards = [chess.Board(), _board("e4")]
    batch = random_model.predict_batch(boards, [CTX, CTX])
    for b, pred in zip(boards, batch):
        single = random_model.predict(b, CTX)
        for move, p in single.probs.items():
            assert pred.probs[move] == pytest.approx(p, abs=1e-5)


# --- real weights ---------------------------------------------------------------

def _real_model():
    path = os.environ.get("MAIA3_CHECKPOINT")
    if not path and not os.environ.get("MAIA3_DOWNLOAD"):
        pytest.skip("set MAIA3_CHECKPOINT or MAIA3_DOWNLOAD=1 for real-weight tests")
    return Maia3Model("maia3-5m", checkpoint_path=path)


@pytest.fixture(scope="module")
def real_model():
    return _real_model()


def test_real_opening_moves_are_human(real_model):
    probs = real_model.move_probs(chess.Board(), CTX)
    top3 = {m.uci() for m in sorted(probs, key=probs.get, reverse=True)[:3]}
    assert {"e2e4", "d2d4"} & top3


def test_real_stronger_players_find_mate_more_often(real_model):
    board = _board("e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6")  # Qxf7# available
    mate = board.parse_san("Qxf7#")
    weak = real_model.move_probs(board, PlayerContext(1000, 1000))[mate]
    strong = real_model.move_probs(board, PlayerContext(2600, 2600))[mate]
    assert strong > weak
    assert strong > 0.5
