import numpy as np
import pandas as pd
import pytest

from elfantasy.backtest.simulator import Lineup, RuleViolation, validate_turn
from elfantasy.rules import RULES_2026_27 as R
from elfantasy.turns import Choice, LineupSpace, best_start, simulate_policy

PLAYERS = ["G0", "G1", "G2", "G3", "F0", "F1", "F2", "F3", "C0", "C1"]
POS = {p: p[0] for p in PLAYERS}


@pytest.fixture(scope="module")
def space():
    return LineupSpace(PLAYERS, POS, R)


def vec(d: dict) -> np.ndarray:
    return np.array([d.get(p, 0.0) for p in PLAYERS], dtype=float)


def test_space_size_and_roundtrip(space):
    # 184 starting fives over the 5 formations, x 5 sixth men x 5 captains.
    assert len(space) == 184 * 5 * 5
    c = Choice(("G0", "G1", "F0", "F1", "C0"), "G2", "F0")
    assert space.choice(space.index(c)) == c
    with pytest.raises(ValueError):
        space.index(Choice(("G0", "G1", "G2", "G3", "C0"), "F0", "G0"))  # 4-0-1


def test_multipliers_follow_rules(space):
    c = Choice(("G0", "G1", "F0", "F1", "C0"), "G2", "F0")
    m = dict(zip(PLAYERS, space.mult[space.index(c)], strict=True))
    assert m["F0"] == R.starter_multiplier * R.captain_multiplier
    assert m["G0"] == R.starter_multiplier and m["G2"] == R.sixth_man_multiplier
    assert m["G3"] == R.bench_multiplier


def test_bench_a_bad_early_starter(space):
    """G0 started and scored 2 in turn 1; G3 on the bench plays later, expected 10.
    Swapping gains 0.5 x (10 - 2) = 4."""
    cur = space.index(Choice(("G0", "G1", "F0", "F1", "C0"), "F2", "F0"))
    played = np.array([p in {"G0", "G1", "F0", "C0"} for p in PLAYERS])
    values = vec({"G0": 2, "G1": 10, "F0": 15, "F1": 8, "C0": 9, "F2": 6, "G3": 10, "C1": 1})
    best = space.choice(space.best(cur, played, values))
    assert "G3" in best.starters and "G0" not in best.starters
    gain = space.mult[space.index(best)] @ values - space.mult[cur] @ values
    assert gain == pytest.approx(4.0)


def test_move_captain_to_a_later_player(space):
    """Captain F0 scored 3 in turn 1; F1 (not played, expected 12) takes the armband."""
    cur = space.index(Choice(("G0", "G1", "F0", "F1", "C0"), "F2", "F0"))
    played = np.array([p in {"G0", "F0"} for p in PLAYERS])
    values = vec({"G0": 8, "G1": 8, "F0": 3, "F1": 12, "C0": 8, "F2": 5})
    assert space.choice(space.best(cur, played, values)).captain == "F1"


def test_keep_a_good_captain(space):
    cur = space.index(Choice(("G0", "G1", "F0", "F1", "C0"), "F2", "F0"))
    played = np.array([p == "F0" for p in PLAYERS])
    values = vec({"G0": 8, "G1": 8, "F0": 30, "F1": 12, "C0": 8, "F2": 5})
    assert space.best(cur, played, values) == cur


def test_played_players_cannot_move_up(space):
    """Every lineup `legal_from` allows obeys the rule the simulator enforces."""
    cur_choice = Choice(("G0", "G1", "F0", "F1", "C0"), "F2", "F0")
    cur = space.index(cur_choice)
    played_set = {"G2", "F0", "C1"}
    played = np.array([p in played_set for p in PLAYERS])
    old = Lineup(tuple(PLAYERS), "HC", cur_choice.starters, cur_choice.sixth, cur_choice.captain)
    legal = np.flatnonzero(space.legal_from(cur, played))
    assert cur in legal
    for i in legal[::25]:
        c = space.choice(i)
        validate_turn(old, Lineup(tuple(PLAYERS), "HC", c.starters, c.sixth, c.captain), played_set)
    # G2 played on the bench, so a lineup starting him is not reachable.
    starts_g2 = [i for i in range(len(space)) if "G2" in space.choice(i).starters]
    assert not set(starts_g2) & set(legal.tolist())


def test_validate_turn_rejects_illegal_changes():
    old = Lineup(tuple(PLAYERS), "HC", ("G0", "G1", "F0", "F1", "C0"), "F2", "F0")
    up = Lineup(tuple(PLAYERS), "HC", ("G3", "G1", "F0", "F1", "C0"), "F2", "F0")
    with pytest.raises(RuleViolation, match="already played"):
        validate_turn(old, up, {"G3"})
    new_cap = Lineup(tuple(PLAYERS), "HC", old.starters, "F2", "G1")
    with pytest.raises(RuleViolation, match="captain"):
        validate_turn(old, new_cap, {"G1"})
    validate_turn(old, new_cap, {"F0"})  # the old captain played, the new one hasn't


def test_simulated_start_prefers_an_early_captain(space):
    """With identical players, a turn-1 captain is worth more than a turn-2 one:
    if he flops, the armband can still move."""
    pred = pd.DataFrame({"p_play": 1.0, "mean": 10.0, "q10": 0.0, "q50": 9.0, "q90": 22.0},
                        index=PLAYERS)  # fmt: skip
    pred["expected"] = pred["mean"]
    turns = {p: 1 if p in {"G0", "F0", "C0", "G1", "F1"} else 2 for p in PLAYERS}
    idx, best_mean, static_mean = best_start(space, pred, turns, shortlist=200, sims=400)
    assert turns[space.choice(idx).captain] == 1
    assert best_mean >= static_mean

    # The policy never does worse than standing still, scenario by scenario.
    rng = np.random.default_rng(1)
    actual = rng.normal(10, 6, (300, len(PLAYERS)))
    t = np.array([turns[p] for p in PLAYERS])
    exp = pred.loc[PLAYERS, "expected"].to_numpy()
    start = space.index(Choice(("G0", "G1", "F0", "F2", "C0"), "G2", "G0"))
    with_policy = simulate_policy(space, start, t, exp, actual)
    standing = actual @ space.mult[start]
    assert with_policy.mean() > standing.mean()


def test_turns_by_team_from_game_dates():
    import duckdb

    from elfantasy.backtest.turn_eval import turns_by_team

    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE games AS SELECT * FROM (VALUES
          ('E2025', 1, 'RS', '2025-10-01T18:00:00Z', 'AAA', 'BBB'),
          ('E2025', 1, 'RS', '2025-10-01T20:00:00Z', 'CCC', 'DDD'),
          ('E2025', 1, 'RS', '2025-10-02T19:00:00Z', 'EEE', 'FFF')
        ) t(season, round, phase, utc_date, home_code, away_code)
        """
    )
    t = turns_by_team(con, "E2025").set_index("team_code")["turn"]
    assert t["AAA"] == t["DDD"] == 1
    assert t["EEE"] == t["FFF"] == 2


def test_summarize_reports_gain_over_static():
    from elfantasy.backtest.turn_eval import summarize

    r = pd.DataFrame(
        {"season": "E2025", "round": [1, 1, 2, 2], "squad": [0, 1, 0, 1],
         "static": [100.0, 110, 90, 95], "adjust": [104.0, 112, 93, 95],
         "sim_start": [99.0, 110, 90, 94], "sim_start_adjust": [106.0, 114, 94, 97]}
    )  # fmt: skip
    s = summarize(r).set_index("policy")
    assert s.at["adjust", "gain_per_round"] == pytest.approx((3 + 1.5) / 2)
    assert s.at["sim_start_adjust", "share_of_rounds_better"] == 1.0
