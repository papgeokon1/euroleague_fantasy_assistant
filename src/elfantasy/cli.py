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


@app.command()
def backtest(
    season: int = typer.Option(2026, help="Season start year (2026 = 2026-27)."),
    strategy: str = typer.Option("all", help="Strategy name, or 'all'."),
    rounds: bool = typer.Option(True, help="Show the round-by-round table."),
) -> None:
    """Replay a season with only pre-deadline information and score each strategy.

    Seasons with fantasy prices get a full simulation (budget, transfers, lineup).
    Older seasons only measure projection error (MAE), since there are no prices.
    """
    from elfantasy.backtest.data import load_season
    from elfantasy.backtest.simulator import run_season
    from elfantasy.backtest.strategies import STRATEGIES
    from elfantasy.data.euroleague import season_code
    from elfantasy.data.store import connect

    names = list(STRATEGIES) if strategy == "all" else [strategy]
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        typer.echo(f"Unknown strategy {unknown}. Choose from: {', '.join(STRATEGIES)}", err=True)
        raise typer.Exit(1)

    with connect(settings.db_path) as con:
        data = load_season(con, season_code(season))
    mode = "full simulation" if data.has_prices else "projection error only (no prices)"
    typer.echo(f"{data.season}: rounds {data.rounds[0]}-{data.rounds[-1]}, {mode}\n")

    summary = []
    for name in names:
        result = run_season(data, STRATEGIES[name]())
        if rounds:
            typer.echo(f"== {name}")
            typer.echo(result.rounds.round(2).to_string(index=False))
            typer.echo()
        row = {"strategy": name, "mae": round(result.mae, 3), "bias": round(result.bias, 3)}
        if data.has_prices:
            row["total_points"] = round(result.total_points, 1)
            row["per_round"] = round(result.total_points / len(data.rounds), 1)
        summary.append(row)

    import pandas as pd

    typer.echo(pd.DataFrame(summary).to_string(index=False))


@app.command("backtest-turns")
def backtest_turns(
    seasons: list[int] = typer.Option([2024, 2025], "--season", help="Season start year."),
    squads: int = typer.Option(20, help="Random squads per round."),
) -> None:
    """Measure the in-round turn strategy against a fixed lineup, on many squads.

    Lineup decisions don't need prices, so past seasons work: every policy gets the
    same squads and the same actual results.
    """
    from elfantasy.backtest.turn_eval import evaluate, summarize
    from elfantasy.data.euroleague import season_code
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        results = evaluate(con, [season_code(s) for s in seasons], squads_per_round=squads)
    typer.echo(results[["static", "adjust", "sim_start", "sim_start_adjust"]].mean().round(2))
    typer.echo()
    typer.echo(summarize(results).round(2).to_string(index=False))


@app.command()
def optimize(
    horizon: int = typer.Option(3, help="Rounds to plan ahead (1 = this round only)."),
    from_scratch: bool = typer.Option(False, help="Ignore your squad: best team for the budget."),
    sell: list[str] | None = typer.Option(
        None, help="Last name of a player to force out (repeatable)."
    ),
    injuries: bool = typer.Option(True, help="Treat players the game lists as out as out."),
    team_id: int = typer.Option(None, help="Fantasy team id (default: your first team)."),
) -> None:
    """Recommend transfers, lineup and captain for the upcoming round.

    Refreshes fantasy prices first, then plans the next HORIZON rounds with the
    model_v1 projections and the optimizer.
    """
    import pandas as pd

    from elfantasy.advise import recommend
    from elfantasy.data.fantasy import FantasyAuthError, FantasyClient
    from elfantasy.data.player_map import refresh_player_map
    from elfantasy.data.store import connect
    from elfantasy.data.sync_fantasy import sync_fantasy

    if settings.fantasy_token is None:
        typer.echo("ELF_FANTASY_TOKEN is not set in .env. See docs/FANTASY_API.md.", err=True)
        raise typer.Exit(1)
    sell = sell or []
    season = settings.season_code
    try:
        with (
            FantasyClient(
                settings.raw_dir,
                settings.fantasy_token.get_secret_value(),
                league_id=settings.fantasy_league_id,
            ) as client,
            connect(settings.db_path) as con,
        ):
            sync_fantasy(client, con, season, log=lambda _: None)
            refresh_player_map(con, season, log=lambda _: None)
            config = client.league_config()
            md, prev = config["current_matchday"], config.get("previous_matchday")
            team = team_id or client.my_teams()[0]["id"]
            state = client.team_round(team, md["id"])
            live = {p["id"] for p in client.roster(team, md["id"])["players"]}
            baseline = None
            if not from_scratch and prev:
                baseline = {p["id"] for p in client.roster(team, prev["id"])["players"]}
    except FantasyAuthError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e

    with connect(settings.db_path) as con:
        names = con.execute(
            "SELECT player_id, last_name FROM fantasy_prices WHERE season = ? AND round = ?",
            [season, md["number"]],
        ).df()
        sell_ids = set(names.loc[names["last_name"].str.lower().isin(
            [s.lower() for s in sell]), "player_id"])  # fmt: skip
        if len(sell_ids) < len(sell):
            typer.echo(f"Warning: not every name in {sell} matched a player.", err=True)
        typer.echo(f"Round {md['number']}: budget {state['credits']}, training the model...")
        rec = recommend(con, season, md["number"], RULES_2026_27, float(state["credits"]),
                        baseline, horizon=horizon, use_injuries=injuries,
                        sell=sell_ids)  # fmt: skip

    t = rec.table
    view = pd.DataFrame(
        {
            "slot": t["slot"],
            "player": t["first_name"].str[0] + ". " + t["last_name"],
            "pos": t["position"],
            "team": t["team_code"],
            "opp": t["opp_code"].fillna("")
            + t["home"].map({True: " (h)", False: " (a)"}).fillna(""),
            "turn": t["turn"],
            "price": t["price"],
            "p_play": t["p_play"].round(2),
            "low": t["q10"].round(1),
            "median": t["q50"].round(1),
            "high": t["q90"].round(1),
            "expected": t["expected"].round(1),
            "new": t.index.map(lambda i: "IN" if i not in live else ""),
        }
    )
    limit = "unlimited" if rec.transfer_limit is None else rec.transfer_limit
    typer.echo(f"\nRecommended squad for round {rec.round} (planned over rounds {rec.rounds}, "
               f"transfer limit this round: {limit})\n")  # fmt: skip
    typer.echo(view.to_string(index=False))
    out = live - rec.plan.squads[0]
    sold = names.set_index("player_id").reindex(list(out))["last_name"].dropna().tolist()
    bought = view.loc[view["new"] == "IN", "player"].tolist()
    typer.echo(
        f"\nCompared with your current roster: sell {sold or 'nobody'}, buy {bought or 'nobody'}"
    )
    typer.echo(f"Squad cost {t['price'].sum():.1f} of {rec.budget:.1f}")
    mult = t["slot"].map({"captain": 2.0, "starter": 1.0, "sixth": 1.0, "bench": 0.5, "coach": 1.0})
    typer.echo(f"Expected points this round: {(t['expected'] * mult).sum():.1f}")


@app.command()
def turn(
    injuries: bool = typer.Option(True, help="Treat players the game lists as out as out."),
    team_id: int = typer.Option(None, help="Fantasy team id (default: your first team)."),
) -> None:
    """Lineup and captain changes for your live roster.

    Before the round: the starting lineup and captain to set, chosen with later
    changes in mind. Between turns: which swaps and captain move gain the most,
    given the scores already in. Players whose game has started can't move up.
    """
    import pandas as pd

    from elfantasy.advise import predict_round, slot_name, turn_advice
    from elfantasy.data.fantasy import FantasyAuthError, FantasyClient
    from elfantasy.data.player_map import refresh_player_map
    from elfantasy.data.store import connect
    from elfantasy.data.sync_fantasy import sync_fantasy

    if settings.fantasy_token is None:
        typer.echo("ELF_FANTASY_TOKEN is not set in .env. See docs/FANTASY_API.md.", err=True)
        raise typer.Exit(1)
    season = settings.season_code
    try:
        with (
            FantasyClient(
                settings.raw_dir,
                settings.fantasy_token.get_secret_value(),
                league_id=settings.fantasy_league_id,
            ) as client,
            connect(settings.db_path) as con,
        ):
            sync_fantasy(client, con, season, log=lambda _: None)
            refresh_player_map(con, season, log=lambda _: None)
            md = client.league_config()["current_matchday"]
            team = team_id or client.my_teams()[0]["id"]
            roster = client.roster(team, md["id"])["players"]
    except FantasyAuthError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e

    with connect(settings.db_path) as con:
        market, pred = predict_round(con, season, md["number"], RULES_2026_27, injuries)
    advice = turn_advice(roster, market, pred, RULES_2026_27)

    names = {p["id"]: f"{p['first_name'][:1]}. {p['last_name']}" for p in roster}
    rows = []
    for pid in advice.values.index:
        now = slot_name(pid, advice.current, market)
        then = slot_name(pid, advice.best, market)
        rows.append(
            {
                "player": names[pid],
                "pos": market.at[pid, "position"],
                "turn": market.at[pid, "turn"],
                "status": "played" if pid in advice.played else "to play",
                "points": round(advice.values[pid], 1),
                "now": now,
                "recommended": then if then != now else "",
            }
        )
    order = {"captain": 0, "starter": 1, "sixth": 2, "bench": 3}
    view = pd.DataFrame(rows).sort_values(
        ["now", "turn"], key=lambda c: c.map(order) if c.name == "now" else c
    )
    title = (
        "Before the round"
        if advice.before_round
        else f"After {len(advice.played)} players have played"
    )
    typer.echo(f"Round {md['number']}: {title} (points = actual if played, else expected)\n")
    typer.echo(view.to_string(index=False))
    if advice.best == advice.current:
        typer.echo("\nNo change: your current lineup is already the best option.")
    else:
        typer.echo(
            f"\nMake the changes in the 'recommended' column: +{advice.gain:.1f} expected points."
        )
        if advice.best.captain != advice.current.captain:
            typer.echo(f"Captain: {names[advice.current.captain]} -> {names[advice.best.captain]}")


if __name__ == "__main__":
    app()
