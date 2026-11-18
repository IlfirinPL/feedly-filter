"""Minimal Feedly web API client. Login is handled elsewhere; this only uses a session."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = "https://cloud.feedly.com/v3"


class FeedlyError(Exception):
    pass


class AuthError(FeedlyError):
    """The session is missing or expired (HTTP 401/403)."""


REQUEST_KINDS = {
    "/profile": "profile",
    "/subscriptions": "subscriptions",
    "/categories": "categories",
    "/markers/counts": "unread_counts",
    "/streams/contents": "stream_contents",
}


def request_kind(method: str, path: str) -> str:
    """Short name of an endpoint, used for the request statistics."""
    if path == "/markers" and method == "POST":
        return "mark_as_read"
    return REQUEST_KINDS.get(path, "other")


class FeedlyClient:
    def __init__(
        self,
        headers: dict[str, str],
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        on_request: Callable[[str, int], None] | None = None,
    ):
        self._http = httpx.Client(
            headers={**headers, "Accept": "application/json"},
            timeout=30,
            transport=transport,
        )
        self._max_retries = max_retries
        self._sleep = sleep
        # Called once per HTTP attempt with (kind, status); status is 0 for network errors.
        self._on_request = on_request

    def __enter__(self) -> FeedlyClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _count(self, kind: str, status: int) -> None:
        if self._on_request is not None:
            self._on_request(kind, status)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{BASE_URL}{path}"
        kind = request_kind(method, path)
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(method, url, **kwargs)
            except httpx.HTTPError as error:
                self._count(kind, 0)
                # Network and TLS failures become FeedlyError so the GUI and scheduler handle them.
                raise FeedlyError(f"{method} {path} -> network error: {type(error).__name__}") from error
            self._count(kind, response.status_code)
            if response.status_code in (401, 403):
                raise AuthError(f"{method} {path} -> {response.status_code}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < self._max_retries:
                    self._sleep(2 ** attempt)
                    continue
            if response.status_code >= 400:
                raise FeedlyError(f"{method} {path} -> {response.status_code}")
            return response.json() if response.content else None
        raise FeedlyError(f"{method} {path} -> retries exhausted")

    def profile(self) -> dict:
        return self._request("GET", "/profile")

    def subscriptions(self) -> list[dict]:
        return self._request("GET", "/subscriptions")

    def unread_counts(self) -> dict:
        return self._request("GET", "/markers/counts")

    def stream_entries(self, stream_id: str, page_size: int = 200, max_items: int = 1000) -> list[dict]:
        """Unread entries of one stream, following continuation tokens."""
        items: list[dict] = []
        continuation: str | None = None
        while len(items) < max_items:
            params: dict[str, str | int] = {"streamId": stream_id, "count": page_size, "unreadOnly": "true"}
            if continuation:
                params["continuation"] = continuation
            data = self._request("GET", "/streams/contents", params=params)
            batch = data.get("items", [])
            items.extend(batch)
            continuation = data.get("continuation")
            if not continuation or not batch:
                break
        return items[:max_items]

    def mark_as_read(self, entry_ids: list[str], chunk_size: int = 100) -> None:
        """Mark entries as read. Request shape follows the public API docs; not yet verified on the web API."""
        for start in range(0, len(entry_ids), chunk_size):
            chunk = entry_ids[start:start + chunk_size]
            self._request("POST", "/markers", json={"action": "markAsRead", "type": "entries", "entryIds": chunk})
