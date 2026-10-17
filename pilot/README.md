# chessmetrics

Broadcast explainability metrics for chess, following the *Chess Explainability
Metrics: Build Plan*. Everything is expressed in expected points
(`E = P(win) + draw_value · P(draw)`), with a configurable points function
(standard, Armageddon, must-win).

## Status against the build plan

| Plan component | Module | Status |
| --- | --- | --- |
| Parse and normalise (FEN, ratings, time control, clocks before/after, result) | `pgn.py` | Done |
| Engine pass: MultiPV over all legal moves (or a candidate set), cp + WDL, fixed depth/nodes | `engine.py` | Done |
| Cache keyed by position + engine version + limit | `engine.AnalysisCache` (SQLite) | Done |
| Points function, incl. Armageddon / must-win | `points.py` | Done |
| Human move model interface | `human.py` | Interface + placeholder (`EngineSoftmaxModel`); Maia-2 adapter next |
| Good-enough set, find rate, P(blunder), expected loss | `difficulty.py` | Done (τ = 0.04, β = 0.15, untuned) |
| Only-move chains | `difficulty.only_move_chain` | Done |
| EPA, swing moments, seized / gifted / missed ledger | `epa.py` | Done; move vs clock EPA split awaits HWP |
| Expected vs actual accuracy (accuracy over expected) | `report.py` | Done; opening exclusion is `--skip-plies` until a book is wired in |
| SEE, pin-aware attackers, x-rays | `structure/see.py` | Done |
| Square control, space, outposts, key squares | `structure/control.py` | Done |
| En prise, loose pieces, overload (defends / mate-in-1 / blockade duties) | `structure/tension.py` | Done |
| King safety with breakdown | `structure/king_safety.py` | Done |
| Connectivity network | — | Not started (lowest priority in plan) |
| Contextual piece value | — | Not started (needs engine; easy next add) |
| Human-aware win probability (HWP) | — | Not started: needs Lichess training data |
| Feature store, live service | — | Not started |
| Validation framework | — | Not started |

Numbers from `EngineSoftmaxModel` are a stand-in so the pipeline runs end to
end. They are **not** fit for air until the real human model is calibrated.

## Usage

```bash
cd pilot
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                                      # engine tests need Stockfish (STOCKFISH=/path)

chessmetrics records games.pgn > moves.csv            # per-move normalised records
chessmetrics structure "<FEN>"                         # structural snapshot as JSON
chessmetrics analyse games.pgn --depth 12              # engine + difficulty + EPA report, one JSON line per game
```

## Conventions

- Every `WDL` is from White's point of view; `MoveEval.cp` / `.mate` are from the mover's.
- Every engine record carries the engine id and search limit; changing either changes the cache key.
- Thresholds (`TAU`, `BETA`, `GIFT_THRESHOLD`, king-safety and key-square weights) are module constants: tune once, publish, freeze.

## Performance note

All-legal-move MultiPV at depth 12, single thread: about 2 s per position on the
dev container. Live use will need the plan's latency strategy (precompute on the
opponent's time, top-N candidates in fast time controls, more threads).
