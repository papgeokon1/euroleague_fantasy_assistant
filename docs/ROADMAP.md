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

## Phase 0: Project setup 

Package layout, tests, lint, CI, settings, and the rules as data in `rules.py`.

## Phase 1: Pin down the rules 

The rules are in [GAME_RULES.md](GAME_RULES.md) and encoded in `rules.py`: squad, formations, multipliers, PIR, win bonus, coach scoring, transfer limits and unlimited windows. A few minor open questions are listed at the end of GAME_RULES.md.

What the rules mean for the plan:

- **Every player plays exactly one game per round.** There are no double-game weeks to exploit.
- **In-round management is a real edge.** Between turns you can swap starters and bench, and move the captain to a player who hasn't played yet. That makes lineup and captain choices *options*, not fixed bets (see Phase 5b).
- **Winning teams matter.** The +10% win bonus and the coach's margin-based score both reward predicting game results, not just box scores.
- **Transfers are scarce** (4 per round), apart from 6 unlimited windows. Those windows are the moments for a full rebuild.

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

**Done when:** `elf backtest --season 2026 --strategy season_avg` produces a season score.

Status (2026-11-21): done. The fantasy API only has prices for the current season, so
the full simulation (budget, transfers, lineup, score) runs on 2026-27 only and covers
more rounds each week. Older seasons (`--season 2025` and earlier) report projection
error only. The simulator reproduces a real official round score exactly (180.2 in
round 13).

Baselines on 2026-27, rounds 1–13:

| Strategy | Season points | Per round | Projection MAE |
|---|---|---|---|
| `season_avg` | 1136.8 | 87.4 | 6.41 |
| `last5` | 1351.7 | 104.0 | 6.38 |

For comparison, a real manager's team scored 2049.4 over the same rounds (158 per round).

## Phase 4: Projections v1 (2 weeks)

Predict each player's points for each game.

- Split it as **expected minutes × PIR per minute**. Minutes are where most of the uncertainty is.
- Features: recent form (exponentially weighted), season and career rates, home or away, opponent's defense (PIR allowed per position), rest days (double-round weeks mean 2 games in about 3 days), team pace, and missing teammates (more usage for the players who remain).
- Models: start with weighted averages, then move to a gradient-boosted model such as LightGBM.
- Output a **distribution** (for example the 10th, 50th and 90th percentiles), not just a mean. Captain picks benefit from upside.
- **Game outcome model:** win probability and expected margin for every game. This drives both the +10% win bonus and the coach's score.
- Head coach projection, built from the coach scoring table and the margin distribution.

**Done when:** it beats both baselines on MAE across at least 2 held-out seasons.

Status (2026-11-21): v1 done, as the `model_v1` strategy (code in `src/elfantasy/model/`).
LightGBM predicts P(plays) and points if he plays (mean plus 10th/50th/90th
percentiles), from leak-free features: weighted form, season/previous/career rates,
availability (games missed, games played in the team's last 5), home/away, rest,
team and opponent strength, and points the opponent allows by position. Each season
is predicted by a model trained only on earlier seasons.

| | season_avg | last5 | model_v1 |
|---|---|---|---|
| MAE 2024-25 (held out) | 5.95 | 6.10 | **5.79** |
| MAE 2025-26 (held out) | 6.11 | 6.29 | **5.90** |
| MAE 2026-27 | 6.41 | 6.38 | **5.97** |
| Season points 2026-27, rounds 1–13 | 1137 | 1352 | **1682** |

Known gaps for v2: the game model (weighted margin plus home advantage) predicts
winners no better than "the home team wins". The 10th–90th percentile range covers
about 77% of outcomes instead of 80%. Missing teammates and team pace aren't
features yet. Injury and lineup news (Phase 7) isn't used.

## Phase 5: Optimizer (2 weeks)

Pick the squad as an integer linear program using PuLP or OR-Tools, the same approach as the strongest fantasy football (FPL) solvers.

- Variables: squad, starters, sixth man, captain, and transfers for each round over a horizon of H rounds (3–5).
- Objective: maximize expected points over the horizon, with future rounds counting less, while respecting the transfer limit.
- Constraints: budget, 4/4/2 positions, at most 6 players per club, 4 transfers (unlimited in the free windows), and the allowed formations.
- Plan around the unlimited windows (after rounds 6, 13, 18, 23, 28 and 34): use the limited rounds for small tweaks and the windows for a rebuild.
- Tools: "best squad from scratch", "best transfers for this week", "what if I sell X".

### 5b: In-round turn strategy

- Know which turn each player plays in (from the schedule).
- Squad value includes **option value**: a strong bench player who plays in a late turn insures the early starters.
- Captain policy: start with an early-turn captain, then after each turn decide whether to keep him or move the armband to a later player who hasn't played yet. This is a stopping problem: keep the captain if his score beats the expected score of the remaining options.
- The same logic applies to benching a weak early starter for a later bench player.
- `elf turn` recommends what to change after each turn.

**Done when:** the backtested season score clearly beats the Phase 3 baselines, the turn strategy adds measurable points on top, and it runs fast enough to use before every deadline.

Status (2026-11-22): the optimizer and 5b are both done. Optimizer: `src/elfantasy/optimize.py`.
It's an integer program solved with HiGHS. CBC, which the roadmap first named, no
longer ships with PuLP 4. It plans squad, starters, sixth man, captain and transfers
over the next 3 rounds, with later rounds weighted ×0.85 and prices held at today's
values. Players who can't plausibly make the squad are dropped before solving: this
gave the same answers 2–4× faster.

| 2026-27, rounds 1–13 | Season points | Per round |
|---|---|---|
| `last5` (best baseline) | 1352 | 104.0 |
| `model_v1` (greedy picker) | 1682 | 129.4 |
| `model_ilp` (optimizer) | **1799** | **138.4** |

`elf optimize` gives the live recommendation for the coming round: it fetches your
squad and budget, plans 3 rounds ahead, and prints transfers, lineup, captain and each
player's range. It takes about a minute once the model is cached in `models/`. The
game's injury flag for the current round is used live only (`--no-injuries` turns it
off), because there's no injury history to backtest it against.

Training is now reproducible. Before 2026-11-22, the training rows were built from
Python sets, whose order changes in every process, so LightGBM sampled different rows
on each run (a fixed seed alone didn't help). Projections then varied slightly between
runs, which sent the optimizer down different transfer paths and moved season scores
by up to about 10%. The numbers in this file are from reproducible runs. Even so, one
13-round season is a small sample: two strategies' different transfer paths can move
their totals apart by tens of points by chance.

**5b, the turn strategy** (`src/elfantasy/turns.py`): a 10-player squad has 4,600 legal
lineups (formation × starters × sixth man × captain), held as arrays. After each turn
the lineup is re-optimised: players who already played count their actual points, the
rest their expected points, and the turn rules limit which lineups are reachable. The
starting lineup is chosen by simulating the round 300 times with that policy in play.
This puts the option value into the starting lineup, for example a captain who plays
in turn 1 and so can still be replaced.

Measured with `elf backtest-turns` on 2024-25 and 2025-26 (72 rounds × 20 squads; each
policy gets the same squads and actual results; gain over a fixed lineup):

| Policy | Points per round | 95% interval | Rounds better |
|---|---|---|---|
| Changes between turns only | +3.4 | 2.9 to 3.9 | 100% |
| Simulated starting lineup only | −0.6 | −1.0 to −0.2 | 35% |
| **Both** (`model_turns`, `elf turn`) | **+4.9** | 4.3 to 5.4 | 100% |

That's about +185 points over a season. A single season simulation can't show it:
on 2026-27, `model_turns` scored 1774 against 1799 for `model_ilp`, because their
different decisions lead to different squads and the luck of 13 rounds dominates.

`elf turn` reads the live roster. Before the round it suggests the starting lineup and
captain. After a turn it suggests the swaps and captain move. `elf optimize` also picks
its starting lineup this way now.

Not modelled: with 3 turns the policy decides greedily at each turn boundary (exactly
optimal with 2 turns). Players' scores are simulated independently, so teammates and
opponents aren't correlated.

## Phase 6: Price and team-value model (1 week)

- Work out how prices move from the price history (actual points against expected points).
- Predict next round's price changes and add a value-growth term to the optimizer. This matters most early in the season.

**Done when:** we predict the direction of price changes with at least 70% accuracy, and the backtest score improves.

Status (2026-11-22): the price model is done (`src/elfantasy/model/prices.py`). The
value-growth term is built but **not shipped**, because it lowered the backtest score.

- **The formula:** after a round in which a player played, change ≈ 0.040 × fantasy
  points − 0.046 × price + 0.04, rounded to 0.1. A player who didn't play loses 0.1.
  Prices never go below 4.0. Fitted on earlier rounds only, it reproduces 93–97% of
  next-round prices exactly. Coaches have their own coefficients. Details are in
  GAME_RULES.md.
- **Before the round:** expected change = P(plays) × formula(expected points) +
  P(doesn't play) × −0.1. Over rounds 4–13 it ranks risers well (AUC 0.72), its rise
  probabilities beat the base rate (Brier skill +8.5%), and expected changes are well
  calibrated (for example, predicted −0.13 against actual −0.10).
- **The 70% target isn't a useful test:** about 70% of price moves are falls, so
  "always predict a fall" already scores 69.9%. Our 69.7% says nothing either way. The
  AUC and Brier skill above are the meaningful measures.
- **Value growth in the optimizer:** the plan earns `value_weight` points per credit of
  expected rise (`model_value`, `elf optimize --value-weight`). On 2026-27 it raised
  team value by about 2 credits by round 13 but lowered points:

  | Strategy | Season points |
  |---|---|
  | `model_turns` (no value term) | 1774 |
  | `model_value` (weight 5) | 1739 |
  | `model_value10` (weight 10) | 1718 |

  So the default stays at 0. The idea may pay off once the planning horizon looks
  further ahead than 3 rounds, or later in the season with more price history.

## Phase 7: The AI assistant layer (2 weeks)

Claude with tool use, built on the Anthropic SDK.

- **Tools for Claude to call:** `get_projections`, `optimize_squad`, `suggest_transfers`, `compare_players`, `get_schedule`, `get_news`, and `explain_pick`.
- **News reading:** collect injury and lineup news, have Claude turn it into structured statuses (out, doubtful, limited minutes), and feed those into the projections.
- **Conversation:** questions like "Should I captain Vezenkov or Nunn this week?" get answers backed by numbers.
- A weekly report: recommended transfers, captain, lineup, risks, and the reasoning behind each.

**Done when:** you can run the whole round from one chat, and the news layer measurably improves projections for players marked doubtful.

Status (2026-11-22): built (`src/elfantasy/assistant/`) and tested live. A chat question
("captain Vezenkov or Hifi?") took about 30 seconds: Claude called `compare_players` and
`get_turn_advice` and answered with their numbers. A news refresh took about 2.5 minutes
and logged 50 sourced statuses for round 14, all matched to the right players. The news
part of "done" can't be measured until rounds with logged news have been played.

- **Chat** (`elf chat`): Claude Opus 5.5 on the Messages API with a manual tool loop.
  The loop keeps the whole conversation, thinking blocks included, and only appends to
  it. Server-side refusal fallback is on. Nine read-only tools: `get_projections`,
  `compare_players`, `explain_projection`, `get_schedule`, `get_my_team`,
  `suggest_transfers` (the optimizer), `get_turn_advice`, `get_news` and
  `refresh_news`. Nothing can change the team in the game. The system prompt tells
  Claude to take every number from a tool and to keep its own opinions separate from
  the optimizer's answer.
- **Report** (`elf report`): the same assistant writes the round report (transfers,
  lineup and captain, news and risks, watch list) to `data/reports/`.
- **News** (`elf news --refresh`): Claude searches the web for team news, then a second
  call turns it into structured statuses (out, doubtful, questionable, limited,
  available). These are matched to fantasy player ids by name and team, and appended
  to the `news_status` table. They're shown, but **not applied to the optimizer by
  default**: the adjustment sizes are untested guesses, and CLAUDE.md requires a
  change to beat the baseline first. `suggest_transfers` can apply them when asked.
  `elf news --evaluate` compares the error with and without news for flagged players,
  once their rounds are played. That measurement is this phase's remaining "done"
  check.
- **Setup:** put `ELF_ANTHROPIC_API_KEY=...` in `.env`, or set `ANTHROPIC_API_KEY`.

## Phase 8: Interface and automation (1 week)

- Start with the CLI, then add a small web UI with Streamlit or FastAPI plus a simple frontend.
- Scheduled job: sync data, update projections, and send you a recommendation N hours before each deadline.
- Optional: notify you on your phone or Telegram when there's late injury news.

Decided (2026-11-22): automation first, then a FastAPI backend with a React + Vite
frontend. Notifications are Windows desktop notifications.

**Automation, done** (`src/elfantasy/automation.py`, `elf auto`, `elf schedule`):

- `elf auto` is safe to run any time. Each run syncs this season's box scores, fantasy
  prices and player links. Within 8 hours of a round's first game, it refreshes the
  news, runs the optimizer and the turn-aware lineup, saves
  `data/reports/auto_<season>_round_NN.md`, and pops up a notification (sell, buy,
  captain, deadline). Between turns (after the last game of a turn, within 6 hours of
  the next), it sends the lineup and captain changes for the next turn.
- What has been sent is kept in `data/auto_state.json`, so nothing is sent twice. An
  expired fantasy token triggers a notification, at most once a day. If another `elf`
  command has the database open (DuckDB allows one writer), the run logs "busy" and
  tries again next time. Everything goes to `data/logs/auto.log`.
- `elf schedule install` registers a Windows scheduled task that runs `elf auto`
  every 2 hours through `pythonw`, so no console window appears. `elf schedule status`
  shows the next run and the log, and `elf schedule remove` deletes the task. The PC
  has to be on and logged in for runs and notifications.
- Settings now load `.env` by absolute path, because scheduled tasks start in another
  folder.
- Tested end to end on 2026-11-22. A forced pre-round run synced the data, saved the
  round 14 report and showed the notification (sell, buy, captain Vezenkov, deadline Tue
  24 Nov 19:00). The scheduled task ran under Task Scheduler and logged "nothing due".
  It's installed to run every 2 hours.
- Known limitation: news search coverage varies from run to run (50, 0 and 12 statuses
  on three refreshes the same evening). A thin refresh doesn't erase earlier statuses,
  because the log keeps each player's latest one, and news isn't applied to picks by
  default.

**Web app, done** (`src/elfantasy/api/` + `frontend/`, run with `elf serve`):

- **Structure:** the Python package stays the core. `elfantasy.api` is a thin FastAPI
  layer over it (installed with the `api` extra). `frontend/` is React + Vite +
  TypeScript with React Router, TanStack Query, Tailwind and react-markdown.
- **Pages:**
  - **My team:** the live squad with projections, ranges, news and turn advice,
    changes highlighted.
  - **Optimize:** the optimizer runs as a background job. Options: horizon, players
    to sell, from scratch, apply news.
  - **Compare:** up to 6 players, covering the next 3 rounds, break-even price and
    chance of a price rise, plus a top-projections browser.
  - **Chat:** the assistant. Tool calls stream as server-sent events while it works.
- **Database:** each request opens it briefly, so `elf auto` and the CLI can still
  write. The trained model and this round's projections are cached and rebuilt when
  the database changes. A locked database returns 503 "busy, try again".
- **Tested end to end on 2026-11-22**, in the browser against real data. Speeds:
  dashboard about 20 seconds on a cold start, optimizer about 16 seconds, first
  comparison about 10 seconds, a chat answer about 40 seconds. In the chat, Claude
  called four tools and answered with their numbers.

## Phase 9: Live season loop (ongoing)

- After every round, compare predictions with actual results, track calibration, and retrain.
- Keep a log of every decision with the projected and actual points, to learn where the model goes wrong.
- Compare our score each round with the top managers' scores.

Status (2026-11-22): the loop is built (`src/elfantasy/review.py`, `elf review`, the
Season page). In-season retraining was tested and doesn't ship (see the end of this
section).

- **Logs** (DuckDB tables, appended): `prediction_log` (projections as used, `live`
  from `elf auto` before each round, or `reconstructed` leak-free for past rounds),
  `recommendation_log` (the optimizer's lineup), `manager_rounds` and
  `manager_rosters` (your score and final lineup, from the game API), and
  `benchmark_rounds` (top-1, top-10 and top-25 round scores from the Global league:
  aggregates only, no names).
- **Review** per played round: projection error and bias; calibration (share inside the
  10–90% range, plus below and above it); chance-of-playing calibration; your score
  against the model's expectation for your lineup; what the recommended plan would
  have scored; the benchmark; your biggest surprises. Stored in `round_reviews`.
  `elf auto` reviews each round once its box scores are complete and sends a
  notification.
- **Backfill of rounds 1–13**, with reconstructed predictions:

  | | Rounds 1–13 |
  |---|---|
  | Projection error (MAE) | 5.6–6.4 |
  | Results inside the 10–90% range | 78% on average (74–76% in recent rounds) |
  | Your score | 157.6 per round |
  | Model's expectation for your final lineups | 124.6 |
  | Top-10 managers of each round | about 235 |
  | Season leader | about 186 per round |

  The ranges are slightly too narrow and getting narrower. Your lineups outscore the
  model's expectation for them by about 33 points a round. Part of that is in-round
  changes made after early scores are known, so it's worth a look in Phase 10.
- **In-season retraining:** tested and **not shipped**. Retraining with the current
  season's completed rounds (leak-free: only rounds before each block) gave the same
  error as the model trained on earlier seasons:

  | Weighted MAE | Current model | Retrained in season |
  |---|---|---|
  | 2025-26, 4 blocks | **5.867** | 5.869 |
  | 2026-27, rounds 7–13 | 6.140 | **6.128** |

  The differences are noise, in opposite directions. The features already carry this
  season's information (form, season averages, games missed), so the model weights
  don't need weekly updates. Retraining between seasons is still needed and happens
  automatically, because the model cache key changes when a new season's data arrives.
  The calibration drift (ranges too narrow) is the more useful target; recalibration
  belongs in Phase 10.

## Phase 10: Team quality (after Phase 9)

The user isn't happy with the squads the optimizer suggests so far, even though the
backtest scores look good. After the other phases, work on the quality of the teams
themselves. Start from the user's concrete complaints, and compare suggested squads
with what strong real managers pick, not only with backtest totals.

Status (2026-11-23): first pass done. The complaint was **too few stars**, and the
target was to **look like top managers**.

- **The projections aren't the cause.** Over rounds 1–13 they're calibrated in every
  price tier (14–17 credits: expected 13.8, actual 14.3; 17+: 20.6 against 20.9).
- **The star gap is real.** The optimizer's backtest squads held 1.8 players at 14+
  credits per round, against 2.5 in the user's real rosters (widest in rounds 2–6).
  The user also outscored the optimizer by about 21 points a round (2,049 against
  1,774). The same total budget went on the six scoring slots (72 credits); the user
  spent more on the bench.
- **Top managers' squads aren't readable** through the API (403), so overall
  ownership (`popularity`) is the available proxy.
- **Test bed:** past seasons with estimated prices (`backtest/synthetic.py`). Starting
  prices come from the previous-season average (correlation 0.92 with real round-1
  prices). The fitted price formula is applied each round. These seasons are used
  only to compare variants with each other. `elf backtest-variants` runs the
  comparison in parallel.
- **Results** (points per round; estimated 2024-25 and 2025-26, real 2026-27): one
  season's path varies by about ±5 a round from luck alone, so only consistent
  patterns count.

  | Variant | 2024-25 | 2025-26 | 2026-27 |
  |---|---|---|---|
  | original optimizer | 166.0 | 138.6 | 136.4 |
  | **captain upside 1.0 (shipped)** | **172.7** | **145.2** | **138.3** |
  | captain upside 0.5 / 0.75 / 1.5 / 2.0 | 163.0 / 160.1 / 164.5 / 176.0 | 139.5 / 148.0 / 135.8 / 140.2 | 132.4 / 144.7 / 138.3 / 147.6 |
  | bench weight 0.7 | 178.5 | 138.6 | 140.4 |
  | transfer cost 2 / 4 | 177.5 / 155.3 | 135.6 / 140.3 | 138.3 / 128.5 |

  Captain upside, which values the captain at expected points plus his chance of
  playing × (90% value − median), beat the original in 10 of 15 runs across
  strengths, by +2.7 a round on average. Strength 1.0 is the only one positive in all
  three seasons, so it's now the default (`optimize.CAPTAIN_UPSIDE`) in the backtest,
  `elf optimize`, `elf auto` and the web app. Transfer cost and bench weight were
  inconsistent and aren't shipped.
- **Minimum stars, tested:** a rule forcing the squad to hold at least N players
  priced 14+ (gain per round over the shipped default):

  | Rule | 2024-25 | 2025-26 | 2026-27 |
  |---|---|---|---|
  | at least 2 stars | −3.3 | −9.1 | +0.7 |
  | at least 3 stars | +2.3 | −7.6 | −7.8 |
  | at least 4 stars | −2.4 | −9.8 | −0.8 |

  7 of 9 runs were worse, by about −4 a round on average, although the squads did hold
  3–4 stars. So stars alone don't explain why the user outscores the optimizer. The
  user's edge more likely comes from what the model can't see: news, minutes and
  rotation reads, and live in-round decisions. The rule **isn't a default**. It's
  offered as a personal preference (`elf optimize --min-stars N`, and on the Optimize
  page), and the result shows its cost: in round 14, forcing 5 stars took expected
  points from 156.4 to 148.5.
- **Experiments** now compute projections once per season and share them across
  variants (`models/experiments/`). That saved only about 15% (36 against 42 minutes),
  because the optimizer's solve dominates. Cached runs reproduce uncached ones exactly.
- **Where the gap is now:** the user beats the optimizer by about 21 points a round,
  and the measured settings recover about 2–5. The rest looks like information
  (news, lineups) and live decisions. The levers that are left: feed news into
  projections once `elf news --evaluate` shows it helps (from round 14 on), widen the
  10–90% ranges (they cover 78%, not 80%), and model minutes more directly.

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
