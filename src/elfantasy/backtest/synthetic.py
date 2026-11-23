"""Seasons with estimated prices, for testing optimizer changes on more rounds.

Real fantasy prices only exist for the current season. To test squad-selection
changes (which need a budget) over more than a handful of rounds, this builds past
seasons with estimated prices:

- Starting price: fitted on the current season's round-1 prices against each player's
  previous-season average (about 0.58 x average + 3.5, correlation 0.92). Players
  without one get the median price of such players; coaches are fitted on their club's
  previous-season coach points.
- Every round after: the price model fitted on the current season's real price history
  (model/prices.py), applied to what each player actually scored.
- The market is every player who appeared that season, so injured players still cost
  money and score 0. Turns come from game dates.

These seasons are for comparing strategies with each other, never for absolute claims
about scores with real prices. Results on real 2026-27 prices are always reported
separately.
"""

import duckdb
import numpy as np
import pandas as pd

from elfantasy.backtest.data import COACH, SeasonData, _outcomes, load_season
from elfantasy.backtest.turn_eval import turns_by_team
from elfantasy.model.features import prev_season
from elfantasy.model.prices import PriceModel, price_history
from elfantasy.rules import RULES_2026_27, GameRules

POS = {"Guard": "G", "Forward": "F", "Center": "C"}
MIN_PREV_GAMES = 5


def _prev_means(con, season: str) -> tuple[pd.Series, pd.Series]:
    """Previous-season fantasy average per player (if 5+ games) and per club's coach."""
    prev = prev_season(season)
    players = (
        con.execute(
            """
        SELECT player_code, avg(fantasy_points) AS m FROM player_games
        WHERE season = ? AND seconds > 0 GROUP BY 1 HAVING count(*) >= ?
        """,
            [prev, MIN_PREV_GAMES],
        )
        .df()
        .set_index("player_code")["m"]
    )
    coaches = (
        con.execute(
            "SELECT team_code, avg(fantasy_points) AS m FROM coach_games "
            "WHERE season = ? GROUP BY 1",
            [prev],
        )
        .df()
        .set_index("team_code")["m"]
    )
    return players, coaches


def fit_starting_prices(con, reference: str = "E2026") -> dict:
    """Starting-price rules fitted on the reference season's round-1 prices."""
    r1 = con.execute(
        """
        SELECT f.player_id, f.position, f.team_code, f.price, m.el_code
        FROM fantasy_prices f
        LEFT JOIN player_map m ON m.season = f.season AND m.fantasy_player_id = f.player_id
             AND m.kind = 'player'
        WHERE f.season = ? AND f.round = 1
        """,
        [reference],
    ).df()
    prev_players, prev_coaches = _prev_means(con, reference)
    players = r1[r1["position"] != "Head Coach"].assign(
        prev=lambda d: d["el_code"].map(prev_players)
    )
    has = players.dropna(subset=["prev"])
    a = np.column_stack([has["prev"], np.ones(len(has))])
    player_coef, *_ = np.linalg.lstsq(a, has["price"], rcond=None)

    from elfantasy.data.player_map import team_map

    teams = team_map(con, reference)
    coaches = r1[r1["position"] == "Head Coach"].assign(
        prev=lambda d: d["team_code"].map(teams).map(prev_coaches)
    )
    c_has = coaches.dropna(subset=["prev"])
    c = np.column_stack([c_has["prev"], np.ones(len(c_has))])
    coach_coef, *_ = np.linalg.lstsq(c, c_has["price"], rcond=None)
    return {
        "player_coef": player_coef,
        "player_default": float(players.loc[players["prev"].isna(), "price"].median()),
        "coach_coef": coach_coef,
        "coach_default": float(coaches["price"].median()),
        "max_price": float(r1["price"].max()),
    }


def build_season(
    con: duckdb.DuckDBPyConnection,
    season: str,
    rules: GameRules = RULES_2026_27,
    reference: str = "E2026",
    start: dict | None = None,
    prices: PriceModel | None = None,
) -> SeasonData:
    """A past season with estimated prices, in the shape the simulator expects."""
    start = start or fit_starting_prices(con, reference)
    prices = prices or PriceModel.fit(price_history(con, reference), rules)
    base = load_season(con, season)  # history, coach history and played rounds

    games = con.execute(
        """
        SELECT round, player_code AS el_code, team_code, position, seconds, fantasy_points
        FROM player_games WHERE season = ? AND phase = 'RS' ORDER BY round
        """,
        [season],
    ).df()
    positions = games.groupby("el_code")["position"].agg(lambda s: s.mode().iloc[0]).map(POS)
    prev_players, prev_coaches = _prev_means(con, season)
    max_price = start["max_price"]

    def start_price(prev):
        if pd.isna(prev):
            return start["player_default"]
        return float(
            np.clip(
                start["player_coef"][0] * prev + start["player_coef"][1], rules.min_price, max_price
            )
        )

    players = positions.index
    price = pd.Series({p: round(start_price(prev_players.get(p)), 1) for p in players})
    teams_by_round = games.pivot_table(
        index="round", columns="el_code", values="team_code", aggfunc="first"
    )
    clubs = sorted(set(games["team_code"]))
    coach_ids = [f"HC_{t}" for t in clubs]
    coach_price = pd.Series(
        {
            f"HC_{t}": round(
                float(
                    np.clip(
                        start["coach_coef"][0] * prev_coaches[t] + start["coach_coef"][1]
                        if t in prev_coaches.index
                        else start["coach_default"],
                        rules.min_price,
                        max_price,
                    )
                ),
                1,
            )
            for t in clubs
        }
    )
    turns = turns_by_team(con, season).set_index(["round", "team_code"])["turn"]
    coach_pts = base.coach_history[
        (base.coach_history["season"] == season) & (base.coach_history["phase"] == "RS")
    ]

    rows = []
    for rnd in base.rounds:
        # Each player's club: his latest club before this round (or his first club).
        known = teams_by_round.loc[teams_by_round.index < rnd].ffill()
        last = known.iloc[-1] if len(known) else pd.Series(dtype=object)
        first = teams_by_round.bfill().iloc[0]
        club = last.reindex(players).fillna(first.reindex(players))
        rows.append(
            pd.DataFrame(
                {
                    "round": rnd,
                    "player_id": players,
                    "el_code": players,
                    "position": positions.values,
                    "team_code": club.values,
                    "el_team": club.values,
                    "price": price.reindex(players).values,
                }
            )
        )
        rows.append(
            pd.DataFrame(
                {
                    "round": rnd,
                    "player_id": coach_ids,
                    "el_code": None,
                    "position": COACH,
                    "team_code": clubs,
                    "el_team": clubs,
                    "price": coach_price.reindex(coach_ids).values,
                }
            )
        )

        # Prices move after the round, from what was scored.
        g = (
            games[games["round"] == rnd]
            .groupby("el_code")
            .agg(fp=("fantasy_points", "sum"), seconds=("seconds", "sum"))
        )
        fp = g["fp"].reindex(players).fillna(0.0)
        played = g["seconds"].reindex(players).fillna(0.0) > 0
        price = price.reindex(players) + prices.change(
            price.reindex(players), fp, played, np.zeros(len(players), bool)
        )
        cp = coach_pts[coach_pts["round"] == rnd].groupby("team_code")["fantasy_points"].sum()
        c_fp = pd.Series([cp.get(t, 0.0) for t in clubs], index=coach_ids)
        coach_price = coach_price + prices.change(
            coach_price, c_fp, np.ones(len(clubs), bool), np.ones(len(clubs), bool)
        )

    market = pd.concat(rows, ignore_index=True)
    market["turn"] = [
        turns.get((r, t), 1) for r, t in zip(market["round"], market["el_team"], strict=True)
    ]
    market["price"] = market["price"].round(1)
    return SeasonData(
        season=season,
        rounds=base.rounds,
        market=market,
        outcomes=_outcomes(market, base.history, base.coach_history, season),
        history=base.history,
        coach_history=base.coach_history,
    )
