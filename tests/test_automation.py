from datetime import UTC, datetime, timedelta

import duckdb
import pandas as pd

from elfantasy.automation import (
    GAME_LENGTH,
    PRE_ROUND_HOURS,
    Turn,
    _md_table,
    _token_alert,
    due_actions,
    load_state,
    round_turns,
    save_state,
)
from elfantasy.notify import SCRIPT

S = "E2026"
T1 = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
TURNS = [
    Turn(1, T1, T1 + timedelta(hours=3)),  # turn 1: 17:00-20:00 tip-offs
    Turn(2, T1 + timedelta(days=1, hours=-1), T1 + timedelta(days=1, hours=2)),  # next day
]


def kinds(now, state=None):
    return [(k, t) for _, k, t in due_actions(now, S, 14, TURNS, state or {})]


def test_nothing_due_long_before_the_round():
    assert kinds(T1 - timedelta(days=2)) == []


def test_pre_round_window():
    assert kinds(T1 - timedelta(hours=PRE_ROUND_HOURS + 1)) == []
    assert kinds(T1 - timedelta(hours=PRE_ROUND_HOURS - 1)) == [("pre_round", 1)]
    assert kinds(T1 + timedelta(minutes=1)) == []  # too late: games started


def test_turn_window_opens_after_the_previous_turn_ends():
    still_playing = TURNS[0].last_start + GAME_LENGTH - timedelta(minutes=5)
    assert kinds(still_playing) == []
    assert kinds(TURNS[1].start - timedelta(hours=2)) == [("turn", 2)]
    assert kinds(TURNS[1].start + timedelta(minutes=1)) == []


def test_done_actions_are_not_repeated():
    now = T1 - timedelta(hours=2)
    key = due_actions(now, S, 14, TURNS, {})[0][0]
    assert kinds(now, {key: "sent"}) == []


def test_round_turns_from_schedule():
    con = duckdb.connect()
    df = pd.DataFrame({"season": S, "round": 14, "turn": [1, 1, 2],  # noqa: F841
                       "started_at": ["2026-11-24T17:00:00Z", "2026-11-24T20:00:00Z",
                                      "2026-11-25T19:15:00Z"]})  # fmt: skip
    con.execute("CREATE TABLE fantasy_schedule AS SELECT * FROM df")
    turns = round_turns(con, S, 14)
    assert [t.number for t in turns] == [1, 2]
    assert turns[0].start == T1 and turns[0].last_start == T1 + timedelta(hours=3)
    assert turns[0].start.tzinfo is not None


def test_state_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {}
    save_state(path, {"a": "1"})
    assert load_state(path) == {"a": "1"}
    path.write_text("not json", encoding="utf-8")
    assert load_state(path) == {}  # a corrupt file never blocks the pipeline


def test_token_alert_at_most_once_a_day():
    sent, state = [], {}
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    for hours in (0, 5, 23):
        _token_alert(state, now + timedelta(hours=hours), lambda t, b: sent.append(t),
                     lambda m: None, "expired")  # fmt: skip
    assert len(sent) == 1
    _token_alert(state, now + timedelta(hours=25), lambda t, b: sent.append(t),
                 lambda m: None, "expired")  # fmt: skip
    assert len(sent) == 2


def test_md_table():
    out = _md_table(pd.DataFrame({"a": [1, None], "b": ["x", "y"]}))
    assert out.splitlines()[0] == "| a | b |"
    assert out.splitlines()[3] == "|  | y |"


def test_toast_script_keeps_type_literal_on_one_line():
    first = SCRIPT.splitlines()[0]
    assert first.startswith("$m = [") and first.endswith("ContentType = WindowsRuntime]")
    assert "$env:ELF_TOAST_TITLE" in SCRIPT  # text comes from env vars, not the script
