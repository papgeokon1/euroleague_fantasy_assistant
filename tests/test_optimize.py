from collections import Counter

import pandas as pd
import pytest

from elfantasy.backtest.simulator import Lineup, RoundContext, validate
from elfantasy.optimize import candidates, optimize
from elfantasy.rules import RULES_2026_27 as R

CLUBS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH"]


@pytest.fixture
def market() -> pd.DataFrame:
    """8 clubs x (3 guards, 3 forwards, 2 centers, 1 coach). Better players cost more."""
    rows = []
    for i, club in enumerate(CLUBS):
        for pos, n in (("G", 3), ("F", 3), ("C", 2), ("HC", 1)):
            for k in range(n):
                q = 4 + i + 2 * k
                rows.append(dict(player_id=f"{club}-{pos}{k}", position=pos, team_code=club,
                                 price=4 + q / 2, q=float(q)))  # fmt: skip
    return pd.DataFrame(rows).set_index("player_id")


def proj_for(market, rounds=1) -> pd.DataFrame:
    return pd.DataFrame({r: market["q"] for r in range(rounds)})


def as_lineup(plan, market, t=0) -> Lineup:
    coach = next(p for p in plan.squads[t] if market.at[p, "position"] == "HC")
    players = tuple(sorted(plan.squads[t] - {coach}))
    return Lineup(players, coach, tuple(sorted(plan.starters[t])), plan.sixth[t], plan.captain[t])


def check_legal(plan, market, budget, t=0):
    ctx = RoundContext("E2026", 1, market, pd.DataFrame(), pd.DataFrame(), R, None, budget, None)
    validate(as_lineup(plan, market, t), ctx)


def test_unlimited_budget_takes_the_best_players(market):
    plan = optimize(market, proj_for(market), R, budget=1000)
    check_legal(plan, market, 1000)
    squad = market.loc[list(plan.squads[0])]
    for pos, n in (("G", 4), ("F", 4), ("C", 2), ("HC", 1)):
        best = market[market["position"] == pos]["q"].nlargest(n).min()
        assert (squad[squad["position"] == pos]["q"] >= best).all()
    starters = market.loc[list(plan.starters[0]), "q"]
    assert plan.captain[0] == starters.idxmax()


def test_budget_and_club_limit(market):
    plan = optimize(market, proj_for(market), R, budget=80)
    check_legal(plan, market, 80)
    assert market.loc[list(plan.squads[0]), "price"].sum() <= 80 + 1e-6
    clubs = Counter(market.loc[[p for p in plan.squads[0] if market.at[p, "position"] != "HC"],
                               "team_code"])  # fmt: skip
    assert max(clubs.values()) <= R.max_players_per_club


def test_transfer_limits_hold_over_the_horizon(market):
    start = optimize(market, proj_for(market) * 0 + 1, R, budget=75).squads[0]
    plan = optimize(market, proj_for(market, 3), R, budget=200, current=start, limits=[2, 1, 0])
    assert [len(t) for t in plan.transfers_in] == [2, 1, 0]
    assert plan.squads[2] == plan.squads[1]
    for t in range(3):
        check_legal(plan, market, 200, t)


def test_unlimited_window_allows_a_rebuild(market):
    start = optimize(market, proj_for(market) * 0 + 1, R, budget=75).squads[0]
    plan = optimize(market, proj_for(market), R, budget=200, current=start, limits=[None])
    assert len(plan.transfers_in[0]) > R.transfers_per_round


def test_forced_out(market):
    best = market["q"].idxmax()
    plan = optimize(market, proj_for(market), R, budget=1000, forced_out={best})
    assert best not in plan.squads[0]


def test_plans_ahead_for_a_later_round(market):
    """A player useless now but great next round is bought now when transfers next
    round are locked."""
    proj = proj_for(market, 2)
    star = "AAA-G0"
    proj.loc[star] = [0.0, 100.0]
    start = optimize(market, proj_for(market), R, budget=200).squads[0]
    plan = optimize(market, proj, R, budget=200, current=start, limits=[1, 0])
    assert star in plan.squads[0]


def test_candidates_keep_current_squad_and_cheapest(market):
    proj = proj_for(market)
    keep = {"AAA-C0"}
    pool = candidates(market, proj, 0.85, keep)
    assert "AAA-C0" in pool
    for _pos, group in market.groupby("position"):
        assert group["price"].idxmin() in pool
