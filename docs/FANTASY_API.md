# Fantasy game API (Dunkest "fantaking")

Found on 2026-11-21 by inspecting the web app at
`euroleaguefantasy.euroleaguebasketball.net`, which is a Flutter app built by Dunkest.
This is an undocumented private API, so it can change without notice. Check the
game's terms of use before automating calls against it.

- **Base URL:** `https://fantaking-api.dunkest.com/api/v1`
- **Auth:** `Authorization: Bearer <token>` header, taken from a logged-in session.
- **Browser note:** the built-in browser hits CORS errors on this API, but plain
  HTTP clients (curl, httpx) work fine.

## In this project

`elf data fantasy` (code in `src/elfantasy/data/fantasy.py`, `parse_fantasy.py` and
`sync_fantasy.py`) fills two DuckDB tables:

- `fantasy_prices`: one row per player per round, from round 1 up to the current round.
  `price`, `team_code`, `opp_code`, `home` and `turn` are that round's values.
  `popularity`, `is_injured`, `probability_of_playing` and `started_from_bench` are
  filled only when `live` is true, meaning the snapshot was taken while that round was
  the current one.
- `fantasy_schedule`: every game of all 38 rounds, with its `turn`.

**Run `elf data fantasy` at least once every round, before the deadline.** Past rounds'
prices can always be downloaded again, but ownership and injury values can only be
captured live. Those live files in `data/raw/fantasy/` are never overwritten, and they
are the one part of `data/` that can't be rebuilt from code, so back them up.

## Key IDs (2026-27 season)

| Name | Value | Where it comes from |
|---|---|---|
| game | 7 | `/games/7/config` |
| league (EuroLeague) | 10 | EuroCup is 11 |
| competition / players-list / schedule | 49 | `/leagues/10/config` → `current_*_id` |
| matchday ids | 1528 (round 1) … | `/leagues/10/config` → `matchdays` (38 entries) |
| positions | 28 Guard, 29 Forward, 30 Center, 31 Head Coach | `/games/7/config` |

Always read the current ids from `/leagues/10/config` instead of hard-coding them.

## Public endpoints (no login needed)

| Endpoint | What it returns |
|---|---|
| `GET /games/7/config` | Positions, formations, league statuses |
| `GET /leagues/10/config` | Current competition, players-list and schedule ids, the current and previous matchday, the round start time, the full matchday list and the squad limits |
| `GET /schedules/{schedule}/matchdays/{matchday}` | Games for a round, split into turns (`rounds`), with team ids, start times and scores |
| `GET /leagues/10/fantasy-leaders` | Top fantasy scorers from the last matchday |

## Endpoints that need a login (return 401 without a token)

The token lives in `.env` as `ELF_FANTASY_TOKEN`. Its format is `<id>|<secret>`, and the
whole string goes after `Bearer `. All of these were checked on 2026-11-21.

### Player list with prices (the key endpoint)

`GET /players-lists/{list}/matchdays/{matchday}/players?per_page=500`

- Returns every player in one page when you pass `per_page=500` (362 players in round 14).
  Without it, results come 10 per page.
- **Works for past matchdays too.** Each round returns that round's prices. For example,
  round 1 (`1528`) gives the starting prices, so the season's price history can be
  rebuilt for any round.
- Fields per player: `id`, `first_name`, `last_name`, **`quotation`** (the price),
  `avg_pts`, `popularity` (ownership share, from 0 to 1), `position {id,name}`,
  `team {id,name,abbreviation}`, `opponent`, **`round {id,number}`** (which turn the player
  plays in), `is_injured`, `probability_of_playing`, `is_on_fire`, `started_from_bench`.
- `avg_pts` is the **current** season average even when you ask for a past matchday, so
  it leaks future data. Only `quotation` (and probably `opponent`/`round`) are values
  from that round. Never use `avg_pts` from this endpoint as a feature.

### Other endpoints

| Endpoint | Returns |
|---|---|
| `GET /competitions/{competition}/stats/players/table?per_page=100&page=N` | Season stats table, 381 players, at most 100 per page. Columns: `rank, name, position, team, fpt, quotation, plus, pts, reb, ast, stl, tov, blk, blka, fd, pf, fg_missed, ft_missed, win_*/loss_*` (rows are arrays of strings) |
| `GET /basketball/leagues/10/players/{id}/match-log/rendered` | One row per matchday. Columns: `MD, VS, CR` (the price that round), `PLUS` (price change), `FPT, MIN, ST` (starter), `PTS, REB, AST, STL, BLK, BA, FGM, FGA, …, TOV, PF, FD, +/-` |
| `GET /players/{id}/trend?league=10` | `last_five_avg`, `last_ten_avg`, and fantasy points per matchday |
| `GET /players/{id}/fantasy-pts?league=10&matchday={md}` | One game's fantasy points and stat breakdown |
| `GET /players/{id}/upcoming-matches?league=10` | The next 5 games |
| `GET /matches/{id}/lineups` | Both teams' players with `status` (starter/…), `probability_of_playing`, `quotation` |
| `GET /leagues/10/injuries` | Injury list (it was empty when checked) |
| `GET /odds/matches/{id}` | Bookmaker odds (empty when checked) |
| `GET /user/fantasy-teams?league=10&game_mode=1` | Your teams: id, name, rank, points |
| `GET /fantasy-teams/{team}/matchdays/{md}/roster` | Your roster: `formation_id` and players with `court_position`, `is_captain` |

The `league`, `matchday` and `game_mode` query parameters are required where they're
shown. Without them the server returns a 422 error.

Endpoints that write data (`roster` POST/PUT, trades, joining tournaments) exist too,
but we only read.
