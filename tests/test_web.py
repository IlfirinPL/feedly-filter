from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from feedly_filter.__main__ import build_services
from feedly_filter.client import AuthError
from feedly_filter.config import Rule
from feedly_filter.web import create_app


class OkClient:
    def __init__(self, headers):
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return None

    def profile(self):
        return {"email": "x"}


class RejectingClient(OkClient):
    def profile(self):
        raise AuthError("GET /profile -> 401")


def make_app(tmp_path, client_factory=OkClient):
    services = build_services(tmp_path)
    services.engine.client_factory = client_factory
    return TestClient(create_app(services), follow_redirects=False), services


def test_healthz(tmp_path):
    client, _ = make_app(tmp_path)
    assert client.get("/healthz").json() == {"status": "ok"}


def test_dashboard_renders_without_session(tmp_path):
    client, _ = make_app(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Dry run" in response.text
    assert "no session saved" in response.text


def test_add_feed_rule_sets_override_and_persists(tmp_path):
    client, services = make_app(tmp_path)
    response = client.post("/scope", data={
        "scope": "feed|feed/http://example.com/rss", "action": "add_rule",
        "field": "title", "op": "contains", "value": "sponsored",
    })
    assert response.status_code == 303
    feed = services.store.config.find_feed("feed/http://example.com/rss")
    assert feed.override is True
    assert feed.rules[0].value == "sponsored"


def test_history_run_view_lists_only_that_run(tmp_path):
    client, services = make_app(tmp_path)
    history = services.history
    run = history.start_run("feed/1", "Example", False, datetime(2026, 11, 18, tzinfo=UTC))
    history.add_marks(run, [("feed/1", "Example", "e1", "Sponsored offer", "2026-11-18T10:00:00+00:00",
                             "rule (global): title contains 'sponsored'", "marked")])
    response = client.get(f"/history?run={run}")
    assert response.status_code == 200
    assert f"Entries marked by run #{run}" in response.text
    assert "rule (global): title contains" in response.text
    assert "Sponsored offer" in response.text
    assert "Entries marked by run" not in client.get("/history").text


def test_data_tables_are_marked_sortable(tmp_path):
    client, _ = make_app(tmp_path)
    assert 'table class="sortable"' in client.get("/history").text
    assert 'table class="sortable"' in client.get("/stats").text
    assert 'table class="sortable"' not in client.get("/settings").text  # key/value form, not sorted


def test_folders_are_listed_on_the_feeds_page(tmp_path):
    client, services = make_app(tmp_path)
    services.store.update(lambda config: config.get_folder("Tech"))
    text = client.get("/feeds").text
    assert "/folder?name=Tech" in text
    assert "Folders" in text
    assert client.get("/folders").headers["location"] == "/feeds"  # old link still works


def test_aging_entries_are_shaded_in_history(tmp_path):
    client, services = make_app(tmp_path)
    run = services.history.start_run("feed/1", "Example", False, datetime(2026, 11, 18, tzinfo=UTC))
    services.history.add_marks(run, [
        ("feed/1", "Example", "e1", "Old story", "2026-11-01T10:00:00+00:00", "aging older than 7 d", "marked"),
        ("feed/1", "Example", "e2", "Ad", "2026-11-18T10:00:00+00:00", "rule (global): title contains 'ad'", "marked"),
    ])
    text = client.get(f"/history?run={run}").text
    assert text.count('class="by-aging"') == 1  # only the aging row is shaded


def test_preview_with_invalid_regex_is_rejected_before_fetching(tmp_path):
    client, services = make_app(tmp_path)
    response = client.post("/scope/preview", data={"scope": "global", "field": "title", "op": "regex", "value": "(bad"})
    assert response.status_code == 303
    assert "error=" in response.headers["location"]


def test_invalid_regex_is_shown_as_error(tmp_path):
    client, services = make_app(tmp_path)
    response = client.post("/scope", data={
        "scope": "global", "action": "add_rule", "field": "title", "op": "regex", "value": "(bad",
    })
    assert "error=" in response.headers["location"]
    assert services.store.config.global_rules == []


def test_dry_run_switch_turns_marking_on_and_off(tmp_path):
    client, services = make_app(tmp_path)
    assert services.store.config.dry_run is True
    client.post("/dry-run", data={"state": "off"})
    assert services.store.config.dry_run is False
    assert "OFF" in client.get("/").text
    client.post("/dry-run", data={"state": "on"})
    assert services.store.config.dry_run is True


def test_settings_save(tmp_path):
    client, services = make_app(tmp_path)
    client.post("/settings", data={"days": "3", "history_limit_mb": "50", "timezone": "UTC", "aging_enabled": "on"})
    assert services.store.config.aging.days == 3
    assert services.store.config.history_limit_mb == 50
    assert services.store.config.dry_run is False  # checkbox not sent


def test_working_token_is_checked_and_saved(tmp_path):
    client, services = make_app(tmp_path)
    response = client.post("/login", data={"authorization": "abc123", "cookie": "a=1"})
    assert "msg=" in response.headers["location"]
    assert services.session.headers() == {"Authorization": "Bearer abc123", "Cookie": "a=1"}
    assert "abc123" not in client.get("/login").text


def test_rejected_token_is_not_saved(tmp_path):
    client, services = make_app(tmp_path, client_factory=RejectingClient)
    response = client.post("/login", data={"authorization": "bad"})
    assert "error=" in response.headers["location"]
    assert services.session.headers() == {}


def test_network_error_is_not_saved(tmp_path):
    class Broken(OkClient):
        def profile(self):
            raise httpx.ConnectError("no route")

    client, services = make_app(tmp_path, client_factory=Broken)
    response = client.post("/login", data={"authorization": "tok"})
    assert "error=" in response.headers["location"]
    assert services.session.headers() == {}


class FeedClient(OkClient):
    def subscriptions(self):
        return [{"id": "feed/1", "title": "Example", "categories": [{"label": "Tech"}]}]

    def unread_counts(self):
        return {"unreadcounts": []}

    def stream_entries(self, stream_id):
        now = int(datetime.now(UTC).timestamp() * 1000)
        return [
            {"id": "e1", "title": "Sponsored offer", "published": now, "alternate": [{"href": "https://example.com/1"}]},
            {"id": "e2", "title": "Hello", "published": now, "alternate": []},
        ]


def test_inspect_shows_decision_for_each_entry(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    services.store.config.global_rules.append(Rule(value="sponsored"))
    text = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert "Sponsored offer" in text
    assert "rule (global): title contains" in text
    assert "will mark" in text
    assert "keep" in text
    assert 'value="folder|Tech"' in text
    assert '<option value="feed|feed/1" selected>this feed</option>' in text  # default rule scope follows the source
    assert 'data-filter="select"' in text  # Decision and Reason are drop-down filters


def test_inspect_shows_all_rule_fields_and_collapses_long_content(tmp_path):
    class RichClient(FeedClient):
        def stream_entries(self, stream_id):
            now = int(datetime.now(UTC).timestamp() * 1000)
            long_text = "<p>" + "word " * 100 + "</p>"
            return [{"id": "e1", "title": "Rich", "author": "Jan Kowalski", "published": now,
                     "alternate": [{"href": "https://example.com/rich"}],
                     "summary": {"content": long_text}}]

    client, services = make_app(tmp_path, client_factory=RichClient)
    services.session.save("abc123", "")
    text = client.get("/inspect?source=feed%7Cfeed%2F1").text
    for header in ("Author", "URL", "Content"):
        assert f"<th>{header}</th>" in text
    assert "Jan Kowalski" in text
    assert "https://example.com/rich" in text
    assert "<p>" not in text  # HTML in the summary is shown as plain text
    assert "<details><summary>" in text  # long content starts collapsed
    assert "word word" in text


def test_inspect_folder_merges_its_feeds_newest_first(tmp_path):
    class FolderClient(OkClient):
        def subscriptions(self):
            return [
                {"id": "feed/1", "title": "Alpha", "categories": [{"label": "Tech"}]},
                {"id": "feed/2", "title": "Beta", "categories": [{"label": "Tech"}]},
                {"id": "feed/3", "title": "Gamma", "categories": [{"label": "News"}]},
            ]

        def unread_counts(self):
            return {"unreadcounts": []}

        def stream_entries(self, stream_id):
            hour = int(datetime.now(UTC).timestamp() * 1000) - 3_600_000
            titles = {"feed/1": "Tech one", "feed/2": "Tech two", "feed/3": "News item"}
            published = {"feed/1": hour, "feed/2": hour + 60_000, "feed/3": hour}
            return [{"id": stream_id, "title": titles[stream_id], "published": published[stream_id], "alternate": []}]

    client, services = make_app(tmp_path, client_factory=FolderClient)
    services.session.save("abc123", "")
    text = client.get("/inspect?source=folder%7CTech").text
    assert "Folder: Tech" in text
    assert "Tech one" in text and "Tech two" in text
    assert "News item" not in text
    assert text.index("Tech two") < text.index("Tech one")  # newest first across feeds
    assert '<option value="folder|Tech" selected>' in text
    assert '<option value="folder|Tech" selected>folder: Tech</option>' in text  # default rule scope follows the source

    response = client.post("/inspect/rule", data={
        "source": "folder|Tech", "page": "1", "scope": "feed|feed/2", "action": "add_rule",
        "field": "title", "op": "contains", "value": "Tech two",
    })
    assert response.headers["location"].startswith("/inspect?source=folder%7CTech&msg=")
    assert services.store.config.find_feed("feed/2").rules[0].value == "Tech two"


def test_inspect_rule_adds_rule_and_returns_to_inspect(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    response = client.post("/inspect/rule", data={
        "source": "feed|feed/1", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "contains", "value": "Sponsored offer",
    })
    assert response.status_code == 303
    assert response.headers["location"].startswith("/inspect?source=feed%7Cfeed%2F1&msg=")
    feed = services.store.config.find_feed("feed/1")
    assert feed.override is True
    assert feed.rules[0].value == "Sponsored offer"


def test_inspect_rule_returns_to_the_same_page(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    response = client.post("/inspect/rule", data={
        "source": "feed|feed/1", "page": "3", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "contains", "value": "Hello",
    })
    assert response.headers["location"].startswith("/inspect?source=feed%7Cfeed%2F1&page=3&msg=")


class ManyClient(FeedClient):
    def stream_entries(self, stream_id):
        now = int(datetime.now(UTC).timestamp() * 1000)
        return [{"id": f"e{n}", "title": f"Story {n}", "published": now, "alternate": []} for n in range(45)]


def test_inspect_pages_twenty_entries_at_a_time(tmp_path):
    client, services = make_app(tmp_path, client_factory=ManyClient)
    services.session.save("abc123", "")
    first = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert "1-20 of 45 unread entries" in first
    assert "Story 0<" in first and "Story 19<" in first and "Story 20<" not in first
    assert "Next</a>" in first and "Previous</a>" not in first
    last = client.get("/inspect?source=feed%7Cfeed%2F1&page=3").text
    assert "41-45 of 45 unread entries" in last
    assert "Story 44<" in last and "Story 40<" in last
    assert "Next</a>" not in last and "Previous</a>" in last
    assert "41-45 of 45 unread entries" in client.get("/inspect?source=feed%7Cfeed%2F1&page=99").text  # clamped to the last page
    client, services = make_app(tmp_path, client_factory=FeedClient)
    client.post("/inspect/rule", data={
        "source": "feed|feed/1", "scope": "global", "action": "add_whitelist",
        "field": "title", "op": "contains", "value": "Hello",
    })
    assert services.store.config.global_whitelist[0].value == "Hello"


def test_decision_column_updates_after_adding_a_rule(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    before = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert "will mark" not in before
    client.post("/inspect/rule", data={
        "source": "feed|feed/1", "page": "1", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "contains", "value": "sponsored",
    })
    after = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert "will mark" in after
    assert "rule (feed): title contains" in after


def test_folder_rule_survives_adding_an_own_rule_to_a_feed(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    client.post("/scope", data={"scope": "folder|Tech", "action": "add_rule", "field": "title", "op": "contains", "value": "Hello"})
    client.post("/inspect/rule", data={
        "source": "feed|feed/1", "page": "1", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "contains", "value": "sponsored",
    })
    text = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert text.count("will mark") == 2  # "Hello" (copied folder rule) and "Sponsored offer" (own rule)


def test_adding_an_existing_rule_is_not_duplicated(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    client.post("/scope", data={"scope": "global", "action": "add_rule", "field": "title", "op": "contains", "value": "TV"})
    response = client.post("/scope", data={"scope": "global", "action": "add_rule", "field": "title", "op": "contains", "value": "tv"})
    assert "msg=" in response.headers["location"] and "already" in response.headers["location"]
    assert len(services.store.config.global_rules) == 1


def test_inspect_decision_matches_a_dry_run(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    services.session.save("abc123", "")
    client.post("/inspect/rule", data={
        "source": "feed|feed/1", "page": "1", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "contains", "value": "sponsored",
    })
    services.engine.run(dry_run=True)
    marked = {title for _, _, title, _, _, _ in services.history.recent_marks("feed/1", limit=100)}
    text = client.get("/inspect?source=feed%7Cfeed%2F1").text
    assert marked == {"Sponsored offer"}
    assert text.count("will mark") == len(marked)


def test_inspect_exception_goes_to_whitelist(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    client.post("/inspect/rule", data={
        "source": "feed|feed/1", "scope": "global", "action": "add_whitelist",
        "field": "title", "op": "contains", "value": "Hello",
    })
    assert services.store.config.global_whitelist[0].value == "Hello"


def test_inspect_invalid_regex_is_shown_as_error(tmp_path):
    client, services = make_app(tmp_path, client_factory=FeedClient)
    response = client.post("/inspect/rule", data={
        "source": "feed|feed/1", "scope": "feed|feed/1", "action": "add_rule",
        "field": "title", "op": "regex", "value": "(bad",
    })
    assert response.headers["location"].startswith("/inspect?source=feed%7Cfeed%2F1&error=")
