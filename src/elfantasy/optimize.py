"""Squad optimizer: an integer linear program over the next few rounds.

For every round t in the horizon it chooses the squad, starting five, sixth man and
captain, linked by the transfer limits between rounds. It maximises expected points,
with later rounds discounted because their projections are less certain. This is the
same approach as the strongest FPL solvers.

All rule values (squad shape, formations, multipliers, club limit, transfers) come
from GameRules. Prices are held at today's values over the horizon; Phase 6 adds
predicted price changes.
"""

from dataclasses import dataclass

import pandas as pd
import pulp

from elfantasy.rules import GameRules, Position

COACH = "HC"
DECAY = 0.85  # weight of each further round
TRANSFER_COST = 0.01  # tiny cost so equal-value plans don't churn the squad
MIP_GAP = 0.002  # stop within 0.2% of the best possible plan
# The captain's extra share is valued at expected points plus this much of his upside
# (chance of playing x (90% value - median)). Phase 10: 1.0 beat 0.0 in all three test
# seasons (+6.7, +6.6, +1.9 points a round; strengths 0.5-2.0 averaged +2.7).
CAPTAIN_UPSIDE = 1.0
STAR_PRICE = 14.0  # "star": a player priced at this or more


def captain_values(preds: dict, upside: float = CAPTAIN_UPSIDE) -> pd.DataFrame | None:
    """Captain value per player for each horizon round, from projection frames with
    p_play, q50, q90 and expected (as ModelV1.predict returns). None when upside is 0."""
    if not upside:
        return None
    return pd.DataFrame(
        {
            r: p["expected"]
            + upside * p["p_play"] * (p["q90"] - p["q50"]).clip(lower=0).fillna(0.0)
            for r, p in preds.items()
        }
    )


@dataclass
class Plan:
    """The optimizer's answer. Lists are per horizon round; [0] is this round."""

    squads: list[set]
    starters: list[set]
    sixth: list
    captain: list
    transfers_in: list[set]
    transfers_out: list[set]
    objective: float
    status: str


# How many players per position the pruned pool keeps, by projected points.
POOL_TOP = {"G": 30, "F": 30, "C": 18, COACH: 8}
POOL_VALUE = 12  # plus the best points per credit
POOL_CHEAP = 5  # plus the cheapest, so a squad always fits the budget


def candidates(market: pd.DataFrame, proj: pd.DataFrame, decay: float, keep: set) -> list:
    """Drop players who can't plausibly make an optimal squad, to keep the program
    small. Always keeps `keep` (the current squad and forced picks)."""
    weights = [decay**t for t in range(proj.shape[1])]
    total = (proj * weights).sum(axis=1)
    m = market.assign(total=total, value=total / market["price"])
    out = set(keep) & set(m.index)
    for pos, group in m.groupby("position"):
        out |= set(group.nlargest(POOL_TOP.get(pos, 30), "total").index)
        out |= set(group.nlargest(POOL_VALUE, "value").index)
        out |= set(group.nsmallest(POOL_CHEAP, "price").index)
    return [i for i in m.index if i in out]


def optimize(
    market: pd.DataFrame,
    proj: pd.DataFrame,
    rules: GameRules,
    budget: float,
    current: set | frozenset | None = None,
    limits: list[int | None] | None = None,
    decay: float = DECAY,
    forced_out: set | None = None,
    forced_in: set | None = None,
    time_limit: float = 60,
    prune: bool = True,
    value: pd.DataFrame | None = None,
    value_weight: float = 0.0,
    transfer_cost: float = TRANSFER_COST,
    captain_proj: pd.DataFrame | None = None,
    bench_weight: float | None = None,
    min_stars: int = 0,
    star_price: float = STAR_PRICE,
) -> Plan:
    """Best plan over len(proj.columns) rounds.

    market: indexed by player id, with columns position (G/F/C/HC), team_code, price.
    proj: expected points per player (rows, same index) and horizon round (columns,
        in order). Points are before slot multipliers.
    current: the squad owned now (players and coach). None means building from scratch.
    limits: transfers allowed into each horizon round (None = unlimited). Ignored
        for round 0 when `current` is None.
    value: expected price change per player and horizon round (same shape as proj).
        Owning a player through round t earns value_weight x his expected change, so
        the plan prefers players likely to rise (Phase 6). value_weight is points per
        credit of team value.
    transfer_cost: points charged per transfer, so a move must gain more than that to
        be worth making (a guard against chasing projection noise).
    captain_proj: what the captain's extra share is worth per player and round (same
        shape as proj), e.g. expected points plus some upside. Defaults to proj.
    bench_weight: how much a bench player's expected points count in the objective.
        Defaults to the bench multiplier; higher values reflect cover for starters.
    min_stars: every planned squad holds at least this many players priced at
        star_price or more (capped at how many such players the market has).
    """
    proj = proj.reindex(market.index).fillna(0.0)
    cap_proj = proj if captain_proj is None else captain_proj.reindex(market.index).fillna(0.0)
    cap_proj = cap_proj.set_axis(proj.columns, axis=1)
    if value is not None and value_weight:
        proj_value = value.reindex(market.index).fillna(0.0).set_axis(proj.columns, axis=1)
    else:
        proj_value = None
    current = set(current or [])
    if prune:
        must = current | set(forced_in or ())
        if min_stars:  # keep every star, so the rule can always be met
            must |= set(
                market.index[(market["position"] != COACH) & (market["price"] >= star_price)]
            )
        keep = candidates(market, proj, decay, must)
        market, proj, cap_proj = market.loc[keep], proj.loc[keep], cap_proj.loc[keep]
        if proj_value is not None:
            proj_value = proj_value.loc[keep]
    ids = list(market.index)
    rounds = list(range(proj.shape[1]))
    limits = limits or [rules.transfers_per_round] * len(rounds)
    pos = market["position"]
    coaches = [i for i in ids if pos[i] == COACH]
    players = [i for i in ids if pos[i] != COACH]
    price = market["price"]

    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    b = prob.add_variable_dicts  # PuLP 4: variables belong to the problem
    s = b("squad", (ids, rounds), cat="Binary")
    x = b("start", (players, rounds), cat="Binary")
    six = b("sixth", (players, rounds), cat="Binary")
    cap = b("captain", (players, rounds), cat="Binary")
    tin = b("in", (ids, rounds), cat="Binary")
    formations = list(range(len(rules.starting_formations)))
    form = b("formation", (formations, rounds), cat="Binary")

    m_bench = rules.bench_multiplier if bench_weight is None else bench_weight
    weight = {t: decay**t for t in rounds}
    objective = []
    for t in rounds:
        p, pc = proj.iloc[:, t], cap_proj.iloc[:, t]
        extra = rules.starter_multiplier * (rules.captain_multiplier - 1)
        for i in players:
            objective.append(
                weight[t]
                * (
                    p[i]
                    * (
                        m_bench * s[i][t]
                        + (rules.starter_multiplier - m_bench) * x[i][t]
                        + (rules.sixth_man_multiplier - m_bench) * six[i][t]
                    )
                    + pc[i] * extra * cap[i][t]
                )
            )
        objective += [weight[t] * p[i] * s[i][t] for i in coaches]
        if proj_value is not None:
            v = proj_value.iloc[:, t]
            objective += [weight[t] * value_weight * v[i] * s[i][t] for i in ids]
        objective += [-transfer_cost * tin[i][t] for i in ids]
    prob += pulp.lpSum(objective)

    for t in rounds:
        # Squad shape, club limit, budget.
        for position, n in rules.squad_positions.items():
            prob += pulp.lpSum(s[i][t] for i in players if pos[i] == position.value) == n
        if rules.has_head_coach:
            prob += pulp.lpSum(s[i][t] for i in coaches) == 1
        for _club, group in market.loc[players].groupby("team_code"):
            prob += pulp.lpSum(s[i][t] for i in group.index) <= rules.max_players_per_club
        prob += pulp.lpSum(price[i] * s[i][t] for i in ids) <= budget
        if min_stars:
            stars = [i for i in players if price[i] >= star_price]
            prob += pulp.lpSum(s[i][t] for i in stars) >= min(min_stars, len(stars))

        # Lineup: five starters in an allowed formation, a sixth man, a captain.
        prob += pulp.lpSum(form[k][t] for k in formations) == 1
        for j, position in enumerate((Position.GUARD, Position.FORWARD, Position.CENTER)):
            prob += pulp.lpSum(x[i][t] for i in players if pos[i] == position.value) == pulp.lpSum(
                rules.starting_formations[k][j] * form[k][t] for k in formations
            )
        prob += pulp.lpSum(six[i][t] for i in players) == 1
        prob += pulp.lpSum(cap[i][t] for i in players) == 1
        for i in players:
            prob += x[i][t] + six[i][t] <= s[i][t]
            prob += cap[i][t] <= x[i][t]

        # Transfers: a player in the squad who wasn't there the round before.
        for i in ids:
            before = (1 if i in current else 0) if t == 0 else s[i][t - 1]
            prob += tin[i][t] >= s[i][t] - before
        limited = limits[t] is not None and (t > 0 or current)
        if limited:
            prob += pulp.lpSum(tin[i][t] for i in ids) <= limits[t]

    for i in forced_out or ():
        if i in s:
            for t in rounds:
                prob += s[i][t] == 0
    for i in forced_in or ():
        prob += s[i][0] == 1

    solver = pulp.HiGHS(
        msg=False, timeLimit=time_limit, gapRel=MIP_GAP, log_to_console=False, threads=1
    )
    result = prob.solve(solver)
    status = pulp.LpSolveStatus(result.status)
    # A time-limited solve still returns its best plan so far.
    timed_out = status == pulp.LpSolveStatus.TimeLimit and result.has_solution
    if status != pulp.LpSolveStatus.Optimal and not timed_out:
        raise RuntimeError(f"optimizer status: {status.name}")

    def on(var, keys, t):
        return {i for i in keys if var[i][t].value() > 0.5}

    squads = [on(s, ids, t) for t in rounds]
    previous = [current, *squads[:-1]]
    pairs = list(zip(squads, previous, strict=True))
    return Plan(
        squads=squads,
        starters=[on(x, players, t) for t in rounds],
        sixth=[next(iter(on(six, players, t))) for t in rounds],
        captain=[next(iter(on(cap, players, t))) for t in rounds],
        transfers_in=[sq - prev if prev else set() for sq, prev in pairs],
        transfers_out=[prev - sq if prev else set() for sq, prev in pairs],
        objective=float(prob.objective.value()),
        status=status.name,
    )
