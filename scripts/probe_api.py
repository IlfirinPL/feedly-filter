"""Read-only probe of the Feedly web API using the token saved in data/session.json.

Only GET requests are made. Status codes and JSON shapes are printed; response
contents and the token itself are never printed.
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = ROOT / "data" / "session.json"
PROBE_URLS = [
    "https://cloud.feedly.com/v3/profile",
    "https://cloud.feedly.com/v3/subscriptions",
    "https://cloud.feedly.com/v3/categories",
    "https://cloud.feedly.com/v3/markers/counts",
]


def describe_shape(value, depth: int = 0):
    """Return the structure of a JSON value without its contents."""
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        return {key: describe_shape(item, depth + 1) for key, item in list(value.items())[:25]}
    if isinstance(value, list):
        return {"list_len": len(value), "item": describe_shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def get(url: str, authorization: str) -> tuple[int, object | None]:
    request = urllib.request.Request(url, headers={"Authorization": authorization, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        return error.code, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def main() -> int:
    session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    authorization = session["authorization"]
    for url in PROBE_URLS:
        status, data = get(url, authorization)
        print(f"GET {url} -> {status}")
        if data is not None:
            print("   shape:", json.dumps(describe_shape(data), ensure_ascii=False)[:600])
    return 0


if __name__ == "__main__":
    sys.exit(main())
