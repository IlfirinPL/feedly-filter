from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient

from feedly_filter.__main__ import build_services
from feedly_filter.client import FeedlyClient, FeedlyError, request_kind
from feedly_filter.history import History
from feedly_filter.web import create_app, daily_bars, outcome


def counting_client(responses):
    seen: list[tuple[str, int]] = []
    client = FeedlyClient({"Authorization": "Bearer t"}, transport=httpx.MockTransport(responses),
                          sleep=lambda seconds: None, on_request=lambda kind, status: seen.append((kind, status)))
    return client, seen


def test_request_kind_names_each_endpoint():
    assert request_kind("GET", "/profile") == "profile"
    assert request_kind("GET", "/markers/counts") == "unread_counts"
    assert request_kind("GET", "/streams/contents") == "stream_contents"
    assert request_kind("POST", "/markers") == "mark_as_read"
    assert request_kind("GET", "/something-new") == "other"


def test_every_attempt_is_counted_including_retries():
    responses = iter([httpx.Response(429), httpx.Response(200, json={"ok": True})])
    client, seen = counting_client(lambda request: next(responses))
    client.profile()
    assert seen == [("profile", 429), ("profile", 200)]


def test_network_error_is_counted_as_status_zero():
    def handler(request):
        raise httpx.ConnectError("no route")

    client, seen = counting_client(handler)
    try:
        client.profile()
    except FeedlyError:
        pass
    assert seen == [("profile", 0)]


def test_outcome_groups_statuses():
    assert outcome(200) == "2xx"
    assert outcome(404) == "4xx"
    assert outcome(429) == "5xx / 429"
    assert outcome(503) == "5xx / 429"
    assert outcome(0) == "network error"


def test_history_counts_per_kind_status_and_day(tmp_path):
    history = History(tmp_path / "history.db")
    history.record_request("profile", 200)
    history.record_request("profile", 200)
    history.record_request("mark_as_read", 429)
    stats = sorted(history.request_stats("1970-01-01"))
    assert stats == [("mark_as_read", 429, 1), ("profile", 200, 2)]
    today = datetime.now(UTC).date().isoformat()
    assert history.daily_requests("1970-01-01") == [(today, 3)]
    assert history.daily_requests((datetime.now(UTC).date() + timedelta(days=1)).isoformat()) == []
    history.close()


def test_daily_bars_scale_to_busiest_day():
    chart = daily_bars([("2026-11-16", 0), ("2026-11-17", 5), ("2026-11-18", 10)])
    heights = [bar["h"] for bar in chart["bars"]]
    assert heights[0] == 0
    assert heights[2] == 2 * heights[1]
    assert chart["grid"][0]["value"] == 10  # the top gridline is the busiest day


def test_stats_page_shows_totals_by_type_and_chart(tmp_path):
    services = build_services(tmp_path)
    services.history.record_request("stream_contents", 200)
    services.history.record_request("mark_as_read", 429)
    client = TestClient(create_app(services), follow_redirects=False)
    response = client.get("/stats?days=7")
    assert response.status_code == 200
    assert "Feed entries" in response.text
    assert "Mark as read" in response.text
    assert "<svg" in response.text
    assert "Requests in this window: <strong>2</strong>" in response.text


def test_stats_window_is_clamped(tmp_path):
    services = build_services(tmp_path)
    client = TestClient(create_app(services), follow_redirects=False)
    assert "90 days" in client.get("/stats?days=1000").text.replace("<strong>", "").replace("</strong>", "")
