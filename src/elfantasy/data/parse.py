"""Turn raw EuroLeague API responses into flat table rows."""

from elfantasy.rules import RULES_2026_27, BoxScore, pir

# API stat name -> our column name
STAT_COLUMNS = {
    "points": "points",
    "fieldGoalsMade2": "fg2_made",
    "fieldGoalsAttempted2": "fg2_attempted",
    "fieldGoalsMade3": "fg3_made",
    "fieldGoalsAttempted3": "fg3_attempted",
    "freeThrowsMade": "ft_made",
    "freeThrowsAttempted": "ft_attempted",
    "offensiveRebounds": "off_rebounds",
    "defensiveRebounds": "def_rebounds",
    "totalRebounds": "rebounds",
    "assistances": "assists",
    "steals": "steals",
    "turnovers": "turnovers",
    "blocksFavour": "blocks",
    "blocksAgainst": "blocks_against",
    "foulsCommited": "fouls_committed",
    "foulsReceived": "fouls_drawn",
    "plusMinus": "plus_minus",
}


def parse_game(season: str, game: dict) -> dict:
    """One row of the `games` table. The API's `winner` field is unreliable, so the
    result is derived from the scores."""
    home, away = game["local"], game["road"]
    played = bool(game["played"])
    return {
        "season": season,
        "game_code": game["gameCode"],
        "round": game["round"],
        "phase": game["phaseType"]["code"],
        "utc_date": game["utcDate"],
        "home_code": home["club"]["code"],
        "away_code": away["club"]["code"],
        "home_name": home["club"]["name"],
        "away_name": away["club"]["name"],
        "played": played,
        "home_score": home["score"] if played else None,
        "away_score": away["score"] if played else None,
        "overtime": bool(home["partials"].get("extraPeriods")) if played else None,
    }


def _box_score(stats: dict) -> BoxScore:
    return BoxScore(
        points=int(stats["points"]),
        rebounds=int(stats["totalRebounds"]),
        assists=int(stats["assistances"]),
        steals=int(stats["steals"]),
        blocks=int(stats["blocksFavour"]),
        fouls_drawn=int(stats["foulsReceived"]),
        turnovers=int(stats["turnovers"]),
        blocks_against=int(stats["blocksAgainst"]),
        fouls_committed=int(stats["foulsCommited"]),
        missed_field_goals=int(stats["fieldGoalsAttemptedTotal"] - stats["fieldGoalsMadeTotal"]),
        missed_free_throws=int(stats["freeThrowsAttempted"] - stats["freeThrowsMade"]),
    )


def parse_game_stats(game_row: dict, stats: dict) -> tuple[list[dict], list[dict]]:
    """Rows of the `player_games` and `coach_games` tables for one played game."""
    players: list[dict] = []
    coaches: list[dict] = []
    sides = (
        ("local", game_row["home_code"], game_row["home_score"], game_row["away_score"]),
        ("road", game_row["away_code"], game_row["away_score"], game_row["home_score"]),
    )
    for side, team_code, team_score, opp_score in sides:
        is_home = side == "local"
        opp_code = game_row["away_code"] if is_home else game_row["home_code"]
        won = team_score > opp_score
        base = {
            "season": game_row["season"],
            "game_code": game_row["game_code"],
            "round": game_row["round"],
            "phase": game_row["phase"],
            "utc_date": game_row["utc_date"],
            "team_code": team_code,
            "opp_code": opp_code,
            "is_home": is_home,
            "won": won,
        }

        coach = stats[side].get("coach") or {}
        margin = team_score - opp_score
        coaches.append(
            base
            | {
                "coach_code": coach.get("code"),
                "coach_name": coach.get("name"),
                "margin": margin,
                "fantasy_points": RULES_2026_27.coach_points(
                    margin, overtime=bool(game_row["overtime"])
                ),
            }
        )

        for entry in stats[side]["players"]:
            person, s = entry["player"]["person"], entry["stats"]
            our_pir = pir(_box_score(s))
            players.append(
                base
                | {
                    "player_code": person["code"],
                    "player_name": person["name"],
                    "position": entry["player"].get("positionName"),
                    "is_starter": bool(s.get("startFive")),
                    "seconds": float(s["timePlayed"]),
                    "pir": int(s["valuation"]),
                    "pir_computed": our_pir,
                    "fantasy_points": RULES_2026_27.player_points(s["valuation"], team_won=won),
                }
                | {col: s[api] for api, col in STAT_COLUMNS.items()}
            )
    return players, coaches
