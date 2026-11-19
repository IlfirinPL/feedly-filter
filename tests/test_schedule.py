from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from feedly_filter.__main__ import build_services
from feedly_filter.config import Config, FeedSettings
from feedly_filter.engine import Engine
from feedly_filter.scheduler import Scheduler
from feedly_filter.web import create_app


def warsaw(hour: int, minute: int = 0) -> datetime:
    """A UTC instant that is `hour`:`minute` in Europe/Warsaw in November (UTC+1)."""
    return datetime(2026, 11, 18, hour - 1, minute, tzinfo=UTC)


def test_defaults_are_seven_to_twenty_two_and_sixty_minutes():
    config = Config()
    assert (config.active_from, config.active_until) == (7, 22)
    assert config.default_interval_minutes == 60


@pytest.mark.parametrize(("hour", "active"), [(6, False), (7, True), (21, True), (22, False)])
def test_working_hours_use_local_time(hour, active):
    assert Config().is_active_hour(warsaw(hour)) is active


def test_feed_without_interval_uses_default_and_override_wins():
    config = Config(default_interval_minutes=15, feeds=[FeedSettings(id="a"), FeedSettings(id="b", interval_minutes=30)])
    assert config.interval_for("a") == 15
    assert config.interval_for("b") == 30
    assert config.interval_for("unknown") == 15


def test_inverted_hours_are_rejected():
    with pytest.raises(ValidationError):
        Config(active_from=22, active_until=7)


def test_feeds_with_the_same_interval_run_at_different_minutes():
    config = Config(feeds=[FeedSettings(id=f"feed/{number}") for number in range(20)])
    assert len({config.schedule_phase(f"feed/{number}") for number in range(20)}) > 10


def test_hourly_feed_runs_once_an_hour_inside_working_hours_at_its_own_minute():
    config = Config()
    runs = config.scheduled_runs("feed/1", date(2026, 11, 18))
    assert [run.hour for run in runs] == list(range(7, 22))  # 7:xx … 21:xx, nothing at 22:00 or later
    assert all(run.minute == config.schedule_phase("feed/1") for run in runs)


def test_next_run_is_the_next_planned_time_and_wraps_to_the_next_morning():
    config = Config()
    next_run = config.next_scheduled_run("feed/1", warsaw(9))
    assert warsaw(9) < next_run <= warsaw(10)
    after_hours = config.next_scheduled_run("feed/1", warsaw(22, 30))
    assert (after_hours.day, after_hours.hour) == (19, 7)


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValidationError):
        Config(timezone="Mars/Olympus")


def test_scheduler_skips_ticks_outside_working_hours(tmp_path):
    services = build_services(tmp_path)
    services.session.save("token")
    calls = []

    class Recording:
        def __init__(self, headers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def subscriptions(self):
            calls.append("subscriptions")
            return []

        def unread_counts(self):
            return {"unreadcounts": []}

    engine = Engine(services.store, services.session, services.history,
                    client_factory=Recording, clock=lambda: warsaw(3))
    Scheduler(engine).tick()
    assert calls == []  # nothing fetched at 03:00

    engine.clock = lambda: warsaw(9)
    Scheduler(engine).tick()
    assert calls == ["subscriptions"]


def test_due_feeds_use_the_default_interval(tmp_path):
    services = build_services(tmp_path)
    services.store.update(lambda config: setattr(config, "default_interval_minutes", 15))
    services.session.save("token")

    class Two:
        def __init__(self, headers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def subscriptions(self):
            return [{"id": "feed/1", "title": "one", "categories": []}]

        def unread_counts(self):
            return {"unreadcounts": []}

    now = warsaw(9)
    engine = Engine(services.store, services.session, services.history, client_factory=Two, clock=lambda: now)
    engine.refresh_catalog()
    planned = services.store.config.next_scheduled_run("feed/1", now)
    assert now < planned <= now + timedelta(minutes=15)
    engine.clock = lambda: planned + timedelta(minutes=1)
    assert engine.due_feed_ids() == ["feed/1"]
    services.history.set_last_run("feed/1", planned + timedelta(minutes=1))
    assert engine.due_feed_ids() == []
    engine.clock = lambda: planned + timedelta(minutes=16)
    assert engine.due_feed_ids() == ["feed/1"]


def test_settings_reject_bad_hours_and_keep_old_values(tmp_path):
    services = build_services(tmp_path)
    client = TestClient(create_app(services), follow_redirects=False)
    response = client.post("/settings", data={"days": "7", "history_limit_mb": "100", "timezone": "Europe/Warsaw",
                                              "active_from": "22", "active_until": "7",
                                              "default_interval_minutes": "60"})
    assert "error=" in response.headers["location"]
    assert services.store.config.active_from == 7


def test_settings_save_new_defaults(tmp_path):
    services = build_services(tmp_path)
    client = TestClient(create_app(services), follow_redirects=False)
    client.post("/settings", data={"days": "7", "history_limit_mb": "100", "timezone": "Europe/Warsaw",
                                   "active_from": "8", "active_until": "20", "default_interval_minutes": "30"})
    config = services.store.config
    assert (config.active_from, config.active_until, config.default_interval_minutes) == (8, 20, 30)


def test_empty_feed_interval_resets_to_default(tmp_path):
    services = build_services(tmp_path)
    client = TestClient(create_app(services), follow_redirects=False)
    client.post("/scope", data={"scope": "feed|feed/1", "action": "set_interval", "minutes": "15"})
    assert services.store.config.find_feed("feed/1").interval_minutes == 15
    client.post("/scope", data={"scope": "feed|feed/1", "action": "set_interval", "minutes": ""})
    assert services.store.config.find_feed("feed/1").interval_minutes is None
