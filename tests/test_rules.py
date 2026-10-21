import pytest

from elfantasy.rules import RULES_2026_27 as R
from elfantasy.rules import BoxScore, Slot, pir


def test_squad_is_ten_players():
    assert R.squad_size == 10


def test_multipliers():
    assert R.multiplier(slot=Slot.STARTER) == 1.0
    assert R.multiplier(slot=Slot.STARTER, is_captain=True) == 2.0
    assert R.multiplier(slot=Slot.SIXTH_MAN) == 1.0
    assert R.multiplier(slot=Slot.BENCH) == 0.5


def test_captain_must_start():
    with pytest.raises(ValueError):
        R.multiplier(slot=Slot.SIXTH_MAN, is_captain=True)


def test_player_who_already_played_cannot_move_into_the_lineup():
    assert R.can_enter_lineup(Slot.STARTER, has_played=False)
    assert not R.can_enter_lineup(Slot.STARTER, has_played=True)
    assert not R.can_enter_lineup(Slot.SIXTH_MAN, has_played=True)
    assert R.can_enter_lineup(Slot.BENCH, has_played=True)


def test_formations_need_at_least_one_of_each_position():
    assert R.is_valid_formation(2, 2, 1)
    assert R.is_valid_formation(3, 1, 1)
    assert not R.is_valid_formation(3, 2, 0)
    assert not R.is_valid_formation(1, 1, 3)


def test_pir():
    box = BoxScore(
        points=20,
        rebounds=8,
        assists=3,
        steals=1,
        blocks=1,
        fouls_drawn=5,
        turnovers=2,
        blocks_against=1,
        fouls_committed=3,
        missed_field_goals=6,
        missed_free_throws=1,
    )
    assert pir(box) == 38 - 13


def test_win_bonus():
    assert R.player_points(20, team_won=True) == pytest.approx(22.0)
    assert R.player_points(20, team_won=False) == 20


@pytest.mark.parametrize(
    ("margin", "overtime", "expected"),
    [
        (1, False, 10),
        (10, False, 10),
        (11, False, 20),
        (20, False, 20),
        (21, False, 25),
        (25, True, 10),
        (-3, False, -5),
        (-15, False, -10),
        (-30, False, -20),
        (-12, True, -5),
    ],
)
def test_coach_points(margin, overtime, expected):
    assert R.coach_points(margin, overtime=overtime) == expected


def test_transfer_limits():
    assert R.transfer_limit(5) == 4
    assert R.transfer_limit(6) is None
    assert R.transfer_limit(34) is None
    assert R.transfer_limit(36, postseason=True) is None
