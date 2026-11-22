"""Measure the turn strategy (Phase 5b) on many squads.

A season simulation gives one squad per round, and small changes upstream send the
optimizer down different transfer paths, so season totals are too noisy to judge
lineup decisions. Lineup decisions don't need prices, though, so here they're scored
on many squads per round over past seasons. Every policy sees the same squad and the
same actual results, so differences come from the decisions alone.

Policies:
- static: best lineup by expected points, never changed
- adjust: static start, re-optimised after every turn but the last
- sim_start: starting lineup chosen by simulation, never changed
- sim_start_adjust: both, which is what model_turns and `elf turn` do

Turns for past seasons come from game dates: games on the round's first day are
turn 1, the next day turn 2, and so on. Projections come from model_v1 trained only
on earlier seasons.
"""

import duckdb
import numpy as np
import pandas as pd

from elfantasy.backtest.data import COACH, load_season
from elfantasy.backtest.simulator import RoundContext
from elfantasy.backtest.strategies import ModelV1
from elfantasy.rules import RULES_2026_27, GameRules
from elfantasy.turns import LineupSpace, best_start, simulate_policy

POS = {"Guard": "G", "Forward": "F", "Center": "C"}


def turns_by_team(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """(round, team_code) -> turn number, from game dates."""
    g = con.execute(
        """
        SELECT round, home_code, away_code, (utc_date::TIMESTAMPTZ)::DATE AS day
        FROM games WHERE season = ? AND phase = 'RS'
        """,
        [season],
    ).df()
    g["turn"] = g.groupby("round")["day"].rank(method="dense").astype(int)
    both = pd.concat(
        [g.rename(columns={"home_code": "team_code"}), g.rename(columns={"away_code": "team_code"})]
    )
    return both[["round", "team_code", "turn"]].drop_duplicates()


def evaluate(
    con: duckdb.DuckDBPyConnection,
    seasons: list[str],
    squads_per_round: int = 20,
    pool_size: int = 120,
    rules: GameRules = RULES_2026_27,
    seed: int = 0,
    model: ModelV1 | None = None,
) -> pd.DataFrame:
    """One row per (season, round, squad) with each policy's actual score."""
    rng = np.random.default_rng(seed)
    model = model or ModelV1(con=con)
    rows = []
    for season in seasons:
        data = load_season(con, season)
        positions = (
            con.execute(
                "SELECT player_code, mode(position) AS pos FROM player_games "
                "WHERE season = ? GROUP BY 1",
                [season],
            )
            .df()
            .set_index("player_code")["pos"]
            .map(POS)
        )
        turns = turns_by_team(con, season)
        for rnd in data.rounds:
            market = data.market[data.market["round"] == rnd].set_index("player_id")
            market = market[market["position"] != COACH].copy()
            market["position"] = market["el_code"].map(positions)
            market = market.dropna(subset=["position"])
            t = turns[turns["round"] == rnd].set_index("team_code")["turn"]
            market["turn"] = market["el_team"].map(t).fillna(1).astype(int)
            if market["turn"].nunique() < 2:
                continue  # a one-day round has no turn decisions
            ctx = RoundContext(season, rnd, market, pd.DataFrame(), pd.DataFrame(), rules,
                               None, 0.0, None)  # fmt: skip
            pred = model.predict(ctx)
            actual = data.outcomes[data.outcomes["round"] == rnd].set_index("player_id")["points"]
            pool = pred["expected"].nlargest(pool_size).index
            by_pos = {p: [i for i in pool if market.at[i, "position"] == p] for p in "GFC"}
            for k in range(squads_per_round):
                squad = [
                    *rng.choice(by_pos["G"], 4, replace=False),
                    *rng.choice(by_pos["F"], 4, replace=False),
                    *rng.choice(by_pos["C"], 2, replace=False),
                ]
                rows.append(_score_policies(season, rnd, k, squad, market, pred, actual, rules))
    return pd.DataFrame(rows)


def _score_policies(season, rnd, k, squad, market, pred, actual, rules) -> dict:
    space = LineupSpace(squad, market["position"].to_dict(), rules)
    expected = pred.loc[squad, "expected"].to_numpy(dtype=float)
    real = actual.reindex(squad).fillna(0.0).to_numpy(dtype=float)[None, :]
    turn = market.loc[squad, "turn"].to_numpy()
    static = int(np.argmax(space.mult @ expected))
    sim = best_start(space, pred, market["turn"].to_dict())[0]
    return {
        "season": season,
        "round": rnd,
        "squad": k,
        "static": float(real[0] @ space.mult[static]),
        "adjust": float(simulate_policy(space, static, turn, expected, real)[0]),
        "sim_start": float(real[0] @ space.mult[sim]),
        "sim_start_adjust": float(simulate_policy(space, sim, turn, expected, real)[0]),
    }


def summarize(results: pd.DataFrame) -> pd.DataFrame:
    """Mean gain per round over the static lineup, with a 95% interval."""
    out = []
    for policy in ("adjust", "sim_start", "sim_start_adjust"):
        # Average over squads within a round first: squads share the round's luck.
        per_round = (
            (results[policy] - results["static"])
            .groupby([results["season"], results["round"]])
            .mean()
        )
        se = per_round.std(ddof=1) / np.sqrt(len(per_round))
        out.append(
            {
                "policy": policy,
                "gain_per_round": per_round.mean(),
                "ci95_low": per_round.mean() - 1.96 * se,
                "ci95_high": per_round.mean() + 1.96 * se,
                "rounds": len(per_round),
                "share_of_rounds_better": (per_round > 0).mean(),
            }
        )
    return pd.DataFrame(out)
