# Roadmap

## Core idea

Being the best manager in the game comes down to four things you do every round:

1. **Predict** how many fantasy points (PIR) each player will score, including the range of outcomes and not only the average.
2. **Optimize** the squad, starters, captain and transfers under the rules and budget, looking several rounds ahead.
3. **React** to news such as injuries, rest days and rotation changes before the deadline.
4. **Grow team value** by buying players before their price rises.

The "brain" is a statistical model plus a mathematical optimizer. The LLM (Claude) sits on top: it reads news, answers questions, and explains the picks. It does not choose the team on its own. An LLM guessing lineups would lose to a good projection model.

Every phase ends with something measurable, and nothing ships unless it beats the previous baseline in a backtest.

---

## Phase 0: Project setup ✅

Package layout, tests, lint, CI, settings, and the rules as data in `rules.py`.

## Phase 1: Pin down the rules (1–2 days)

The optimizer is only as good as its constraints. Confirm each of these in the in-game rules and update `rules.py`:

- [ ] Transfers per round, and whether double rounds and the playoffs change the limit
- [ ] Captain: can it be switched between games during a round?
- [ ] Lineup: 5 starters plus a sixth man? Which position constraints apply to starters?
- [ ] Bench scoring at 50%, and whether automatic substitutions exist for players who don't play
- [ ] Head coach: how a coach scores points
- [ ] Maximum number of players from one club
- [ ] Price changes: when they happen and how big they can be
- [ ] Deadline: first game of the round, or a lock per game

**Done when:** `rules.py` has no UNVERIFIED fields and `docs/GAME_RULES.md` summarizes the rules with sources.

## Phase 2: Data (1–2 weeks)

| Source | What | How |
|---|---|---|
| EuroLeague public API (`api-live.euroleague.net`), or the [`euroleague-api`](https://github.com/giasemidis/euroleague_api) Python package | Box scores, PIR, minutes, schedules, play-by-play, every season since about 2007 | `src/elfantasy/data/euroleague.py` |
| Fantasy game's own endpoints (find them in the browser's network tab) | Prices, positions, ownership %, your squad | `src/elfantasy/data/fantasy.py`, using your own session. Check the terms of use first. |
| News: club sites, EuroLeague injury reports, journalists | Injuries, probable absences | Phase 7 |

- Store raw JSON in `data/raw/` and clean tables in a local **DuckDB** or Parquet files in `data/processed/`.
- Core tables: `games`, `player_games` (one row per player per game), `players`, `teams`, `fantasy_prices` (one row per player per round), `schedule`.
- `elf data sync` pulls in whatever is new and is safe to re-run.

**Done when:** every game from the last 5+ seasons is stored, our PIR matches the official PIR, and prices are snapshotted every round.

## Phase 3: Backtest harness, built before any model (1 week)

This is the most important piece of infrastructure. It replays a past season round by round and gives each strategy **only the information that was available before that round's deadline**. Without it, there is no way to tell whether a change actually helps.

- Simulates the whole game: budget, prices, transfers, captain, bench multipliers.
- Measures projection error (MAE and calibration) per round, and the season's total fantasy points.
- Baseline strategies to beat: "pick the highest season-average PIR per credit" and "last 5 games average".

**Done when:** `elf backtest --season 2025 --strategy baseline` produces a season score.

## Phase 4: Projections v1 (2 weeks)

Predict each player's points for each game.

- Split it as **expected minutes × PIR per minute**. Minutes are where most of the uncertainty is.
- Features: recent form (exponentially weighted), season and career rates, home or away, opponent's defense (PIR allowed per position), rest days and back-to-backs (EuroLeague has double-game weeks), team pace, and missing teammates (more usage for the players who remain).
- Models: start with weighted averages, then move to a gradient-boosted model such as LightGBM.
- Output a **distribution** (for example the 10th, 50th and 90th percentiles), not just a mean. Captain picks benefit from upside.
- Head coach projection, built from the coach scoring rule and a team win-probability model.

**Done when:** it beats both baselines on MAE across at least 2 held-out seasons.

## Phase 5: Optimizer (2 weeks)

Pick the squad as an integer linear program using PuLP or OR-Tools, the same approach as the strongest fantasy football (FPL) solvers.

- Variables: squad, starters, sixth man, captain, and transfers for each round over a horizon of H rounds (3–5).
- Objective: maximize expected points over the horizon, with future rounds counting less, while respecting the transfer limit.
- Constraints: budget, positions, club limit, transfers, and lineup rules.
- Account for double-game weeks: a player with 2 games that week is worth roughly twice as much.
- Captain choice: favor the player with the best upside, not only the best mean.
- Tools: "best squad from scratch", "best transfers for this week", "what if I sell X".

**Done when:** the backtested season score clearly beats the Phase 3 baselines, and runs fast enough to use before every deadline.

## Phase 6: Price and team-value model (1 week)

- Work out how prices move from the price history (actual points against expected points).
- Predict next round's price changes and add a value-growth term to the optimizer. This matters most early in the season.

**Done when:** we predict the direction of price changes with at least 70% accuracy, and the backtest score improves.

## Phase 7: The AI assistant layer (2 weeks)

Claude with tool use, built on the Anthropic SDK.

- **Tools for Claude to call:** `get_projections`, `optimize_squad`, `suggest_transfers`, `compare_players`, `get_schedule`, `get_news`, and `explain_pick`.
- **News reading:** collect injury and lineup news, have Claude turn it into structured statuses (out, doubtful, limited minutes), and feed those into the projections.
- **Conversation:** questions like "Should I captain Vezenkov or Nunn this week?" get answers backed by numbers.
- A weekly report: recommended transfers, captain, lineup, risks, and the reasoning behind each.

**Done when:** you can run the whole round from one chat, and the news layer measurably improves projections for players marked doubtful.

## Phase 8: Interface and automation (1 week)

- Start with the CLI, then add a small web UI with Streamlit or FastAPI plus a simple frontend.
- Scheduled job: sync data, update projections, and send you a recommendation N hours before each deadline.
- Optional: notify you on your phone or Telegram when there's late injury news.

## Phase 9: Live season loop (ongoing)

- After every round, compare predictions with actual results, track calibration, and retrain.
- Keep a log of every decision with the projected and actual points, to learn where the model goes wrong.
- Compare our score each round with the top managers' scores.

---

## Suggested order

Phases 1 → 2 → 3 → 4 → 5 make up the minimum product that is already a strong manager. Phases 6–8 add an edge and make it easier to use. The season is already running, so a good plan is to have Phases 1–3 working within a few weeks and use projections v1 with the optimizer for the rest of the season.

## Tech choices

| Need | Choice |
|---|---|
| Language | Python 3.12 |
| Storage | DuckDB / Parquet |
| Modeling | pandas, scikit-learn, LightGBM |
| Optimization | PuLP (CBC solver), with OR-Tools as an alternative |
| LLM | Anthropic API (Claude) with tool use |
| UI | Typer CLI, then Streamlit |
