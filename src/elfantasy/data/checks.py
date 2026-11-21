"""Data quality checks. Each check is a query returning the offending rows, so an
empty result means the check passed."""

import duckdb
import pandas as pd

REGULATION_SECONDS = 5 * 40 * 60  # five players on court for 40 minutes
OVERTIME_SECONDS = 5 * 5 * 60
SECONDS_TOLERANCE = 60

CHECKS: dict[str, str] = {
    # Our PIR formula must reproduce the official PIR exactly.
    "pir_mismatch": """
        SELECT season, game_code, player_name, pir, pir_computed
        FROM player_games WHERE pir <> pir_computed
    """,
    # Every played game needs a box score.
    "missing_box_score": """
        SELECT g.season, g.game_code, g.home_code, g.away_code
        FROM games g
        WHERE g.played AND NOT EXISTS (
            SELECT 1 FROM player_games p
            WHERE p.season = g.season AND p.game_code = g.game_code
        )
    """,
    "duplicate_player": """
        SELECT season, game_code, player_code, count(*) AS rows
        FROM player_games
        GROUP BY season, game_code, player_code
        HAVING count(*) > 1
    """,
    # Players' points must add up to the team's final score.
    "points_mismatch": """
        SELECT p.season, p.game_code, p.team_code,
               sum(p.points) AS player_points,
               any_value(CASE WHEN p.is_home THEN g.home_score ELSE g.away_score END)
                   AS team_score
        FROM player_games p JOIN games g USING (season, game_code)
        GROUP BY p.season, p.game_code, p.team_code
        HAVING sum(p.points) <> any_value(
            CASE WHEN p.is_home THEN g.home_score ELSE g.away_score END)
    """,
    # Team playing time must be 200 minutes plus 25 per overtime period, and the
    # number of overtime periods must agree with the overtime flag.
    "minutes_mismatch": f"""
        WITH t AS (
            SELECT p.season, p.game_code, p.team_code,
                   sum(p.seconds) AS seconds, any_value(g.overtime) AS overtime
            FROM player_games p JOIN games g USING (season, game_code)
            GROUP BY p.season, p.game_code, p.team_code
        ), k AS (
            SELECT *, greatest(0, round((seconds - {REGULATION_SECONDS})
                                        / {OVERTIME_SECONDS}))::INT AS ot_periods
            FROM t
        )
        SELECT season, game_code, team_code, seconds, overtime, ot_periods
        FROM k
        WHERE abs(seconds - ({REGULATION_SECONDS} + {OVERTIME_SECONDS} * ot_periods))
                  > {SECONDS_TOLERANCE}
           OR (ot_periods > 0) <> overtime
    """,
}


def run_checks(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    return {name: con.sql(query).df() for name, query in CHECKS.items()}
