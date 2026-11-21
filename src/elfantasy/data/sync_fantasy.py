"""Download fantasy prices and the turn schedule into the local database."""

from collections.abc import Callable

import duckdb

from elfantasy.data.fantasy import FantasyClient
from elfantasy.data.parse_fantasy import parse_players, parse_schedule
from elfantasy.data.store import replace_season


def sync_fantasy(
    client: FantasyClient,
    con: duckdb.DuckDBPyConnection,
    season: str,
    log: Callable[[str], None] = print,
) -> None:
    """Price snapshots for every matchday up to the current one (past ones come from
    the cache after the first run), plus the schedule for the whole season."""
    config = client.league_config()
    current = config["current_matchday"]
    matchdays = sorted(config["matchdays"], key=lambda m: m["number"])

    prices: list[dict] = []
    for md in matchdays:
        if md["number"] > current["number"]:
            break
        is_current = md["id"] == current["id"]
        snapshot = client.players(
            season,
            config["current_players_list_id"],
            md,
            current=is_current,
            league_status_id=config.get("status_id"),
        )
        prices += parse_players(season, snapshot)

    schedule: list[dict] = []
    schedule_id = config["current_schedule_id"]
    for md in matchdays:
        schedule += parse_schedule(season, client.schedule(season, schedule_id, md))

    replace_season(con, "fantasy_prices", season, prices)
    replace_season(con, "fantasy_schedule", season, schedule)
    log(
        f"{season}: prices for rounds 1-{current['number']} ({len(prices)} rows), "
        f"schedule for {len(matchdays)} rounds ({len(schedule)} games)"
    )
