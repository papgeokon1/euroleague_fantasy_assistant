"""Leak-free features for player projections.

Every feature for (season, round, player) is computed only from games that tipped off
before that round's first game (its deadline). The same function builds training
rows from past seasons and prediction rows for the fantasy market, so there is one
code path and no way for future data to slip in.

Feature groups:
- form: exponentially weighted fantasy points, minutes and points per minute
- rates: season-to-date, previous-season and career averages
- availability: team games missed since the player last played, games played in the
  team's last 5, and how many games of the season the team has played
- matchup: home or away, days of rest, team strength and opponent strength (weighted
  point margin), and the fantasy points the opponent allows to the player's position
"""

from dataclasses import dataclass, field

import duckdb
import numpy as np
import pandas as pd

FORM_HALFLIFE = 5  # games
TEAM_HALFLIFE = 8  # games

FEATURES = [
    "ew_fp",
    "ew_min",
    "ew_fppm",
    "season_mean",
    "season_gp",
    "prev_mean",
    "career_mean",
    "career_gp",
    "games_missed",
    "played_last5",
    "team_gp",
    "new_to_team",
    "home",
    "rest_days",
    "team_str",
    "opp_str",
    "opp_allowed",
    "pos_code",
]
POS_CODE = {"Guard": 0, "Forward": 1, "Center": 2}


def _utc(s: pd.Series) -> pd.Series:
    """One timestamp dtype everywhere, so time-ordered joins line up."""
    return pd.to_datetime(s, utc=True).astype("datetime64[ns, UTC]")


def prev_season(season: str) -> str:
    return f"E{int(season[1:]) - 1}"


@dataclass
class Logs:
    """Everything features are computed from, loaded once."""

    player: pd.DataFrame  # one row per player game, all phases, sorted by date
    team: pd.DataFrame  # one row per team per played game
    schedule: pd.DataFrame  # one row per team per regular-season game, played or not
    deadlines: pd.DataFrame  # (season, round) -> first tip-off of the round
    _times: dict | None = field(default=None, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)

    def cached(self, name: str, fn):
        """Derived tables don't change once loaded; compute each one once."""
        if name not in self._cache:
            self._cache[name] = fn(self)
        return self._cache[name]

    @classmethod
    def load(cls, con: duckdb.DuckDBPyConnection) -> "Logs":
        player = con.execute(
            """
            SELECT season, game_code, round, phase, utc_date::TIMESTAMPTZ AS t,
                   player_code AS el_code, team_code, opp_code, position,
                   seconds / 60 AS minutes, seconds > 0 AS played, fantasy_points AS fp
            FROM player_games ORDER BY t, game_code
            """
        ).df()
        player["t"] = _utc(player["t"])
        games = con.execute(
            """
            SELECT season, game_code, round, phase, utc_date::TIMESTAMPTZ AS t, played,
                   home_code, away_code, home_score, away_score
            FROM games
            """
        ).df()
        games["t"] = _utc(games["t"])
        home = games.assign(
            team_code=games["home_code"],
            opp_code=games["away_code"],
            home=True,
            margin=games["home_score"] - games["away_score"],
        )
        away = games.assign(
            team_code=games["away_code"],
            opp_code=games["home_code"],
            home=False,
            margin=games["away_score"] - games["home_score"],
        )
        cols = ["season", "game_code", "round", "phase", "t", "played", "team_code",
                "opp_code", "home", "margin"]  # fmt: skip
        both = pd.concat([home[cols], away[cols]], ignore_index=True).sort_values("t")
        team = both[both["played"]].reset_index(drop=True)
        schedule = both[both["phase"] == "RS"].reset_index(drop=True)
        deadlines = schedule.groupby(["season", "round"], as_index=False)["t"].min()
        return cls(player, team, schedule, deadlines.rename(columns={"t": "deadline"}))


def _asof(left: pd.DataFrame, right: pd.DataFrame, on_left: str, on_right: str, by: str):
    """For each left row, the latest right row strictly before left[on_left]."""
    left = left.reset_index().sort_values(on_left)
    right = right.sort_values(on_right)
    out = pd.merge_asof(
        left, right, left_on=on_left, right_on=on_right, by=by, allow_exact_matches=False
    )
    return out.set_index("index").sort_index()


def _player_state(logs: Logs) -> pd.DataFrame:
    """Player state after each game he played in."""
    p = logs.player[logs.player["played"]].copy()
    g = p.groupby("el_code")
    p["ew_fp"] = g["fp"].transform(lambda s: s.ewm(halflife=FORM_HALFLIFE).mean())
    p["ew_min"] = g["minutes"].transform(lambda s: s.ewm(halflife=FORM_HALFLIFE).mean())
    p["career_gp"] = g.cumcount() + 1
    p["career_mean"] = g["fp"].cumsum() / p["career_gp"]
    gs = p.groupby(["el_code", "season"])
    p["season_gp"] = gs.cumcount() + 1
    p["season_mean"] = gs["fp"].cumsum() / p["season_gp"]
    keep = ["el_code", "t", "season", "team_code", "position", "ew_fp", "ew_min",
            "career_gp", "career_mean", "season_gp", "season_mean"]  # fmt: skip
    return p[keep].rename(columns={"t": "last_t", "season": "last_season",
                                   "team_code": "last_team"})  # fmt: skip


def _team_strength(logs: Logs) -> pd.DataFrame:
    t = logs.team.copy()
    t["str"] = t.groupby("team_code")["margin"].transform(
        lambda s: s.ewm(halflife=TEAM_HALFLIFE).mean()
    )
    return t[["team_code", "t", "str"]]


def _opp_allowed(logs: Logs) -> pd.DataFrame:
    """Weighted fantasy points a team allows per game to each position."""
    p = logs.player[logs.player["played"]]
    allowed = (
        p.groupby(["opp_code", "game_code", "season", "position"], as_index=False)
        .agg(fp=("fp", "sum"), t=("t", "first"))
        .sort_values("t")
    )
    allowed["opp_allowed"] = allowed.groupby(["opp_code", "position"])["fp"].transform(
        lambda s: s.ewm(halflife=TEAM_HALFLIFE).mean()
    )
    allowed["key"] = allowed["opp_code"] + "|" + allowed["position"]
    return allowed[["key", "t", "opp_allowed"]]


def _team_times(logs: Logs) -> dict:
    """(season, team) -> sorted tip-off times (naive UTC) of the games it played."""
    if logs._times is None:
        logs._times = {
            k: np.sort(v["t"].dt.tz_convert(None).to_numpy().astype("datetime64[ns]"))
            for k, v in logs.team.groupby(["season", "team_code"])
        }
    return logs._times


def _team_games_before(logs: Logs, season: pd.Series, team: pd.Series, t: pd.Series):
    """Number of games `team` played in `season` strictly before time `t`."""
    times = _team_times(logs)
    tv = t.dt.tz_convert(None).to_numpy().astype("datetime64[ns]")
    out = np.full(len(t), np.nan)
    for i, (s, tm) in enumerate(zip(season, team, strict=True)):
        arr = times.get((s, tm))
        if arr is None:
            out[i] = 0
        elif not np.isnat(tv[i]):
            out[i] = np.searchsorted(arr, tv[i], side="left")
    return out


def build_features(logs: Logs, targets: pd.DataFrame) -> pd.DataFrame:
    """Features for target rows with columns season, round, el_code, team_code
    (EuroLeague codes). Rounds are regular-season rounds."""
    x = targets.reset_index(drop=True).merge(logs.deadlines, on=["season", "round"], how="left")
    game = logs.schedule[["season", "round", "team_code", "t", "home", "opp_code"]]
    x = x.merge(game.rename(columns={"t": "game_t"}), on=["season", "round", "team_code"],
                how="left")  # fmt: skip

    # Player form and rates as of the deadline.
    state = logs.cached("state", _player_state)
    x = _asof(x, state, "deadline", "last_t", "el_code")
    same_season = x["last_season"] == x["season"]
    x["season_gp"] = x["season_gp"].where(same_season, 0)
    x["season_mean"] = x["season_mean"].where(same_season)
    x["ew_fppm"] = x["ew_fp"] / x["ew_min"].replace(0, np.nan)

    prev = (
        logs.player[logs.player["played"]]
        .groupby(["el_code", "season"], as_index=False)["fp"]
        .mean()
        .rename(columns={"fp": "prev_mean", "season": "prev_season"})
    )
    x["prev_season"] = x["season"].map(prev_season)
    x = x.merge(prev, on=["el_code", "prev_season"], how="left")

    # Availability.
    x["team_gp"] = _team_games_before(logs, x["season"], x["team_code"], x["deadline"])
    last_here = same_season & (x["last_team"] == x["team_code"])
    after_last = _team_games_before(
        logs, x["season"], x["team_code"], x["last_t"] + pd.Timedelta(seconds=1)
    )
    x["games_missed"] = np.where(last_here, x["team_gp"] - after_last, np.nan)
    x["new_to_team"] = (~last_here).astype(float)

    # Games played in the team's last 5: career games now minus career games
    # before the team's 5th-most-recent game.
    times = _team_times(logs)
    t5 = np.empty(len(x), dtype="datetime64[ns]")
    for i, (s, tm, d) in enumerate(zip(x["season"], x["team_code"], x["deadline"], strict=True)):
        arr = times.get((s, tm))
        d64 = np.datetime64(d.tz_convert(None), "ns")
        if arr is None:
            t5[i] = d64
            continue
        n = np.searchsorted(arr, d64, side="left")
        t5[i] = arr[max(n - 5, 0)] if n else d64
    x["t5"] = pd.to_datetime(t5).tz_localize("UTC")
    before5 = _asof(
        x[["el_code", "t5"]], state[["el_code", "last_t", "career_gp"]], "t5", "last_t", "el_code"
    )
    x["played_last5"] = (
        (x["career_gp"].fillna(0) - before5["career_gp"].fillna(0)).clip(0, 5).where(same_season, 0)
    )

    # Matchup.
    strength = logs.cached("strength", _team_strength)
    x = _asof(x, strength.rename(columns={"str": "team_str"}), "deadline", "t", "team_code")
    x = x.drop(columns="t")
    opp = strength.rename(columns={"team_code": "opp_code", "str": "opp_str"})
    x = _asof(x, opp, "deadline", "t", "opp_code").drop(columns="t")

    prev_game = logs.schedule[["team_code", "t"]].rename(columns={"t": "prev_t"})
    prev_game = prev_game.assign(prev_game_t=prev_game["prev_t"])
    x = _asof(x, prev_game, "game_t", "prev_t", "team_code")
    x["rest_days"] = ((x["game_t"] - x["prev_game_t"]).dt.total_seconds() / 86400).clip(upper=14)

    x["position"] = x["position"].fillna("Forward")
    x["pos_code"] = x["position"].map(POS_CODE)
    x["key"] = x["opp_code"].fillna("") + "|" + x["position"]
    x = _asof(x, logs.cached("allowed", _opp_allowed), "deadline", "t", "key").drop(
        columns=["t", "key"]
    )
    x["home"] = x["home"].astype(float)
    return x


def training_targets(logs: Logs, seasons: list[str]) -> pd.DataFrame:
    """Regular-season rows to learn from, with labels.

    Candidates for (season, round, team) are everyone who appeared for that team
    earlier in the season, plus everyone in that round's box score. A candidate
    who did not get on the court that round has played = 0.
    """
    p = logs.player[logs.player["season"].isin(seasons)]
    rs = p[p["phase"] == "RS"]
    box = rs[["season", "round", "el_code", "team_code", "played", "fp", "minutes"]]

    rows = []
    for season, s in rs.groupby("season"):
        seen: dict[str, set] = {}
        for rnd, r in s.groupby("round"):
            for team, tr in r.groupby("team_code"):
                cands = seen.setdefault(team, set()) | set(tr["el_code"])
                rows += [(season, rnd, code, team) for code in cands]
                seen[team] = cands
    targets = pd.DataFrame(rows, columns=["season", "round", "el_code", "team_code"])
    targets = targets.merge(box, on=["season", "round", "el_code", "team_code"], how="left")
    targets["played"] = targets["played"].fillna(False).astype(bool)
    return targets


def team_game_features(logs: Logs) -> pd.DataFrame:
    """One row per team per played game, with both teams' strength as of before
    tip-off. Training data for the game outcome model."""
    strength = logs.cached("strength", _team_strength)
    g = logs.team[["season", "phase", "team_code", "opp_code", "home", "margin", "t"]].copy()
    g["home"] = g["home"].astype(float)
    g = _asof(g, strength.rename(columns={"t": "st", "str": "team_str"}), "t", "st", "team_code")
    opp = strength.rename(columns={"team_code": "opp_code", "t": "st", "str": "opp_str"})
    g = _asof(g.drop(columns="st"), opp, "t", "st", "opp_code").drop(columns="st")
    return g
