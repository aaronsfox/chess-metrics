"""Integration tests against a real Stockfish. Skipped when no binary is found."""

import os
import shutil
from pathlib import Path

import chess
import chess.pgn
import pytest

from chessmetrics.difficulty import good_enough, only_move_chain
from chessmetrics.engine import DEFAULT_STOCKFISH, AnalysisCache, EngineAnalyzer, SearchLimit
from chessmetrics.human import EngineSoftmaxModel, PlayerContext
from chessmetrics.report import analyse_game

STOCKFISH = os.environ.get("STOCKFISH", DEFAULT_STOCKFISH if Path(DEFAULT_STOCKFISH).exists() else shutil.which("stockfish"))
pytestmark = [pytest.mark.engine, pytest.mark.skipif(not STOCKFISH, reason="no Stockfish binary")]
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def analyzer(tmp_path_factory):
    cache = AnalysisCache(tmp_path_factory.mktemp("cache") / "a.sqlite")
    with EngineAnalyzer(STOCKFISH, SearchLimit(depth=8), cache) as a:
        yield a


def test_all_legal_moves_analysed_and_cached(analyzer):
    board = chess.Board()
    a = analyzer.analyse(board)
    assert len(a.moves) == 20
    assert analyzer.engine_id.lower().startswith("stockfish")
    es = [m.expected(chess.WHITE) for m in a.moves]
    assert es == sorted(es, reverse=True)
    assert analyzer.analyse(board).moves == a.moves  # served from cache


def test_mate_in_one_is_the_only_good_move(analyzer):
    # Black's two knights and extra pawns win unless White finds Ra8#
    board = chess.Board("6k1/5ppp/8/1n2n3/1pppp3/8/5PPP/R5K1 w - - 0 1")
    a = analyzer.analyse(board)
    assert good_enough(a) == {chess.Move.from_uci("a1a8")}
    assert a.best.mate == 1


def test_terminal_evaluation(analyzer):
    mated = chess.Board("r1bqkb1r/pppp1Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR b KQkq - 0 4")
    assert analyzer.evaluate(mated).white == 1.0


def test_only_move_chain(analyzer):
    # Black to move must stop Ra8#: the chain should start with an only move
    board = chess.Board("6k1/5ppp/8/8/8/8/r4PPP/R5K1 b - - 0 1")
    chain = only_move_chain(board, analyzer, EngineSoftmaxModel(), PlayerContext(2000, 2000), max_good=3, max_plies=4)
    assert 0.0 <= chain.hold_probability <= 1.0
    for link in chain.links:
        assert 0.0 <= link.find_rate <= 1.0


def test_game_report(analyzer):
    with open(FIXTURES / "scholars.pgn") as f:
        game = chess.pgn.read_game(f)
    report = analyse_game(game, analyzer, EngineSoftmaxModel())
    moves = report["moves"]
    assert [m["san"] for m in moves][-1] == "Qxf7#"
    nf6 = moves[5]
    assert nf6["epa"] < -0.3 and not nf6["in_good_set"]  # allowing mate is a huge loss
    assert any(s["san"] == "Nf6" for s in report["swings"])
    # EPA ledger telescopes to the final result for each player
    white = report["summary"]["white"]
    assert white["net"] == pytest.approx(1.0 - moves[0]["e_before"], abs=1e-9)


def test_tiered_analysis_deep_on_candidates_only(analyzer):
    board = chess.Board()
    cand = {chess.Move.from_uci("a2a3"), chess.Move.from_uci("e2e4")}
    a = analyzer.analyse_tiered(board, cand, scan_limit=SearchLimit(depth=6), scan_top_n=2)
    deep = {m.move for m in a.moves if m.deep}
    assert cand <= deep and len(deep) <= 4  # candidates + scan top 2
    assert len(a.moves) == 20  # every legal move is still classified
    assert a.best.deep and a.scan_limit == SearchLimit(depth=6)
    assert all(m.deep for m in a.moves[:len(deep)])  # deep moves first


class _StubHuman:
    def move_probs(self, board, ctx, analysis=None):
        moves = sorted(board.legal_moves, key=chess.Move.uci)
        return {m: (0.5 if i == 0 else 0.5 / (len(moves) - 1)) for i, m in enumerate(moves)}


def test_game_report_tiered_telescopes(analyzer):
    with open(FIXTURES / "scholars.pgn") as f:
        game = chess.pgn.read_game(f)
    report = analyse_game(game, analyzer, _StubHuman(), scan_limit=SearchLimit(depth=6))
    assert report["scan_limit"] == "d6-nNone"
    nf6 = report["moves"][5]
    assert nf6["epa"] < -0.3
    for color, final in (("white", 1.0), ("black", 0.0)):
        s = report["summary"][color]
        start = report["moves"][0]["e_before"] if color == "white" else 1 - report["moves"][0]["e_before"]
        assert s["net"] == pytest.approx(final - start, abs=0.02)  # draw value 0.5: E_black ~ 1 - E_white


def test_wdl_model_is_flatter_and_keyed_separately(tmp_path):
    cache = AnalysisCache(tmp_path / "w.sqlite")
    board = chess.Board("6k1/5ppp/8/8/8/8/2N2PPP/6K1 w - - 0 1")  # a knight up
    with EngineAnalyzer(STOCKFISH, SearchLimit(depth=8), cache) as sf, \
            EngineAnalyzer(STOCKFISH, SearchLimit(depth=8), cache, wdl_model="lichess") as li:
        assert sf.engine_id != li.engine_id
        e_sf = sf.analyse(board).best.expected(chess.WHITE)
        e_li = li.analyse(board).best.expected(chess.WHITE)
    assert e_li < e_sf  # Stockfish's own WDL is far more confident
