"""Baseline strategies for the backtest.

Every strategy here shares the same picking logic (`GreedyPicker`) and differs only
in how it projects points, so a better projection shows up directly as a better
season score. The real optimizer (Phase 5) will replace the picker.
"""

from collections import Counter

import numpy as np
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


# Bump when features or model settings change, so cached models are retrained.
MODEL_VERSION = 3


class ModelV1(GreedyPicker):
    """Phase 4 projections: LightGBM play probability and points-if-he-plays, plus the
    game model for coaches.

    `project` returns the median points if the player plays (the number MAE is
    measured on). The squad is picked on expected points, P(plays) x mean, so a
    player likely to miss the game is avoided.

    Models are trained only on seasons before the one being replayed, and features
    only use games before each round's deadline.
    """

    name = "model_v1"

    def __init__(self, db_path=None, con=None):
        """Reads the database at `db_path` (default: settings), or through an
        already-open connection `con`."""
        self.db_path = db_path
        self.con = con
        self._logs = None
        self._features = None
        self._models: dict[str, tuple] = {}
        self._expected: pd.Series | None = None

    def _load(self):
        if self._logs is None:
            import duckdb

            from elfantasy.config import settings
            from elfantasy.model.features import Logs

            if self.con is not None:
                self._logs = Logs.load(self.con)
            else:
                path = self.db_path or settings.db_path
                with duckdb.connect(str(path), read_only=True) as con:
                    self._logs = Logs.load(con)
        return self._logs

    def _training_features(self) -> pd.DataFrame:
        if self._features is None:
            from elfantasy.model.features import build_features, training_targets

            logs = self._load()
            seasons = sorted(logs.player["season"].unique())
            self._features = build_features(logs, training_targets(logs, seasons))
        return self._features

    def _fit(self, season: str):
        """Models for `season`, trained on all earlier seasons. Cached in models/,
        keyed by the training data, so a later run with the same data skips training."""
        if season not in self._models:
            import hashlib
            import pickle

            from elfantasy.config import settings
            from elfantasy.model.features import team_game_features
            from elfantasy.model.game import GameModel
            from elfantasy.model.projection import ProjectionModel

            logs = self._load()
            train = tuple(s for s in sorted(logs.player["season"].unique()) if s < season)
            if not train:
                raise ValueError(f"no seasons before {season} to train on")
            used = logs.player[logs.player["season"].isin(train)]
            key = f"{MODEL_VERSION}|{train}|{len(used)}|{used['fp'].sum():.2f}"
            digest = hashlib.sha1(key.encode()).hexdigest()[:12]
            # One cache for every strategy built on these projections.
            path = settings.models_dir / f"model_v1_{season}_{digest}.pkl"
            if path.exists():
                self._models[season] = pickle.loads(path.read_bytes())
            else:
                games = team_game_features(logs)
                self._models[season] = (
                    ProjectionModel.fit(self._training_features(), train),
                    GameModel.fit(games[games["season"].isin(train)]),
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(pickle.dumps(self._models[season]))
        return self._models[season]

    def predict(self, ctx: RoundContext, target_round: int | None = None) -> pd.DataFrame:
        """Per market player: p_play, mean, q10, q50, q90, expected (coaches: expected
        coach points in `expected` and `q50`).

        `target_round` projects a later round using only what is known before
        ctx.round's deadline (that round's matchups come from the schedule)."""
        target = ctx.round if target_round is None else target_round
        from elfantasy.model.features import build_features

        logs = self._load()
        players, games = self._fit(ctx.season)
        m = ctx.market
        is_coach = m["position"] == COACH
        out = pd.DataFrame(0.0, index=m.index, columns=["p_play", "mean", "q10", "q50", "q90"])

        known = m[~is_coach & m["el_code"].notna()]
        if len(known):
            targets = pd.DataFrame(
                {"season": ctx.season, "round": target, "as_of_round": ctx.round,
                 "el_code": known["el_code"].to_numpy(), "team_code": known["el_team"].to_numpy()}
            )  # fmt: skip
            pred = players.predict(build_features(logs, targets))
            pred.index = known.index
            out.loc[known.index, pred.columns.drop("expected")] = pred.drop(columns="expected")

        coaches = m[is_coach]
        if len(coaches):
            targets = pd.DataFrame(
                {"season": ctx.season, "round": target, "as_of_round": ctx.round,
                 "el_code": "__coach__", "team_code": coaches["el_team"].to_numpy()}
            )  # fmt: skip
            x = build_features(logs, targets)
            pts = games.coach_points(x["team_str"], x["opp_str"], x["home"].fillna(0.5), ctx.rules)
            out.loc[coaches.index, ["p_play", "mean", "q50"]] = np.column_stack(
                [np.ones(len(pts)), pts, pts]
            )
            out.loc[coaches.index, ["q10", "q90"]] = np.nan  # only the mean is modelled
        out["expected"] = out["p_play"] * out["mean"]
        return out

    def project(self, ctx: RoundContext) -> pd.Series:
        pred = self.predict(ctx)
        self._pred = pred
        self._expected = pred["expected"]
        return pred["q50"]

    def pick(self, ctx: RoundContext, projections: pd.Series) -> Lineup:
        # Pick on expected points (which account for the chance of not playing).
        return super().pick(ctx, self._expected.reindex(ctx.market.index).fillna(0.0))


class ModelILP(ModelV1):
    """model_v1 projections, picked by the integer-programming optimizer over the
    next `horizon` rounds instead of the greedy one-round picker."""

    name = "model_ilp"
    horizon = 3
    value_weight = 0.0  # points per credit of expected price rise (Phase 6)

    def pick(self, ctx: RoundContext, projections: pd.Series) -> Lineup:
        from elfantasy.optimize import optimize

        logs = self._load()
        last = int(logs.schedule.loc[logs.schedule["season"] == ctx.season, "round"].max())
        rounds = list(range(ctx.round, min(ctx.round + self.horizon, last + 1)))
        preds = {r: self._pred if r == ctx.round else self.predict(ctx, r) for r in rounds}
        proj = pd.DataFrame({r: preds[r]["expected"] for r in rounds})
        value = self._value(ctx, preds) if self.value_weight else None
        limits = [ctx.transfer_limit] + [
            ctx.rules.transfer_limit(after_round=r - 1) for r in rounds[1:]
        ]
        current = ctx.lineup.squad if ctx.lineup else None
        plan = optimize(ctx.market, proj, ctx.rules, ctx.budget, current, limits,
                        value=value, value_weight=self.value_weight)  # fmt: skip
        coach = next(p for p in plan.squads[0] if ctx.market.at[p, "position"] == COACH)
        players = tuple(sorted(plan.squads[0] - {coach}, key=str))
        return Lineup(
            players, coach, tuple(sorted(plan.starters[0], key=str)), plan.sixth[0],
            plan.captain[0],
        )  # fmt: skip

    def _value(self, ctx: RoundContext, preds: dict) -> pd.DataFrame | None:
        """Expected price change per player for each horizon round, from a price
        model fitted only on changes already published before this round."""
        from elfantasy.model.prices import PriceModel, price_history

        if getattr(self, "_prices", None) is None:
            import duckdb

            from elfantasy.config import settings

            if self.con is not None:
                self._prices = price_history(self.con, ctx.season)
            else:
                path = self.db_path or settings.db_path
                with duckdb.connect(str(path), read_only=True) as con:
                    self._prices = price_history(con, ctx.season)
        model = PriceModel.fit(self._prices[self._prices["round"] < ctx.round], ctx.rules)
        if model is None:
            return None
        m = ctx.market
        is_coach = m["position"] == COACH
        return pd.DataFrame(
            {r: model.expected_change(m["price"], p["p_play"], p["mean"], is_coach)
             for r, p in preds.items()},
            index=m.index,
        )  # fmt: skip


class ModelTurns(ModelILP):
    """model_ilp plus the Phase 5b turn strategy: the starting lineup is chosen by
    simulating the round with in-round changes in play, and after each turn the
    lineup and captain are re-optimised with the scores known so far."""

    name = "model_turns"
    simulate_start = True

    def pick(self, ctx: RoundContext, projections: pd.Series) -> Lineup:
        from elfantasy.turns import LineupSpace, best_start

        lineup = super().pick(ctx, projections)
        self._space = LineupSpace(list(lineup.players), ctx.market["position"].to_dict(), ctx.rules)
        if not self.simulate_start:
            return lineup
        idx, _, _ = best_start(self._space, self._pred, ctx.market["turn"].to_dict())
        c = self._space.choice(idx)
        return Lineup(lineup.players, lineup.coach, c.starters, c.sixth, c.captain)

    def adjust(self, t) -> Lineup | None:
        from elfantasy.turns import Choice

        space = self._space
        players = space.players
        played = np.array([p in t.played for p in players])
        known = t.points.reindex(players).fillna(0.0).to_numpy(dtype=float)
        expected = self._expected.reindex(players).fillna(0.0).to_numpy(dtype=float)
        current = space.index(Choice(t.lineup.starters, t.lineup.sixth, t.lineup.captain))
        best = space.best(current, played, np.where(played, known, expected))
        if best == current:
            return None
        c = space.choice(best)
        return Lineup(t.lineup.players, t.lineup.coach, c.starters, c.sixth, c.captain)


class ModelILPAdjust(ModelTurns):
    """model_ilp's starting lineup, with only the between-turn changes added."""

    name = "model_ilp_adjust"
    simulate_start = False


class ModelValue(ModelTurns):
    """model_turns plus the Phase 6 value-growth term: the optimizer is paid
    `value_weight` points per credit of expected price rise."""

    name = "model_value"
    value_weight = 5.0


class ModelValue10(ModelValue):
    name = "model_value10"
    value_weight = 10.0


STRATEGIES = {
    s.name: s
    for s in (SeasonAverage, LastFive, ModelV1, ModelILP, ModelILPAdjust, ModelTurns,
              ModelValue, ModelValue10)
}  # fmt: skip
