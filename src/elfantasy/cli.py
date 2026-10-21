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
    from elfantasy.data.euroleague import EuroleagueClient
    from elfantasy.data.store import connect
    from elfantasy.data.sync import sync_season

    with EuroleagueClient(settings.raw_dir) as client, connect(settings.db_path) as con:
        for year in range(first, last + 1):
            sync_season(client, con, year, log=typer.echo)


@data_app.command("check")
def data_check() -> None:
    """Sanity-check the database: row counts and PIR formula vs official PIR."""
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        typer.echo(
            con.sql("""
                SELECT season,
                       count(DISTINCT game_code) AS games,
                       count(*) AS player_rows,
                       count(*) FILTER (WHERE pir <> pir_computed) AS pir_mismatches
                FROM player_games GROUP BY season ORDER BY season
            """)
            .df()
            .to_string(index=False)
        )


if __name__ == "__main__":
    app()
