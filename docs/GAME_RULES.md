# EuroLeague Fantasy Challenge: game rules (2026-27, Classic mode)

These are the rules the assistant follows. They are encoded in `src/elfantasy/rules.py`. If the in-game rules ever disagree with this page, the game wins, and both this page and `rules.py` get updated.

## Season

- One competition runs the whole season. The regular season has 38 rounds (24 Sep 2026 to 16 Apr 2027), followed by the play-in, playoffs and Final Four (final on 30 May 2027).
- **Round:** one EuroLeague round, in which every team plays one game.
- **Turn (T1, T2, …):** the block of games played on one day within a round. A round usually has 2 turns, and at most 3.

## Squad

| Rule | Value |
|---|---|
| Budget | 100 credits |
| Squad | 10 players: 4 guards, 4 forwards, 2 centers |
| Head coach | 1, bought with the same budget |
| Max players from one club | 6 (the playoff limit is also 6) |
| Teams per manager | Up to 3 |
| Rosters | Non-exclusive: any number of managers can own the same player |
| Joining late | Budget of 100 + 0.3 credits for each round already played. The new team starts at the average score of all teams. |

## Lineup

- **Starting five:** formation (guards-forwards-centers) must be one of 2-2-1, 1-2-2, 2-1-2, 1-3-1 or 3-1-1. In other words, at least one of each position.
- **Sixth man:** scores 100%.
- **Bench (the other 4):** scores 50%.
- **Head coach:** scores 100%.
- **Captain:** chosen from the starting five, and scores ×2.

### Changes during a round

Between turns you can:

- Swap starters and bench players.
- Change the formation.
- Change the captain.

There is one condition: **the player coming in, or the new captain, must not have played yet in this round.** For example, a bench player who played in T1 can't become a starter or the sixth man in T2. A player moved from the starters to the bench scores 50%. You can reset all substitutions before a turn begins.

There are **no automatic substitutions**. A starter who doesn't play scores 0 unless you bench him yourself before his turn.

> **Why this matters:** the lineup is not fixed for the whole round. Starting players who play in T1 creates an option: if one of them scores badly, you can bench him (he then scores 50%) and bring in a bench player who plays later. The same option exists for the captain. Planning which turn each player plays in is a real source of points.

## Scoring

### Players

Fantasy points are the player's **PIR** (Performance Index Rating), counting regular time plus overtime:

| +1 for each | −1 for each |
|---|---|
| point scored | missed field goal |
| rebound | missed free throw |
| assist | turnover |
| steal | shot of his blocked by an opponent |
| block | foul committed |
| foul drawn | |

**Win bonus:** +10% of the player's score if his team wins.

So for one game: `fantasy points = PIR × (1.10 if team won else 1.00) × slot multiplier (×2 if captain)`.

### Head coach

| Result | Win | Loss |
|---|---|---|
| By 1–10 points, or in overtime | +10 | −5 |
| By 11–20 points | +20 | −10 |
| By more than 20 points | +25 | −20 |

A coach still scores if dismissed, absent or ejected.

## Transfers

- Made **between rounds only**. The market locks when the round's first game tips off.
- **4 transfers per round** in the regular season. Changing the coach counts as one.
- Unused transfers **do not carry over** to the next round.
- **Unlimited transfers** in the windows after rounds 6, 13, 18, 23, 28 and 34, and throughout the play-in, playoffs and Final Four.

## Prices

- Prices update **after every round**, based on the score each player earned.
- For the same score, a cheaper player rises more than an expensive one.
- Price changes change your team's total value, so a squad that gains value can afford better players later.
- Players who miss games lose value. Stars coming back from injury can therefore be bought cheaply.
- The exact formula is not published. Phase 6 works it out from price history.

## Postponed or moved games

- **Moved to a gap between rounds:** the game counts normally.
- **Moved into a period that overlaps another round:** at the end of T1, the players of the teams involved (including coaches, injured and suspended players) get their **season average score**. In this case **the captain is not doubled**.
- For special cases, such as a whole round being postponed, the organizers decide case by case.

## Open questions

Check these in the app, then update `rules.py`:

- [x] Unused transfers don't carry over (confirmed in the app).
- [x] Unlimited-transfer windows are after rounds 6/13/18/23/28/34 (confirmed in the app).
- [x] No automatic substitutions. Players who already played can't move into the lineup (confirmed in the app).
- [x] Win bonus: a player on a winning team scores PIR + 10% of PIR (confirmed in the app).
- [ ] How is the win bonus rounded? Phase 2 will work this out from real scores.
- [ ] Does the 10% win bonus also apply to the coach? Probably not, since the coach has his own table.
- [ ] The price change formula (Phase 6).

## Sources

- Fantaking (the official game provider) rulebook, Classic mode: [initial team](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/initial-team), [managing your team](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/managing-your-team), [player scoring](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/players-scoring), [coach scoring](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/coach-scoring), [prices](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/quotations-and-price-variations), [postponed games](https://fantaking.gitbook.io/euroleague-fantasy-challenge-rules/classic-mode/suspended-or-changed-games)
- [EuroLeague: Fantasy Challenge returns for 2026-27](https://www.euroleaguebasketball.net/en/euroleague/news/euroleague-fantasy-challenge-returns-for-2026-27/)
- [Basketball Sphere: EuroLeague Fantasy 2026-27](https://basketballsphere.com/en/euroleague-fantasy/)
