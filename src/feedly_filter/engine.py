"""Runs the filter: fetches unread entries per feed, decides, marks (or reports in dry-run)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .client import AuthError, FeedlyClient, FeedlyError
from .config import ConfigStore, Rule
from .history import History
from .rules import Decision, decide, effective, entry_fields, is_saved, matches, published_at
from .session import SessionStore

log = logging.getLogger(__name__)


class RunBusy(Exception):
    """Another run is already in progress."""


@dataclass(frozen=True)
class FeedInfo:
    id: str
    title: str
    folder: str
    unread: int
    velocity: float


@dataclass(frozen=True)
class FeedResult:
    feed_id: str
    feed_name: str
    processed: int
    marked: int
    error: str


@dataclass
class _FeedRun:
    """One feed's part of a run: collected entries waiting for the batch mark."""

    info: FeedInfo
    run_id: int
    processed: int = 0
    error: str = ""
    ids: list[str] = field(default_factory=list)
    rows: list[tuple[str, str, str, str, str, str, str]] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.error = self.error or message
        self.ids = []
        self.rows = []  # nothing was marked, so nothing is recorded as marked


@dataclass(frozen=True)
class PreviewResult:
    feeds_scanned: int
    matches: int
    samples: list[tuple[str, str]]  # (feed title, entry title)


def folder_of(subscription: dict) -> str:
    categories = subscription.get("categories") or []
    if categories and isinstance(categories[0], dict):
        return categories[0].get("label", "")
    return ""


class Engine:
    def __init__(
        self,
        store: ConfigStore,
        session: SessionStore,
        history: History,
        client_factory: Callable[[dict[str, str]], FeedlyClient] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        catalog_ttl: timedelta = timedelta(minutes=10),
    ):
        self.store = store
        self.session = session
        self.history = history
        # The default client counts every request in the history database.
        self.client_factory = client_factory or (
            lambda headers: FeedlyClient(headers, on_request=history.record_request))
        self.clock = clock
        self.catalog_ttl = catalog_ttl
        self.catalog: dict[str, FeedInfo] = {}
        self._catalog_at: datetime | None = None
        self._run_lock = threading.Lock()

    def _client(self) -> FeedlyClient:
        headers = self.session.headers()
        if not headers:
            raise AuthError("no session saved, see Login")
        return self.client_factory(headers)

    def refresh_catalog(self, force: bool = False) -> None:
        now = self.clock()
        if not force and self._catalog_at is not None and now - self._catalog_at < self.catalog_ttl:
            return
        with self._client() as client:
            subscriptions = client.subscriptions()
            counts = {item["id"]: item.get("count", 0) for item in client.unread_counts().get("unreadcounts", [])}
        self.catalog = {
            sub["id"]: FeedInfo(
                id=sub["id"],
                title=sub.get("title", ""),
                folder=folder_of(sub),
                unread=counts.get(sub["id"], 0),
                velocity=float(sub.get("velocity") or 0),
            )
            for sub in subscriptions
        }
        self._catalog_at = now

    def forget_catalog(self) -> None:
        """Drop the cached catalog so the next call fetches it again, e.g. after a new session."""
        self._catalog_at = None

    def due_feed_ids(self) -> list[str]:
        now = self.clock()
        config = self.store.config
        last_runs = self.history.last_runs()
        due = []
        for feed_id in self.catalog:
            interval = timedelta(minutes=config.interval_for(feed_id))
            previous = last_runs.get(feed_id)
            if previous is None or now - previous >= interval:
                due.append(feed_id)
        return due

    def run(self, feed_ids: list[str] | None = None, dry_run: bool | None = None) -> list[FeedResult]:
        if not self._run_lock.acquire(blocking=False):
            raise RunBusy()
        try:
            return self._run(feed_ids, dry_run)
        finally:
            self._run_lock.release()

    def is_running(self) -> bool:
        return self._run_lock.locked()

    def run_in_background(self, feed_ids: list[str] | None = None, dry_run: bool | None = None) -> bool:
        """Start a run without blocking the web request. False if a run is already active."""
        if self.is_running():
            return False

        def target() -> None:
            try:
                self.run(feed_ids, dry_run)
            except (FeedlyError, RunBusy) as error:
                log.warning("background run stopped: %s", error)

        threading.Thread(target=target, name="manual-run", daemon=True).start()
        return True

    def preview_rule(self, scope: str, rule: Rule, sample_size: int = 10) -> PreviewResult:
        """Count the unread entries this rule would mark now, without changing anything.

        Uses the same scope rules as a real run: folder rules skip feeds that override them,
        global rules reach every feed, and saved entries and whitelisted entries are skipped.
        """
        self.refresh_catalog()
        config = self.store.config
        kind, _, key = scope.partition("|")
        if kind == "global":
            feed_ids = list(self.catalog)
        elif kind == "folder":
            feed_ids = []
            for feed_id, info in self.catalog.items():
                feed = config.find_feed(feed_id)
                if info.folder == key and not (feed is not None and feed.override):
                    feed_ids.append(feed_id)
        else:
            feed_ids = [key] if key in self.catalog else []

        total = 0
        samples: list[tuple[str, str]] = []
        with self._client() as client:
            for feed_id in feed_ids:
                info = self.catalog[feed_id]
                eff = effective(config, config.find_feed(feed_id), info.folder)
                for item in client.stream_entries(feed_id):
                    if is_saved(item):
                        continue
                    fields = entry_fields(item)
                    if any(matches(exception, fields) for exception, _ in eff.whitelist):
                        continue
                    if matches(rule, fields):
                        total += 1
                        if len(samples) < sample_size:
                            samples.append((info.title, fields["title"]))
        return PreviewResult(feeds_scanned=len(feed_ids), matches=total, samples=samples)

    def _run(self, feed_ids: list[str] | None, dry_run: bool | None) -> list[FeedResult]:
        self.store.reload()
        config = self.store.config
        dry = config.dry_run if dry_run is None else dry_run
        self.refresh_catalog()
        targets = [self.catalog[feed_id] for feed_id in (feed_ids or self.catalog) if feed_id in self.catalog]
        now = self.clock()
        runs: list[_FeedRun] = []
        try:
            with self._client() as client:
                # Phase 1: read every feed and decide. Phase 2: mark all matches in one batch.
                for info in targets:
                    run = _FeedRun(info, self.history.start_run(info.id, info.title, dry, now))
                    runs.append(run)
                    self._collect(client, run, dry, now)
                if not dry:
                    self._apply_marks(client, runs)
        except AuthError as exc:
            for run in runs:
                run.fail(str(exc))
            self._finish(runs, now)
            raise
        self._finish(runs, now)
        self.history.prune(config.history_limit_mb * 1024 * 1024)
        return [FeedResult(run.info.id, run.info.title, run.processed, len(run.rows), run.error) for run in runs]

    def _collect(self, client: FeedlyClient, run: _FeedRun, dry: bool, now: datetime) -> None:
        config = self.store.config
        eff = effective(config, config.find_feed(run.info.id), run.info.folder)
        try:
            items = client.stream_entries(run.info.id)
        except AuthError:
            raise
        except FeedlyError as exc:
            run.error = str(exc)
            log.warning("feed %s failed: %s", run.info.id, exc)
            return
        run.processed = len(items)
        for item in items:
            decision = decide(item, eff, now)
            if decision.mark:
                run.ids.append(item["id"])
                run.rows.append(self._mark_row(run.info, item, decision, dry))

    @staticmethod
    def _apply_marks(client: FeedlyClient, runs: list[_FeedRun]) -> None:
        ids = [entry_id for run in runs for entry_id in run.ids]
        if not ids:
            return
        try:
            client.mark_as_read(ids)  # one batch for the whole run, sent in chunks of 100
        except FeedlyError as exc:
            for run in runs:
                run.fail(str(exc))

    def _finish(self, runs: list[_FeedRun], now: datetime) -> None:
        for run in runs:
            if run.rows:
                self.history.add_marks(run.run_id, run.rows)
            self.history.finish_run(run.run_id, run.processed, len(run.rows), run.error, self.clock())
            self.history.set_last_run(run.info.id, now)

    @staticmethod
    def _mark_row(info: FeedInfo, item: dict, decision: Decision, dry: bool) -> tuple[str, str, str, str, str, str, str]:
        published = published_at(item)
        return (
            info.id,
            info.title,
            item.get("id", ""),
            entry_fields(item)["title"],
            published.isoformat() if published else "",
            decision.reason,
            "would_mark" if dry else "marked",
        )
