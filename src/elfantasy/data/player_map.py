"""Link fantasy players to EuroLeague players.

The two sources use different ids and partly different team codes (fantasy "EFS" is
EuroLeague "IST"). Matching happens in two steps:

1. Teams: a fantasy game and a EuroLeague game in the same round that start at the
   same time are the same game, which pairs up the home and away team codes.
2. People: within the same team, a fantasy player is matched to the EuroLeague player
   with the most similar name, or failing that, the only teammate with the same last
   name. Head coaches are matched by team, since the game keeps one coach entry per club.

The result is the `player_map` table: one row per (fantasy player, EuroLeague person).
Players are one-to-one; a club's coach entry can map to several real coaches.
"""

import re
import unicodedata
from difflib import SequenceMatcher

import duckdb
import pandas as pd

from elfantasy.data.store import replace_season

MIN_SCORE = 0.8  # below this a name match is rejected
MAX_KICKOFF_GAP_HOURS = 3


def normalize(name: str) -> str:
    """'Šarūnas Jasikevičius' -> 'SARUNAS JASIKEVICIUS'; 'JAMES, MIKE' -> 'MIKE JAMES'."""
    if "," in name:
        last, first = name.split(",", 1)
        name = f"{first} {last}"
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z ]+", " ", name.replace("'", ""))
    return " ".join(name.upper().split())


def name_score(a: str, b: str) -> float:
    """Similarity of two normalized names, from 0 to 1. Word order doesn't matter, and a
    name contained in the other (a dropped middle name) still scores high."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    full = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    subset = 0.95 if set(shorter) <= set(longer) else 0.0
    # Same last name plus a matching first initial, e.g. "Nick" vs "Nicholas".
    initial = 0.85 if ta[-1] == tb[-1] and ta[0][0] == tb[0][0] else 0.0
    return max(full, subset, initial)


def team_map(con: duckdb.DuckDBPyConnection, season: str) -> dict[str, str]:
    """Fantasy team code -> EuroLeague team code, from games that start together."""
    pairs = con.execute(
        f"""
        WITH f AS (
            SELECT round, home_code, away_code, started_at::TIMESTAMPTZ AS t
            FROM fantasy_schedule WHERE season = $season
        ), e AS (
            SELECT round, home_code, away_code, utc_date::TIMESTAMPTZ AS t
            FROM games WHERE season = $season AND phase = 'RS'
        ), j AS (
            SELECT f.home_code AS fh, f.away_code AS fa, e.home_code AS eh, e.away_code AS ea,
                   row_number() OVER (
                       PARTITION BY f.round, f.home_code
                       ORDER BY abs(epoch(f.t) - epoch(e.t))) AS rn
            FROM f JOIN e ON f.round = e.round
             AND abs(epoch(f.t) - epoch(e.t)) <= {MAX_KICKOFF_GAP_HOURS} * 3600
        )
        SELECT fh AS f, eh AS e FROM j WHERE rn = 1
        UNION ALL
        SELECT fa, ea FROM j WHERE rn = 1
        """,
        {"season": season},
    ).fetchall()
    votes = pd.DataFrame(pairs, columns=["f", "e"]).value_counts().reset_index(name="n")
    # Keep the most frequent pairing for each fantasy code.
    best = votes.sort_values("n", ascending=False).drop_duplicates("f")
    return dict(zip(best["f"], best["e"], strict=True))


def build_player_map(con: duckdb.DuckDBPyConnection, season: str) -> list[dict]:
    teams = team_map(con, season)
    fantasy = con.execute(
        """
        SELECT player_id, any_value(first_name) AS first_name, any_value(last_name) AS last_name,
               any_value(position) AS position, list(DISTINCT team_code) AS teams
        FROM fantasy_prices WHERE season = ? GROUP BY player_id
        """,
        [season],
    ).df()
    people = con.execute(
        """
        SELECT DISTINCT player_code AS code, player_name AS name, team_code, 'player' AS kind
        FROM player_games WHERE season = $season
        UNION
        SELECT DISTINCT coach_code, coach_name, team_code, 'coach'
        FROM coach_games WHERE season = $season
        """,
        {"season": season},
    ).df()
    people["norm"] = people["name"].map(normalize)
    people["last"] = people["norm"].str.split().str[-1]

    def row(f, p, kind: str, score: float, method: str) -> dict:
        return {
            "season": season,
            "fantasy_player_id": int(f.player_id),
            "fantasy_name": f"{f.first_name} {f.last_name}",
            "kind": kind,
            "el_code": p.code,
            "el_name": p.name,
            "score": round(score, 3),
            "method": method,
        }

    rows, candidates = [], []
    for f in fantasy.itertuples():
        el_teams = {teams[t] for t in f.teams if t in teams}
        if f.position == "Head Coach":
            # The game has one coach entry per club and renames it when the coach
            # changes, so every coach of that club maps to it.
            pool = people[(people["kind"] == "coach") & people["team_code"].isin(el_teams)]
            rows += [row(f, p, "coach", 1.0, "team") for p in pool.itertuples()]
            continue
        pool = people[(people["kind"] == "player") & people["team_code"].isin(el_teams)]
        pool = pool.drop_duplicates("code")
        fname = normalize(f"{f.first_name} {f.last_name}")
        for p in pool.itertuples():
            score = name_score(fname, p.norm)
            if score >= MIN_SCORE:
                candidates.append((score, f, p, "name"))
        # Nicknames ("Olek" for "Aleksander"): accept a last name that is unique on the team.
        same_last = pool[pool["last"] == fname.split()[-1]] if fname else pool.iloc[:0]
        if len(same_last) == 1:
            candidates.append((MIN_SCORE - 0.05, f, next(same_last.itertuples()), "last_name"))

    # Greedy one-to-one: best scores first, each id used once on each side.
    used_f, used_e = set(), set()
    for score, f, p, method in sorted(candidates, key=lambda c: -c[0]):
        if f.player_id in used_f or p.code in used_e:
            continue
        used_f.add(f.player_id)
        used_e.add(p.code)
        rows.append(row(f, p, "player", score, method))
    return rows


def refresh_player_map(con: duckdb.DuckDBPyConnection, season: str, log=print) -> None:
    """Rebuild `player_map` for a season, if both sources are in the database."""
    needed = {"fantasy_prices", "fantasy_schedule", "games", "player_games", "coach_games"}
    have = {
        r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    if not needed <= have:
        log(f"player_map: skipped, missing tables {sorted(needed - have)}")
        return
    rows = build_player_map(con, season)
    replace_season(con, "player_map", season, rows)
    players = sum(r["kind"] == "player" for r in rows)
    log(f"player_map: {players} players and {len(rows) - players} coaches linked")
