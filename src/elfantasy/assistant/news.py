"""Injury and availability news, turned into structured player statuses.

Claude searches the web for the round's team news, then a second call turns what it
found into a list of statuses (out / doubtful / questionable / limited / available),
which are matched to fantasy player ids and stored in the `news_status` table.

Statuses are shown and logged, but by default they do NOT change the numbers the
optimizer uses: CLAUDE.md requires a change to beat the baseline before it ships, and
there's no news history to test against yet. `evaluate()` measures whether applying
them would have helped, once rounds with logged news have been played.
"""

from datetime import UTC, datetime
from typing import Literal

import anthropic
import duckdb
import numpy as np
import pandas as pd
from pydantic import BaseModel

from elfantasy.data.player_map import name_score, normalize

MODEL = "claude-opus-5-5"
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
MATCH_SCORE = 0.85

Status = Literal["out", "doubtful", "questionable", "limited", "available"]

# How a status would adjust a projection: (multiplier on P(plays), multiplier on
# points if he plays). Untested guesses until evaluate() has played rounds to check.
ADJUST: dict[str, tuple[float, float]] = {
    "out": (0.0, 1.0),
    "doubtful": (0.35, 0.9),
    "questionable": (0.7, 0.95),
    "limited": (1.0, 0.75),
    "available": (1.0, 1.0),
}


class PlayerStatus(BaseModel):
    player: str
    team: str
    status: Status
    reason: str
    source: str


class NewsReport(BaseModel):
    statuses: list[PlayerStatus]


SEARCH_PROMPT = """Find the latest injury and availability news for EuroLeague \
basketball players ahead of round {round} of the {season} regular season (games from \
{first_game} to {last_game}). Teams: {teams}.

Look for official injury reports, club announcements and reliable journalists. For each \
player who is ruled out, doubtful, a game-time decision, or expected to play limited \
minutes, give the player's name, team, what is known, and the source with its date. \
Only include news from the last 10 days. Leave out rumours you can't source."""

EXTRACT_PROMPT = """Turn these news notes into a list of player statuses.

status must be one of: out (won't play), doubtful (unlikely to play), questionable \
(game-time decision), limited (will play, reduced minutes), available (was doubtful, now \
cleared). Use the player's full name as written in the notes, the team name, a one-line \
reason, and the source (URL or outlet). Skip anything without a named player.

Notes:
{notes}"""


def fetch(
    client: anthropic.Anthropic,
    season: str,
    round_: int,
    teams: list[str],
    first_game: str,
    last_game: str,
    max_searches: int = 8,
) -> tuple[list[PlayerStatus], str]:
    """Search the web and extract statuses. Returns (statuses, the raw notes)."""
    messages = [
        {
            "role": "user",
            "content": SEARCH_PROMPT.format(
                round=round_, season=season, first_game=first_game, last_game=last_game,
                teams=", ".join(teams),
            ),
        }
    ]  # fmt: skip
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": max_searches}]
    while True:
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, tools=tools, messages=messages,
            output_config={"effort": "medium"}, **FALLBACK,
        )  # fmt: skip
        if response.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": response.content})
    if response.stop_reason == "refusal":
        raise RuntimeError("the news search was declined")
    notes = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not notes:
        return [], ""
    parsed = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": EXTRACT_PROMPT.format(notes=notes)}],
        output_format=NewsReport,
    )
    report = parsed.parsed_output
    return (report.statuses if report else []), notes


def match(statuses: list[PlayerStatus], market: pd.DataFrame) -> pd.DataFrame:
    """Match statuses to fantasy players by name (and team, when it helps). One row per
    status, with player_id None when nothing matched well enough."""
    names = {
        pid: normalize(f"{r.first_name} {r.last_name}")
        for pid, r in market.iterrows()
        if isinstance(r.get("first_name"), str)
    }
    rows = []
    for s in statuses:
        target = normalize(s.player)
        team = normalize(s.team)
        scored = []
        for pid, n in names.items():
            score = name_score(target, n)
            code = str(market.at[pid, "team_code"]).upper()
            if code and code in team.split():
                score += 0.05  # same team abbreviation mentioned
            scored.append((score, pid))
        best = max(scored, default=(0.0, None))
        rows.append(
            {
                "player_id": best[1] if best[0] >= MATCH_SCORE else None,
                "match_score": round(min(best[0], 1.0), 3),
                "player_raw": s.player, "team_raw": s.team, "status": s.status,
                "reason": s.reason, "source": s.source,
            }
        )  # fmt: skip
    columns = ["player_id", "match_score", "player_raw", "team_raw", "status", "reason",
               "source"]  # fmt: skip
    return pd.DataFrame(rows, columns=columns)  # columns even when nothing was found


def store(con: duckdb.DuckDBPyConnection, season: str, round_: int, matched: pd.DataFrame):
    """Append to the news_status log (kept, never replaced: it's the history that
    evaluate() needs)."""
    if matched.empty:
        return
    df = matched.assign(  # noqa: F841 (read by DuckDB by name)
        season=season, round=round_,
        fetched_at=datetime.now(UTC).isoformat(timespec="microseconds"),
    )  # fmt: skip
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'news_status'"
    ).fetchone()[0]
    if exists:
        con.execute("INSERT INTO news_status BY NAME SELECT * FROM df")
    else:
        con.execute("CREATE TABLE news_status AS SELECT * FROM df")


def latest(con: duckdb.DuckDBPyConnection, season: str, round_: int) -> pd.DataFrame:
    """The most recent status per player for a round."""
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'news_status'"
    ).fetchone()[0]
    if not exists:
        return pd.DataFrame(columns=["player_id", "status", "reason", "source", "fetched_at"])
    return con.execute(
        """
        SELECT * FROM news_status
        WHERE season = ? AND round = ? AND player_id IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY player_id ORDER BY fetched_at DESC) = 1
        """,
        [season, round_],
    ).df()


def apply(pred: pd.DataFrame, statuses: pd.DataFrame) -> pd.DataFrame:
    """Projections adjusted by news statuses (see ADJUST)."""
    out = pred.copy()
    for _, s in statuses.iterrows():
        pid = s["player_id"]
        if pid not in out.index:
            continue
        play, pts = ADJUST[s["status"]]
        out.loc[pid, "p_play"] *= play
        for col in ("mean", "q10", "q50", "q90"):
            if col in out:
                out.loc[pid, col] *= pts
    out["expected"] = out["p_play"] * out["mean"]
    return out


def evaluate(pred: pd.DataFrame, statuses: pd.DataFrame, actual: pd.Series) -> dict:
    """For players with a logged status: error of expected points with and without
    the news adjustment, against what they actually scored."""
    ids = [i for i in statuses["player_id"] if i in pred.index]
    if not ids:
        return {"players": 0}
    adjusted = apply(pred, statuses)
    truth = actual.reindex(ids).fillna(0.0)
    before = (pred.loc[ids, "expected"] - truth).abs()
    after = (adjusted.loc[ids, "expected"] - truth).abs()
    return {
        "players": len(ids),
        "mae_without_news": float(before.mean()),
        "mae_with_news": float(after.mean()),
        "improved_share": float(np.mean(after < before)),
    }
