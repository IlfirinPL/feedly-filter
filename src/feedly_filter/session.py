"""Session (token and cookies) kept in data/session.json. Values are never logged."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def normalize_authorization(value: str) -> str:
    value = value.strip()
    if value and not value.lower().startswith(("bearer ", "oauth ")):
        return f"Bearer {value}"
    return value


class SessionStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, authorization: str, cookie: str = "") -> None:
        authorization = normalize_authorization(authorization)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".session-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"authorization": authorization, "cookie": cookie.strip()}, handle, indent=2)
        os.replace(tmp_name, self.path)

    def headers(self) -> dict[str, str]:
        data = self.load()
        headers: dict[str, str] = {}
        if data.get("authorization"):
            headers["Authorization"] = data["authorization"]
        if data.get("cookie"):
            headers["Cookie"] = data["cookie"]
        return headers

    def status(self) -> dict[str, str | bool]:
        """Status for the GUI. Contains no token or cookie values."""
        if not self.path.exists():
            return {"present": False, "has_cookie": False, "updated": ""}
        updated = datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        data = self.load()
        return {"present": bool(data.get("authorization")), "has_cookie": bool(data.get("cookie")), "updated": updated}
