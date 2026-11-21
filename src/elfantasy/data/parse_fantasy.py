"""Turn raw fantasy API responses into flat table rows."""


def parse_players(season: str, snapshot: dict) -> list[dict]:
    """Rows of the `fantasy_prices` table: one per player per matchday.

    `avg_pts` is left out on purpose: the API returns today's season average even for
    past matchdays, so it would leak future results. `popularity`, `is_injured`,
    `probability_of_playing` and `started_from_bench` have the same problem, so they are
    kept only for live snapshots (taken while that matchday was the current one) and
    are null otherwise.
    """
    matchday = snapshot["matchday"]
    live = bool(snapshot["live"])
    rows = []
    for p in snapshot["players"]:
        team, opponent, turn = p["team"], p.get("opponent") or {}, p.get("round") or {}
        rows.append(
            {
                "season": season,
                "round": matchday["number"],
                "matchday_id": matchday["id"],
                "player_id": p["id"],
                "first_name": p["first_name"],
                "last_name": p["last_name"],
                "position": p["position"]["name"],
                "team_id": team["id"],
                "team_code": team["abbreviation"],
                "home": team.get("position") == "home" if team.get("position") else None,
                "opp_code": opponent.get("abbreviation"),
                "turn": turn.get("number"),
                "price": float(p["quotation"]),
                "is_on_fire": bool(p.get("is_on_fire")),
                "live": live,
                "fetched_at": snapshot["fetched_at"],
                "league_status_id": snapshot.get("league_status_id"),
                "popularity": p.get("popularity") if live else None,
                "is_injured": p.get("is_injured") if live else None,
                "probability_of_playing": p.get("probability_of_playing") if live else None,
                "started_from_bench": p.get("started_from_bench") if live else None,
            }
        )
    return rows


def parse_schedule(season: str, data: dict) -> list[dict]:
    """Rows of the `fantasy_schedule` table: one per game, with the turn it belongs to."""
    rows = []
    for turn in data["rounds"]:
        for m in turn["matches"]:
            home, away = m["home_team"], m["away_team"]
            rows.append(
                {
                    "season": season,
                    "round": data["number"],
                    "matchday_id": data["id"],
                    "turn": turn["number"],
                    "turn_id": turn["id"],
                    "match_id": m["id"],
                    "status": m["status"],
                    "started_at": m["started_at"],
                    "home_team_id": home["id"],
                    "home_code": home["abbreviation"],
                    "away_team_id": away["id"],
                    "away_code": away["abbreviation"],
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "overtime": bool(m.get("ot")),
                }
            )
    return rows
