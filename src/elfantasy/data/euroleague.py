"""Client for the public EuroLeague API (api-live.euroleague.net/v2).

Raw responses are cached under data/raw/euroleague/<season>/, so a finished game is
downloaded only once.
"""

import json
import time
from pathlib import Path

import httpx

BASE_URL = "https://api-live.euroleague.net/v2/competitions/E/seasons"


def season_code(year: int) -> str:
    """2026 -> "E2026" (the 2026-27 season)."""
    return f"E{year}"


class EuroleagueClient:
    def __init__(self, raw_dir: Path, *, delay: float = 0.6, timeout: float = 30.0):
        self.raw_dir = raw_dir / "euroleague"
        self.delay = delay
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "elfantasy/0.1"})

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "EuroleagueClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _get(self, url: str, retries: int = 6) -> dict:
        for attempt in range(retries):
            try:
                response = self.http.get(url)
                if response.status_code == 429:  # Cloudflare rate limit: back off hard
                    wait = float(response.headers.get("Retry-After", 30 * (attempt + 1)))
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                time.sleep(self.delay)
                return response.json()
            except (httpx.HTTPError, json.JSONDecodeError):
                if attempt == retries - 1:
                    raise
                time.sleep(2**attempt)
        raise httpx.HTTPError(f"still rate-limited after {retries} attempts: {url}")

    def games(self, season: str) -> list[dict]:
        """All games of a season, played or not. Always re-downloaded, since the
        schedule and results change during the season."""
        data = self._get(f"{BASE_URL}/{season}/games")["data"]
        path = self.raw_dir / season / "games.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def game_stats(self, season: str, game_code: int, *, refresh: bool = False) -> dict:
        """Box score of one finished game, served from the cache when possible."""
        path = self.raw_dir / season / "stats" / f"{game_code}.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))
        data = self._get(f"{BASE_URL}/{season}/games/{game_code}/stats")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data
