from datetime import UTC, datetime, timedelta

import httpx
import pytest

from feedly_filter.client import AuthError, FeedlyClient, FeedlyError
from feedly_filter.config import Config, ConfigStore, FeedSettings, Rule
from feedly_filter.engine import Engine
from feedly_filter.history import History
from feedly_filter.session import SessionStore

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, feeds):
        self.feeds = feeds  # feed id -> list of stream items
        self.marked: list[str] = []
        self.mark_calls: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def subscriptions(self):
        return [{"id": feed_id, "title": f"title {feed_id}", "categories": [{"id": "c", "label": "Tech"}],
                 "velocity": 2.5} for feed_id in self.feeds]

    def unread_counts(self):
        return {"unreadcounts": [{"id": feed_id, "count": len(items)} for feed_id, items in self.feeds.items()]}

    def stream_entries(self, stream_id):
        return list(self.feeds[stream_id])

    def mark_as_read(self, entry_ids):
        self.mark_calls.append(list(entry_ids))
        self.marked.extend(entry_ids)


def entry(entry_id, title, days_old=0):
    return {"id": entry_id, "title": title,
            "published": int((NOW - timedelta(days=days_old)).timestamp() * 1000),
            "alternate": [{"href": "https://x.test"}], "summary": {"content": ""}, "categories": []}


@pytest.fixture
def services(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    session = SessionStore(tmp_path / "session.json")
    session.save("token")
    history = History(tmp_path / "history.db")
    fake = FakeClient({
        "feed/1": [entry("a", "buy SPONSORED now"), entry("b", "normal news"), entry("c", "old story", days_old=10)],
    })
    engine = Engine(store, session, history, client_factory=lambda headers: fake, clock=lambda: NOW)
    store.update(lambda config: config.global_rules.append(Rule(value="sponsored")))
    return store, history, fake, engine


def test_dry_run_reports_but_marks_nothing(services):
    store, history, fake, engine = services
    assert store.config.dry_run is True
    results = engine.run()
    assert fake.marked == []
    assert results[0].marked == 2
    rows = history.recent_marks()
    assert {row[5] for row in rows} == {"would_mark"}
    assert {row[4][:20] for row in rows} == {"rule (global): title", "aging older than 7 d"}


def test_real_run_marks_matching_entries_only(services):
    store, history, fake, engine = services
    store.update(lambda config: setattr(config, "dry_run", False))
    engine.run()
    assert sorted(fake.marked) == ["a", "c"]
    assert {row[5] for row in history.recent_marks()} == {"marked"}


def test_feed_rules_apply_only_when_override_set(services):
    store, history, fake, engine = services
    store.update(lambda config: setattr(config, "dry_run", False))
    store.update(lambda config: config.feeds.append(FeedSettings(
        id="feed/1", override=True, rules=[Rule(value="news")])))
    engine.run()
    # global "sponsored" still applies, own "news" rule applies, aging catches the old entry
    assert sorted(fake.marked) == ["a", "b", "c"]


def test_preview_counts_matches_without_changing_anything(services):
    store, history, fake, engine = services
    preview = engine.preview_rule("global", Rule(value="news"))
    assert preview.matches == 1  # "normal news" only
    assert preview.samples == [("title feed/1", "normal news")]
    assert preview.feeds_scanned == 1
    assert fake.marked == []
    assert history.recent_runs() == []


def test_preview_ignores_aging_and_saved_entries(services):
    store, history, fake, engine = services
    assert engine.preview_rule("global", Rule(value="story")).matches == 1  # old story matches by text
    fake.feeds["feed/1"].append({**entry("s", "saved story"), "categories": [{"id": "u/tag/global.saved"}]})
    assert engine.preview_rule("global", Rule(value="saved story")).matches == 0


def test_matches_from_all_feeds_are_marked_in_one_batch(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.update(lambda config: (setattr(config, "dry_run", False), config.global_rules.append(Rule(value="sale"))))
    session = SessionStore(tmp_path / "session.json")
    session.save("token")
    history = History(tmp_path / "history.db")
    fake = FakeClient({
        "feed/1": [entry("a", "big sale"), entry("b", "news")],
        "feed/2": [entry("c", "sale again"), entry("d", "more news")],
    })
    engine = Engine(store, session, history, client_factory=lambda headers: fake, clock=lambda: NOW)
    results = engine.run()
    assert fake.mark_calls == [["a", "c"]]  # one request for the whole run
    assert [result.marked for result in results] == [1, 1]


def test_failed_batch_mark_is_recorded_as_error_not_as_marked(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    store.update(lambda config: (setattr(config, "dry_run", False), config.global_rules.append(Rule(value="sale"))))
    session = SessionStore(tmp_path / "session.json")
    session.save("token")
    history = History(tmp_path / "history.db")

    class BrokenMarks(FakeClient):
        def mark_as_read(self, entry_ids):
            raise FeedlyError("POST /markers -> 500")

    fake = BrokenMarks({"feed/1": [entry("a", "big sale")]})
    engine = Engine(store, session, history, client_factory=lambda headers: fake, clock=lambda: NOW)
    results = engine.run()
    assert results[0].marked == 0
    assert results[0].error == "POST /markers -> 500"
    assert history.recent_marks() == []


def test_forced_run_marks_even_with_dry_run_on(services):
    store, history, fake, engine = services
    assert store.config.dry_run is True
    engine.run(dry_run=False)
    assert sorted(fake.marked) == ["a", "c"]


def test_background_run_refused_while_busy(services):
    store, history, fake, engine = services
    assert engine._run_lock.acquire(blocking=False)
    try:
        assert engine.is_running()
        assert engine.run_in_background() is False
    finally:
        engine._run_lock.release()
    assert not engine.is_running()


def test_due_feeds_run_once_per_interval_at_their_planned_time(services):
    store, history, fake, engine = services
    engine.refresh_catalog()
    planned = store.config.next_scheduled_run("feed/1", NOW)
    engine.clock = lambda: planned - timedelta(minutes=1)
    assert engine.due_feed_ids() == []
    engine.clock = lambda: planned + timedelta(minutes=1)
    assert engine.due_feed_ids() == ["feed/1"]
    engine.run()
    assert engine.due_feed_ids() == []
    engine.clock = lambda: planned + timedelta(minutes=61)
    assert engine.due_feed_ids() == ["feed/1"]


def test_auth_error_stops_run_and_is_recorded(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    session = SessionStore(tmp_path / "session.json")
    session.save("token")
    history = History(tmp_path / "history.db")

    class Expired(FakeClient):
        def stream_entries(self, stream_id):
            raise AuthError("GET /streams/contents -> 401")

    fake = Expired({"feed/1": []})
    engine = Engine(store, session, history, client_factory=lambda headers: fake, clock=lambda: NOW)
    with pytest.raises(AuthError):
        engine.run()
    assert history.recent_runs()[0][7].startswith("GET /streams")


def test_feedly_error_is_recorded_and_other_feeds_continue(tmp_path):
    store = ConfigStore(tmp_path / "config.yaml")
    session = SessionStore(tmp_path / "session.json")
    session.save("token")
    history = History(tmp_path / "history.db")

    class Partly(FakeClient):
        def stream_entries(self, stream_id):
            if stream_id == "feed/bad":
                raise FeedlyError("boom")
            return [entry("z", "fine")]

    fake = Partly({"feed/bad": [], "feed/good": []})
    engine = Engine(store, session, history, client_factory=lambda headers: fake, clock=lambda: NOW)
    results = engine.run()
    assert [result.error for result in results] == ["boom", ""]
    assert len(history.recent_runs()) == 2
