"""Local DuckDB database with the clean tables.

Each season is replaced as a whole on every sync, so re-running a sync is safe.
"""

from pathlib import Path

import duckdb
import pandas as pd

TABLES = ("games", "player_games", "coach_games")


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def replace_season(con: duckdb.DuckDBPyConnection, table: str, season: str, rows: list[dict]):
    if not rows:
        return
    df = pd.DataFrame(rows)  # noqa: F841 (read by DuckDB by name)
    exists = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0]
    if exists:
        con.execute(f"DELETE FROM {table} WHERE season = ?", [season])
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM df")
    else:
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM df")
