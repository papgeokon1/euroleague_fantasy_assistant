from elfantasy.rules import RULES_2026_27


def test_squad_positions_add_up_to_squad_size():
    assert sum(RULES_2026_27.squad_positions.values()) == RULES_2026_27.squad_size


def test_multipliers():
    r = RULES_2026_27
    assert r.multiplier(is_captain=False, slot="starter") == 1.0
    assert r.multiplier(is_captain=True, slot="starter") == 2.0
    assert r.multiplier(is_captain=False, slot="bench") == 0.5
