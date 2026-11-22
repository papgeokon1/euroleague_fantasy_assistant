"""Tools Claude can call. Each one reads the projections, optimizer or game data and
returns plain text. None of them changes anything in the game.

The numbers come from the model and the optimizer; Claude's job is to explain them.
"""

from collections.abc import Callable

import pandas as pd

from elfantasy.assistant import news as news_mod
from elfantasy.assistant.context import FantasyContext

POSITIONS = {"G": "Guard", "F": "Forward", "C": "Center", "HC": "Head coach"}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TOOL_DEFINITIONS = [
    {
        "name": "get_projections",
        "description": (
            "Projected fantasy points for this round's players, from the model. Returns "
            "price, opponent, turn, chance of playing (p_play), the 10/50/90% range of "
            "points if he plays, expected points (p_play x mean) and points per credit. "
            "Filter by position, team or max price; sort by expected points, value "
            "(points per credit) or upside (90% value)."
        ),
        "input_schema": _schema(
            {
                "position": {"type": "string", "enum": list(POSITIONS)},
                "team": {"type": "string", "description": "Team code, e.g. OLY, PAO, RMB."},
                "max_price": {"type": "number"},
                "sort_by": {"type": "string", "enum": ["expected", "value", "upside"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 40},
            }
        ),
    },
    {
        "name": "compare_players",
        "description": (
            "Side-by-side comparison of 2-6 players: price, this round's projection and "
            "range, expected points over the next 3 rounds, opponent and turn, the score "
            "needed to keep his price, the chance his price rises, and any news status."
        ),
        "input_schema": _schema(
            {
                "players": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                }
            },
            ["players"],
        ),  # fmt: skip
    },
    {
        "name": "explain_projection",
        "description": (
            "Why the model projects what it does for one player: recent form, season and "
            "career averages, games missed lately, minutes, matchup (home/away, rest, "
            "opponent strength, points the opponent allows to his position)."
        ),
        "input_schema": _schema({"player": {"type": "string"}}, ["player"]),
    },
    {
        "name": "get_schedule",
        "description": "Games of a round with their turn (day) and kick-off times.",
        "input_schema": _schema({"round": {"type": "integer", "minimum": 1}}),
    },
    {
        "name": "get_my_team",
        "description": (
            "The user's current squad from the game: lineup slot, captain, turn, price, "
            "points so far this round (if played) and projected points."
        ),
        "input_schema": _schema({}),
    },
    {
        "name": "suggest_transfers",
        "description": (
            "Run the optimizer: the best squad, lineup and captain for this round, "
            "planned over the next rounds within the budget and transfer limit. Slow "
            "(up to a minute). Optionally force players out, ignore the current squad, "
            "or apply logged news statuses to the projections (off by default: untested)."
        ),
        "input_schema": _schema(
            {
                "horizon": {"type": "integer", "minimum": 1, "maximum": 5},
                "sell": {"type": "array", "items": {"type": "string"}},
                "from_scratch": {"type": "boolean"},
                "apply_news": {"type": "boolean"},
            }
        ),
    },
    {
        "name": "get_turn_advice",
        "description": (
            "Lineup and captain advice for the user's live roster. Before the round: the "
            "starting lineup and captain to set, keeping later options open. Between "
            "turns: which swaps and captain move gain the most given scores already in."
        ),
        "input_schema": _schema({}),
    },
    {
        "name": "get_news",
        "description": (
            "Logged injury and availability statuses for this round (out, doubtful, "
            "questionable, limited), with reason and source. Optionally one team."
        ),
        "input_schema": _schema({"team": {"type": "string"}}),
    },
    {
        "name": "refresh_news",
        "description": (
            "Search the web for the latest injury and lineup news for this round and log "
            "it. Slow and costs a web search; use when the user asks for the latest news "
            "or the logged news is missing or old."
        ),
        "input_schema": _schema({}),
    },
]


class Tools:
    def __init__(self, ctx: FantasyContext, anthropic_client=None):
        self.ctx = ctx
        self.anthropic = anthropic_client
        self._prices = None
        self.handlers: dict[str, Callable[[dict], str]] = {
            "get_projections": self.get_projections,
            "compare_players": self.compare_players,
            "explain_projection": self.explain_projection,
            "get_schedule": self.get_schedule,
            "get_my_team": self.get_my_team,
            "suggest_transfers": self.suggest_transfers,
            "get_turn_advice": self.get_turn_advice,
            "get_news": self.get_news,
            "refresh_news": self.refresh_news,
        }

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """(result text, is_error)."""
        handler = self.handlers.get(name)
        if handler is None:
            return f"Unknown tool {name}.", True
        try:
            return handler(args or {}), False
        except Exception as e:  # report to Claude instead of crashing the chat
            return f"Error: {e}", True

    # -- helpers ------------------------------------------------------------------

    def _table(self, pred: pd.DataFrame, ids) -> pd.DataFrame:
        m, c = self.ctx.market, self.ctx
        rows = []
        for pid in ids:
            r, p = m.loc[pid], pred.loc[pid]
            rows.append(
                {
                    "player": c.name(pid),
                    "pos": r["position"],
                    "team": r["team_code"],
                    "opp": f"{r.get('opp_code') or ''}{' (h)' if r.get('home') else ' (a)'}",
                    "turn": r.get("turn"),
                    "price": r["price"],
                    "p_play": round(p["p_play"], 2),
                    "low": None if pd.isna(p["q10"]) else round(p["q10"], 1),
                    "median": round(p["q50"], 1),
                    "high": None if pd.isna(p["q90"]) else round(p["q90"], 1),
                    "expected": round(p["expected"], 1),
                    "per_credit": round(p["expected"] / r["price"], 2),
                }
            )
        return pd.DataFrame(rows)

    def _resolve(self, names: list[str]) -> tuple[list, list[str]]:
        found, missing = [], []
        for n in names:
            hit = self.ctx.find(n)
            (found.append(hit[0]) if hit else missing.append(n))
        return found, missing

    def _price_model(self):
        if self._prices is None:
            from elfantasy.model.prices import PriceModel, price_history

            h = price_history(self.ctx.con, self.ctx.season)
            self._prices = PriceModel.fit(h[h["round"] < self.ctx.round], self.ctx.rules) or False
        return self._prices or None

    def _news(self) -> pd.DataFrame:
        return news_mod.latest(self.ctx.con, self.ctx.season, self.ctx.round)

    # -- tools ----------------------------------------------------------------------

    def get_projections(self, args: dict) -> str:
        m, pred = self.ctx.market, self.ctx.predictions()
        ids = m.index
        if args.get("position"):
            ids = ids[m.loc[ids, "position"] == args["position"]]
        if args.get("team"):
            ids = ids[m.loc[ids, "team_code"].str.upper() == args["team"].upper()]
        if args.get("max_price"):
            ids = ids[m.loc[ids, "price"] <= args["max_price"]]
        t = self._table(pred, ids)
        key = {"expected": "expected", "value": "per_credit", "upside": "high"}[
            args.get("sort_by", "expected")
        ]
        t = t.sort_values(key, ascending=False).head(args.get("limit", 15))
        return f"Round {self.ctx.round} projections:\n" + t.to_string(index=False)

    def compare_players(self, args: dict) -> str:
        ids, missing = self._resolve(args["players"])
        if not ids:
            return f"No players matched {missing}."
        c, m = self.ctx, self.ctx.market
        out = self._table(c.predictions(), ids)
        later = [r for r in (c.round + 1, c.round + 2) if r <= c.last_round()]
        out["next_3_rounds"] = [
            round(sum(c.predictions(r).at[pid, "expected"] for r in [c.round, *later]), 1)
            for pid in ids
        ]
        prices = self._price_model()
        if prices is not None:
            pred = c.predictions().loc[ids]
            out["keep_price_at"] = prices.break_even(m.loc[ids, "price"]).round(1)
            out["p_price_rise"] = prices.rise_probability(m.loc[ids, "price"], pred).round(2)
        news = self._news().set_index("player_id")
        out["news"] = [news.at[pid, "status"] if pid in news.index else "" for pid in ids]
        text = out.to_string(index=False)
        if missing:
            text += f"\nNot found: {missing}"
        return text

    def explain_projection(self, args: dict) -> str:
        from elfantasy.model.features import build_features

        ids, _ = self._resolve([args["player"]])
        if not ids:
            return f"No player matched {args['player']!r}."
        pid, c = ids[0], self.ctx
        r = c.market.loc[pid]
        if c.is_coach(pid):
            p = c.predictions().loc[pid]
            return (f"{c.name(pid)} is a head coach: expected {p['expected']:.1f} points from "
                    f"the game model (win probability and margin), {r['team_code']} vs "
                    f"{r.get('opp_code')}.")  # fmt: skip
        if pd.isna(r["el_code"]):
            return f"{c.name(pid)} has no EuroLeague games on record, so he's projected 0."
        targets = pd.DataFrame(
            {"season": [c.season], "round": [c.round], "el_code": [r["el_code"]],
             "team_code": [r["el_team"]]}
        )  # fmt: skip
        f = build_features(c.model._load(), targets).iloc[0]
        p = c.predictions().loc[pid]

        def fmt(v, d=1):
            return "n/a" if pd.isna(v) else f"{v:.{d}f}"

        lines = [
            f"{c.name(pid)} ({r['team_code']}, {r['position']}, {r['price']} credits)",
            f"Projection: plays {p['p_play']:.0%}; if he plays {fmt(p['q10'])} / "
            f"{fmt(p['q50'])} / {fmt(p['q90'])} (10/50/90%); expected {p['expected']:.1f}",
            f"Form (weighted recent games): {fmt(f['ew_fp'])} pts in {fmt(f['ew_min'])} min",
            f"Season average: {fmt(f['season_mean'])} over {int(f['season_gp'])} games; "
            f"last season {fmt(f['prev_mean'])}; career {fmt(f['career_mean'])}",
            f"Availability: missed {fmt(f['games_missed'], 0)} of the team's games since "
            f"his last one; played {int(f['played_last5'])} of the team's last 5",
            f"Matchup: {'home' if f['home'] else 'away'} vs {f['opp_code']}, "
            f"{fmt(f['rest_days'])} days rest; team strength {fmt(f['team_str'])}, "
            f"opponent {fmt(f['opp_str'])} (weighted point margin); opponent allows "
            f"{fmt(f['opp_allowed'])} pts per game to his position",
        ]
        return "\n".join(lines)

    def get_schedule(self, args: dict) -> str:
        rnd = args.get("round", self.ctx.round)
        g = self.ctx.con.execute(
            """
            SELECT turn, started_at, home_code, away_code, status, home_score, away_score
            FROM fantasy_schedule WHERE season = ? AND round = ? ORDER BY started_at
            """,
            [self.ctx.season, rnd],
        ).df()
        if g.empty:
            return f"No games found for round {rnd}."
        return f"Round {rnd}:\n" + g.to_string(index=False)

    def get_my_team(self, args: dict) -> str:
        roster = self.ctx.roster()
        pred, m = self.ctx.predictions(), self.ctx.market
        slot = {**{i: "starter" for i in range(1, 6)}, 6: "sixth", 11: "coach"}
        rows = []
        for p in sorted(roster, key=lambda p: p["court_position"]):
            pid = p["id"]
            rows.append(
                {
                    "slot": (
                        "captain" if p["is_captain"] else slot.get(p["court_position"], "bench")
                    ),  # fmt: skip
                    "player": f"{p['first_name']} {p['last_name']}",
                    "pos": m.at[pid, "position"] if pid in m.index else "",
                    "team": m.at[pid, "team_code"] if pid in m.index else "",
                    "turn": m.at[pid, "turn"] if pid in m.index else "",
                    "price": p.get("quotation"),
                    "played": bool(p.get("match_played")),
                    "points": p.get("pts"),
                    "expected": round(pred.at[pid, "expected"], 1) if pid in pred.index else None,
                }
            )
        return pd.DataFrame(rows).to_string(index=False)

    def suggest_transfers(self, args: dict) -> str:
        from elfantasy.advise import recommend

        c = self.ctx
        sell, missing = self._resolve(args.get("sell", []))
        budget, baseline, live = 100.0, None, set()
        if c.fantasy_client is not None:
            config = c.fantasy_client.league_config()
            team = c.team_id or c.fantasy_client.my_teams()[0]["id"]
            md, prev = config["current_matchday"], config.get("previous_matchday")
            budget = float(c.fantasy_client.team_round(team, md["id"])["credits"])
            live = {p["id"] for p in c.fantasy_client.roster(team, md["id"])["players"]}
            if prev and not args.get("from_scratch"):
                baseline = {p["id"] for p in c.fantasy_client.roster(team, prev["id"])["players"]}
        news = self._news() if args.get("apply_news") else None
        rec = recommend(c.con, c.season, c.round, c.rules, budget, baseline,
                        horizon=args.get("horizon", 3), sell=set(sell), model=c.model,
                        news=news)  # fmt: skip
        t = rec.table
        view = pd.DataFrame(
            {
                "slot": t["slot"],
                "player": t["first_name"] + " " + t["last_name"],
                "pos": t["position"], "team": t["team_code"], "turn": t["turn"],
                "price": t["price"], "p_play": t["p_play"].round(2),
                "expected": t["expected"].round(1),
                "change": ["BUY" if live and i not in live else "" for i in t.index],
            }
        )  # fmt: skip
        sold = [c.name(i) for i in live - rec.plan.squads[0] if i in c.market.index]
        limit = "unlimited" if rec.transfer_limit is None else rec.transfer_limit
        mult = t["slot"].map({"captain": 2.0, "starter": 1.0, "sixth": 1.0, "bench": 0.5,
                              "coach": 1.0})  # fmt: skip
        lines = [
            f"Optimizer plan for round {c.round} (planned over rounds {rec.rounds}; "
            f"transfer limit {limit}; budget {budget:.1f}; squad cost {t['price'].sum():.1f}; "
            f"news applied: {bool(args.get('apply_news'))})",
            view.to_string(index=False),
            f"Sell: {sold or 'nobody'}",
            f"Expected points this round: {(t['expected'] * mult).sum():.1f}",
        ]
        if missing:
            lines.append(f"Could not find players to sell: {missing}")
        return "\n".join(lines)

    def get_turn_advice(self, args: dict) -> str:
        from elfantasy.advise import slot_name, turn_advice

        c = self.ctx
        roster = c.roster()
        advice = turn_advice(roster, c.market, c.predictions(), c.rules)
        names = {p["id"]: f"{p['first_name']} {p['last_name']}" for p in roster}
        rows = []
        for pid in advice.values.index:
            now = slot_name(pid, advice.current, c.market)
            best = slot_name(pid, advice.best, c.market)
            rows.append({"player": names[pid], "turn": c.market.at[pid, "turn"],
                         "status": "played" if pid in advice.played else "to play",
                         "points": round(advice.values[pid], 1), "now": now,
                         "recommended": best if best != now else ""})  # fmt: skip
        head = ("Before the round" if advice.before_round
                else f"{len(advice.played)} players have played")  # fmt: skip
        tail = ("No change: the current lineup is already the best option."
                if advice.best == advice.current else
                f"Recommended changes gain {advice.gain:+.1f} expected points"
                + (f"; captain {names[advice.current.captain]} -> "
                   f"{names[advice.best.captain]}"
                   if advice.best.captain != advice.current.captain else ""))  # fmt: skip
        return (f"{head} (points = actual if played, else expected):\n"
                f"{pd.DataFrame(rows).to_string(index=False)}\n{tail}")  # fmt: skip

    def get_news(self, args: dict) -> str:
        n = self._news()
        if n.empty:
            return "No news logged for this round yet (refresh_news fetches it)."
        m = self.ctx.market
        n = n.assign(player=[self.ctx.name(i) for i in n["player_id"]],
                     team=[m.at[i, "team_code"] for i in n["player_id"]])  # fmt: skip
        if args.get("team"):
            n = n[n["team"].str.upper() == args["team"].upper()]
        cols = ["player", "team", "status", "reason", "source", "fetched_at"]
        return n[cols].to_string(index=False)

    def refresh_news(self, args: dict) -> str:
        if self.anthropic is None:
            return "No Anthropic client available."
        c = self.ctx
        sched = c.con.execute(
            "SELECT min(started_at), max(started_at) FROM fantasy_schedule "
            "WHERE season = ? AND round = ?",
            [c.season, c.round],
        ).fetchone()
        teams = (
            c.con.execute(
                "SELECT DISTINCT home_name FROM games WHERE season = ? ORDER BY 1", [c.season]
            )
            .df()["home_name"]
            .tolist()
        )
        statuses, _notes = news_mod.fetch(self.anthropic, c.season, c.round, teams,
                                          str(sched[0]), str(sched[1]))  # fmt: skip
        matched = news_mod.match(statuses, c.market)
        news_mod.store(c.con, c.season, c.round, matched)
        unmatched = matched[matched["player_id"].isna()]["player_raw"].tolist()
        return (f"Logged {matched['player_id'].notna().sum()} statuses for round {c.round}."
                + (f" Could not match: {unmatched}" if unmatched else "")
                + "\n" + self.get_news({}))  # fmt: skip
