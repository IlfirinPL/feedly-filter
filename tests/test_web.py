import httpx
from fastapi.testclient import TestClient

from feedly_filter.__main__ import build_services
from feedly_filter.client import AuthError
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


def test_invalid_regex_is_shown_as_error(tmp_path):
    client, services = make_app(tmp_path)
    response = client.post("/scope", data={
        "scope": "global", "action": "add_rule", "field": "title", "op": "regex", "value": "(bad",
    })
    assert "error=" in response.headers["location"]
    assert services.store.config.global_rules == []


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
