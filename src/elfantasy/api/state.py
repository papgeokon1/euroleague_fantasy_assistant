"""Shared state of the API process.

The database is opened only for the length of each request (a short-lived connection),
so the scheduled job and the CLI can still write between requests: DuckDB allows one
writing process at a time.

What's expensive to build (the trained model, this round's market and projections) is
cached in one FantasyContext and rebuilt only when the database file changes (after a
sync). Each request gets a shallow copy of it bound to its own connection.
"""

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import duckdb

from elfantasy.assistant.context import FantasyContext
from elfantasy.data.store import connect


class DatabaseBusy(RuntimeError):
    """Another process (e.g. `elf auto`) is writing to the database right now."""


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"  # pending | running | done | error
    result: object = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class AppState:
    def __init__(self, settings, rules):
        self.settings = settings
        self.rules = rules
        self._build_lock = threading.Lock()
        self._ctx: FantasyContext | None = None
        self._stamp: tuple | None = None
        self._fantasy = None
        self._anthropic = None
        self.jobs: dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="elf-job")
        self.chats: dict[str, object] = {}  # session id -> Assistant

    # -- clients ------------------------------------------------------------------

    @property
    def fantasy(self):
        if self._fantasy is None and self.settings.fantasy_token is not None:
            from elfantasy.data.fantasy import FantasyClient

            self._fantasy = FantasyClient(
                self.settings.raw_dir,
                self.settings.fantasy_token.get_secret_value(),
                league_id=self.settings.fantasy_league_id,
            )
        return self._fantasy

    @property
    def anthropic(self):
        if self._anthropic is None and self.settings.anthropic_api_key:
            import anthropic

            self._anthropic = anthropic.Anthropic(api_key=self.settings.anthropic_api_key)
        return self._anthropic

    # -- database sessions --------------------------------------------------------------

    @contextmanager
    def session(self) -> Iterator[FantasyContext]:
        """A FantasyContext bound to a fresh connection, closed when the block ends."""
        try:
            con = connect(self.settings.db_path)
        except duckdb.IOException as e:
            raise DatabaseBusy(
                "The database is busy (the scheduled job or another elf command is "
                "writing). Try again in a minute."
            ) from e
        try:
            yield replace(self._snapshot(con), con=con)
        finally:
            con.close()

    def _snapshot(self, con) -> FantasyContext:
        season = self.settings.season_code
        rnd = con.execute(
            "SELECT max(round) FROM fantasy_prices WHERE season = ? AND live", [season]
        ).fetchone()[0]
        if rnd is None:
            raise RuntimeError("No live fantasy prices yet: run `elf data fantasy` first.")
        stamp = (int(rnd), self.settings.db_path.stat().st_mtime)
        with self._build_lock:
            if self._ctx is None or self._stamp != stamp:
                ctx = FantasyContext(con=con, season=season, round=int(rnd), rules=self.rules,
                                     fantasy_client=self.fantasy)  # fmt: skip
                ctx.market  # noqa: B018 (build the caches now, while we hold the lock)
                ctx.predictions()
                self._ctx, self._stamp = ctx, stamp
            return self._ctx

    # -- background jobs ------------------------------------------------------------------

    def submit(self, kind: str, fn: Callable[[], object]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        self.jobs[job.id] = job

        def run():
            job.status = "running"
            try:
                job.result = fn()
                job.status = "done"
            except Exception as e:  # reported to the client through the job
                job.error = str(e)
                job.status = "error"

        self._executor.submit(run)
        return job
