"""The live season loop (Phase 9): log what we predicted and recommended, then review
each round once it has been played.

Logs (DuckDB tables, appended, never rewritten):
- prediction_log: projections per player, as used before a round's deadline. Source is
  'live' (logged by `elf auto` before the round) or 'reconstructed' (recomputed later,
  leak-free, as of that round's deadline; used to backfill past rounds).
- recommendation_log: the optimizer's recommended lineup for a round.
- manager_rounds / manager_rosters: the user's actual score and final lineup per round.
- benchmark_rounds: the top managers' round scores (aggregates only, no names).

The review compares the predictions with what happened: error and bias, whether the
10-90% ranges hold, whether the chance-of-playing is calibrated, the user's score against
the projection and the benchmark, and what the recommended lineup would have scored.
"""

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from elfantasy.backtest.data import COACH

SLOT_MULT = {"captain": 2.0, "starter": 1.0, "sixth": 1.0, "bench": 0.5, "coach": 1.0}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _exists(con, table: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
    )


def _append(con, table: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    tmp = df  # noqa: F841 (read by DuckDB by name)
    if _exists(con, table):
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM tmp")
    else:
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM tmp")


def _replace_round(con, table: str, season: str, round_: int, df: pd.DataFrame, **match):
    """Replace one round's rows (used for facts that only get more complete, like the
    user's points, rather than for the prediction logs)."""
    if _exists(con, table):
        where = " AND ".join(["season = ?", "round = ?", *(f"{k} = ?" for k in match)])
        con.execute(f"DELETE FROM {table} WHERE {where}", [season, round_, *match.values()])
    _append(con, table, df)


# --- logging -----------------------------------------------------------------------


def log_predictions(
    con,
    season: str,
    round_: int,
    market: pd.DataFrame,
    pred: pd.DataFrame,
    source: str,
    model_version: str,
) -> int:
    cols = ["p_play", "mean", "q10", "q50", "q90", "expected"]
    df = market[["position", "team_code", "price", "turn", "el_code"]].join(pred[cols])
    df = df.reset_index().rename(columns={"index": "player_id"})
    df = df.assign(
        season=season, round=round_, logged_at=_now(), source=source, model_version=model_version
    )
    _append(con, "prediction_log", df)
    return len(df)


def log_recommendation(con, season: str, round_: int, table: pd.DataFrame, budget: float) -> None:
    df = pd.DataFrame(
        {
            "player_id": table.index,
            "slot": table["slot"].to_numpy(),
            "expected": table["expected"].to_numpy(),
            "price": table["price"].to_numpy(),
        }
    ).assign(season=season, round=round_, logged_at=_now(), budget=budget)
    _append(con, "recommendation_log", df)


def log_manager_round(
    con, season: str, round_: int, team_id: int, team_round: dict, roster: list[dict]
) -> None:
    head = pd.DataFrame(
        [
            {
                "season": season,
                "round": round_,
                "team_id": team_id,
                "points": team_round.get("pts"),
                "total_points": team_round.get("total_pts"),
                "credits": team_round.get("credits"),
                "transfers": team_round.get("trades"),
                "logged_at": _now(),
            }
        ]
    )
    _replace_round(con, "manager_rounds", season, round_, head, team_id=team_id)
    rows = pd.DataFrame(
        [
            {
                "season": season,
                "round": round_,
                "team_id": team_id,
                "player_id": p["id"],
                "court_position": p["court_position"],
                "is_captain": bool(p["is_captain"]),
                "points": p.get("pts"),
                "price": p.get("quotation"),
            }
            for p in roster
        ]
    )
    _replace_round(con, "manager_rosters", season, round_, rows, team_id=team_id)


def log_benchmark(con, season: str, round_: int, standings: list[dict]) -> None:
    """Aggregates of a round's top managers (from the league standings for that round)."""
    pts = sorted(
        (float(r["matchday_pts"]) for r in standings if r.get("matchday_pts") is not None),
        reverse=True,
    )
    if not pts:
        return
    df = pd.DataFrame(
        [
            {
                "season": season,
                "round": round_,
                "top1": pts[0],
                "top10_avg": float(np.mean(pts[:10])),
                "top25_avg": float(np.mean(pts[:25])),
                "logged_at": _now(),
            }
        ]
    )
    _replace_round(con, "benchmark_rounds", season, round_, df)


# --- the review ----------------------------------------------------------------------


def predictions_for(con, season: str, round_: int) -> tuple[pd.DataFrame, str] | None:
    """The logged predictions for a round: the latest live log before the deadline if
    there is one, else the latest reconstructed one."""
    if not _exists(con, "prediction_log"):
        return None
    for source in ("live", "reconstructed"):
        df = con.execute(
            """
            SELECT * FROM prediction_log
            WHERE season = ? AND round = ? AND source = ?
            QUALIFY logged_at = max(logged_at) OVER ()
            """,
            [season, round_, source],
        ).df()
        if len(df):
            return df.set_index("player_id"), source
    return None


def actual_points(con, season: str, round_: int) -> pd.DataFrame:
    """Fantasy points and minutes per fantasy player id for a played round."""
    return (
        con.execute(
            """
        SELECT m.fantasy_player_id AS player_id, sum(g.fantasy_points) AS points,
               sum(g.seconds) AS seconds
        FROM player_map m
        JOIN player_games g ON g.player_code = m.el_code AND g.season = m.season
        WHERE m.season = ? AND m.kind = 'player' AND g.round = ? AND g.phase = 'RS'
        GROUP BY 1
        """,
            [season, round_],
        )
        .df()
        .set_index("player_id")
    )


def round_played(con, season: str, round_: int) -> bool:
    """True once every regular-season game of the round has a box score."""
    games, played = con.execute(
        """
        SELECT count(*), count(*) FILTER (WHERE played) FROM games
        WHERE season = ? AND round = ? AND phase = 'RS'
        """,
        [season, round_],
    ).fetchone()
    with_stats = con.execute(
        "SELECT count(DISTINCT game_code) FROM player_games WHERE season = ? AND round = ?",
        [season, round_],
    ).fetchone()[0]
    return games > 0 and played == games and with_stats == games


def accuracy(pred: pd.DataFrame, actual: pd.DataFrame) -> dict:
    """Projection error and calibration for players with a box-score line."""
    players = pred[(pred["position"] != COACH) & pred["el_code"].notna()]
    j = players.join(actual, how="left").fillna({"points": 0.0, "seconds": 0.0})
    played = j[j["seconds"] > 0]
    if played.empty:
        return {"players": 0}
    err = played["q50"] - played["points"]
    has_range = played.dropna(subset=["q10", "q90"])
    inside = (has_range["points"] >= has_range["q10"]) & (has_range["points"] <= has_range["q90"])
    did_play = (j["seconds"] > 0).astype(float)
    return {
        "players": int(len(played)),
        "mae": float(err.abs().mean()),
        "bias": float(err.mean()),  # negative: projections too low
        "within_10_90": float(inside.mean()),  # target 0.80
        "below_10": float((has_range["points"] < has_range["q10"]).mean()),  # target 0.10
        "above_90": float((has_range["points"] > has_range["q90"]).mean()),  # target 0.10
        "p_play_predicted": float(j["p_play"].mean()),
        "p_play_actual": float(did_play.mean()),
        "p_play_brier": float(((j["p_play"] - did_play) ** 2).mean()),
    }


def _slot(court_position: int, is_captain: bool) -> str:
    if court_position == 11:
        return "coach"
    if is_captain:
        return "captain"
    return "starter" if court_position <= 5 else "sixth" if court_position == 6 else "bench"


def review_round(con, season: str, round_: int) -> dict:
    """Everything known about a played round, as one dict (also stored in round_reviews)."""
    out: dict = {"season": season, "round": round_}
    logged = predictions_for(con, season, round_)
    actual = actual_points(con, season, round_)
    if logged is not None:
        pred, source = logged
        out["prediction_source"] = source
        out["accuracy"] = accuracy(pred, actual)
    else:
        pred = None

    if _exists(con, "manager_rounds"):
        mine = con.execute(
            "SELECT * FROM manager_rounds WHERE season = ? AND round = ? "
            "ORDER BY logged_at DESC LIMIT 1",
            [season, round_],
        ).df()
        roster = con.execute(
            "SELECT * FROM manager_rosters WHERE season = ? AND round = ?", [season, round_]
        ).df()
        if len(mine):
            out["your_points"] = mine["points"].iloc[0]
            out["total_points"] = mine["total_points"].iloc[0]
            if len(roster) and pred is not None:
                roster["slot"] = [
                    _slot(c, k)
                    for c, k in zip(roster["court_position"], roster["is_captain"], strict=True)
                ]
                roster["mult"] = roster["slot"].map(SLOT_MULT)
                exp = pred["expected"].reindex(roster["player_id"]).fillna(0.0).to_numpy()
                roster["expected"] = exp
                out["your_projected"] = float((roster["expected"] * roster["mult"]).sum())
                roster["surprise"] = roster["points"].fillna(0) - roster["expected"]
                top = roster.reindex(roster["surprise"].abs().sort_values(ascending=False).index)
                out["surprises"] = [
                    {
                        "player_id": int(r.player_id),
                        "slot": r.slot,
                        "expected": round(r.expected, 1),
                        "points": r.points,
                    }
                    for r in top.head(4).itertuples()
                ]

    if _exists(con, "recommendation_log"):
        rec = con.execute(
            """
            SELECT * FROM recommendation_log WHERE season = ? AND round = ?
            QUALIFY logged_at = max(logged_at) OVER ()
            """,
            [season, round_],
        ).df()
        if len(rec):
            pts = actual["points"].reindex(rec["player_id"]).fillna(0.0).to_numpy()
            coach_pts = _coach_points(con, season, round_, rec)
            mult = rec["slot"].map(SLOT_MULT).to_numpy()
            pts = np.where(rec["slot"] == "coach", coach_pts, pts)
            out["recommended_points"] = float((pts * mult).sum())
            out["recommended_projected"] = float((rec["expected"] * mult).sum())

    if _exists(con, "benchmark_rounds"):
        b = con.execute(
            "SELECT top1, top10_avg, top25_avg FROM benchmark_rounds "
            "WHERE season = ? AND round = ?",
            [season, round_],
        ).df()
        if len(b):
            out["benchmark"] = b.iloc[0].to_dict()
    return out


def _coach_points(con, season, round_, rec: pd.DataFrame) -> np.ndarray:
    """Coach points for recommended coaches (by their club's game that round)."""
    coaches = rec[rec["slot"] == "coach"]["player_id"].tolist()
    if not coaches:
        return np.zeros(len(rec))
    pts = (
        con.execute(
            """
        SELECT DISTINCT m.fantasy_player_id AS player_id, c.fantasy_points AS points
        FROM player_map m JOIN coach_games c
          ON c.coach_code = m.el_code AND c.season = m.season AND c.phase = 'RS'
        WHERE m.season = ? AND m.kind = 'coach' AND c.round = ?
        """,
            [season, round_],
        )
        .df()
        .groupby("player_id")["points"]
        .max()
    )
    return rec["player_id"].map(pts).fillna(0.0).to_numpy()


def store_review(con, review: dict) -> None:
    import json

    df = pd.DataFrame(
        [
            {
                "season": review["season"],
                "round": review["round"],
                "reviewed_at": _now(),
                "review": json.dumps(review, default=float),
            }
        ]
    )
    _replace_round(con, "round_reviews", review["season"], review["round"], df)


def season_summary(con, season: str) -> pd.DataFrame:
    """One row per reviewed round: the headline numbers."""
    import json

    if not _exists(con, "round_reviews"):
        return pd.DataFrame()
    rows = []
    for r in con.execute(
        "SELECT review FROM round_reviews WHERE season = ? ORDER BY round", [season]
    ).fetchall():
        v = json.loads(r[0])
        a = v.get("accuracy", {})
        b = v.get("benchmark", {})
        rows.append(
            {
                "round": v["round"],
                "source": v.get("prediction_source"),
                "mae": a.get("mae"),
                "bias": a.get("bias"),
                "within_10_90": a.get("within_10_90"),
                "you": v.get("your_points"),
                "you_projected": v.get("your_projected"),
                "recommended": v.get("recommended_points"),
                "top10_avg": b.get("top10_avg"),
                "top1": b.get("top1"),
            }
        )
    return pd.DataFrame(rows)


# --- collecting from the game and the model --------------------------------------------


def global_tournament(fantasy, team_id: int) -> int | None:
    """The id of the public league every team is in (named 'Global')."""
    for t in fantasy.tournaments(team_id):
        if str(t.get("name", "")).lower() == "global":
            return int(t["id"])
    return None


def collect_round(
    con,
    fantasy,
    season: str,
    round_: int,
    matchday_id: int,
    team_id: int,
    tournament_id: int | None,
) -> None:
    """Store the user's score and lineup and the benchmark for a played round."""
    log_manager_round(
        con,
        season,
        round_,
        team_id,
        fantasy.team_round(team_id, matchday_id),
        fantasy.roster(team_id, matchday_id)["players"],
    )
    if tournament_id is not None:
        log_benchmark(con, season, round_, fantasy.standings(tournament_id, matchday_id))


def reconstruct_predictions(con, season: str, round_: int, rules, model=None) -> int:
    """Leak-free projections for a past round (as of its deadline), logged as
    'reconstructed'. Used to backfill rounds that were played before logging began."""
    from elfantasy.backtest.data import _fantasy_market
    from elfantasy.backtest.simulator import RoundContext
    from elfantasy.backtest.strategies import MODEL_VERSION, ModelV1

    model = model or ModelV1(con=con)
    market = _fantasy_market(con, season)
    market = market[market["round"] == round_].set_index("player_id")
    ctx = RoundContext(
        season, round_, market, pd.DataFrame(), pd.DataFrame(), rules, None, 0.0, None
    )
    pred = model.predict(ctx)
    return log_predictions(
        con, season, round_, market, pred, "reconstructed", f"model_v1:{MODEL_VERSION}"
    )


def run_review(con, fantasy, season: str, round_: int, rules, log=print, model=None) -> dict:
    """Collect, reconstruct predictions if none were logged, review and store a round."""
    config = fantasy.league_config()
    md = {m["number"]: m["id"] for m in config["matchdays"]}
    team_id = fantasy.my_teams()[0]["id"]
    collect_round(
        con, fantasy, season, round_, md[round_], team_id, global_tournament(fantasy, team_id)
    )
    if predictions_for(con, season, round_) is None:
        n = reconstruct_predictions(con, season, round_, rules, model)
        log(f"Round {round_}: no live predictions logged; reconstructed {n} (leak-free).")
    review = review_round(con, season, round_)
    store_review(con, review)
    return review


def headline(review: dict) -> str:
    """One line for a notification or log."""
    parts = [f"Round {review['round']}"]
    if review.get("your_points") is not None:
        parts.append(f"you {review['your_points']:.1f}")
    b = review.get("benchmark")
    if b:
        parts.append(f"top-10 avg {b['top10_avg']:.1f}")
    if review.get("recommended_points") is not None:
        parts.append(f"our plan {review['recommended_points']:.1f}")
    a = review.get("accuracy", {})
    if a.get("players"):
        parts.append(f"model error {a['mae']:.1f}, {a['within_10_90']:.0%} inside range")
    return "; ".join(parts)
