from types import SimpleNamespace

import duckdb
import numpy as np
import pandas as pd
import pytest

from elfantasy.assistant import news
from elfantasy.assistant.chat import FALLBACK, MODEL, Assistant
from elfantasy.assistant.context import FantasyContext
from elfantasy.assistant.tools import TOOL_DEFINITIONS, Tools

S = "E2026"


@pytest.fixture
def ctx():
    market = pd.DataFrame(
        {
            "first_name": ["Sasha", "Kendrick", "Kenan", "Ergin"],
            "last_name": ["Vezenkov", "Nunn", "Kamenjas", "Ataman"],
            "position": ["F", "G", "C", "HC"],
            "team_code": ["OLY", "PAO", "KBA", "PAO"],
            "price": [20.0, 15.2, 6.2, 9.0],
            "opp_code": ["HTA", "VBC", "EFS", "VBC"],
            "home": [True, False, True, False],
            "turn": [2, 1, 2, 1],
            "el_code": ["P1", "P2", "P3", None],
            "el_team": ["OLY", "PAN", "BAS", "PAN"],
        },
        index=pd.Index([1, 2, 3, 4], name="player_id"),
    )
    pred = pd.DataFrame(
        {"p_play": [0.97, 0.95, 0.9, 1.0], "mean": [24.0, 15.5, 15.0, 12.0],
         "q10": [12.0, 5.0, 2.0, np.nan], "q50": [23.0, 15.0, 13.0, 12.0],
         "q90": [33.0, 28.0, 24.0, np.nan]},
        index=market.index,
    )  # fmt: skip
    pred["expected"] = pred["p_play"] * pred["mean"]
    con = duckdb.connect()
    sched = pd.DataFrame(  # noqa: F841
        {"season": S, "round": 14, "turn": [1, 2], "started_at": ["2026-11-24", "2026-11-25"],
         "home_code": ["VBC", "OLY"], "away_code": ["PAO", "HTA"], "status": "scheduled",
         "home_score": None, "away_score": None}
    )  # fmt: skip
    con.execute("CREATE TABLE fantasy_schedule AS SELECT * FROM sched")
    c = FantasyContext(con=con, season=S, round=14, _market=market,
                       _preds={14: pred, 15: pred, 16: pred})  # fmt: skip
    c.last_round = lambda: 38
    return c


@pytest.fixture
def tools(ctx):
    t = Tools(ctx)
    t._prices = False  # no price history in this fixture
    return t


def test_tool_definitions_are_valid():
    names = [t["name"] for t in TOOL_DEFINITIONS]
    assert len(names) == len(set(names))
    for t in TOOL_DEFINITIONS:
        s = t["input_schema"]
        assert s["type"] == "object" and s["additionalProperties"] is False
        assert set(s["required"]) <= set(s["properties"])


def test_every_tool_has_a_handler(tools):
    assert {t["name"] for t in TOOL_DEFINITIONS} == set(tools.handlers)


def test_find_players(ctx):
    assert ctx.find("Vezenkov") == [1]
    assert ctx.find("kendrick nunn") == [2]
    assert ctx.find("nobody at all") == []


def test_get_projections_filters_and_sorts(tools):
    out, err = tools.run("get_projections", {"position": "G"})
    assert not err and "Nunn" in out and "Vezenkov" not in out
    out, _ = tools.run("get_projections", {"sort_by": "value", "limit": 1})
    assert "Kamenjas" in out and "Vezenkov" not in out  # best points per credit


def test_compare_players_reports_missing(tools):
    out, err = tools.run("compare_players", {"players": ["Vezenkov", "Nunn", "Nobody"]})
    assert not err
    assert "Vezenkov" in out and "Nunn" in out and "Not found: ['Nobody']" in out
    assert "next_3_rounds" in out


def test_get_schedule(tools):
    out, err = tools.run("get_schedule", {})
    assert not err and "VBC" in out and "OLY" in out


def test_errors_become_tool_errors(tools):
    out, err = tools.run("get_my_team", {})  # no fantasy client in the fixture
    assert err and "ELF_FANTASY_TOKEN" in out
    assert tools.run("no_such_tool", {})[1]


def test_news_match_apply_and_evaluate(ctx):
    statuses = [
        news.PlayerStatus(player="Kendrick Nunn", team="Panathinaikos PAO", status="out",
                          reason="ankle", source="club"),
        news.PlayerStatus(player="Sasha Vezenkov", team="Olympiacos", status="limited",
                          reason="back", source="x"),
        news.PlayerStatus(player="Someone Else", team="?", status="doubtful", reason="",
                          source=""),
    ]  # fmt: skip
    matched = news.match(statuses, ctx.market)
    assert list(matched["player_id"])[:2] == [2, 1]
    assert pd.isna(matched["player_id"].iloc[2])

    pred = ctx.predictions()
    adjusted = news.apply(pred, matched.dropna(subset=["player_id"]))
    assert adjusted.at[2, "expected"] == 0.0
    assert adjusted.at[1, "mean"] == pytest.approx(24.0 * news.ADJUST["limited"][1])
    assert adjusted.at[3, "expected"] == pytest.approx(pred.at[3, "expected"])

    actual = pd.Series({1: 18.0, 2: 0.0})
    result = news.evaluate(pred, matched.dropna(subset=["player_id"]), actual)
    assert result["players"] == 2
    assert result["mae_with_news"] < result["mae_without_news"]


def test_news_store_appends_and_latest_keeps_newest():
    con = duckdb.connect()
    first = pd.DataFrame({"player_id": [1], "status": ["doubtful"], "reason": ["a"],
                          "source": ["s"], "player_raw": ["x"], "team_raw": ["t"],
                          "match_score": [1.0]})  # fmt: skip
    news.store(con, S, 14, first)
    news.store(con, S, 14, first.assign(status="out"))
    assert con.execute("SELECT count(*) FROM news_status").fetchone()[0] == 2
    assert news.latest(con, S, 14)["status"].tolist() == ["out"]
    assert news.latest(con, S, 15).empty


# --- the conversation loop, with a scripted fake client --------------------------


def block(**kw):
    return SimpleNamespace(**kw)


def reply(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return self.replies.pop(0)


def test_assistant_runs_tools_and_keeps_history(tools):
    think = block(type="thinking", thinking="", signature="sig")
    client = FakeClient([
        reply("tool_use", think,
              block(type="tool_use", id="t1", name="get_projections", input={"limit": 2}),
              block(type="tool_use", id="t2", name="get_schedule", input={})),
        reply("end_turn", block(type="text", text="Captain Vezenkov.")),
    ])  # fmt: skip
    seen = []
    bot = Assistant(tools, client, effort="low", on_tool=lambda n, a: seen.append(n))
    assert bot.ask("Who should I captain?") == "Captain Vezenkov."
    assert seen == ["get_projections", "get_schedule"]

    first = client.calls[0]
    assert first["model"] == MODEL and first["output_config"] == {"effort": "low"}
    assert first["betas"] == FALLBACK["betas"] and first["fallbacks"] == "default"

    roles = [m["role"] for m in bot.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert bot.messages[1]["content"][0] is think  # thinking block passed back unchanged
    results = bot.messages[2]["content"]  # both tool results in one user message
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert all(r["is_error"] is False for r in results)


def test_assistant_handles_refusal_and_pause(tools):
    client = FakeClient([
        reply("pause_turn", block(type="text", text="")),
        reply("refusal"),
    ])  # fmt: skip
    bot = Assistant(tools, client)
    assert "declined" in bot.ask("hi")
    assert len(client.calls) == 2


def test_assistant_reports_tool_errors_to_claude(tools):
    client = FakeClient([
        reply("tool_use", block(type="tool_use", id="t1", name="get_my_team", input={})),
        reply("end_turn", block(type="text", text="I can't see your team.")),
    ])  # fmt: skip
    bot = Assistant(tools, client)
    bot.ask("show my team")
    assert bot.messages[2]["content"][0]["is_error"] is True
