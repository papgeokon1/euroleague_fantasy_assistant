"""Price changes: fitted from the price history, predicted before a round.

The game doesn't publish its formula, but the 2026-27 history fits a simple rule
almost exactly. After a round in which a player played:

    change = round(a x fantasy_points + b x price + c, to price_step)

with a ~ 0.04 and b ~ -0.046. A player needs about 1.18 x price - 0.9 points to keep
his value, and for the same score a cheaper player rises more. A player who didn't
play loses a flat amount (0.1). Prices never drop below rules.min_price. Coaches have
their own coefficients, driven by their result band.

Before a round the points are unknown, so the expected change is
P(plays) x (a x E[points | plays] + b x price + c) + (1 - P(plays)) x dnp_change.
"""

from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd

from elfantasy.rules import GameRules

MIN_ROWS = 50  # below this, there isn't enough history to fit


def price_history(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """One row per player per round with this round's price, next round's price, the
    fantasy points scored in the round and whether he played. `round` is the round
    the points were scored in; next_price is known at the next round's deadline."""
    return con.execute(
        """
        WITH p AS (
            SELECT round, player_id, position, price FROM fantasy_prices WHERE season = $s
        ), nxt AS (
            SELECT a.*, b.price AS next_price
            FROM p a JOIN p b ON a.player_id = b.player_id AND b.round = a.round + 1
        ), players AS (
            SELECT m.fantasy_player_id AS player_id, g.round,
                   sum(g.fantasy_points) AS fp, sum(g.seconds) AS seconds
            FROM player_map m
            JOIN player_games g
              ON g.player_code = m.el_code AND g.season = m.season AND g.phase = 'RS'
            WHERE m.season = $s AND m.kind = 'player'
            GROUP BY ALL
        ), coaches AS (
            SELECT DISTINCT m.fantasy_player_id AS player_id, c.round,
                   c.fantasy_points AS fp, 1.0 AS seconds
            FROM player_map m
            JOIN coach_games c
              ON c.coach_code = m.el_code AND c.season = m.season AND c.phase = 'RS'
            WHERE m.season = $s AND m.kind = 'coach'
        ), pts AS (SELECT * FROM players UNION ALL SELECT * FROM coaches)
        SELECT n.round, n.player_id, n.position = 'Head Coach' AS is_coach, n.price,
               n.next_price, coalesce(pts.fp, 0) AS fp,
               coalesce(pts.seconds, 0) > 0 AS played
        FROM nxt n LEFT JOIN pts USING (player_id, round)
        """,
        {"s": season},
    ).df()


@dataclass
class PriceModel:
    player_coef: np.ndarray  # (a, b, c) for players who played
    coach_coef: np.ndarray  # (a, b, c) for coaches
    dnp_change: float  # change for a player who didn't play
    rules: GameRules

    @classmethod
    def fit(cls, history: pd.DataFrame, rules: GameRules) -> "PriceModel | None":
        """Fit on observed changes. Rows at the price floor are left out, since the
        floor hides what the formula would have done. None if history is too thin."""
        h = history.assign(change=history["next_price"] - history["price"])
        free = h[h["price"] > rules.min_price + 1e-9]
        played = free[free["played"] & ~free["is_coach"]]
        coaches = free[free["is_coach"]]
        missed = free[~free["played"] & ~free["is_coach"]]
        if len(played) < MIN_ROWS or len(coaches) < 5:
            return None
        return cls(
            player_coef=_lstsq(played),
            coach_coef=_lstsq(coaches),
            dnp_change=float(missed["change"].median()) if len(missed) else -rules.price_step,
            rules=rules,
        )

    def change(self, price, fp, played, is_coach) -> np.ndarray:
        """Price change after a round with known points (rounded, floor applied)."""
        price, fp = np.asarray(price, float), np.asarray(fp, float)
        raw = np.where(
            np.asarray(is_coach, bool),
            _linear(self.coach_coef, fp, price),
            np.where(np.asarray(played, bool), _linear(self.player_coef, fp, price),
                     self.dnp_change),
        )  # fmt: skip
        return self._finish(price, raw)

    def expected_change(self, price, p_play, mean_if_plays, is_coach) -> np.ndarray:
        """Expected change before the round, from the projection. Not rounded: it's
        an average over outcomes."""
        price = np.asarray(price, float)
        p = np.asarray(p_play, float)
        mean = np.asarray(mean_if_plays, float)
        coach = np.asarray(is_coach, bool)
        played = _linear(self.player_coef, mean, price)
        raw = np.where(coach, _linear(self.coach_coef, mean, price),
                       p * played + (1 - p) * self.dnp_change)  # fmt: skip
        return np.maximum(raw, self.rules.min_price - price)

    def break_even(self, price) -> np.ndarray:
        """Points a playing player needs to keep his price."""
        a, b, c = self.player_coef
        return -(b * np.asarray(price, float) + c) / a

    def rise_probability(self, price, pred: pd.DataFrame) -> np.ndarray:
        """P(price goes up) from the projection: P(plays) x P(points > break-even),
        with the points distribution read off the 10/50/90% quantiles."""
        be = self.break_even(price)
        q = pred[["q10", "q50", "q90"]].to_numpy(dtype=float)
        lo = q[:, 0] - 0.6 * (q[:, 1] - q[:, 0])
        hi = q[:, 2] + 0.6 * (q[:, 2] - q[:, 1])
        xs = np.column_stack([lo, q, hi])
        cdf = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
        below = np.array([np.interp(be[i], xs[i], cdf) for i in range(len(be))])
        return pred["p_play"].to_numpy(dtype=float) * (1 - below)

    def _finish(self, price: np.ndarray, raw: np.ndarray) -> np.ndarray:
        step = self.rules.price_step
        new = np.maximum(self.rules.min_price, np.round((price + np.round(raw / step) * step), 1))
        return new - price


def _lstsq(rows: pd.DataFrame) -> np.ndarray:
    a = np.column_stack([rows["fp"], rows["price"], np.ones(len(rows))])
    coef, *_ = np.linalg.lstsq(a, (rows["next_price"] - rows["price"]).to_numpy(), rcond=None)
    return coef


def _linear(coef: np.ndarray, fp, price) -> np.ndarray:
    return coef[0] * fp + coef[1] * price + coef[2]
