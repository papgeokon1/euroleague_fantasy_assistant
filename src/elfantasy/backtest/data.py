"""Load one season from the database in the shape the backtest needs.

`market` is what a manager sees before a round: who can be bought, at what price.
`outcomes` is what actually happened, used only to score a round after the picks
are locked. `history` is every game ever played; the simulator slices it so a
strategy only sees games from before the round it is picking for.
"""

from dataclasses import dataclass

import duckdb
import pandas as pd

from elfantasy.data.player_map import team_map

COACH = "HC"
POSITIONS = {"Guard": "G", "Forward": "F", "Center": "C", "Head Coach": COACH}


@dataclass
class SeasonData:
    season: str
    rounds: list[int]
    # One row per buyable player per round: round, player_id, el_code, el_team,
    # position (G/F/C/HC), team_code, price. Price is NaN when the season has no
    # fantasy prices, which limits the backtest to projection error.
    market: pd.DataFrame
    # round, player_id, points (fantasy points before slot multipliers), seconds
    outcomes: pd.DataFrame
    # Every player game, all seasons: season, round, el_code, team_code, seconds,
    # fantasy_points, utc_date
    history: pd.DataFrame
    # Every coach game, all seasons: season, round, team_code, fantasy_points
    coach_history: pd.DataFrame

    @property
    def has_prices(self) -> bool:
        return bool(self.market["price"].notna().any())


def _tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }


def load_season(con: duckdb.DuckDBPyConnection, season: str) -> SeasonData:
    history = con.execute(
        """
        SELECT season, round, phase, el_code, team_code, seconds, fantasy_points, utc_date
        FROM (SELECT *, player_code AS el_code FROM player_games)
        ORDER BY season, utc_date
        """
    ).df()
    coach_history = con.execute(
        """
        SELECT season, round, phase, team_code, fantasy_points
        FROM coach_games ORDER BY season, utc_date
        """
    ).df()
    played_rounds = sorted(
        history.loc[(history["season"] == season) & (history["phase"] == "RS"), "round"].unique()
    )
    if not played_rounds:
        raise ValueError(f"no regular-season games for {season}")

    if {"fantasy_prices", "player_map"} <= _tables(con) and con.execute(
        "SELECT count(*) FROM fantasy_prices WHERE season = ?", [season]
    ).fetchone()[0]:
        market = _fantasy_market(con, season)
    else:
        market = _market_from_box_scores(con, season)
    rounds = [r for r in played_rounds if r in set(market["round"])]
    market = market[market["round"].isin(rounds)].reset_index(drop=True)

    return SeasonData(
        season=season,
        rounds=[int(r) for r in rounds],
        market=market,
        outcomes=_outcomes(market, history, coach_history, season),
        history=history,
        coach_history=coach_history,
    )


def _fantasy_market(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    market = con.execute(
        """
        SELECT f.round, f.player_id, f.position, f.team_code, f.price,
               m.el_code
        FROM fantasy_prices f
        LEFT JOIN player_map m
          ON m.season = f.season AND m.fantasy_player_id = f.player_id AND m.kind = 'player'
        WHERE f.season = ?
        """,
        [season],
    ).df()
    market["position"] = market["position"].map(POSITIONS)
    market["el_team"] = market["team_code"].map(team_map(con, season))
    return market


def _market_from_box_scores(con: duckdb.DuckDBPyConnection, season: str) -> pd.DataFrame:
    """Everyone who appeared in a round, for seasons without fantasy prices. Ids are
    the EuroLeague codes, and coaches are one entry per club."""
    players = con.execute(
        """
        SELECT round, player_code AS player_id, player_code AS el_code, team_code,
               team_code AS el_team, 'P' AS position
        FROM player_games WHERE season = ? AND phase = 'RS'
        """,
        [season],
    ).df()
    coaches = con.execute(
        """
        SELECT DISTINCT round, 'HC_' || team_code AS player_id, NULL AS el_code, team_code,
               team_code AS el_team, 'HC' AS position
        FROM coach_games WHERE season = ? AND phase = 'RS'
        """,
        [season],
    ).df()
    market = pd.concat([players, coaches], ignore_index=True)
    market["price"] = float("nan")
    return market


def _outcomes(market, history, coach_history, season) -> pd.DataFrame:
    """Actual fantasy points per market row. A player with no box score that round
    scores 0 (injured, not dressed, or never played)."""
    cur = history[(history["season"] == season) & (history["phase"] == "RS")]
    player_pts = cur.groupby(["round", "el_code"], as_index=False).agg(
        points=("fantasy_points", "sum"), seconds=("seconds", "sum")
    )
    cur_c = coach_history[(coach_history["season"] == season) & (coach_history["phase"] == "RS")]
    coach_pts = cur_c.groupby(["round", "team_code"], as_index=False).agg(
        points=("fantasy_points", "sum")
    )

    is_coach = market["position"] == COACH
    p = market[~is_coach].merge(player_pts, on=["round", "el_code"], how="left")
    c = market[is_coach].merge(
        coach_pts.rename(columns={"team_code": "el_team"}), on=["round", "el_team"], how="left"
    )
    out = pd.concat([p, c], ignore_index=True)[["round", "player_id", "points", "seconds"]]
    return out.fillna({"points": 0.0, "seconds": 0.0})
