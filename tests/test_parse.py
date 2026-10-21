import json
from pathlib import Path

import pytest

from elfantasy.data.parse import parse_game, parse_game_stats

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def games():
    return json.loads((FIXTURES / "games.json").read_text(encoding="utf-8"))


@pytest.fixture
def stats():
    return json.loads((FIXTURES / "game_stats.json").read_text(encoding="utf-8"))


def test_parse_game(games):
    row = parse_game("E2025", games[0])
    assert row["game_code"] == 1
    assert (row["home_code"], row["home_score"]) == ("IST", 85)
    assert (row["away_code"], row["away_score"]) == ("TEL", 78)
    assert row["overtime"] is False


def test_parse_overtime_game(games):
    assert parse_game("E2025", games[1])["overtime"] is True


def test_parse_game_stats(games, stats):
    game = parse_game("E2025", games[0])
    players, coaches = parse_game_stats(game, stats)

    assert len(players) == 5
    assert all(p["pir"] == p["pir_computed"] for p in players)

    cordinier = next(p for p in players if p["player_name"] == "CORDINIER, ISAIA")
    assert cordinier["won"] and cordinier["is_home"]
    assert cordinier["fantasy_points"] == pytest.approx(3 * 1.1)

    blatt = next(p for p in players if p["player_name"] == "BLATT, TAMIR")
    assert not blatt["won"]
    assert blatt["fantasy_points"] == 9

    home_coach, away_coach = coaches
    assert (home_coach["margin"], home_coach["fantasy_points"]) == (7, 10)
    assert (away_coach["margin"], away_coach["fantasy_points"]) == (-7, -5)
