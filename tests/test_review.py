import duckdb
import numpy as np
import pandas as pd
import pytest

from elfantasy import review as rv

S = "E2026"


def pred_frame(ids=(1, 2, 3, 4)):
    return pd.DataFrame(
        {"position": ["G", "F", "C", "HC"][: len(ids)], "team_code": "AAA", "price": 10.0,
         "turn": 1, "el_code": [f"P{i}" for i in ids[:-1]] + [None],
         "p_play": [0.9, 0.9, 0.5, 1.0], "mean": [12.0, 10.0, 8.0, 10.0],
         "q10": [4.0, 3.0, 1.0, np.nan], "q50": [11.0, 9.0, 7.0, 10.0],
         "q90": [20.0, 18.0, 15.0, np.nan], "expected": [10.8, 9.0, 4.0, 10.0]},
        index=pd.Index(list(ids), name="player_id"),
    )  # fmt: skip


def test_accuracy_and_calibration():
    pred = pred_frame()
    actual = pd.DataFrame({"points": [25.0, 9.0], "seconds": [1200.0, 900.0]},
                          index=pd.Index([1, 2], name="player_id"))  # fmt: skip
    a = rv.accuracy(pred, actual)
    assert a["players"] == 2  # the coach is left out; player 3 didn't play
    assert a["mae"] == pytest.approx((14 + 0) / 2)
    assert a["within_10_90"] == pytest.approx(0.5)  # 25 is above player 1's 90% value
    assert a["above_90"] == pytest.approx(0.5)
    assert a["p_play_actual"] == pytest.approx(2 / 3)


def test_slot_mapping():
    assert rv._slot(11, False) == "coach"
    assert rv._slot(3, True) == "captain"
    assert rv._slot(5, False) == "starter"
    assert rv._slot(6, False) == "sixth"
    assert rv._slot(9, False) == "bench"


def test_live_predictions_win_over_reconstructed():
    con = duckdb.connect()
    market = pred_frame()[["position", "team_code", "price", "turn", "el_code"]]
    pred = pred_frame()
    rv.log_predictions(con, S, 5, market, pred, "reconstructed", "v")
    assert rv.predictions_for(con, S, 5)[1] == "reconstructed"
    rv.log_predictions(con, S, 5, market, pred.assign(expected=99.0), "live", "v")
    logged, source = rv.predictions_for(con, S, 5)
    assert source == "live" and (logged["expected"] == 99.0).all()
    assert rv.predictions_for(con, S, 6) is None


def test_benchmark_aggregates_and_replacement():
    con = duckdb.connect()
    standings = [{"matchday_pts": p} for p in [100.0, 90.0, 80.0] + [50.0] * 30]
    rv.log_benchmark(con, S, 3, standings)
    rv.log_benchmark(con, S, 3, standings)  # re-running replaces, doesn't duplicate
    rows = con.execute("SELECT top1, top10_avg, top25_avg FROM benchmark_rounds").fetchall()
    assert len(rows) == 1
    top1, top10, top25 = rows[0]
    assert top1 == 100.0
    assert top10 == pytest.approx((100 + 90 + 80 + 50 * 7) / 10)
    assert top25 == pytest.approx((100 + 90 + 80 + 50 * 22) / 25)


def test_manager_round_replaces_the_round():
    con = duckdb.connect()
    roster = [{"id": 1, "court_position": 1, "is_captain": True, "pts": 20, "quotation": 10}]
    rv.log_manager_round(con, S, 4, 77, {"pts": 150.0, "total_pts": 600.0}, roster)
    rv.log_manager_round(con, S, 4, 77, {"pts": 151.0, "total_pts": 601.0}, roster)
    assert con.execute("SELECT points FROM manager_rounds").fetchall() == [(151.0,)]
    assert con.execute("SELECT count(*) FROM manager_rosters").fetchone()[0] == 1


def test_round_played_needs_every_box_score():
    con = duckdb.connect()
    games = pd.DataFrame({"season": S, "round": 2, "phase": "RS", "game_code": [1, 2],  # noqa: F841
                          "played": [True, True]})  # fmt: skip
    con.execute("CREATE TABLE games AS SELECT * FROM games")
    stats = pd.DataFrame({"season": S, "round": 2, "game_code": [1]})  # noqa: F841
    con.execute("CREATE TABLE player_games AS SELECT * FROM stats")
    assert not rv.round_played(con, S, 2)
    con.execute("INSERT INTO player_games VALUES ('E2026', 2, 2)")
    assert rv.round_played(con, S, 2)
    assert not rv.round_played(con, S, 3)


def test_headline():
    line = rv.headline(
        {
            "round": 14,
            "your_points": 172.4,
            "benchmark": {"top10_avg": 230.0},
            "recommended_points": 168.0,
            "accuracy": {"players": 300, "mae": 5.9, "within_10_90": 0.78},
        }
    )
    assert line == ("Round 14; you 172.4; top-10 avg 230.0; our plan 168.0; "
                    "model error 5.9, 78% inside range")  # fmt: skip
