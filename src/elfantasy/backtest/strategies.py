"""Baseline strategies for the backtest.

Every strategy here shares the same picking logic (`GreedyPicker`) and differs only
in how it projects points, so a better projection shows up directly as a better
season score. The real optimizer (Phase 5) will replace the picker.
"""

from collections import Counter

import pandas as pd

from elfantasy.backtest.data import COACH
from elfantasy.backtest.simulator import EPS, Lineup, RoundContext
from elfantasy.rules import GameRules


class GreedyPicker:
    """Squad by projected points per credit, then upgrades with the money left.
    With a transfer limit it makes the best single swaps instead of rebuilding."""

    def pick(self, ctx: RoundContext, projections: pd.Series) -> Lineup:
        m = ctx.market.assign(proj=projections.reindex(ctx.market.index).fillna(0.0))
        if ctx.lineup is None or ctx.transfer_limit is None:
            squad = self._build(m, ctx.budget, ctx.rules)
        else:
            squad = self._swap(m, ctx.lineup.squad, ctx.budget, ctx.transfer_limit, ctx.rules)
        return self._lineup(m, squad, ctx.rules)

    # -- squad ----------------------------------------------------------------

    @staticmethod
    def _slots(rules: GameRules) -> dict[str, int]:
        slots = {pos.value: n for pos, n in rules.squad_positions.items()}
        if rules.has_head_coach:
            slots[COACH] = 1
        return slots

    def _build(self, m: pd.DataFrame, budget: float, rules: GameRules) -> set:
        need = self._slots(rules)
        cheapest = {pos: sorted(m.loc[m["position"] == pos, "price"]) for pos in need}

        def min_fill(open_slots: dict[str, int]) -> float:
            return sum(sum(cheapest[pos][:n]) for pos, n in open_slots.items())

        squad: set = set()
        clubs: Counter = Counter()
        left = budget
        order = m.assign(value=m["proj"] / m["price"]).sort_values(
            ["value", "proj"], ascending=False
        )
        for pid, r in order.iterrows():
            pos = r["position"]
            if need[pos] == 0:
                continue
            if pos != COACH and clubs[r["team_code"]] >= rules.max_players_per_club:
                continue
            after = {**need, pos: need[pos] - 1}
            if r["price"] + min_fill(after) > left + EPS:
                continue
            squad.add(pid)
            need = after
            left -= r["price"]
            if pos != COACH:
                clubs[r["team_code"]] += 1
            if not any(need.values()):
                break
        if any(need.values()):
            raise RuntimeError(f"could not fill the squad within {budget:.1f}: {need}")
        return self._upgrade(m, squad, budget, rules)

    def _upgrade(self, m: pd.DataFrame, squad: set, budget: float, rules: GameRules) -> set:
        """Repeatedly make the swap that adds the most projected points and fits."""
        while True:
            best = self._best_swap(m, squad, budget, rules)
            if best is None:
                return squad
            out, inn = best
            squad = (squad - {out}) | {inn}

    def _swap(self, m, squad: frozenset, budget: float, limit: int, rules: GameRules) -> set:
        # A player who left the game's market must be replaced; that uses a transfer.
        squad = {p for p in squad if p in m.index}
        used = sum(self._slots(rules).values()) - len(squad)
        squad = self._refill(m, squad, budget, rules)
        for _ in range(max(0, limit - used)):
            best = self._best_swap(m, squad, budget, rules)
            if best is None:
                break
            out, inn = best
            squad = (squad - {out}) | {inn}
        return squad

    def _refill(self, m: pd.DataFrame, squad: set, budget: float, rules: GameRules) -> set:
        have = Counter(m.loc[list(squad), "position"])
        for pos, n in self._slots(rules).items():
            for _ in range(n - have[pos]):
                left = budget - m.loc[list(squad), "price"].sum()
                pool = m[(m["position"] == pos) & ~m.index.isin(squad) & (m["price"] <= left)]
                if pos != COACH:
                    sq = m.loc[[p for p in squad if m.at[p, "position"] != COACH]]
                    full = sq["team_code"].value_counts()
                    full = full[full >= rules.max_players_per_club].index
                    pool = pool[~pool["team_code"].isin(full)]
                squad.add(pool.sort_values("proj", ascending=False).index[0])
        return squad

    @staticmethod
    def _best_swap(m: pd.DataFrame, squad: set, budget: float, rules: GameRules):
        """The single same-position swap that adds the most projected points while
        staying within budget and the club limit, or None."""
        cost = m.loc[list(squad), "price"].sum()
        clubs = Counter(m.loc[[p for p in squad if m.at[p, "position"] != COACH], "team_code"])
        pool = m[~m.index.isin(squad)].sort_values("proj", ascending=False)
        best, best_gain = None, EPS
        for out in squad:
            o = m.loc[out]
            cands = pool[
                (pool["position"] == o["position"])
                & (pool["price"] <= budget - cost + o["price"] + EPS)
                & (pool["proj"] > o["proj"] + best_gain)
            ]
            for inn, c in cands.iterrows():  # best first
                club_full = clubs[c["team_code"]] >= rules.max_players_per_club
                if o["position"] != COACH and c["team_code"] != o["team_code"] and club_full:
                    continue
                best, best_gain = (out, inn), c["proj"] - o["proj"]
                break
        return best

    # -- lineup ---------------------------------------------------------------

    @staticmethod
    def _lineup(m: pd.DataFrame, squad: set, rules: GameRules) -> Lineup:
        sq = m.loc[list(squad)]
        coach = sq.index[sq["position"] == COACH][0]
        players = sq[sq["position"] != COACH].sort_values("proj", ascending=False)
        by_pos = {pos: list(players.index[players["position"] == pos]) for pos in "GFC"}
        best, best_val = None, float("-inf")
        for g, f, c in rules.starting_formations:
            starters = by_pos["G"][:g] + by_pos["F"][:f] + by_pos["C"][:c]
            if len(starters) != 5:
                continue
            rest = [p for p in players.index if p not in starters]
            sixth = rest[0]
            proj = players["proj"]
            val = (
                proj[starters].sum() * rules.starter_multiplier
                + proj[starters].max() * (rules.captain_multiplier - 1)
                + proj[sixth] * rules.sixth_man_multiplier
                + proj[rest[1:]].sum() * rules.bench_multiplier
            )
            if val > best_val:
                captain = proj[starters].idxmax()
                best, best_val = (tuple(starters), sixth, captain), val
        starters, sixth, captain = best
        return Lineup(tuple(players.index), coach, starters, sixth, captain)


def _player_mean(hist: pd.DataFrame, codes: pd.Series, last_n: int | None) -> pd.Series:
    played = hist[hist["seconds"] > 0]
    if last_n:
        played = played.groupby("el_code").tail(last_n)
    means = played.groupby("el_code")["fantasy_points"].mean()
    return codes.map(means)


def _coach_mean(hist: pd.DataFrame, teams: pd.Series, last_n: int | None) -> pd.Series:
    if last_n:
        hist = hist.groupby("team_code").tail(last_n)
    return teams.map(hist.groupby("team_code")["fantasy_points"].mean())


class SeasonAverage(GreedyPicker):
    """Average fantasy points this season so far; last season's average in round 1
    or for players new to this season."""

    name = "season_avg"

    def project(self, ctx: RoundContext) -> pd.Series:
        m = ctx.market
        h, ch = ctx.history, ctx.coach_history
        this = h[h["season"] == ctx.season]
        prev_season = h.loc[h["season"] < ctx.season, "season"].max()
        prev = h[h["season"] == prev_season]
        players = _player_mean(this, m["el_code"], None).fillna(
            _player_mean(prev, m["el_code"], None)
        )
        cthis = ch[ch["season"] == ctx.season]
        cprev = ch[ch["season"] == prev_season]
        coaches = _coach_mean(cthis, m["el_team"], None).fillna(
            _coach_mean(cprev, m["el_team"], None)
        )
        return players.where(m["position"] != COACH, coaches).fillna(0.0)


class LastFive(GreedyPicker):
    """Average of each player's last 5 games played, across seasons."""

    name = "last5"

    def project(self, ctx: RoundContext) -> pd.Series:
        m = ctx.market
        players = _player_mean(ctx.history, m["el_code"], 5)
        coaches = _coach_mean(ctx.coach_history, m["el_team"], 5)
        return players.where(m["position"] != COACH, coaches).fillna(0.0)


STRATEGIES = {s.name: s for s in (SeasonAverage, LastFive)}
