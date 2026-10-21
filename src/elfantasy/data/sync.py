"""Download seasons from the EuroLeague API into the local database."""

from collections.abc import Callable

import duckdb

from elfantasy.data.euroleague import EuroleagueClient, season_code
from elfantasy.data.parse import parse_game, parse_game_stats
from elfantasy.data.store import replace_season


def sync_season(
    client: EuroleagueClient,
    con: duckdb.DuckDBPyConnection,
    year: int,
    log: Callable[[str], None] = print,
) -> None:
    season = season_code(year)
    games = [parse_game(season, g) for g in client.games(season)]
    played = [g for g in games if g["played"]]

    players: list[dict] = []
    coaches: list[dict] = []
    for i, game in enumerate(played, 1):
        stats = client.game_stats(season, game["game_code"])
        p, c = parse_game_stats(game, stats)
        players += p
        coaches += c
        if i % 50 == 0:
            log(f"  {season}: {i}/{len(played)} box scores")

    replace_season(con, "games", season, games)
    replace_season(con, "player_games", season, players)
    replace_season(con, "coach_games", season, coaches)
    log(f"{season}: {len(games)} games ({len(played)} played), {len(players)} player rows")
