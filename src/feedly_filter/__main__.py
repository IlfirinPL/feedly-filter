"""Entry point: builds the services, starts the scheduler and the web GUI on port 8080."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from .config import ConfigStore
from .engine import Engine
from .history import History
from .scheduler import Scheduler
from .session import SessionStore
from .web import Services, create_app


def build_services(data_dir: Path) -> Services:
    data_dir.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(data_dir / "config.yaml")
    session = SessionStore(data_dir / "session.json")
    history = History(data_dir / "history.db")
    engine = Engine(store, session, history)
    scheduler = Scheduler(engine)
    return Services(store=store, session=session, history=history, engine=engine, scheduler=scheduler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    port = int(os.environ.get("PORT", "8080"))
    services = build_services(data_dir)
    services.scheduler.start()
    uvicorn.run(create_app(services), host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
