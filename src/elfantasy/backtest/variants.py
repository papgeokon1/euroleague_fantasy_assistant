"""Compare optimizer settings over several seasons (Phase 10).

Each variant is model_turns with different optimizer settings. Seasons with real
fantasy prices (2026-27) use them; earlier seasons use estimated prices
(backtest/synthetic.py), so they're for comparing variants with each other only.

Projections don't depend on the optimizer settings, so they're computed once per
season (in parallel), cached on disk, and shared by every variant. Then the variants
run in parallel processes, one (variant, season) per process.
"""

import pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

VARIANTS: dict[str, dict] = {
    "base": {},  # the shipped defaults (captain upside 1.0 since Phase 10)
    "no_captain_upside": {"captain_upside": 0.0},
    "min_stars_2": {"min_stars": 2},
    "min_stars_3": {"min_stars": 3},
    "min_stars_4": {"min_stars": 4},
}
HORIZON = 3  # rounds the strategy plans ahead (and so the projections cached)


def _season_data(con, season: str):
    from elfantasy.backtest.data import load_season
    from elfantasy.backtest.synthetic import build_season

    real = con.execute("SELECT count(*) FROM fantasy_prices WHERE season = ?", [season]).fetchone()[
        0
    ]
    return (load_season(con, season) if real else build_season(con, season)), bool(real)


def _cache_path(season: str) -> Path:
    from elfantasy.backtest.strategies import MODEL_VERSION
    from elfantasy.config import settings

    return settings.models_dir / "experiments" / f"predictions_{season}_v{MODEL_VERSION}.pkl"


def _precompute(args: tuple[str, str]) -> str:
    """Every projection a strategy can ask for in a season: each round, as of its own
    deadline, for it and the next HORIZON - 1 rounds."""
    season, db_path = args
    path = _cache_path(season)
    if path.exists():
        return str(path)
    import duckdb

    from elfantasy.backtest.simulator import RoundContext
    from elfantasy.backtest.strategies import ModelV1
    from elfantasy.rules import RULES_2026_27

    con = duckdb.connect(db_path, read_only=True)
    data, _ = _season_data(con, season)
    model = ModelV1(con=con)
    schedule = model._load().schedule
    last = int(schedule.loc[schedule["season"] == season, "round"].max())
    cache = {}
    for rnd in data.rounds:
        market = data.market[data.market["round"] == rnd].set_index("player_id")
        ctx = RoundContext(season, rnd, market, pd.DataFrame(), pd.DataFrame(), RULES_2026_27,
                           None, 0.0, None)  # fmt: skip
        for target in range(rnd, min(rnd + HORIZON, last + 1)):
            cache[(season, rnd, target)] = model.predict(ctx, target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pickle.dumps(cache))
    return str(path)


def _run(args: tuple[str, dict, str, str]) -> dict:
    name, settings, season, db_path = args
    import duckdb

    from elfantasy.backtest import simulator as sim
    from elfantasy.backtest.strategies import ModelTurns

    con = duckdb.connect(db_path, read_only=True)
    data, real = _season_data(con, season)
    strategy = ModelTurns(con=con)
    strategy.prediction_cache = pickle.loads(_cache_path(season).read_bytes())
    for key, value in settings.items():
        setattr(strategy, key, value)
    squads = {}
    validate = sim.validate

    def spy(lineup, ctx):
        squads[ctx.round] = ctx.market.loc[list(lineup.players), "price"]
        return validate(lineup, ctx)

    sim.validate = spy
    try:
        result = sim.run_season(data, strategy)
    finally:
        sim.validate = validate
    rounds = result.rounds
    return {
        "variant": name,
        "season": season,
        "prices": "real" if real else "estimated",
        "rounds": len(rounds),
        "points": float(rounds["points"].sum()),
        "per_round": float(rounds["points"].mean()),
        "transfers": float(rounds["transfers"].iloc[1:].mean()),
        "stars_14plus": float(pd.Series({r: (p >= 14).sum() for r, p in squads.items()}).mean()),
        "top3_price": float(pd.Series({r: p.nlargest(3).sum() for r, p in squads.items()}).mean()),
    }


def compare(
    db_path: str, seasons: list[str], variants: dict[str, dict] | None = None, workers: int = 6
) -> pd.DataFrame:
    variants = variants or VARIANTS
    with ProcessPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_precompute, [(s, db_path) for s in seasons]))
        jobs = [(name, v, s, db_path) for name, v in variants.items() for s in seasons]
        rows = list(pool.map(_run, jobs))
    return pd.DataFrame(rows)
