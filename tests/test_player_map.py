import duckdb
import pandas as pd
import pytest

from elfantasy.data.checks import PLAYER_MAP_CHECKS
from elfantasy.data.player_map import build_player_map, name_score, normalize, team_map

S = "E2026"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("JAMES, MIKE", "MIKE JAMES"),
        ("Šarūnas Jasikevičius", "SARUNAS JASIKEVICIUS"),
        ("N'Diaye, Amath", "AMATH NDIAYE"),
        ("Sasha  Vezenkov-Jr.", "SASHA VEZENKOV JR"),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_name_score():
    assert name_score("MIKE JAMES", "MIKE JAMES") == 1
    assert name_score("SASHA VEZENKOV", "ALEKSANDAR SASHA VEZENKOV") >= 0.9  # extra name
    assert name_score("NICK CALATHES", "NICHOLAS CALATHES") >= 0.8  # nickname
    assert name_score("MIKE JAMES", "KOSTAS SLOUKAS") < 0.5


def table(con, name, rows):
    df = pd.DataFrame(rows)  # noqa: F841
    con.execute(f"CREATE TABLE {name} AS SELECT * FROM df")


@pytest.fixture
def con():
    con = duckdb.connect()
    # One round, two games. Fantasy calls Efes "EFS"; the EuroLeague calls it "IST".
    table(con, "fantasy_schedule", [
        {"season": S, "round": 1, "home_code": "BAR", "away_code": "EFS",
         "started_at": "2026-10-01T18:00:00.000000Z"},
        {"season": S, "round": 1, "home_code": "OLY", "away_code": "PAO",
         "started_at": "2026-10-01T20:00:00.000000Z"},
    ])  # fmt: skip
    table(con, "games", [
        {"season": S, "round": 1, "phase": "RS", "home_code": "BAR", "away_code": "IST",
         "utc_date": "2026-10-01T18:00:00Z"},
        {"season": S, "round": 1, "phase": "RS", "home_code": "OLY", "away_code": "PAN",
         "utc_date": "2026-10-01T20:00:00Z"},
    ])  # fmt: skip
    fp = [
        (1, "Mike", "James", "Guard", "EFS"),
        (2, "Sasha", "Vezenkov", "Forward", "OLY"),
        (3, "Kendrick", "Nunn", "Guard", "PAO"),
        (4, "Ergin", "Ataman", "Head Coach", "PAO"),
        (5, "Never", "Played", "Center", "BAR"),
        (6, "Aleksander", "Balcerowski", "Center", "BAR"),
    ]
    table(con, "fantasy_prices", [
        {"season": S, "player_id": i, "first_name": f, "last_name": last, "position": pos,
         "team_code": t}
        for i, f, last, pos, t in fp
    ])  # fmt: skip
    table(con, "player_games", [
        {"season": S, "player_code": "P1", "player_name": "JAMES, MIKE", "team_code": "IST"},
        {"season": S, "player_code": "P2", "player_name": "VEZENKOV, ALEKSANDAR SASHA",
         "team_code": "OLY"},
        {"season": S, "player_code": "P3", "player_name": "NUNN, KENDRICK", "team_code": "PAN"},
        # Same name on a different team must not match fantasy player 3.
        {"season": S, "player_code": "P9", "player_name": "NUNN, KENDRICK", "team_code": "BAR"},
        # Nickname: only the last name matches.
        {"season": S, "player_code": "P6", "player_name": "BALCEROWSKI, OLEK", "team_code": "BAR"},
    ])  # fmt: skip
    table(con, "coach_games", [
        {"season": S, "coach_code": "C1", "coach_name": "ATAMAN, ERGIN", "team_code": "PAN"},
        # A coach replaced mid-season still maps to the club's single fantasy coach entry.
        {"season": S, "coach_code": "C2", "coach_name": "FORMER, COACH", "team_code": "PAN"},
    ])  # fmt: skip
    return con


def test_team_map(con):
    assert team_map(con, S) == {"BAR": "BAR", "EFS": "IST", "OLY": "OLY", "PAO": "PAN"}


def test_build_player_map(con):
    rows = build_player_map(con, S)
    pairs = {(r["fantasy_player_id"], r["el_code"], r["method"]) for r in rows}
    assert pairs == {
        (1, "P1", "name"),
        (2, "P2", "name"),
        (3, "P3", "name"),
        (6, "P6", "last_name"),
        (4, "C1", "team"),
        (4, "C2", "team"),
    }


def test_unmapped_checks(con):
    table(con, "player_map", build_player_map(con, S))
    unmapped = con.execute(PLAYER_MAP_CHECKS["unmapped_player"]).df()
    assert list(unmapped["player_code"]) == ["P9"]  # played, but no fantasy match
    assert con.execute(PLAYER_MAP_CHECKS["unmapped_coach"]).df().empty
