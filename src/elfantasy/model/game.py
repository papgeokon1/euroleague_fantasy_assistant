"""Game outcome model: expected margin, win probability, and expected coach points.

margin = a + b x (team strength - opponent strength) + c x home, with normal errors.
Strength is the weighted point margin from features.py. Coach points come from
rules.coach_points, averaged over the margin distribution.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from elfantasy.rules import RULES_2026_27, GameRules

MARGINS = np.array([m for m in range(-60, 61) if m != 0])


@dataclass
class GameModel:
    intercept: float
    slope: float
    home_edge: float
    sigma: float

    @classmethod
    def fit(cls, x: pd.DataFrame) -> "GameModel":
        """`x`: one row per team game with team_str, opp_str, home, margin."""
        x = x.dropna(subset=["team_str", "opp_str", "margin"])
        a = np.column_stack(
            [np.ones(len(x)), x["team_str"] - x["opp_str"], x["home"].astype(float)]
        )
        coef, *_ = np.linalg.lstsq(a, x["margin"].to_numpy(dtype=float), rcond=None)
        sigma = float(np.std(x["margin"] - a @ coef))
        return cls(*map(float, coef), sigma)

    def expected_margin(self, team_str, opp_str, home) -> np.ndarray:
        diff = np.nan_to_num(np.asarray(team_str, float) - np.asarray(opp_str, float))
        return self.intercept + self.slope * diff + self.home_edge * np.asarray(home, float)

    def win_prob(self, team_str, opp_str, home) -> np.ndarray:
        return norm.cdf(self.expected_margin(team_str, opp_str, home) / self.sigma)

    def coach_points(self, team_str, opp_str, home, rules: GameRules = RULES_2026_27):
        """Expected coach points, ignoring overtime."""
        pts = np.array([rules.coach_points(int(m)) for m in MARGINS], dtype=float)
        mu = self.expected_margin(team_str, opp_str, home)[:, None]
        w = norm.pdf(MARGINS[None, :], loc=mu, scale=self.sigma)
        return (w * pts).sum(axis=1) / w.sum(axis=1)
