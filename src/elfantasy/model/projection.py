"""Player projections: will he play, and how many fantasy points if he does.

Expected points = P(plays) x E[points | plays]. The split matters because missing
games is the biggest source of uncertainty, and the game has no automatic subs: a
starter who doesn't play scores 0.

Quantiles of points-if-he-plays (10th, 50th, 90th) give the range of outcomes;
captain picks care about the upside, not just the average.
"""

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from elfantasy.model.features import FEATURES

QUANTILES = (0.1, 0.5, 0.9)
PARAMS = dict(
    n_estimators=400,
    learning_rate=0.03,
    num_leaves=31,
    min_child_samples=50,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    verbose=-1,
    random_state=0,  # same data -> same model, so backtests are reproducible
    deterministic=True,
    force_row_wise=True,
)


@dataclass
class ProjectionModel:
    play: lgb.LGBMClassifier
    mean: lgb.LGBMRegressor
    quantiles: dict[float, lgb.LGBMRegressor]
    trained_on: tuple[str, ...]

    @classmethod
    def fit(cls, x: pd.DataFrame, seasons: tuple[str, ...]) -> "ProjectionModel":
        """`x` holds FEATURES plus labels `played` and `fp` (training_targets output
        passed through build_features)."""
        x = x[x["season"].isin(seasons)]
        play = lgb.LGBMClassifier(**PARAMS).fit(x[FEATURES], x["played"].astype(int))
        p = x[x["played"]]
        mean = lgb.LGBMRegressor(**PARAMS).fit(p[FEATURES], p["fp"])
        quantiles = {
            q: lgb.LGBMRegressor(objective="quantile", alpha=q, **PARAMS).fit(p[FEATURES], p["fp"])
            for q in QUANTILES
        }
        return cls(play, mean, quantiles, tuple(seasons))

    def predict(self, x: pd.DataFrame) -> pd.DataFrame:
        f = x[FEATURES]
        out = pd.DataFrame(index=x.index)
        out["p_play"] = self.play.predict_proba(f)[:, 1]
        out["mean"] = self.mean.predict(f)
        for q, m in self.quantiles.items():
            out[f"q{int(q * 100)}"] = m.predict(f)
        # Independent quantile models can cross; keep them ordered.
        qs = [f"q{int(q * 100)}" for q in QUANTILES]
        out[qs] = np.sort(out[qs].to_numpy(), axis=1)
        out["expected"] = out["p_play"] * out["mean"]
        return out
