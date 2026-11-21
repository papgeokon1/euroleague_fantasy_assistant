import pandas as pd
import pytest

from elfantasy.backtest.data import SeasonData
from elfantasy.backtest.simulator import (
    Lineup,
    RoundContext,
    RuleViolation,
    run_season,
    score,
    validate,
    visible_history,
)
from elfantasy.backtest.strategies import LastFive, SeasonAverage
from elfantasy.rules import RULES_2026_27

S = "E2026"


def test_score_matches_official_round():
    """A real lineup from round 13 of 2026-27, which the game scored 180.2."""
    pts = {
        "Diarra": 15, "Kamenjas": 25, "Vezenkov": 16.5, "Maledon": 24, "Nunn": 26.4,
        "Malcolm": 13.2, "Marinkovic": 6.6, "Bryant": 13, "Davidovac": 17.6, "Procida": 0,
        "Laso": 25,
    }  # fmt: skip
    order = list(pts)[:10]
    lineup = Lineup(tuple(order), "Laso", tuple(order[:5]), order[5], "Vezenkov")
    assert score(lineup, pd.Series(pts), RULES_2026_27) == pytest.approx(180.2)


# --- a small synthetic league ------------------------------------------------
# 6 clubs, each with 2 guards, 2 forwards, 1 center and a coach (36 market entries).
# Player quality is fixed; actual points equal quality, so projections are easy.

CLUBS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def _league() -> pd.DataFrame:
    rows = []
    for i, club in enumerate(CLUBS):
        for pos, n in (("G", 2), ("F", 2), ("C", 1), ("HC", 1)):
            for k in range(n):
                quality = 5 + 3 * i + k
                rows.append(
                    {
                        "player_id": f"{club}-{pos}{k}",
                        "el_code": None if pos == "HC" else f"{club}-{pos}{k}",
                        "el_team": club,
                        "team_code": club,
                        "position": pos,
                        "quality": float(quality),
                        "price": 4.0 + quality / 3,
                    }
                )
    return pd.DataFrame(rows)


def _season(n_rounds: int = 4) -> SeasonData:
    league = _league()
    market = pd.concat([league.assign(round=r) for r in range(1, n_rounds + 1)])
    outcomes = market.assign(points=market["quality"], seconds=600.0)[
        ["round", "player_id", "points", "seconds"]
    ]
    players = league[league["position"] != "HC"]
    coaches = league[league["position"] == "HC"]
    history = pd.concat(
        [
            players.assign(
                season=S, round=r, phase="RS", seconds=600.0, utc_date=f"r{r:02d}"
            ).rename(columns={"quality": "fantasy_points"})
            for r in range(1, n_rounds + 1)
        ]
    )
    coach_history = pd.concat(
        [
            coaches.assign(season=S, round=r, phase="RS").rename(
                columns={"quality": "fantasy_points"}
            )
            for r in range(1, n_rounds + 1)
        ]
    )
    return SeasonData(
        S,
        list(range(1, n_rounds + 1)),
        market.drop(columns="quality").reset_index(drop=True),
        outcomes.reset_index(drop=True),
        history,
        coach_history,
    )


def _ctx(data: SeasonData, **kw) -> RoundContext:
    market = data.market[data.market["round"] == 1].set_index("player_id")
    defaults = dict(
        season=S, round=1, market=market, history=data.history.iloc[:0],
        coach_history=data.coach_history.iloc[:0], rules=RULES_2026_27, lineup=None,
        budget=1000.0, transfer_limit=None,
    )  # fmt: skip
    return RoundContext(**{**defaults, **kw})


def _legal_lineup() -> Lineup:
    g = ["AAA-G0", "AAA-G1", "BBB-G0", "BBB-G1"]
    f = ["CCC-F0", "CCC-F1", "DDD-F0", "DDD-F1"]
    c = ["EEE-C0", "FFF-C0"]
    return Lineup(tuple(g + f + c), "AAA-HC0", (g[0], g[1], f[0], f[1], c[0]), g[2], g[0])


def test_validate_accepts_legal_lineup():
    assert validate(_legal_lineup(), _ctx(_season())) > 0


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda lu: lu.__class__(lu.players, lu.coach, lu.starters, lu.sixth, lu.sixth),
         "captain"),
        (lambda lu: lu.__class__(lu.players, "BBB-G0", lu.starters, lu.sixth, lu.captain),
         "coach"),
        (lambda lu: lu.__class__(
            (*lu.players[:9], "AAA-F0"), lu.coach, lu.starters, lu.sixth, lu.captain),
         "positions"),
        (lambda lu: lu.__class__(
            lu.players, lu.coach, ("AAA-G0", "AAA-G1", "BBB-G1", "CCC-F0", "EEE-C0"),
            "CCC-F1", "AAA-G0"),
         None),  # 3-1-1 is a legal formation
        (lambda lu: lu.__class__(
            lu.players, lu.coach, ("AAA-G0", "AAA-G1", "CCC-F0", "CCC-F1", "DDD-F0"),
            "EEE-C0", "AAA-G0"),
         "formation"),  # 2-3-0 is not
    ],
)  # fmt: skip
def test_validate_rejects_illegal_lineups(change, message):
    ctx = _ctx(_season())
    lineup = change(_legal_lineup())
    if message is None:
        validate(lineup, ctx)
        return
    with pytest.raises(RuleViolation, match=message):
        validate(lineup, ctx)


def test_validate_budget_and_transfers():
    data = _season()
    with pytest.raises(RuleViolation, match="budget"):
        validate(_legal_lineup(), _ctx(data, budget=50.0))

    old = _legal_lineup()
    new_players = ("CCC-G0", "CCC-G1", "DDD-G0", "DDD-G1", "EEE-F0", *old.players[5:])
    new = Lineup(new_players, "BBB-HC0", ("CCC-G0", "CCC-G1", "EEE-F0", "CCC-F1", "EEE-C0"),
                 "DDD-G0", "CCC-G0")  # fmt: skip
    with pytest.raises(RuleViolation, match="transfers"):
        validate(new, _ctx(data, lineup=old, transfer_limit=4))
    validate(new, _ctx(data, lineup=old, transfer_limit=None))  # unlimited window


def test_visible_history_hides_current_and_future_rounds():
    h = pd.DataFrame(
        {"season": ["E2025", S, S, S], "phase": ["PO", "RS", "RS", "RS"], "round": [1, 1, 2, 3]}
    )
    seen = visible_history(h, S, 2)
    assert list(zip(seen["season"], seen["round"], strict=True)) == [("E2025", 1), (S, 1)]


class Spy(SeasonAverage):
    """Fails the test if a strategy is ever shown a game from its own round or later."""

    name = "spy"

    def project(self, ctx):
        this = ctx.history[ctx.history["season"] == ctx.season]
        assert this.empty or this["round"].max() < ctx.round
        return super().project(ctx)


@pytest.mark.parametrize("strategy", [SeasonAverage, LastFive, Spy])
def test_run_season(strategy):
    result = run_season(_season(), strategy())
    rounds = result.rounds
    assert list(rounds["round"]) == [1, 2, 3, 4]
    assert (rounds["transfers"].iloc[1:] <= 4).all()
    assert (rounds["bank"] >= -1e-6).all()
    # Round 1 has no history, so it scores whatever the fallback picks; later rounds
    # know every player's quality exactly, so projections match actual points.
    assert rounds["points"].iloc[-1] == pytest.approx(rounds["projected"].iloc[-1])
    assert rounds["points"].iloc[-1] > rounds["points"].iloc[0]
