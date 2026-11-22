import time
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from elfantasy.api import service
from elfantasy.api.app import create_app
from elfantasy.api.state import DatabaseBusy


@pytest.fixture
def settings(tmp_path):
    return SimpleNamespace(
        fantasy_token=None, anthropic_api_key=None, db_path=tmp_path / "db.duckdb",
        raw_dir=tmp_path / "raw", fantasy_league_id=10, season_code="E2026",
    )  # fmt: skip


@pytest.fixture
def client(settings, tmp_path):
    return TestClient(create_app(settings, frontend_dist=tmp_path / "no-frontend"))


def fake_session(state, ctx="ctx"):
    @contextmanager
    def session():
        yield ctx

    state.session = session


def test_records_are_json_safe():
    df = pd.DataFrame({"a": [1.5, float("nan")], "b": pd.array([1, None], dtype="Int64")})
    assert service.records(df) == [{"a": 1.5, "b": 1}, {"a": None, "b": None}]


def test_busy_database_is_a_503(client):
    state = client.app.state.elf

    @contextmanager
    def busy():
        raise DatabaseBusy("busy")
        yield

    state.session = busy
    r = client.get("/api/status")
    assert r.status_code == 503 and r.json()["detail"] == "busy"


def test_team_and_chat_need_credentials(client):
    assert client.get("/api/team").status_code == 400
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 400 and "ANTHROPIC" in r.json()["detail"]


def test_players_endpoint_passes_filters(client, monkeypatch):
    fake_session(client.app.state.elf)
    seen = {}

    def players(ctx, search, position, team, max_price, sort, limit):
        seen.update(search=search, position=position, limit=limit, sort=sort)
        return [{"id": 1}]

    monkeypatch.setattr(service, "players", players)
    r = client.get("/api/players", params={"position": "C", "limit": 9999, "sort": "value"})
    assert r.json() == [{"id": 1}]
    assert seen == {"search": None, "position": "C", "limit": 400, "sort": "value"}


def test_compare_validates_input(client):
    assert client.post("/api/compare", json={"ids": []}).status_code == 422
    assert client.post("/api/compare", json={"ids": list(range(7))}).status_code == 422


def test_optimize_runs_as_a_job(client, monkeypatch):
    fake_session(client.app.state.elf)
    monkeypatch.setattr(service, "optimize", lambda ctx, *a: {"expected_points": 150.0})
    job = client.post("/api/optimize", json={"horizon": 2}).json()
    for _ in range(50):
        status = client.get(f"/api/jobs/{job['job_id']}").json()
        if status["status"] == "done":
            break
        time.sleep(0.05)
    assert status["result"] == {"expected_points": 150.0}
    assert client.get("/api/jobs/unknown").status_code == 404


def test_failed_job_reports_the_error(client, monkeypatch):
    fake_session(client.app.state.elf)

    def boom(ctx, *a):
        raise RuntimeError("solver failed")

    monkeypatch.setattr(service, "optimize", boom)
    job = client.post("/api/optimize", json={}).json()
    for _ in range(50):
        status = client.get(f"/api/jobs/{job['job_id']}").json()
        if status["status"] == "error":
            break
        time.sleep(0.05)
    assert status["error"] == "solver failed"
