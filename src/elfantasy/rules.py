"""The game's rules, as data and scoring functions.

Everything the optimizer and the simulator need to know about the game lives here,
so a rule change is a one-line edit. See docs/GAME_RULES.md for sources and for
the questions that are still open (marked OPEN below).
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel


class Position(StrEnum):
    GUARD = "G"
    FORWARD = "F"
    CENTER = "C"


class Slot(StrEnum):
    STARTER = "starter"
    SIXTH_MAN = "sixth"
    BENCH = "bench"


@dataclass(frozen=True)
class BoxScore:
    """The stat line PIR is computed from (regular time plus overtime)."""

    points: int = 0
    rebounds: int = 0
    assists: int = 0
    steals: int = 0
    blocks: int = 0
    fouls_drawn: int = 0
    turnovers: int = 0
    blocks_against: int = 0
    fouls_committed: int = 0
    missed_field_goals: int = 0
    missed_free_throws: int = 0


@dataclass(frozen=True)
class CoachBand:
    """Coach points for a game decided by at most `max_margin` points."""

    max_margin: int | None  # None means "any margin"
    points: int


class GameRules(BaseModel):
    # Squad
    budget: float = 100.0
    squad_positions: dict[Position, int] = {
        Position.GUARD: 4,
        Position.FORWARD: 4,
        Position.CENTER: 2,
    }
    has_head_coach: bool = True
    max_players_per_club: int = 6
    max_teams_per_manager: int = 3
    late_join_credits_per_round: float = 0.3

    # Lineup: allowed starting fives as (guards, forwards, centers)
    starting_formations: tuple[tuple[int, int, int], ...] = (
        (2, 2, 1),
        (1, 2, 2),
        (2, 1, 2),
        (1, 3, 1),
        (3, 1, 1),
    )
    starter_multiplier: float = 1.0
    sixth_man_multiplier: float = 1.0
    bench_multiplier: float = 0.5
    captain_multiplier: float = 2.0  # captain must be a starter

    # A round has 2 (at most 3) turns. After each turn: starter/bench swaps, captain and
    # formation changes are allowed, but a player who already played (e.g. on the bench
    # in T1) can't become a starter, sixth man or captain. There are no automatic subs.
    in_round_changes: bool = True
    automatic_substitutions: bool = False
    max_turns_per_round: int = 3

    # Scoring
    win_bonus: float = 0.10  # +10% of the player's score when his team wins
    coach_win_bands: tuple[CoachBand, ...] = (
        CoachBand(10, 10),
        CoachBand(20, 20),
        CoachBand(None, 25),
    )
    coach_loss_bands: tuple[CoachBand, ...] = (
        CoachBand(10, -5),
        CoachBand(20, -10),
        CoachBand(None, -20),
    )

    # Transfers, made between rounds; trades lock at the round's first tip-off
    transfers_per_round: int = 4
    coach_change_counts_as_transfer: bool = True
    unlimited_transfers_after_rounds: tuple[int, ...] = (6, 13, 18, 23, 28, 34)
    unlimited_transfers_in_postseason: bool = True
    unused_transfers_roll_over: bool = False

    # OPEN: unconfirmed rules
    win_bonus_rounding: str | None = None
    price_change_formula: str | None = None

    @property
    def squad_size(self) -> int:
        return sum(self.squad_positions.values())

    def multiplier(self, *, slot: Slot, is_captain: bool = False) -> float:
        if is_captain and slot is not Slot.STARTER:
            raise ValueError("captain must be a starter")
        base = {
            Slot.STARTER: self.starter_multiplier,
            Slot.SIXTH_MAN: self.sixth_man_multiplier,
            Slot.BENCH: self.bench_multiplier,
        }[slot]
        return base * self.captain_multiplier if is_captain else base

    def can_enter_lineup(self, slot: Slot, *, has_played: bool) -> bool:
        """Whether a player can be moved into `slot` between turns of a round."""
        return slot is Slot.BENCH or not has_played

    def is_valid_formation(self, guards: int, forwards: int, centers: int) -> bool:
        return (guards, forwards, centers) in self.starting_formations

    def transfer_limit(self, after_round: int, *, postseason: bool = False) -> int | None:
        """Transfers allowed after `after_round` ends. None means unlimited."""
        if postseason and self.unlimited_transfers_in_postseason:
            return None
        if after_round in self.unlimited_transfers_after_rounds:
            return None
        return self.transfers_per_round

    def player_points(self, pir: float, *, team_won: bool) -> float:
        """Fantasy points for one game, before the slot/captain multiplier."""
        return pir * (1 + self.win_bonus) if team_won else pir

    def coach_points(self, margin: int, *, overtime: bool = False) -> int:
        """Coach points for one game. `margin` is the final score difference from the
        coach's side: positive for a win, negative for a loss."""
        if margin == 0:
            raise ValueError("basketball games can't end in a tie")
        bands = self.coach_win_bands if margin > 0 else self.coach_loss_bands
        if overtime:
            return bands[0].points
        for band in bands:
            if band.max_margin is None or abs(margin) <= band.max_margin:
                return band.points
        raise AssertionError("bands must end with an open-ended band")


def pir(box: BoxScore) -> int:
    """EuroLeague Performance Index Rating."""
    plus = box.points + box.rebounds + box.assists + box.steals + box.blocks + box.fouls_drawn
    minus = (
        box.turnovers
        + box.blocks_against
        + box.fouls_committed
        + box.missed_field_goals
        + box.missed_free_throws
    )
    return plus - minus


RULES_2026_27 = GameRules()
