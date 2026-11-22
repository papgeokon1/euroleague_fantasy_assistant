"""`elf auto`: the scheduled pipeline (Phase 8).

Safe to run as often as you like (the scheduled task runs it every couple of hours).
Each run:

1. Syncs data: this season's box scores, fantasy prices and the player links. Prices
   and ownership must be captured while a round is current, so this runs every time.
2. Before a round (within PRE_ROUND_HOURS of its first game): refreshes the news, runs
   the optimizer and the turn-aware lineup, saves a report and sends a notification.
3. Between turns (after a turn's games are over, before the next turn starts): sends
   the lineup and captain changes for the next turn.

What has been sent is remembered in data/auto_state.json, so nothing is sent twice.
If the fantasy token has expired, it says so (once a day), since snapshots would stop.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

PRE_ROUND_HOURS = 8
TURN_HOURS = 6
GAME_LENGTH = timedelta(hours=2, minutes=15)  # tip-off to final buzzer, roughly


@dataclass
class Turn:
    number: int
    start: datetime  # first tip-off of the turn
    last_start: datetime  # last tip-off of the turn

    @property
    def over(self) -> datetime:
        return self.last_start + GAME_LENGTH


def round_turns(con: duckdb.DuckDBPyConnection, season: str, round_: int) -> list[Turn]:
    rows = con.execute(
        """
        SELECT turn, min(started_at) AS start, max(started_at) AS last
        FROM fantasy_schedule WHERE season = ? AND round = ? GROUP BY turn ORDER BY turn
        """,
        [season, round_],
    ).fetchall()
    # ISO strings in one format, so min/max as text is chronological.
    return [Turn(int(t), _utc(s), _utc(e)) for t, s, e in rows]


def _utc(t) -> datetime:
    ts = pd.Timestamp(t)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.to_pydatetime()


def due_actions(now: datetime, season: str, round_: int, turns: list[Turn], state: dict):
    """Actions to take now, as (key, kind, turn number) - kind is 'pre_round' or
    'turn'. A key already in `state` has been done."""
    out = []
    if not turns:
        return out
    key = f"{season}-{round_}-pre"
    first = turns[0]
    if first.start - timedelta(hours=PRE_ROUND_HOURS) <= now < first.start and key not in state:
        out.append((key, "pre_round", 1))
    for prev, nxt in zip(turns, turns[1:], strict=False):
        key = f"{season}-{round_}-turn{nxt.number}"
        window_open = max(prev.over, nxt.start - timedelta(hours=TURN_HOURS))
        if window_open <= now < nxt.start and key not in state:
            out.append((key, "turn", nxt.number))
    return out


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def local(t: datetime) -> str:
    return t.astimezone().strftime("%a %d %b %H:%M")


# --- the pipeline ------------------------------------------------------------------


def run(
    settings,
    rules,
    now: datetime | None = None,
    force: str | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    notify: Callable[[str, str], bool] | None = None,
) -> list[str]:
    """One pass of the pipeline. `force` runs 'pre_round' or 'turn' now regardless of
    the time windows. `dry_run` syncs and reports what is due, but sends nothing and
    records nothing. Returns the keys of the actions taken."""
    from elfantasy.data.fantasy import FantasyAuthError, FantasyClient
    from elfantasy.data.store import connect
    from elfantasy.notify import notify as desktop_notify

    notify = notify or desktop_notify
    now = now or datetime.now(UTC)
    state_path = settings.data_dir / "auto_state.json"
    state = load_state(state_path)
    season = settings.season_code
    done = []

    if settings.fantasy_token is None:
        log("ELF_FANTASY_TOKEN is not set; nothing to do.")
        return done
    fantasy = FantasyClient(settings.raw_dir, settings.fantasy_token.get_secret_value(),
                            league_id=settings.fantasy_league_id)  # fmt: skip
    try:
        con = connect(settings.db_path)
    except duckdb.IOException as e:
        # Another elf command (e.g. `elf chat`) has the database open; DuckDB allows
        # one writer at a time. The next scheduled run will try again.
        log(f"Database busy, will retry next run: {str(e).splitlines()[0]}")
        return done
    with fantasy, con:
        if not _sync(con, fantasy, settings, now, state, log, notify, dry_run):
            save_state(state_path, state)
            return done
        rnd = int(con.execute(
            "SELECT max(round) FROM fantasy_prices WHERE season = ? AND live", [season]
        ).fetchone()[0])  # fmt: skip
        turns = round_turns(con, season, rnd)
        if not dry_run:
            done += _post_round(con, fantasy, settings, rules, rnd, state, now, log, notify)
        actions = due_actions(now, season, rnd, turns, {} if force else state)
        if force:
            actions = [a for a in actions if a[1] == force] or [
                (f"{season}-{rnd}-{force}-forced", force, 1 if force == "pre_round" else 2)
            ]
        if not actions:
            nxt = next((t for t in turns if t.start > now), None)
            log(f"Round {rnd}: nothing due"
                + (f"; next tip-off {local(nxt.start)}" if nxt else ""))  # fmt: skip
        for key, kind, turn in actions:
            log(f"Round {rnd}: {kind} (turn {turn}) is due")
            if dry_run:
                continue
            try:
                if kind == "pre_round":
                    title, body = _pre_round(con, fantasy, settings, rules, rnd, turns, log)
                else:
                    title, body = _turn(con, fantasy, settings, rules, rnd, turn, turns)
            except FantasyAuthError as e:
                _token_alert(state, now, notify, log, str(e))
                break
            notify(title, body)
            state[key] = now.isoformat(timespec="seconds")
            done.append(key)
    if not dry_run:
        save_state(state_path, state)
    return done


def _sync(con, fantasy, settings, now, state, log, notify, dry_run) -> bool:
    """Sync box scores and fantasy data. False if the fantasy token was rejected."""
    from elfantasy.data.euroleague import EuroleagueClient
    from elfantasy.data.fantasy import FantasyAuthError
    from elfantasy.data.player_map import refresh_player_map
    from elfantasy.data.sync import sync_season
    from elfantasy.data.sync_fantasy import sync_fantasy

    year = int(settings.season_code[1:])
    try:
        with EuroleagueClient(settings.raw_dir) as el:
            sync_season(el, con, year, log=lambda _: None)
    except Exception as e:  # box scores can wait for the next run
        log(f"Box score sync failed (will retry next run): {e}")
    try:
        sync_fantasy(fantasy, con, settings.season_code, log=log)
    except FantasyAuthError as e:
        if not dry_run:
            _token_alert(state, now, notify, log, str(e))
        return False
    refresh_player_map(con, settings.season_code, log=lambda _: None)
    return True


def _post_round(con, fantasy, settings, rules, rnd, state, now, log, notify) -> list[str]:
    """Review rounds whose box scores are complete and haven't been reviewed yet."""
    from elfantasy.review import _exists, headline, round_played, run_review

    done = []
    season = settings.season_code
    for r in (rnd - 1, rnd):
        key = f"{season}-{r}-review"
        if r < 1 or key in state or not round_played(con, season, r):
            continue
        reviewed = (
            _exists(con, "round_reviews")
            and con.execute(
                "SELECT count(*) FROM round_reviews WHERE season = ? AND round = ?", [season, r]
            ).fetchone()[0]
        )
        if reviewed:  # e.g. backfilled by `elf review`: nothing new to say
            state[key] = now.isoformat(timespec="seconds")
            continue
        try:
            line = headline(run_review(con, fantasy, season, r, rules, log=log))
        except Exception as e:  # the review must never stop the sync or the plan
            log(f"Round {r} review failed: {e}")
            continue
        log(line)
        notify(f"Round {r} review", line)
        state[key] = now.isoformat(timespec="seconds")
        done.append(key)
    return done


def _token_alert(state, now, notify, log, message) -> None:
    log(message)
    last = state.get("token_alert")
    if last and now - datetime.fromisoformat(last) < timedelta(hours=24):
        return
    notify(
        "Fantasy token expired",
        "Price snapshots have stopped. Copy a fresh token into .env (ELF_FANTASY_TOKEN).",
    )
    state["token_alert"] = now.isoformat(timespec="seconds")


def _context(con, fantasy, settings, rules, rnd):
    from elfantasy.assistant.context import FantasyContext

    return FantasyContext(con=con, season=settings.season_code, round=rnd, rules=rules,
                          fantasy_client=fantasy)  # fmt: skip


def _pre_round(con, fantasy, settings, rules, rnd, turns, log) -> tuple[str, str]:
    from elfantasy.advise import recommend, slot_name, turn_advice
    from elfantasy.assistant import news as news_mod
    from elfantasy.assistant.tools import Tools

    ctx = _context(con, fantasy, settings, rules, rnd)
    if settings.anthropic_api_key:
        try:
            import anthropic

            Tools(ctx, anthropic.Anthropic(api_key=settings.anthropic_api_key)).refresh_news({})
        except Exception as e:  # the plan doesn't depend on news
            log(f"News refresh failed: {e}")

    config = fantasy.league_config()
    team = fantasy.my_teams()[0]["id"]
    md, prev = config["current_matchday"], config.get("previous_matchday")
    budget = float(fantasy.team_round(team, md["id"])["credits"])
    roster = fantasy.roster(team, md["id"])["players"]
    live = {p["id"] for p in roster}
    baseline = {p["id"] for p in fantasy.roster(team, prev["id"])["players"]} if prev else None
    rec = recommend(con, settings.season_code, rnd, rules, budget, baseline, model=ctx.model)
    advice = turn_advice(roster, ctx.market, ctx.predictions(), rules)

    # Phase 9: keep what we predicted and recommended, to review after the round.
    from elfantasy.backtest.strategies import MODEL_VERSION
    from elfantasy.review import log_predictions, log_recommendation

    log_predictions(con, settings.season_code, rnd, ctx.market, ctx.predictions(), "live",
                    f"model_v1:{MODEL_VERSION}")  # fmt: skip
    log_recommendation(con, settings.season_code, rnd, rec.table, budget)

    names = {pid: ctx.name(pid) for pid in ctx.market.index}
    sell = sorted(names.get(i, str(i)) for i in live - rec.plan.squads[0])
    buy = sorted(names.get(i, str(i)) for i in rec.plan.squads[0] - live)
    captain = rec.table.index[rec.table["slot"] == "captain"][0]
    squad_news = news_mod.latest(con, settings.season_code, rnd)
    squad_news = squad_news[squad_news["player_id"].isin(live | rec.plan.squads[0])]

    lines = [
        f"# Round {rnd} plan ({settings.season_code})",
        "",
        f"Deadline: first tip-off {local(turns[0].start)}. Generated "
        f"{datetime.now().strftime('%a %d %b %H:%M')}.",
        "",
        "## Transfers",
        f"- Sell: {', '.join(sell) or 'nobody'}",
        f"- Buy: {', '.join(buy) or 'nobody'}",
        f"- Transfer limit: {'unlimited' if rec.transfer_limit is None else rec.transfer_limit}"
        f"; squad cost {rec.table['price'].sum():.1f} of {budget:.1f}",
        "",
        "## Recommended lineup",
        _md_table(rec.table.assign(player=[names[i] for i in rec.table.index])[
            ["slot", "player", "position", "team_code", "opp_code", "turn", "price", "p_play",
             "expected"]].round(2)),  # fmt: skip
        "",
        "## Your current roster: lineup advice",
        _md_table(pd.DataFrame(
            [{"player": names.get(i, i), "now": slot_name(i, advice.current, ctx.market),
              "recommended": slot_name(i, advice.best, ctx.market)}
             for i in advice.values.index]
        )),  # fmt: skip
        "",
        "## News for these players",
        _md_table(squad_news.assign(player=[names.get(i, i) for i in squad_news["player_id"]])[
            ["player", "status", "reason", "source"]]) if len(squad_news) else "Nothing logged.",
    ]  # fmt: skip
    path = settings.data_dir / "reports" / f"auto_{settings.season_code}_round_{rnd:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"Report saved to {path}")

    body = (f"Sell {', '.join(sell) or 'nobody'}. Buy {', '.join(buy) or 'nobody'}. "
            f"Captain {names[captain]}. Deadline {local(turns[0].start)}.")  # fmt: skip
    return f"Round {rnd} plan", body


def _turn(con, fantasy, settings, rules, rnd, turn, turns) -> tuple[str, str]:
    from elfantasy.advise import slot_name, turn_advice

    ctx = _context(con, fantasy, settings, rules, rnd)
    roster = ctx.roster()
    advice = turn_advice(roster, ctx.market, ctx.predictions(), rules)
    names = {p["id"]: p["last_name"] for p in roster}
    if advice.best == advice.current:
        return f"Round {rnd}, turn {turn}", "No changes needed: your lineup is already best."
    moves = []
    for pid in advice.values.index:
        now, best = slot_name(pid, advice.current, ctx.market), slot_name(pid, advice.best,
                                                                           ctx.market)  # fmt: skip
        if now != best:
            moves.append(f"{names[pid]} {now}->{best}")
    start = next((t.start for t in turns if t.number == turn), None)
    body = (f"{'; '.join(moves)}. +{advice.gain:.1f} expected points."
            + (f" Turn {turn} starts {local(start)}." if start else ""))  # fmt: skip
    return f"Round {rnd}, turn {turn} changes", body


def _md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    rows += ["| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |"
             for r in df.itertuples(index=False)]  # fmt: skip
    return "\n".join(rows)
