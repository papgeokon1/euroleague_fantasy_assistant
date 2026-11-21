"""Command-line entry point: `elf --help`."""

import typer

from elfantasy import __version__
from elfantasy.config import settings
from elfantasy.rules import RULES_2026_27

app = typer.Typer(help="EuroLeague Fantasy assistant.", no_args_is_help=True)
data_app = typer.Typer(help="Download and check data.", no_args_is_help=True)
app.add_typer(data_app, name="data")


@app.command()
def version() -> None:
    """Print the package version."""
    typer.echo(__version__)


@app.command()
def rules() -> None:
    """Show the game rules the assistant currently assumes."""
    typer.echo(RULES_2026_27.model_dump_json(indent=2))


@data_app.command("sync")
def data_sync(
    first: int = typer.Option(2016, help="First season start year (2016 = 2016-17)."),
    last: int = typer.Option(2026, help="Last season start year."),
) -> None:
    """Download games and box scores into the local database."""
    from elfantasy.data.euroleague import EuroleagueClient, season_code
    from elfantasy.data.player_map import refresh_player_map
    from elfantasy.data.store import connect
    from elfantasy.data.sync import sync_season

    with EuroleagueClient(settings.raw_dir) as client, connect(settings.db_path) as con:
        for year in range(first, last + 1):
            sync_season(client, con, year, log=typer.echo)
            if season_code(year) == settings.season_code:
                refresh_player_map(con, settings.season_code, log=typer.echo)


@data_app.command("fantasy")
def data_fantasy() -> None:
    """Download fantasy prices (all rounds so far) and the turn schedule.

    Run it at least once per round before the deadline: popularity and injury fields
    can only be captured live for the current round.
    """
    from elfantasy.data.fantasy import FantasyAuthError, FantasyClient
    from elfantasy.data.player_map import refresh_player_map
    from elfantasy.data.store import connect
    from elfantasy.data.sync_fantasy import sync_fantasy

    if settings.fantasy_token is None:
        typer.echo("ELF_FANTASY_TOKEN is not set in .env. See docs/FANTASY_API.md.", err=True)
        raise typer.Exit(1)
    token = settings.fantasy_token.get_secret_value()
    try:
        with (
            FantasyClient(settings.raw_dir, token, league_id=settings.fantasy_league_id) as client,
            connect(settings.db_path) as con,
        ):
            sync_fantasy(client, con, settings.season_code, log=typer.echo)
            refresh_player_map(con, settings.season_code, log=typer.echo)
    except FantasyAuthError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e


@data_app.command("check")
def data_check() -> None:
    """Sanity-check the database. Exits with an error if any check fails."""
    from elfantasy.data.checks import WARNINGS, run_checks
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        typer.echo(
            con.sql("""
                SELECT season, count(DISTINCT game_code) AS games, count(*) AS player_rows
                FROM player_games GROUP BY season ORDER BY season
            """)
            .df()
            .to_string(index=False)
        )
        typer.echo()
        failed = False
        for name, rows in run_checks(con).items():
            status = "OK  " if rows.empty else "WARN" if name in WARNINGS else "FAIL"
            typer.echo(f"{status} {name}: {len(rows)} rows")
            if not rows.empty:
                failed = failed or name not in WARNINGS
                typer.echo(rows.head(10).to_string(index=False))
    if failed:
        raise typer.Exit(1)


@data_app.command("round")
def data_round(
    round_: int = typer.Argument(..., metavar="ROUND"),
    season: int = typer.Option(2026, help="Season start year."),
    top: int = typer.Option(20, help="How many players to show."),
) -> None:
    """Top fantasy scorers of a round, to compare against the app."""
    from elfantasy.data.euroleague import season_code
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        df = con.execute(
            """
            SELECT player_name, team_code, opp_code, won, pir, round(fantasy_points, 2) AS fantasy
            FROM player_games WHERE season = ? AND round = ? AND phase = 'RS'
            ORDER BY fantasy_points DESC LIMIT ?
            """,
            [season_code(season), round_, top],
        ).df()
    typer.echo(df.to_string(index=False))


if __name__ == "__main__":
    app()
