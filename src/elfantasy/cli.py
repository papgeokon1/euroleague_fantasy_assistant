"""Command-line entry point: `elf --help`."""

import typer

from elfantasy import __version__
from elfantasy.config import settings
from elfantasy.rules import RULES_2026_27

app = typer.Typer(help="EuroLeague Fantasy assistant.", no_args_is_help=True)


@app.callback()
def _main() -> None:
    """EuroLeague Fantasy assistant."""
    # Windows consoles default to a legacy code page; Claude's answers and player
    # names need UTF-8 (dashes, accents).
    import sys

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


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
    value_weight: float = typer.Option(
        0.0, help="Points per credit of expected price rise (0 = ignore prices)."
    ),
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
                        sell=sell_ids, value_weight=value_weight)  # fmt: skip

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


@app.command()
def serve(
    port: int = typer.Option(8000, help="Port for the web app."),
    reload: bool = typer.Option(False, help="Restart on code changes (development)."),
) -> None:
    """Run the web app: the API on http://localhost:PORT/api and, once the frontend is
    built (`npm run build` in frontend/), the app itself at http://localhost:PORT."""
    import uvicorn

    uvicorn.run("elfantasy.api.app:create_app", factory=True, host="127.0.0.1", port=port,
                reload=reload)  # fmt: skip


@app.command()
def review(
    round_: int = typer.Option(None, "--round", help="Round to review (default: latest played)."),
    backfill: bool = typer.Option(False, help="Review every played round this season."),
) -> None:
    """Review played rounds: projection error and calibration, your score against the
    projection, the recommended plan and the top managers. Stored for the season trend."""
    import pandas as pd

    from elfantasy.backtest.strategies import ModelV1
    from elfantasy.data.fantasy import FantasyClient
    from elfantasy.data.store import connect
    from elfantasy.review import headline, round_played, run_review, season_summary

    if settings.fantasy_token is None:
        typer.echo("ELF_FANTASY_TOKEN is not set in .env.", err=True)
        raise typer.Exit(1)
    season = settings.season_code
    fantasy = FantasyClient(settings.raw_dir, settings.fantasy_token.get_secret_value(),
                            league_id=settings.fantasy_league_id)  # fmt: skip
    with fantasy, connect(settings.db_path) as con:
        played = [r for r in range(1, 41) if round_played(con, season, r)]
        if not played:
            typer.echo("No played rounds with box scores yet.")
            return
        targets = played if backfill else [round_ or played[-1]]
        model = ModelV1(con=con)
        for r in targets:
            typer.echo(headline(run_review(con, fantasy, season, r, RULES_2026_27,
                                           log=typer.echo, model=model)))  # fmt: skip
        summary = season_summary(con, season)
    if not summary.empty:
        with pd.option_context("display.float_format", "{:.2f}".format):
            typer.echo("\nSeason so far:")
            typer.echo(summary.to_string(index=False))


TASK_NAME = "elfantasy-auto"


@app.command()
def auto(
    force: str = typer.Option(
        None, help="Run 'pre_round' or 'turn' now, ignoring the time windows."
    ),
    dry_run: bool = typer.Option(False, help="Sync and show what's due; send nothing."),
) -> None:
    """The scheduled pipeline: sync data, and before a round or between turns send the
    recommendation as a desktop notification. Safe to run as often as you like."""
    from datetime import datetime

    from elfantasy.automation import run

    if force not in (None, "pre_round", "turn"):
        typer.echo("--force must be 'pre_round' or 'turn'.", err=True)
        raise typer.Exit(1)
    log_path = settings.data_dir / "logs" / "auto.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        typer.echo(line)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    try:
        run(settings, RULES_2026_27, force=force, dry_run=dry_run, log=log)
    except Exception as e:  # scheduled runs have no console: make failures visible
        log(f"FAILED: {e!r}")
        raise


schedule_app = typer.Typer(help="Run `elf auto` on a Windows schedule.", no_args_is_help=True)
app.add_typer(schedule_app, name="schedule")


def _schtasks(*args: str):
    import subprocess

    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


@schedule_app.command("install")
def schedule_install(
    hours: int = typer.Option(2, min=1, max=12, help="Run every N hours."),
) -> None:
    """Create (or replace) a Windows scheduled task that runs `elf auto` every N hours
    while you're logged in. Uses pythonw, so no console window pops up."""
    import sys
    from pathlib import Path

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        typer.echo(f"pythonw.exe not found next to {sys.executable}", err=True)
        raise typer.Exit(1)
    command = f'"{pythonw}" -m elfantasy.cli auto'
    result = _schtasks("/Create", "/TN", TASK_NAME, "/SC", "HOURLY", "/MO", str(hours),
                       "/TR", command, "/F")  # fmt: skip
    typer.echo(result.stdout or result.stderr)
    if result.returncode != 0:
        raise typer.Exit(1)
    typer.echo(f"Runs every {hours} h. Log: {settings.data_dir / 'logs' / 'auto.log'}")


@schedule_app.command("remove")
def schedule_remove() -> None:
    """Delete the scheduled task."""
    result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    typer.echo(result.stdout or result.stderr)


@schedule_app.command("status")
def schedule_status() -> None:
    """Show the scheduled task (next and last run) and the end of the log."""
    result = _schtasks("/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST")
    if result.returncode != 0:
        typer.echo("Not installed. Run `elf schedule install`.")
    else:
        keep = ("TaskName", "Status", "Next Run Time", "Last Run Time", "Last Result",
                "Task To Run", "Repeat: Every")  # fmt: skip
        typer.echo("\n".join(ln for ln in result.stdout.splitlines() if ln.startswith(keep)))
    log_path = settings.data_dir / "logs" / "auto.log"
    if log_path.exists():
        typer.echo("\nLast log lines:")
        typer.echo("\n".join(log_path.read_text(encoding="utf-8").splitlines()[-8:]))


def _assistant(con):
    """(context, tools, Anthropic client) for the chat, report and news commands."""
    import anthropic

    from elfantasy.assistant.context import FantasyContext
    from elfantasy.assistant.tools import Tools
    from elfantasy.data.fantasy import FantasyClient

    key = settings.anthropic_api_key
    client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    fantasy = None
    if settings.fantasy_token is not None:
        fantasy = FantasyClient(
            settings.raw_dir,
            settings.fantasy_token.get_secret_value(),
            league_id=settings.fantasy_league_id,
        )
    ctx = FantasyContext.current(con, settings.season_code, fantasy_client=fantasy)
    return ctx, Tools(ctx, client), client


def _no_key_help() -> None:
    typer.echo(
        "No Anthropic API key found. Add ELF_ANTHROPIC_API_KEY=... to .env (or set "
        "ANTHROPIC_API_KEY), then try again.",
        err=True,
    )


@app.command()
def chat(
    effort: str = typer.Option("medium", help="Claude's effort: low, medium, high."),
) -> None:
    """Chat with the assistant about your team. Type 'exit' to quit.

    Claude answers with numbers from the projection model and the optimizer, through
    read-only tools. It never changes your team in the game.
    """
    import anthropic

    from elfantasy.assistant.chat import Assistant
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        ctx, tools, client = _assistant(con)
        bot = Assistant(
            tools, client, effort=effort,
            on_tool=lambda name, args: typer.echo(f"  [{name}]", err=True),
        )  # fmt: skip
        typer.echo(f"Round {ctx.round}. Ask about players, transfers, captain, news. "
                   "'exit' to quit.")  # fmt: skip
        while True:
            try:
                question = input("\nyou> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.lower() in {"exit", "quit", ""}:
                break
            try:
                typer.echo(f"\n{bot.ask(question)}")
            except anthropic.AuthenticationError:
                _no_key_help()
                raise typer.Exit(1) from None


@app.command()
def report(
    effort: str = typer.Option("high", help="Claude's effort: low, medium, high."),
) -> None:
    """Write this round's report (transfers, lineup, captain, risks) to data/reports/."""
    import anthropic

    from elfantasy.assistant.chat import REPORT_PROMPT, Assistant
    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        ctx, tools, client = _assistant(con)
        bot = Assistant(tools, client, effort=effort,
                        on_tool=lambda name, args: typer.echo(f"  [{name}]", err=True))  # fmt: skip
        typer.echo(f"Writing the round {ctx.round} report...")
        try:
            text = bot.ask(REPORT_PROMPT)
        except anthropic.AuthenticationError:
            _no_key_help()
            raise typer.Exit(1) from None
    path = settings.data_dir / "reports" / f"{settings.season_code}_round_{ctx.round:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    typer.echo(f"\n{text}\n\nSaved to {path}")


@app.command()
def news(
    refresh: bool = typer.Option(False, help="Search the web for the latest news first."),
    evaluate: bool = typer.Option(
        False, help="Check logged news against results of rounds already played."
    ),
) -> None:
    """Injury and availability news for this round, as structured statuses.

    Statuses are logged in the database; they don't change the optimizer's numbers
    unless asked (`--evaluate` measures whether they would have helped).
    """
    import anthropic

    from elfantasy.data.store import connect

    with connect(settings.db_path) as con:
        if evaluate:
            _evaluate_news(con)
            return
        ctx, tools, _client = _assistant(con)
        try:
            text = tools.refresh_news({}) if refresh else tools.get_news({})
        except anthropic.AuthenticationError:
            _no_key_help()
            raise typer.Exit(1) from None
    typer.echo(text)


def _evaluate_news(con) -> None:
    import pandas as pd

    from elfantasy.assistant import news as news_mod
    from elfantasy.backtest.data import load_season
    from elfantasy.backtest.simulator import RoundContext
    from elfantasy.backtest.strategies import ModelV1

    season = settings.season_code
    try:
        logged = (
            con.execute(
                "SELECT DISTINCT round FROM news_status WHERE season = ? ORDER BY 1", [season]
            )
            .df()["round"]
            .tolist()
        )
    except Exception:
        logged = []
    data = load_season(con, season)
    rounds = [r for r in logged if r in data.rounds]
    if not rounds:
        typer.echo("No logged news for a round that has been played yet.")
        return
    model = ModelV1(con=con)
    for rnd in rounds:
        market = data.market[data.market["round"] == rnd].set_index("player_id")
        ctx = RoundContext(season, rnd, market, pd.DataFrame(), pd.DataFrame(),
                           RULES_2026_27, None, 0.0, None)  # fmt: skip
        pred = model.predict(ctx)
        actual = data.outcomes[data.outcomes["round"] == rnd].set_index("player_id")["points"]
        result = news_mod.evaluate(pred, news_mod.latest(con, season, rnd), actual)
        typer.echo(f"Round {rnd}: {result}")


if __name__ == "__main__":
    app()
