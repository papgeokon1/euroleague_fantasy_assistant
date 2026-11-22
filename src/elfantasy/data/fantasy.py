"""Client for the fantasy game's private API (Dunkest "fantaking"). See docs/FANTASY_API.md.

Raw responses are kept under data/raw/fantasy/<season>/.

Player lists for past matchdays return that round's prices, but some fields
(popularity, injuries, probability of playing) always show today's values. So a player
list is saved as "live" only while its matchday is the current one, and a live file is
never overwritten once that matchday is over. That keeps a true per-round snapshot of
those fields, which can't be rebuilt later.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE_URL = "https://fantaking-api.dunkest.com/api/v1"
ORIGIN = "https://euroleaguefantasy.euroleaguebasketball.net"


class FantasyAuthError(RuntimeError):
    """The token is missing, wrong or expired."""


class FantasyClient:
    def __init__(
        self,
        raw_dir: Path,
        token: str | None,
        *,
        league_id: int = 10,
        delay: float = 0.3,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.raw_dir = raw_dir / "fantasy"
        self.league_id = league_id
        self.delay = delay
        headers = {"Accept": "application/json", "Origin": ORIGIN, "User-Agent": "elfantasy/0.1"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.http = httpx.Client(
            base_url=BASE_URL, timeout=timeout, headers=headers, transport=transport
        )

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "FantasyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, path: str, params: dict | None = None, retries: int = 4) -> dict:
        for attempt in range(retries):
            try:
                response = self.http.get(path, params=params)
                if response.status_code == 401:
                    raise FantasyAuthError(
                        "The fantasy API rejected the token. Log in to the game, copy a fresh "
                        "Bearer token into .env as ELF_FANTASY_TOKEN (see docs/FANTASY_API.md)."
                    )
                if response.status_code == 429:
                    time.sleep(float(response.headers.get("Retry-After", 10 * (attempt + 1))))
                    continue
                response.raise_for_status()
                time.sleep(self.delay)
                return response.json()
            except (httpx.TransportError, json.JSONDecodeError):
                if attempt == retries - 1:
                    raise
                time.sleep(2**attempt)
        raise httpx.HTTPError(f"still rate-limited after {retries} attempts: {path}")

    def league_config(self) -> dict:
        """Current competition, schedule and players-list ids, plus the matchday list."""
        return self._get(f"/leagues/{self.league_id}/config")["data"]

    def schedule(self, season: str, schedule_id: int, matchday: dict) -> dict:
        """Games of one matchday, grouped into turns. Always re-downloaded, since dates
        and results change."""
        data = self._get(f"/schedules/{schedule_id}/matchdays/{matchday['id']}")["data"]
        self._write(self.raw_dir / season / "schedule" / f"{matchday['number']:02d}.json", data)
        return data

    def players(
        self,
        season: str,
        list_id: int,
        matchday: dict,
        *,
        current: bool,
        league_status_id: int | None = None,
    ) -> dict:
        """Player list of one matchday, wrapped as {"fetched_at", "live", "players", ...}.

        `league_status_id` is recorded with live snapshots (1 = trades open, 2 = games in
        progress, ...), so later we can tell a pre-deadline snapshot from one taken
        mid-round.

        Past matchdays are served from the cache. The current matchday is always
        re-downloaded and saved as live.
        """
        path = self.raw_dir / season / "players" / f"{matchday['number']:02d}.json"
        if path.exists() and not current:
            return json.loads(path.read_text(encoding="utf-8"))
        url = f"/players-lists/{list_id}/matchdays/{matchday['id']}/players"
        players: list[dict] = []
        page, last_page = 1, 1
        while page <= last_page:
            body = self._get(url, params={"per_page": 500, "page": page})
            players += body["data"]
            last_page = body.get("meta", {}).get("last_page", 1)
            page += 1
        snapshot = {
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "live": current,
            "league_status_id": league_status_id if current else None,
            "matchday": matchday,
            "players": players,
        }
        self._write(path, snapshot)
        return snapshot

    def my_teams(self) -> list[dict]:
        """The logged-in user's classic-mode teams in this league."""
        return self._get("/user/fantasy-teams", params={"league": self.league_id, "game_mode": 1})[
            "data"
        ]

    def team_round(self, team_id: int, matchday_id: int) -> dict:
        """A team's state for a matchday: `credits` (total budget incl. bank),
        `trades` used and `max_num_trades_per_matchday`."""
        return self._get(f"/fantasy-teams/{team_id}/matchdays/{matchday_id}")["data"]

    def roster(self, team_id: int, matchday_id: int) -> dict:
        """A team's roster for a matchday: players with court_position (1-5 starters,
        6 sixth man, 7-10 bench, 11 coach) and is_captain."""
        return self._get(f"/fantasy-teams/{team_id}/matchdays/{matchday_id}/roster")["data"]

    def tournaments(self, team_id: int) -> list[dict]:
        """Leagues a team plays in (the public 'Global' one included)."""
        return self._get(f"/fantasy-teams/{team_id}/tournaments")["data"]

    def standings(self, tournament_id: int, matchday_id: int | None = None) -> list[dict]:
        """Top of a league's table (first page, 25 teams). With matchday_id: that
        round's ranking, with each team's matchday_pts."""
        params = {"matchday": matchday_id} if matchday_id else None
        return self._get(f"/tournaments/{tournament_id}/standings", params=params)["data"]

    @staticmethod
    def _write(path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
