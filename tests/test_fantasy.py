import json
from pathlib import Path

import httpx
import pytest

from elfantasy.data.fantasy import FantasyAuthError, FantasyClient
from elfantasy.data.parse_fantasy import parse_players, parse_schedule
from elfantasy.data.store import connect
from elfantasy.data.sync_fantasy import sync_fantasy

FIXTURES = Path(__file__).parent / "fixtures"
PLAYERS = json.loads((FIXTURES / "fantasy_players.json").read_text(encoding="utf-8"))
SCHEDULE = json.loads((FIXTURES / "fantasy_schedule.json").read_text(encoding="utf-8"))
MATCHDAY = {"id": 1541, "number": 14, "display_name": None}


def snapshot(live: bool) -> dict:
    return {
        "fetched_at": "2026-11-21T18:00:00+00:00",
        "live": live,
        "league_status_id": 1 if live else None,
        "matchday": MATCHDAY,
        "players": PLAYERS["data"],
    }


def test_parse_players_live():
    rows = parse_players("E2026", snapshot(live=True))
    assert len(rows) == 3
    james = next(r for r in rows if r["player_id"] == 3765)
    assert (james["round"], james["price"], james["position"]) == (14, 14.7, "Guard")
    assert (james["turn"], james["home"]) == (2, False)
    assert james["is_injured"] is True
    assert "avg_pts" not in james  # current-season average leaks future data


def test_parse_players_backfill_hides_current_only_fields():
    rows = parse_players("E2026", snapshot(live=False))
    for r in rows:
        assert r["price"] > 0
        assert r["popularity"] is None
        assert r["is_injured"] is None
        assert r["probability_of_playing"] is None


def test_parse_schedule():
    rows = parse_schedule("E2026", SCHEDULE["data"])
    assert len(rows) == 4
    assert {r["turn"] for r in rows} == {1, 2}
    first = rows[0]
    assert (first["round"], first["home_code"], first["away_code"]) == (14, "BJK", "PBB")


def fake_api(calls: list[str], *, status: int = 200) -> httpx.MockTransport:
    config = {
        "status_id": 1,
        "current_schedule_id": 49,
        "current_players_list_id": 49,
        "current_matchday": {"id": 1529, "number": 2},
        "matchdays": [
            {"id": 1528, "number": 1, "display_name": None},
            {"id": 1529, "number": 2, "display_name": None},
            {"id": 1530, "number": 3, "display_name": None},
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if status != 200:
            return httpx.Response(status, json={"code": "UNAUTHENTICATED"})
        path = request.url.path
        if path.endswith("/config"):
            return httpx.Response(200, json={"data": config})
        if "/players-lists/" in path:
            return httpx.Response(200, json=PLAYERS)
        if "/schedules/" in path:
            return httpx.Response(200, json=SCHEDULE)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def make_client(tmp_path: Path, calls: list[str], **kw) -> FantasyClient:
    return FantasyClient(tmp_path / "raw", "1|secret", delay=0, transport=fake_api(calls, **kw))


def test_sync_caches_past_rounds_and_refreshes_current(tmp_path):
    calls: list[str] = []
    with make_client(tmp_path, calls) as client, connect(tmp_path / "db.duckdb") as con:
        sync_fantasy(client, con, "E2026", log=lambda _: None)
        first_run = [c for c in calls if "/players-lists/" in c]
        assert len(first_run) == 2  # rounds 1 and 2; round 3 is in the future

        calls.clear()
        sync_fantasy(client, con, "E2026", log=lambda _: None)
        second_run = [c for c in calls if "/players-lists/" in c]
        assert second_run == ["/api/v1/players-lists/49/matchdays/1529/players"]

        rows = con.execute(
            "SELECT round, live, count(*) AS n FROM fantasy_prices GROUP BY ALL ORDER BY round"
        ).fetchall()
        assert rows == [(1, False, 3), (2, True, 3)]
        assert con.execute("SELECT count(*) FROM fantasy_schedule").fetchone()[0] == 12


def test_live_snapshot_is_never_overwritten(tmp_path):
    calls: list[str] = []
    with make_client(tmp_path, calls) as client:
        md = {"id": 1528, "number": 1}
        client.players("E2026", 49, md, current=True)
        later = client.players("E2026", 49, md, current=False)
    assert later["live"] is True
    assert len([c for c in calls if "/players-lists/" in c]) == 1


def test_expired_token_raises_clear_error(tmp_path):
    with make_client(tmp_path, [], status=401) as client, pytest.raises(FantasyAuthError):
        client.league_config()
