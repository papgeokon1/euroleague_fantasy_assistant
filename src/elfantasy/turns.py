"""In-round turn strategy (Phase 5b).

A round is played in 2-3 turns. Between turns you can swap starters and bench, change
the formation and move the captain, but a player who already played can't move into
the starting five or sixth-man slot, and can't become the new captain. A player moved
from the lineup to the bench scores 50% (the bench multiplier). See docs/GAME_RULES.md.

That turns the lineup into a set of options. After each turn, with some scores known:
- Benching a starter who scored `a` for a later bench player gains 0.5 x (b - a), so it
  pays off when the bench player's expected score b beats a.
- Moving the captaincy from a captain who scored `c` to a later starter gains d - c.

`LineupSpace` lists every legal lineup of a 10-player squad (about 4,600: formation x
starters x sixth man x captain) as arrays, so "best legal lineup given what we know"
is a vectorised max. With at most 3 turns, choosing greedily on expected values at each
turn boundary is close to optimal (and exactly optimal with 2 turns).

The starting lineup is chosen by simulating the round with the turn policy in play.
That captures option value: a captain in turn 1 can still be moved; one in the last
turn can't.
"""

from dataclasses import dataclass
from itertools import combinations, product

import numpy as np
import pandas as pd

from elfantasy.rules import GameRules, Position

BENCH, STARTER, SIXTH = 0, 1, 2


@dataclass(frozen=True)
class Choice:
    """A lineup within a squad: starter set, sixth man, captain."""

    starters: tuple
    sixth: object
    captain: object


class LineupSpace:
    """Every legal lineup of one squad's 10 players, as arrays."""

    def __init__(self, players: list, positions: dict, rules: GameRules):
        self.players = list(players)
        self.rules = rules
        n = len(self.players)
        idx = {p: i for i, p in enumerate(self.players)}
        by_pos = {
            pos.value: [p for p in self.players if positions[p] == pos.value] for pos in Position
        }
        slots, caps = [], []
        for g, f, c in rules.starting_formations:
            groups = [
                combinations(by_pos[Position.GUARD.value], g),
                combinations(by_pos[Position.FORWARD.value], f),
                combinations(by_pos[Position.CENTER.value], c),
            ]
            for gs, fs, cs in product(*groups):
                starters = [idx[p] for p in (*gs, *fs, *cs)]
                rest = [i for i in range(n) if i not in starters]
                for sixth in rest:
                    row = np.full(n, BENCH, dtype=np.int8)
                    row[starters] = STARTER
                    row[sixth] = SIXTH
                    for cap in starters:
                        slots.append(row)
                        caps.append(cap)
        self.slots = np.array(slots)  # (L, n)
        self.cap = np.array(caps)  # (L,)
        mult = np.where(
            self.slots == STARTER,
            rules.starter_multiplier,
            np.where(self.slots == SIXTH, rules.sixth_man_multiplier, rules.bench_multiplier),
        )
        mult[np.arange(len(self.cap)), self.cap] *= rules.captain_multiplier
        self.mult = mult  # (L, n) points multiplier per player

    def __len__(self) -> int:
        return len(self.cap)

    def index(self, choice: Choice) -> int:
        idx = {p: i for i, p in enumerate(self.players)}
        row = np.full(len(self.players), BENCH, dtype=np.int8)
        row[[idx[p] for p in choice.starters]] = STARTER
        row[idx[choice.sixth]] = SIXTH
        hit = np.flatnonzero((self.slots == row).all(axis=1) & (self.cap == idx[choice.captain]))
        if not len(hit):
            raise ValueError("not a legal lineup of this squad")
        return int(hit[0])

    def choice(self, i: int) -> Choice:
        row = self.slots[i]
        return Choice(
            starters=tuple(self.players[j] for j in np.flatnonzero(row == STARTER)),
            sixth=self.players[int(np.flatnonzero(row == SIXTH)[0])],
            captain=self.players[int(self.cap[i])],
        )

    def legal_from(self, current: int, played: np.ndarray) -> np.ndarray:
        """Lineups reachable from `current` once the players in `played` (bool per
        player) have played: they may stay where they are or drop to the bench, and
        a new captain must not have played."""
        cur = self.slots[current]
        stay_or_bench = (self.slots == cur) | (self.slots == BENCH)
        ok = stay_or_bench[:, played].all(axis=1)
        same_cap = self.cap == self.cap[current]
        return ok & (same_cap | ~played[self.cap])

    def best(self, current: int, played: np.ndarray, values: np.ndarray) -> int:
        """Best reachable lineup when `values` are each player's known score (played)
        or expected score (not played yet). Keeps `current` on ties."""
        legal = self.legal_from(current, played)
        score = self.mult @ values
        score = np.where(legal, score, -np.inf)
        best = int(np.argmax(score))
        return current if score[current] >= score[best] - 1e-9 else best


def sample_points(pred: pd.DataFrame, players: list, sims: int, rng) -> np.ndarray:
    """Simulated fantasy points (sims x players) from p_play and the 10/50/90%
    quantiles of points-if-he-plays, with linear tails beyond them."""
    p = pred.loc[players]
    q10, q50, q90 = (p[c].to_numpy(dtype=float) for c in ("q10", "q50", "q90"))
    lo = q10 - 0.6 * (q50 - q10)
    hi = q90 + 0.6 * (q90 - q50)
    xs = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    u = rng.random((sims, len(players)))
    pts = np.empty_like(u)
    for j in range(len(players)):
        pts[:, j] = np.interp(u[:, j], xs, [lo[j], q10[j], q50[j], q90[j], hi[j]])
    plays = rng.random((sims, len(players))) < p["p_play"].to_numpy(dtype=float)
    return np.where(plays, pts, 0.0)


def simulate_policy(
    space: LineupSpace, start: int, turns: np.ndarray, expected: np.ndarray, actual: np.ndarray
) -> np.ndarray:
    """Final score per simulation when starting from lineup `start` and re-optimising
    after every turn but the last. `actual` is sims x players."""
    sims = actual.shape[0]
    current = np.full(sims, start)
    for k in sorted(set(turns.tolist()))[:-1]:
        played = turns <= k
        values = np.where(played, actual, expected)  # sims x players
        for lineup in np.unique(current):
            rows = current == lineup
            legal = space.legal_from(lineup, played)
            scores = values[rows] @ space.mult[legal].T  # (rows, legal lineups)
            options = np.flatnonzero(legal)
            pick = options[np.argmax(scores, axis=1)]
            keep = scores.max(axis=1) <= values[rows] @ space.mult[lineup] + 1e-9
            current[rows] = np.where(keep, lineup, pick)
    return np.einsum("ij,ij->i", actual, space.mult[current])


def best_start(
    space: LineupSpace,
    pred: pd.DataFrame,
    turns: dict,
    shortlist: int = 40,
    sims: int = 300,
    seed: int = 0,
) -> tuple[int, float, float]:
    """Starting lineup with the highest simulated score under the turn policy.

    Returns (lineup index, its simulated mean, the best static lineup's simulated mean).
    The shortlist is the top lineups by plain expected value.
    """
    players = space.players
    expected = pred.loc[players, "expected"].to_numpy(dtype=float)
    t = np.array([turns[p] for p in players])
    static = space.mult @ expected
    cands = np.argsort(-static)[:shortlist]
    actual = sample_points(pred, players, sims, np.random.default_rng(seed))
    means = np.array([simulate_policy(space, c, t, expected, actual).mean() for c in cands])
    return int(cands[np.argmax(means)]), float(means.max()), float(means[0])
