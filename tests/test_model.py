import duckdb
import numpy as np
import pandas as pd
import pytest

from elfantasy.model.features import (
    FEATURES,
    Logs,
    build_features,
    team_game_features,
    training_targets,
)
from elfantasy.model.game import GameModel
from elfantasy.model.projection import ProjectionModel
from elfantasy.rules import RULES_2026_27

S = "E2026"
TEAMS = ["AAA", "BBB", "CCC", "DDD"]


def _db(
    n_rounds: int = 6, seed: int = 0, bump_after: int | None = None
) -> duckdb.DuckDBPyConnection:
    """A tiny league: 4 teams, 4 players each, one game per team per round.
    `bump_after` makes every game after that round score wildly differently, to
    check that features for earlier rounds don't change."""
    rng = np.random.default_rng(seed)
    games, players = [], []
    code = 0
    for rnd in range(1, n_rounds + 1):
        pairs = [(TEAMS[0], TEAMS[1]), (TEAMS[2], TEAMS[3])] if rnd % 2 else [
            (TEAMS[0], TEAMS[2]), (TEAMS[1], TEAMS[3])]  # fmt: skip
        for g, (h, a) in enumerate(pairs):
            code += 1
            hs, as_ = int(rng.integers(70, 100)), int(rng.integers(70, 100))
            if hs == as_:
                hs += 1
            t = f"2026-10-{rnd:02d}T{18 + g}:00:00Z"
            games.append(dict(season=S, game_code=code, round=rnd, phase="RS", utc_date=t,
                              played=True, home_code=h, away_code=a, home_score=hs,
                              away_score=as_))  # fmt: skip
            for team, opp in ((h, a), (a, h)):
                for k, pos in enumerate(["Guard", "Guard", "Forward", "Center"]):
                    fp = float(rng.normal(8 + 2 * k, 4))
                    if bump_after is not None and rnd > bump_after:
                        fp += 100
                    seconds = 0.0 if (k == 3 and rnd % 3 == 0) else 1200.0
                    players.append(dict(season=S, game_code=code, round=rnd, phase="RS",
                                        utc_date=t, player_code=f"{team}{k}", team_code=team,
                                        opp_code=opp, position=pos, seconds=seconds,
                                        fantasy_points=fp if seconds else 0.0))  # fmt: skip
    con = duckdb.connect()
    g, p = pd.DataFrame(games), pd.DataFrame(players)  # noqa: F841
    con.execute("CREATE TABLE games AS SELECT * FROM g")
    con.execute("CREATE TABLE player_games AS SELECT * FROM p")
    return con


def test_features_ignore_future_games():
    targets = pd.DataFrame({"season": S, "round": 4, "el_code": ["AAA0", "BBB3"],
                            "team_code": ["AAA", "BBB"]})  # fmt: skip
    before = build_features(Logs.load(_db()), targets)
    after = build_features(Logs.load(_db(bump_after=3)), targets)  # rounds 4+ changed
    pd.testing.assert_frame_equal(before[FEATURES], after[FEATURES])
    # ...while a change to round 3 does show up.
    changed = build_features(Logs.load(_db(bump_after=2)), targets)
    assert not np.allclose(before["ew_fp"], changed["ew_fp"])


def test_feature_values():
    logs = Logs.load(_db())
    x = build_features(
        logs, pd.DataFrame({"season": S, "round": [1, 4], "el_code": "AAA0", "team_code": "AAA"})
    )
    first, fourth = x.iloc[0], x.iloc[1]
    assert np.isnan(first["ew_fp"]) and first["team_gp"] == 0
    assert fourth["team_gp"] == 3 and fourth["season_gp"] == 3 and fourth["games_missed"] == 0
    rows = logs.player[(logs.player["el_code"] == "AAA0") & (logs.player["round"] < 4)]
    assert fourth["season_mean"] == pytest.approx(rows["fp"].mean())


def test_games_missed_counts_absences():
    logs = Logs.load(_db())
    # Player 3 sits out rounds 3 and 6 (0 seconds), so before round 4 he has missed 1.
    x = build_features(
        logs, pd.DataFrame({"season": S, "round": [4], "el_code": "AAA3", "team_code": "AAA"})
    )
    assert x.iloc[0]["games_missed"] == 1


def test_training_targets_labels():
    logs = Logs.load(_db())
    t = training_targets(logs, [S])
    assert len(t) == 6 * 4 * 4  # every player is a candidate every round
    sat_out = t[(t["el_code"] == "AAA3") & (t["round"] == 3)]
    assert not sat_out["played"].iloc[0]


def test_projection_model_fits_and_predicts():
    logs = Logs.load(_db(n_rounds=12))
    x = build_features(logs, training_targets(logs, [S]))
    model = ProjectionModel.fit(x, (S,))
    pred = model.predict(x)
    assert pred["p_play"].between(0, 1).all()
    assert (pred["q10"] <= pred["q50"]).all() and (pred["q50"] <= pred["q90"]).all()
    assert np.allclose(pred["expected"], pred["p_play"] * pred["mean"])


def test_game_model():
    logs = Logs.load(_db(n_rounds=12))
    model = GameModel.fit(team_game_features(logs))
    assert model.sigma > 0
    p = model.win_prob([10, -10], [0, 0], [0.5, 0.5])
    assert p[0] > 0.5 > p[1]
    # Expected coach points stay within the rules' best and worst outcomes.
    pts = model.coach_points([30, 0, -30], [0, 0, 0], [1, 1, 0], RULES_2026_27)
    assert pts[0] > pts[1] > pts[2]
    assert pts.min() >= -20 and pts.max() <= 25


def test_training_rows_come_in_a_fixed_order():
    """Row order feeds LightGBM's row sampling, so it must not depend on Python's
    per-process set ordering."""
    t = training_targets(Logs.load(_db()), [S])
    for _, g in t.groupby(["season", "round", "team_code"], sort=False):
        assert list(g["el_code"]) == sorted(g["el_code"])
