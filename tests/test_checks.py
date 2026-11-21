import duckdb
import pandas as pd
import pytest

from elfantasy.data.checks import run_checks


def _player(game_code, team, name, points, seconds, *, pir=10, pir_computed=10, home=True):
    return {
        "season": "E2025",
        "game_code": game_code,
        "team_code": team,
        "is_home": home,
        "player_code": name,
        "player_name": name,
        "points": points,
        "seconds": seconds,
        "pir": pir,
        "pir_computed": pir_computed,
    }


def _team(game_code, team, points, *, home, minutes=200):
    # five players splitting the points and the playing time evenly
    return [
        _player(game_code, team, f"{team}{i}", points // 5, minutes * 60 / 5, home=home)
        for i in range(5)
    ]


@pytest.fixture
def con():
    games = pd.DataFrame(
        [
            # good game
            {"season": "E2025", "game_code": 1, "home_code": "A", "away_code": "B",
             "played": True, "home_score": 80, "away_score": 75, "overtime": False},
            # overtime game, but the team minutes only cover regulation
            {"season": "E2025", "game_code": 2, "home_code": "A", "away_code": "B",
             "played": True, "home_score": 90, "away_score": 85, "overtime": True},
            # played but no box score
            {"season": "E2025", "game_code": 3, "home_code": "A", "away_code": "B",
             "played": True, "home_score": 70, "away_score": 60, "overtime": False},
        ]
    )  # fmt: skip
    rows = (
        _team(1, "A", 80, home=True)
        + _team(1, "B", 75, home=False)
        + _team(2, "A", 90, home=True)
        + _team(2, "B", 85, home=False)
    )
    rows[0]["pir_computed"] = 11  # PIR mismatch
    rows[5]["points"] += 2  # B's points no longer add up to 75
    rows.append(dict(rows[1]))  # duplicate player row
    rows[-1]["seconds"] = rows[-1]["points"] = 0
    player_games = pd.DataFrame(rows)

    c = duckdb.connect()
    c.register("games_df", games)
    c.register("player_games_df", player_games)
    c.execute("CREATE TABLE games AS SELECT * FROM games_df")
    c.execute("CREATE TABLE player_games AS SELECT * FROM player_games_df")
    return c


def test_checks_find_each_problem(con):
    results = run_checks(con)
    assert len(results["pir_mismatch"]) == 1
    assert results["missing_box_score"]["game_code"].tolist() == [3]
    assert len(results["duplicate_player"]) == 1
    assert results["points_mismatch"][["game_code", "team_code"]].values.tolist() == [[1, "B"]]
    assert sorted(results["minutes_mismatch"]["game_code"].tolist()) == [2, 2]
