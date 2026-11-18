"""Runs the filter: fetches unread entries per feed, decides, marks (or reports in dry-run)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .client import AuthError, FeedlyClient, FeedlyError
from .config import ConfigStore
from .history import History
from .rules import Decision, decide, effective, entry_fields, published_at
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
        client_factory: Callable[[dict[str, str]], FeedlyClient] = FeedlyClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        catalog_ttl: timedelta = timedelta(minutes=10),
    ):
        self.store = store
        self.session = session
        self.history = history
        self.client_factory = client_factory
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
            feed = config.find_feed(feed_id)
            interval = timedelta(minutes=feed.interval_minutes if feed else 60)
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

    def run_in_background(self, feed_ids: list[str] | None = None) -> bool:
        """Start a run without blocking the web request. False if a run is already active."""
        if self._run_lock.locked():
            return False

        def target() -> None:
            try:
                self.run(feed_ids)
            except (FeedlyError, RunBusy) as error:
                log.warning("background run stopped: %s", error)

        threading.Thread(target=target, name="manual-run", daemon=True).start()
        return True

    def _run(self, feed_ids: list[str] | None, dry_run: bool | None) -> list[FeedResult]:
        self.store.reload()
        config = self.store.config
        dry = config.dry_run if dry_run is None else dry_run
        self.refresh_catalog()
        targets = [self.catalog[feed_id] for feed_id in (feed_ids or self.catalog) if feed_id in self.catalog]
        results = []
        with self._client() as client:
            for info in targets:
                results.append(self._run_feed(client, info, dry))
        self.history.prune(config.history_limit_mb * 1024 * 1024)
        return results

    def _run_feed(self, client: FeedlyClient, info: FeedInfo, dry: bool) -> FeedResult:
        config = self.store.config
        eff = effective(config, config.find_feed(info.id), info.folder)
        now = self.clock()
        run_id = self.history.start_run(info.id, info.title, dry, now)
        processed = marked = 0
        error = ""
        try:
            items = client.stream_entries(info.id)
            processed = len(items)
            decisions = [(item, decide(item, eff, now)) for item in items]
            to_mark = [(item, decision) for item, decision in decisions if decision.mark]
            if to_mark and not dry:
                client.mark_as_read([item["id"] for item, _ in to_mark])
            self.history.add_marks(run_id, [self._mark_row(info, item, decision, dry) for item, decision in to_mark])
            marked = len(to_mark)
        except AuthError as exc:
            error = str(exc)
            raise
        except FeedlyError as exc:
            error = str(exc)
            log.warning("feed %s failed: %s", info.id, exc)
        finally:
            self.history.finish_run(run_id, processed, marked, error, self.clock())
            self.history.set_last_run(info.id, now)
        return FeedResult(info.id, info.title, processed, marked, error)

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
