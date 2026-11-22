"""Everything the assistant's tools read: the current round's market, projections,
schedule, news and (when a token is set) the user's live roster.

Loaded lazily and cached for the session, so the first tool call pays for training or
loading the model and later calls are quick.
"""

from dataclasses import dataclass, field

import duckdb
import pandas as pd

from elfantasy.advise import apply_injuries, current_market
from elfantasy.backtest.data import COACH
from elfantasy.backtest.simulator import RoundContext
from elfantasy.backtest.strategies import ModelV1
from elfantasy.data.player_map import name_score, normalize
from elfantasy.rules import RULES_2026_27, GameRules


@dataclass
class FantasyContext:
    con: duckdb.DuckDBPyConnection
    season: str
    round: int
    rules: GameRules = field(default_factory=lambda: RULES_2026_27)
    fantasy_client: object | None = None  # FantasyClient, for the live roster
    team_id: int | None = None
    _model: ModelV1 | None = field(default=None, repr=False)
    _market: pd.DataFrame | None = field(default=None, repr=False)
    _preds: dict = field(default_factory=dict, repr=False)

    @classmethod
    def current(cls, con, season: str, **kw) -> "FantasyContext":
        """Context for the latest round with live fantasy prices."""
        rnd = con.execute(
            "SELECT max(round) FROM fantasy_prices WHERE season = ? AND live", [season]
        ).fetchone()[0]
        if rnd is None:
            raise RuntimeError("no live fantasy prices yet: run `elf data fantasy` first")
        return cls(con=con, season=season, round=int(rnd), **kw)

    @property
    def model(self) -> ModelV1:
        if self._model is None:
            self._model = ModelV1(con=self.con)
        return self._model

    @property
    def market(self) -> pd.DataFrame:
        if self._market is None:
            self._market = current_market(self.con, self.season, self.round)
        return self._market

    def predictions(self, target_round: int | None = None) -> pd.DataFrame:
        """Projections for `target_round` (default: this round), as of this round's
        deadline. The game's injury flag is applied to this round only."""
        target = self.round if target_round is None else target_round
        if target not in self._preds:
            ctx = RoundContext(
                season=self.season, round=self.round, market=self.market,
                history=pd.DataFrame(), coach_history=pd.DataFrame(), rules=self.rules,
                lineup=None, budget=0.0, transfer_limit=None,
            )  # fmt: skip
            pred = self.model.predict(ctx, target)
            if target == self.round:
                pred = apply_injuries(pred, self.market)
            self._preds[target] = pred
        return self._preds[target]

    def last_round(self) -> int:
        return int(
            self.con.execute(
                "SELECT max(round) FROM fantasy_schedule WHERE season = ?", [self.season]
            ).fetchone()[0]
        )

    def name(self, pid) -> str:
        r = self.market.loc[pid]
        return f"{r['first_name']} {r['last_name']}"

    def find(self, query: str, limit: int = 1) -> list:
        """Player ids whose name best matches `query` (full name, last name, or a
        part of either)."""
        q = normalize(query)
        scored = []
        for pid, r in self.market.iterrows():
            full = normalize(f"{r['first_name']} {r['last_name']}")
            last = normalize(str(r["last_name"]))
            score = max(name_score(q, full), 1.0 if q == last else 0.0,
                        0.9 if q and q in full else 0.0)  # fmt: skip
            scored.append((score, pid))
        scored.sort(key=lambda s: -s[0])
        return [pid for score, pid in scored[:limit] if score >= 0.75]

    def roster(self) -> list[dict]:
        """The live roster from the game (court_position, is_captain, pts, ...)."""
        if self.fantasy_client is None:
            raise RuntimeError("no fantasy token: set ELF_FANTASY_TOKEN in .env")
        config = self.fantasy_client.league_config()
        team = self.team_id or self.fantasy_client.my_teams()[0]["id"]
        return self.fantasy_client.roster(team, config["current_matchday"]["id"])["players"]

    def is_coach(self, pid) -> bool:
        return self.market.at[pid, "position"] == COACH
