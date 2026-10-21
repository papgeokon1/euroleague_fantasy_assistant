"""The game's rules, as data.

Everything the optimizer and the simulator need to know about the game lives here,
so a rule change is a one-line edit. Values marked UNVERIFIED come from third-party
guides and must be confirmed in the in-game rules before we rely on them.
"""

from enum import StrEnum

from pydantic import BaseModel


class Position(StrEnum):
    GUARD = "G"
    FORWARD = "F"
    CENTER = "C"


class GameRules(BaseModel):
    budget: float = 100.0
    squad_size: int = 10
    squad_positions: dict[Position, int] = {
        Position.GUARD: 4,
        Position.FORWARD: 4,
        Position.CENTER: 2,
    }
    has_head_coach: bool = True
    max_teams_per_manager: int = 3

    starters: int = 5
    captain_multiplier: float = 2.0
    starter_multiplier: float = 1.0
    sixth_man_multiplier: float = 1.0
    bench_multiplier: float = 0.5

    transfers_per_round: int | None = 4  # UNVERIFIED (Basketball Sphere)
    captain_switch_between_games: bool | None = None  # UNVERIFIED: sources conflict
    max_players_per_club: int | None = None  # UNVERIFIED: unknown

    def multiplier(self, *, is_captain: bool, slot: str) -> float:
        """Points multiplier for a player in `slot` ("starter", "sixth", "bench")."""
        base = {
            "starter": self.starter_multiplier,
            "sixth": self.sixth_man_multiplier,
            "bench": self.bench_multiplier,
        }[slot]
        return base * self.captain_multiplier if is_captain else base


RULES_2026_27 = GameRules()
