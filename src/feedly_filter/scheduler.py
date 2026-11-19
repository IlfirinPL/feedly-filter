"""Background loop: once a minute runs the feeds whose planned time has come."""

from __future__ import annotations

import logging
import threading

from .client import FeedlyError
from .engine import Engine, RunBusy

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, engine: Engine, tick_seconds: int = 60):
        self.engine = engine
        self.tick_seconds = tick_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        self.engine.store.reload()
        if not self.engine.store.config.is_active_hour(self.engine.clock()):
            return  # outside the working hours: scheduled runs wait; manual runs still work
        self.engine.refresh_catalog()
        due = self.engine.due_feed_ids()
        if due:
            self.engine.run(feed_ids=due)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except RunBusy:
                log.info("previous run still active, skipping tick")
            except FeedlyError as error:
                log.warning("scheduled run failed: %s", error)
            except Exception:
                log.exception("unexpected scheduler error")
            self._stop.wait(self.tick_seconds)
