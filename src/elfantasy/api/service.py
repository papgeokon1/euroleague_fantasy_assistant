"""What the API endpoints return, as JSON-ready data.

Built on the same core functions as the CLI and the chat tools (projections, the
optimizer, turn advice, news). Nothing here changes anything in the game.
"""

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from elfantasy.assistant import news as news_mod
from elfantasy.assistant.context import FantasyContext
from elfantasy.automation import round_turns


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> JSON-safe list of dicts (NaN -> null, numpy -> Python)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _player_rows(ctx: FantasyContext, ids, pred: pd.DataFrame | None = None) -> pd.DataFrame:
    m = ctx.market.loc[list(ids)]
    p = (pred if pred is not None else ctx.predictions()).loc[list(ids)]
    return pd.DataFrame(
        {
            "id": m.index,
            "name": (m["first_name"] + " " + m["last_name"]).to_numpy(),
            "position": m["position"].to_numpy(),
            "team": m["team_code"].to_numpy(),
            "opponent": m["opp_code"].to_numpy(),
            "home": m["home"].to_numpy(),
            "turn": m["turn"].to_numpy(),
            "price": m["price"].to_numpy(),
            "p_play": p["p_play"].round(3).to_numpy(),
            "low": p["q10"].round(1).to_numpy(),
            "median": p["q50"].round(1).to_numpy(),
            "high": p["q90"].round(1).to_numpy(),
            "expected": p["expected"].round(2).to_numpy(),
            "per_credit": (p["expected"] / m["price"]).round(3).to_numpy(),
            "popularity": m.get("popularity", pd.Series(np.nan, index=m.index)).to_numpy(),
        }
    )


def _news_by_player(ctx: FantasyContext) -> dict:
    n = news_mod.latest(ctx.con, ctx.season, ctx.round)
    return {
        int(r.player_id): {"status": r.status, "reason": r.reason, "source": r.source,
                           "fetched_at": r.fetched_at}
        for r in n.itertuples()
    }  # fmt: skip


def status(ctx: FantasyContext, has_token: bool, has_key: bool) -> dict:
    turns = round_turns(ctx.con, ctx.season, ctx.round)
    synced = ctx.con.execute(
        "SELECT max(fetched_at) FROM fantasy_prices WHERE season = ? AND round = ?",
        [ctx.season, ctx.round],
    ).fetchone()[0]
    now = datetime.now(UTC)
    return {
        "season": ctx.season,
        "round": ctx.round,
        "deadline": turns[0].start.isoformat() if turns else None,
        "turns": [
            {
                "number": t.number,
                "start": t.start.isoformat(),
                "last_start": t.last_start.isoformat(),
                "started": t.start <= now,
            }
            for t in turns
        ],  # fmt: skip
        "prices_synced_at": synced,
        "fantasy_token": has_token,
        "anthropic_key": has_key,
    }


def players(ctx: FantasyContext, search=None, position=None, team=None, max_price=None,
            sort="expected", limit=50) -> list[dict]:  # fmt: skip
    m = ctx.market
    ids = m.index
    if search:
        ids = pd.Index(ctx.find(search, limit=limit))
    if position:
        ids = ids[m.loc[ids, "position"] == position]
    if team:
        ids = ids[m.loc[ids, "team_code"].str.upper() == team.upper()]
    if max_price:
        ids = ids[m.loc[ids, "price"] <= max_price]
    rows = _player_rows(ctx, ids)
    key = {"expected": "expected", "value": "per_credit", "upside": "high",
           "price": "price"}.get(sort, "expected")  # fmt: skip
    if not search:
        rows = rows.sort_values(key, ascending=False)
    news = _news_by_player(ctx)
    rows["news"] = [news.get(int(i), {}).get("status") for i in rows["id"]]
    return records(rows.head(limit))


def compare(ctx: FantasyContext, ids: list[int]) -> list[dict]:
    from elfantasy.model.prices import PriceModel, price_history

    ids = [i for i in ids if i in ctx.market.index]
    rows = _player_rows(ctx, ids)
    later = [r for r in (ctx.round + 1, ctx.round + 2) if r <= ctx.last_round()]
    rows["next_rounds"] = [
        [{"round": r, "expected": round(float(ctx.predictions(r).at[i, "expected"]), 2)}
         for r in [ctx.round, *later]]
        for i in ids
    ]  # fmt: skip
    history = price_history(ctx.con, ctx.season)
    prices = PriceModel.fit(history[history["round"] < ctx.round], ctx.rules)
    if prices is not None:
        price = ctx.market.loc[ids, "price"]
        rows["keep_price_at"] = np.round(prices.break_even(price), 1)
        rows["p_price_rise"] = np.round(
            prices.rise_probability(price, ctx.predictions().loc[ids]), 3
        )
        rows["expected_price_change"] = np.round(
            prices.expected_change(price, ctx.predictions().loc[ids, "p_play"],
                                   ctx.predictions().loc[ids, "mean"],
                                   ctx.market.loc[ids, "position"] == "HC"), 3
        )  # fmt: skip
    news = _news_by_player(ctx)
    rows["news"] = [news.get(int(i)) for i in ids]
    return records(rows)


def team(ctx: FantasyContext) -> dict:
    from elfantasy.advise import slot_name, turn_advice

    client = ctx.fantasy_client
    config = client.league_config()
    team_id = ctx.team_id or client.my_teams()[0]["id"]
    md = config["current_matchday"]
    state = client.team_round(team_id, md["id"])
    roster = client.roster(team_id, md["id"])["players"]
    ids = [p["id"] for p in roster if p["id"] in ctx.market.index]
    rows = _player_rows(ctx, ids).set_index("id")
    advice = turn_advice(roster, ctx.market, ctx.predictions(), ctx.rules)
    by_id = {p["id"]: p for p in roster}
    news = _news_by_player(ctx)
    out = []
    for pid in ids:
        p = by_id[pid]
        row = {"id": pid, **records(rows.loc[[pid]].reset_index(drop=True))[0]}
        row.update(
            slot="coach" if ctx.is_coach(pid) else slot_name(pid, advice.current, ctx.market),
            recommended=("coach" if ctx.is_coach(pid)
                         else slot_name(pid, advice.best, ctx.market)),
            played=bool(p.get("match_played") or p.get("match_live")),
            points=p.get("pts"),
            news=news.get(pid),
        )  # fmt: skip
        out.append(row)
    return {
        "team_name": state.get("name"),
        "budget": state.get("credits"),
        "total_points": state.get("total_pts"),
        "transfers_used": state.get("trades"),
        "transfer_limit": state.get("max_num_trades_per_matchday"),
        "players": out,
        "advice": {
            "before_round": advice.before_round,
            "gain": round(advice.gain, 2),
            "captain_now": advice.current.captain,
            "captain_recommended": advice.best.captain,
            "changes": advice.best != advice.current,
        },
    }


def optimize(ctx: FantasyContext, horizon: int = 3, sell: list[int] | None = None,
             from_scratch: bool = False, apply_news: bool = False) -> dict:  # fmt: skip
    from elfantasy.advise import recommend

    client = ctx.fantasy_client
    budget, baseline, live = 100.0, None, set()
    if client is not None:
        config = client.league_config()
        team_id = ctx.team_id or client.my_teams()[0]["id"]
        md, prev = config["current_matchday"], config.get("previous_matchday")
        budget = float(client.team_round(team_id, md["id"])["credits"])
        live = {p["id"] for p in client.roster(team_id, md["id"])["players"]}
        if prev and not from_scratch:
            baseline = {p["id"] for p in client.roster(team_id, prev["id"])["players"]}
    news = news_mod.latest(ctx.con, ctx.season, ctx.round) if apply_news else None
    rec = recommend(ctx.con, ctx.season, ctx.round, ctx.rules, budget, baseline,
                    horizon=horizon, sell=set(sell or []), model=ctx.model, news=news)  # fmt: skip
    squad = rec.plan.squads[0]
    pred = rec.table[["p_play", "q10", "q50", "q90", "expected"]]
    rows = _player_rows(ctx, rec.table.index, pred=pred)
    rows["slot"] = rec.table["slot"].to_numpy()
    rows["new"] = [bool(live) and i not in live for i in rec.table.index]
    mult = rows["slot"].map({"captain": 2.0, "starter": 1.0, "sixth": 1.0, "bench": 0.5,
                             "coach": 1.0})  # fmt: skip
    sold = [i for i in live - squad if i in ctx.market.index]
    return {
        "round": ctx.round,
        "rounds_planned": rec.rounds,
        "budget": budget,
        "cost": round(float(rows["price"].sum()), 2),
        "transfer_limit": rec.transfer_limit,
        "expected_points": round(float((rows["expected"] * mult).sum()), 1),
        "news_applied": apply_news,
        "squad": records(rows),
        "sell": records(_player_rows(ctx, sold)) if sold else [],
    }


def news(ctx: FantasyContext) -> list[dict]:
    n = news_mod.latest(ctx.con, ctx.season, ctx.round)
    if n.empty:
        return []
    m = ctx.market
    n = n[n["player_id"].isin(m.index)]
    n = n.assign(name=[ctx.name(i) for i in n["player_id"]],
                 team=[m.at[i, "team_code"] for i in n["player_id"]])  # fmt: skip
    return records(n[["player_id", "name", "team", "status", "reason", "source", "fetched_at"]])


def schedule(ctx: FantasyContext, round_: int | None = None) -> list[dict]:
    g = ctx.con.execute(
        """
        SELECT turn, started_at, home_code, away_code, status, home_score, away_score
        FROM fantasy_schedule WHERE season = ? AND round = ? ORDER BY started_at
        """,
        [ctx.season, round_ or ctx.round],
    ).df()
    return records(g)


def season_review(ctx: FantasyContext) -> dict:
    """The season trend from the round reviews, plus the latest review in full."""
    import json as _json

    from elfantasy.review import season_summary

    summary = season_summary(ctx.con, ctx.season)
    latest = None
    if not summary.empty:
        raw = ctx.con.execute(
            "SELECT review FROM round_reviews WHERE season = ? ORDER BY round DESC LIMIT 1",
            [ctx.season],
        ).fetchone()
        latest = _json.loads(raw[0])
        names = {pid: ctx.name(pid) for pid in ctx.market.index}
        for s in latest.get("surprises", []):
            s["name"] = names.get(s["player_id"], str(s["player_id"]))
    return {"rounds": records(summary), "latest": latest}
