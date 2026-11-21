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


# Run only once `player_map` exists (it needs the fantasy data). These are warnings, not
# failures: a few players (late signings, youth call-ups) are never added to the game.
PLAYER_MAP_CHECKS: dict[str, str] = {
    # Everyone who played this season should be in the fantasy game.
    "unmapped_player": """
        SELECT p.season, p.team_code, p.player_code, any_value(p.player_name) AS name,
               count(*) AS games
        FROM player_games p
        WHERE p.season IN (SELECT DISTINCT season FROM player_map)
          AND p.player_code NOT IN (
              SELECT el_code FROM player_map m WHERE m.season = p.season AND m.kind = 'player')
        GROUP BY ALL
    """,
    "unmapped_coach": """
        SELECT c.season, c.team_code, c.coach_code, any_value(c.coach_name) AS name,
               count(*) AS games
        FROM coach_games c
        WHERE c.season IN (SELECT DISTINCT season FROM player_map)
          AND c.coach_code NOT IN (
              SELECT el_code FROM player_map m WHERE m.season = c.season AND m.kind = 'coach')
        GROUP BY ALL
    """,
}


WARNINGS = set(PLAYER_MAP_CHECKS)


def run_checks(con: duckdb.DuckDBPyConnection) -> dict[str, pd.DataFrame]:
    checks = dict(CHECKS)
    tables = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    if "player_map" in tables:
        checks |= PLAYER_MAP_CHECKS
    return {name: con.execute(query).df() for name, query in checks.items()}
