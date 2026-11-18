import httpx
import pytest

from feedly_filter.client import AuthError, FeedlyClient, FeedlyError


def make_client(handler, max_retries=2):
    sleeps: list[float] = []
    client = FeedlyClient({"Authorization": "Bearer t"}, transport=httpx.MockTransport(handler),
                          max_retries=max_retries, sleep=sleeps.append)
    return client, sleeps


def test_sends_authorization_header_and_parses_json():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json=[{"id": "feed/1"}])

    client, _ = make_client(handler)
    assert client.subscriptions() == [{"id": "feed/1"}]
    assert seen["auth"] == "Bearer t"


def test_401_is_auth_error_without_retry():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"errorCode": 401})

    client, sleeps = make_client(handler)
    with pytest.raises(AuthError):
        client.profile()
    assert len(calls) == 1 and sleeps == []


def test_429_is_retried_with_backoff():
    responses = iter([httpx.Response(429), httpx.Response(200, json={"ok": True})])

    client, sleeps = make_client(lambda request: next(responses))
    assert client.profile() == {"ok": True}
    assert sleeps == [1]


def test_gives_up_after_retries():
    client, sleeps = make_client(lambda request: httpx.Response(503), max_retries=2)
    with pytest.raises(FeedlyError):
        client.profile()
    assert sleeps == [1, 2]


def test_stream_entries_follows_continuation():
    pages = iter([
        {"items": [{"id": "a"}], "continuation": "next"},
        {"items": [{"id": "b"}]},
    ])
    seen_params = []

    def handler(request):
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, json=next(pages))

    client, _ = make_client(handler)
    assert [item["id"] for item in client.stream_entries("feed/1")] == ["a", "b"]
    assert seen_params[0]["unreadOnly"] == "true"
    assert seen_params[1]["continuation"] == "next"


def test_mark_as_read_chunks_ids():
    bodies = []

    def handler(request):
        bodies.append(request.read())
        return httpx.Response(200, json={})

    client, _ = make_client(handler)
    client.mark_as_read([f"e{i}" for i in range(5)], chunk_size=2)
    assert len(bodies) == 3
    assert b'"action":"markAsRead"' in bodies[0].replace(b" ", b"")
