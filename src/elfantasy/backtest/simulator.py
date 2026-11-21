"""Replay a season round by round.

Before each round the strategy gets a `RoundContext` holding only what a manager
would know before that round's deadline: the market (prices), its current squad and
bank, the transfer limit, and the games played before this round. It returns a
`Lineup`. The simulator checks it against the rules, then scores it with the round's
actual results.

Not modelled yet: changes between turns of a round (Phase 5b) and postponed games.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from elfantasy.backtest.data import COACH, SeasonData
from elfantasy.rules import RULES_2026_27, GameRules, Position, Slot

EPS = 1e-6


class RuleViolation(ValueError):
    """A strategy returned a lineup the game would not accept."""


@dataclass(frozen=True)
class Lineup:
    players: tuple  # the 10 squad players
    coach: object
    starters: tuple  # 5 of `players`
    sixth: object
    captain: object  # one of `starters`

    @property
    def squad(self) -> frozenset:
        return frozenset(self.players) | {self.coach}


@dataclass
class RoundContext:
    season: str
    round: int
    market: pd.DataFrame  # this round's buyable players, indexed by player_id
    history: pd.DataFrame  # player games before this round only
    coach_history: pd.DataFrame  # coach games before this round only
    rules: GameRules
    lineup: Lineup | None  # last round's lineup, None before round 1
    budget: float  # bank + value of the current squad at today's prices
    transfer_limit: int | None  # None = unlimited


class Strategy(Protocol):
    name: str

    def project(self, ctx: RoundContext) -> pd.Series:
        """Expected fantasy points (before slot multipliers) per market player_id."""
        ...

    def pick(self, ctx: RoundContext, projections: pd.Series) -> Lineup: ...


@dataclass
class SeasonResult:
    strategy: str
    season: str
    rounds: pd.DataFrame  # one row per round
    projections: pd.DataFrame = field(repr=False)  # round, player_id, projected, actual

    @property
    def total_points(self) -> float:
        return float(self.rounds["points"].sum()) if "points" in self.rounds else float("nan")

    @property
    def mae(self) -> float:
        played = self.projections[self.projections["seconds"] > 0]
        return float((played["projected"] - played["points"]).abs().mean())

    @property
    def bias(self) -> float:
        played = self.projections[self.projections["seconds"] > 0]
        return float((played["projected"] - played["points"]).mean())


def visible_history(df: pd.DataFrame, season: str, round_: int) -> pd.DataFrame:
    """Rows a manager could know about before `round_` of `season`: earlier seasons,
    and earlier rounds of this season's regular season."""
    earlier_season = df["season"] < season
    this_season = (df["season"] == season) & (df["phase"] == "RS") & (df["round"] < round_)
    return df[earlier_season | this_season]


def validate(lineup: Lineup, ctx: RoundContext) -> float:
    """Raise RuleViolation if the lineup is illegal; return its cost."""
    rules, market = ctx.rules, ctx.market
    players = list(lineup.players)
    if len(set(players)) != rules.squad_size:
        raise RuleViolation(f"squad needs {rules.squad_size} different players")
    missing = [p for p in [*players, lineup.coach] if p not in market.index]
    if missing:
        raise RuleViolation(f"not in this round's market: {missing}")

    positions = Counter(market.loc[players, "position"])
    wanted = {pos.value: n for pos, n in rules.squad_positions.items()}
    if dict(positions) != wanted:
        raise RuleViolation(f"squad positions {dict(positions)} != {wanted}")
    if rules.has_head_coach and market.at[lineup.coach, "position"] != COACH:
        raise RuleViolation("coach slot needs a head coach")
    club = Counter(market.loc[players, "team_code"]).most_common(1)[0]
    if club[1] > rules.max_players_per_club:
        raise RuleViolation(f"{club[1]} players from {club[0]}")

    if len(set(lineup.starters)) != 5 or not set(lineup.starters) <= set(players):
        raise RuleViolation("need 5 different starters from the squad")
    s = Counter(market.loc[list(lineup.starters), "position"])
    formation = (s[Position.GUARD.value], s[Position.FORWARD.value], s[Position.CENTER.value])
    if not rules.is_valid_formation(*formation):
        raise RuleViolation(f"invalid formation {formation}")
    if lineup.sixth not in players or lineup.sixth in lineup.starters:
        raise RuleViolation("sixth man must be a squad player outside the starting five")
    if lineup.captain not in lineup.starters:
        raise RuleViolation("captain must be a starter")

    cost = float(market.loc[[*players, lineup.coach], "price"].sum())
    if cost > ctx.budget + EPS:
        raise RuleViolation(f"squad costs {cost:.1f}, budget is {ctx.budget:.1f}")
    if ctx.lineup is not None and ctx.transfer_limit is not None:
        transfers = len(lineup.squad - ctx.lineup.squad)
        if transfers > ctx.transfer_limit:
            raise RuleViolation(f"{transfers} transfers, limit is {ctx.transfer_limit}")
    return cost


def score(lineup: Lineup, points: pd.Series, rules: GameRules) -> float:
    """Round score from actual fantasy points per player_id."""
    total = 0.0
    for p in lineup.players:
        if p in lineup.starters:
            slot = Slot.STARTER
        elif p == lineup.sixth:
            slot = Slot.SIXTH_MAN
        else:
            slot = Slot.BENCH
        total += points.get(p, 0.0) * rules.multiplier(slot=slot, is_captain=p == lineup.captain)
    return total + points.get(lineup.coach, 0.0)


def run_season(
    data: SeasonData, strategy: Strategy, rules: GameRules = RULES_2026_27
) -> SeasonResult:
    """Simulate the whole season. Without prices, only projection error is measured."""
    simulate = data.has_prices
    lineup: Lineup | None = None
    bank = rules.budget
    last_price: dict = {}
    round_rows, projection_rows = [], []

    for rnd in data.rounds:
        market = data.market[data.market["round"] == rnd].set_index("player_id")
        outcome = data.outcomes[data.outcomes["round"] == rnd].set_index("player_id")

        budget = bank
        if lineup is not None:
            last_price.update(market["price"].to_dict())
            budget += sum(last_price[p] for p in lineup.squad)
        limit = None if lineup is None else rules.transfer_limit(after_round=rnd - 1)

        ctx = RoundContext(
            season=data.season,
            round=rnd,
            market=market,
            history=visible_history(data.history, data.season, rnd),
            coach_history=visible_history(data.coach_history, data.season, rnd),
            rules=rules,
            lineup=lineup,
            budget=budget,
            transfer_limit=limit,
        )
        proj = strategy.project(ctx).reindex(market.index).fillna(0.0)
        projection_rows.append(
            pd.DataFrame(
                {
                    "round": rnd,
                    "player_id": market.index,
                    "position": market["position"].to_numpy(),
                    "projected": proj.to_numpy(),
                    "points": outcome["points"].reindex(market.index).fillna(0).to_numpy(),
                    "seconds": outcome["seconds"].reindex(market.index).fillna(0).to_numpy(),
                }
            )
        )
        if not simulate:
            continue

        new = strategy.pick(ctx, proj)
        cost = validate(new, ctx)
        transfers = 0 if lineup is None else len(new.squad - lineup.squad)
        lineup, bank = new, budget - cost
        last_price.update(market["price"].to_dict())

        played = projection_rows[-1]
        played = played[(played["seconds"] > 0) & (played["position"] != COACH)]
        round_rows.append(
            {
                "round": rnd,
                "points": score(lineup, outcome["points"], rules),
                "projected": score(lineup, proj, rules),
                "transfers": transfers,
                "transfer_limit": "unlimited" if limit is None else limit,
                "team_value": budget,
                "bank": bank,
                "captain": lineup.captain,
                "mae": (played["projected"] - played["points"]).abs().mean(),
            }
        )

    projections = pd.concat(projection_rows, ignore_index=True)
    rounds = pd.DataFrame(round_rows)
    if rounds.empty:  # projection-only season
        rounds = (
            projections[(projections["seconds"] > 0) & (projections["position"] != COACH)]
            .assign(err=lambda d: (d["projected"] - d["points"]).abs())
            .groupby("round", as_index=False)
            .agg(mae=("err", "mean"), players=("err", "size"))
        )
    projections = projections[projections["position"] != COACH]
    return SeasonResult(strategy.name, data.season, rounds, projections)
