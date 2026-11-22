"""Live recommendation for the upcoming round: transfers, lineup and captain.

Uses the same projections (model_v1) and optimizer as the backtest. The one live-only
input is the game's injury flag for the current round: a player the game lists with
probability_of_playing 0 is treated as out. It can't be backtested (there's no
injury history yet), so it can be switched off.
"""

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from elfantasy.backtest.data import COACH, _fantasy_market
from elfantasy.backtest.simulator import RoundContext
from elfantasy.backtest.strategies import ModelV1
from elfantasy.optimize import Plan, optimize
from elfantasy.rules import GameRules
from elfantasy.turns import Choice, LineupSpace, best_start


@dataclass
class Recommendation:
    round: int
    rounds: list[int]
    budget: float
    plan: Plan
    table: pd.DataFrame  # one row per player in the recommended squad
    baseline: set  # squad the transfers are counted from
    transfer_limit: int | None


def current_market(con: duckdb.DuckDBPyConnection, season: str, round_: int) -> pd.DataFrame:
    """The fantasy market for a round, with live fields, indexed by player_id."""
    market = _fantasy_market(con, season)
    market = market[market["round"] == round_].set_index("player_id")
    live = con.execute(
        """
        SELECT player_id, first_name, last_name, opp_code, home,
               probability_of_playing, is_injured, popularity
        FROM fantasy_prices WHERE season = ? AND round = ?
        """,
        [season, round_],
    ).df()
    return market.join(live.set_index("player_id"))


def apply_injuries(pred: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """Players the game lists with probability_of_playing 0 won't play this round."""
    pred = pred.copy()
    out = (market["probability_of_playing"].fillna(1) == 0) & (market["position"] != COACH)
    pred.loc[out.reindex(pred.index, fill_value=False), "p_play"] = 0.0
    pred["expected"] = pred["p_play"] * pred["mean"]
    return pred


def slot_name(i, choice: Choice, market: pd.DataFrame) -> str:
    if market.at[i, "position"] == COACH:
        return "coach"
    if i == choice.captain:
        return "captain"
    if i in choice.starters:
        return "starter"
    return "sixth" if i == choice.sixth else "bench"


def predict_round(
    con: duckdb.DuckDBPyConnection,
    season: str,
    round_: int,
    rules: GameRules,
    use_injuries: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(market, predictions) for the current round."""
    market = current_market(con, season, round_)
    model = ModelV1(con=con)
    ctx = RoundContext(
        season=season, round=round_, market=market, history=pd.DataFrame(),
        coach_history=pd.DataFrame(), rules=rules, lineup=None, budget=0.0,
        transfer_limit=None,
    )  # fmt: skip
    pred = model.predict(ctx)
    return market, apply_injuries(pred, market) if use_injuries else pred


@dataclass
class TurnAdvice:
    current: Choice
    best: Choice
    played: set
    values: pd.Series  # actual points (played) or expected points (not yet)
    gain: float  # expected points gained by switching to `best`
    before_round: bool  # nobody has played yet: `best` is a starting lineup


def turn_advice(
    roster: list[dict], market: pd.DataFrame, pred: pd.DataFrame, rules: GameRules
) -> TurnAdvice:
    """Best legal lineup change for a live roster (players from the roster endpoint:
    court_position 1-5 starters, 6 sixth man, 7-10 bench, 11 coach)."""
    squad = [p for p in roster if p["court_position"] <= 10]
    players = [p["id"] for p in squad]
    current = Choice(
        starters=tuple(p["id"] for p in squad if p["court_position"] <= 5),
        sixth=next(p["id"] for p in squad if p["court_position"] == 6),
        captain=next(p["id"] for p in squad if p["is_captain"]),
    )
    # A game that has started counts as played: the player can't move up any more.
    played = {p["id"] for p in squad if p.get("match_played") or p.get("match_live")}
    space = LineupSpace(players, market["position"].to_dict(), rules)
    cur = space.index(current)
    known = pd.Series({p["id"]: float(p.get("pts") or 0) for p in squad})
    expected = pred["expected"].reindex(players).fillna(0.0)
    values = pd.Series([known[i] if i in played else expected[i] for i in players], index=players)
    if not played:
        best = best_start(space, pred, market["turn"].to_dict())[0]
    else:
        mask = np.array([i in played for i in players])
        best = space.best(cur, mask, values.to_numpy(dtype=float))
    gain = float(space.mult[best] @ values.to_numpy() - space.mult[cur] @ values.to_numpy())
    return TurnAdvice(current, space.choice(best), played, values, gain, not played)


def recommend(
    con: duckdb.DuckDBPyConnection,
    season: str,
    round_: int,
    rules: GameRules,
    budget: float,
    baseline: set | None,
    horizon: int = 3,
    use_injuries: bool = True,
    sell: set | None = None,
    model: ModelV1 | None = None,
    value_weight: float = 0.0,
) -> Recommendation:
    market = current_market(con, season, round_)
    model = model or ModelV1(con=con)
    ctx = RoundContext(
        season=season, round=round_, market=market, history=pd.DataFrame(),
        coach_history=pd.DataFrame(), rules=rules, lineup=None, budget=budget,
        transfer_limit=None,
    )  # fmt: skip
    logs = model._load()
    last = int(logs.schedule.loc[logs.schedule["season"] == season, "round"].max())
    rounds = list(range(round_, min(round_ + horizon, last + 1)))
    preds = {r: model.predict(ctx, r) for r in rounds}
    if use_injuries:
        preds[round_] = apply_injuries(preds[round_], market)

    proj = pd.DataFrame({r: preds[r]["expected"] for r in rounds})
    limit = None if baseline is None else rules.transfer_limit(after_round=round_ - 1)
    limits = [limit] + [rules.transfer_limit(after_round=r - 1) for r in rounds[1:]]
    value = None
    if value_weight:
        from elfantasy.model.prices import PriceModel, price_history

        history = price_history(con, season)
        prices = PriceModel.fit(history[history["round"] < round_], rules)
        if prices is not None:
            is_coach = market["position"] == COACH
            value = pd.DataFrame(
                {r: prices.expected_change(market["price"], preds[r]["p_play"],
                                           preds[r]["mean"], is_coach) for r in rounds},
                index=market.index,
            )  # fmt: skip
    plan = optimize(market, proj, rules, budget, baseline, limits, forced_out=sell,
                    value=value, value_weight=value_weight)  # fmt: skip

    squad = plan.squads[0]
    p0 = preds[round_]
    # Starting lineup chosen with the turn strategy in mind (Phase 5b).
    players = [i for i in squad if market.at[i, "position"] != COACH]
    space = LineupSpace(players, market["position"].to_dict(), rules)
    choice = space.choice(best_start(space, p0, market["turn"].to_dict())[0])
    table = market.loc[list(squad)].join(p0[["p_play", "q10", "q50", "q90", "expected"]])
    table["slot"] = [slot_name(i, choice, market) for i in table.index]
    table["new"] = [baseline is not None and i not in baseline for i in table.index]
    order = {"captain": 0, "starter": 1, "sixth": 2, "bench": 3, "coach": 4}
    table = table.sort_values(
        ["slot", "expected"], key=lambda c: c.map(order) if c.name == "slot" else -c
    )
    return Recommendation(round_, rounds, budget, plan, table, baseline or set(), limit)
