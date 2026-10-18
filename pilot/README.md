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
| Human move model | `human.py`, `maia.py` | Maia-3 adapter (full move distribution, human WDL, think-time head); clock correction hook unfitted |
| Human model baseline evaluation (plan step 1) | `scripts/eval_human_model.py` | Done on 25 elite games; temperature probe exploratory |
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

`EngineSoftmaxModel` remains as a placeholder baseline (`--human-model softmax`).
No difficulty number is fit for air until the human model is calibrated on a
held-out elite set.

## Usage

```bash
cd pilot
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pip install -e ".[maia]"                    # optional: Maia-3 (torch + maia3, AGPL-3.0)
.venv/bin/pytest                                      # engine tests need Stockfish (STOCKFISH=/path)
MAIA3_DOWNLOAD=1 .venv/bin/pytest tests/test_maia.py  # real-weight tests (downloads maia3-5m, 21 MB)

chessmetrics records games.pgn > moves.csv            # per-move normalised records
chessmetrics structure "<FEN>"                         # structural snapshot as JSON
chessmetrics analyse games.pgn --depth 12              # engine + difficulty + EPA report, one JSON line per game
                                                       # (--human-model maia3-5m by default, or softmax)
```

## Test data

`tests/fixtures/broadcast/olympiad2026_open_2700.pgn`: 25 classical games from
the 46th Chess Olympiad (Samarkand 2026, Open) where both players are rated
2700+, with relay clocks (`%clk`) and Lichess evals (`%eval`). Re-fetch or pull
other events with:

```bash
python scripts/fetch_broadcast.py --tour n1pPI5Q0 --min-elo 2700 \
    --time-control "40/5400+30:1800+30" -o tests/fixtures/broadcast/olympiad2026_open_2700.pgn
```

Broadcast PGNs have no `TimeControl` header (hence `--time-control`), their
`Site` is the venue rather than a game URL, relay clocks are noisy on move 1
(flagged as `clock_anomaly`), and stage bonuses such as +30 min after move 40
often appear a move or two late (detected from the clock data).

## Early findings on elite games

- **EPA is noisy at depth 12.** In Rapport–Abdusattorov (0-1) the swing moments
  look right (17.g4 is the biggest, −0.32), but per-move search inconsistency
  shows up as spurious EPA on moves that hold, e.g. the winner "concedes" 0.81
  points across the game while being "gifted" 1.28. Needs deeper or node-based
  search, or smoothing, before EPA is shown per move.
- **The placeholder human model is far too sharp for 2700s.** P(blunder) is ~0
  and find rates are near 1 almost everywhere, so expected accuracy ≈ 0.94–0.99.

### Maia-3 (maia3-5m) on the 25 elite games

- **Move prediction transfers well to elite OTB classical.** After ply 16
  (1,992 positions): top-1 match 52.6%, mean log-likelihood −1.44 vs −3.26 for
  uniform. The paper reports 57.1% top-1 on (online) Lichess games.
- **Overconfident on its favourite moves.** Predicted 0.65–0.85 is played only
  0.55–0.77 of the time: elites deviate from the "natural" move more than Maia
  expects. Low-probability bins are well calibrated.
- **Shifting ratings up does not fix it** (+200: LL −1.42, +800: −1.93).
  **Temperature ~1.1 does most of it** (LL −1.43, bins mostly within 1–4 pts).
  In-sample on 25 games only, so not applied by default: fit on a held-out
  event before fixing a value.
- **Difficulty now varies meaningfully.** In Rapport–Abdusattorov, before
  17.g4 (the decisive swing) Maia gives 2700s a 9% find rate and 91%
  P(blunder). But some winning positions read as near-impossible (25…Bf5: 1%
  find rate, yet found), and accuracy over expected is +0.40 for Black: both
  consistent with the overconfidence above, plus depth-12 noise in the good set.

### Maia-3 notes

- Released weights take no clock input (`include_time_info=False`); clock
  effects need the correction layer (`Maia3Model(clock_temperature=...)`).
- Trained on Lichess online games, Jan 2023 – Jul 2025.
- Code and (per the model card) weights are **AGPL-3.0**. Running Maia-3
  in-process inside a network-facing broadcast service likely brings AGPL
  obligations to the combined work; check before production.

## Conventions

- Every `WDL` is from White's point of view; `MoveEval.cp` / `.mate` are from the mover's.
- Every engine record carries the engine id and search limit; changing either changes the cache key.
- Thresholds (`TAU`, `BETA`, `GIFT_THRESHOLD`, king-safety and key-square weights) are module constants: tune once, publish, freeze.

## Performance note

All-legal-move MultiPV at depth 12, single thread: about 2 s per position on the
dev container. Live use will need the plan's latency strategy (precompute on the
opponent's time, top-N candidates in fast time controls, more threads).
