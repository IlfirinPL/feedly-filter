"""Configuration model and YAML store for data/config.yaml."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeVar
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

T = TypeVar("T")


class Rule(BaseModel):
    field: Literal["title", "content", "author", "url"] = "title"
    op: Literal["contains", "starts_with", "equals", "regex"] = "contains"
    value: str
    enabled: bool = True
    comment: str = ""

    @field_validator("value")
    @classmethod
    def value_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value

    @model_validator(mode="after")
    def regex_compiles(self) -> Rule:
        if self.op == "regex":
            try:
                re.compile(self.value)
            except re.error as error:
                raise ValueError(f"invalid regex: {error}") from error
        return self


class Scope(BaseModel):
    """Rules and aging that can be set on a folder or a feed."""

    rules: list[Rule] = Field(default_factory=list)
    whitelist: list[Rule] = Field(default_factory=list)
    aging_days: int | None = Field(default=None, ge=0)


class FolderSettings(Scope):
    name: str


class FeedSettings(Scope):
    id: str
    name: str = ""
    folder: str = ""
    # None: use the default interval from Config.
    interval_minutes: int | None = Field(default=None, ge=5)
    # False: rules and whitelist are inherited from the folder.
    # True: own rules replace the folder rules (they are not merged).
    override: bool = False


class AgingSettings(BaseModel):
    enabled: bool = True
    days: int = Field(default=7, ge=0)


class Config(BaseModel):
    version: int = 1
    timezone: str = "Europe/Warsaw"
    dry_run: bool = True
    history_limit_mb: int = Field(default=100, ge=1)
    # Scheduled runs happen only between these hours (local time, end is exclusive).
    active_from: int = Field(default=7, ge=0, le=23)
    active_until: int = Field(default=22, ge=1, le=24)
    default_interval_minutes: int = Field(default=60, ge=5)
    aging: AgingSettings = Field(default_factory=AgingSettings)
    global_rules: list[Rule] = Field(default_factory=list)
    global_whitelist: list[Rule] = Field(default_factory=list)
    folders: list[FolderSettings] = Field(default_factory=list)
    feeds: list[FeedSettings] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_hours_and_timezone(self) -> Config:
        if self.active_from >= self.active_until:
            raise ValueError("active_from must be earlier than active_until")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError) as error:
            raise ValueError(f"unknown timezone {self.timezone!r}") from error
        return self

    def is_active_hour(self, now: datetime) -> bool:
        local = now.astimezone(ZoneInfo(self.timezone))
        return self.active_from <= local.hour < self.active_until

    def interval_for(self, feed_id: str) -> int:
        feed = self.find_feed(feed_id)
        if feed is not None and feed.interval_minutes is not None:
            return feed.interval_minutes
        return self.default_interval_minutes

    def find_folder(self, name: str) -> FolderSettings | None:
        return next((folder for folder in self.folders if folder.name == name), None)

    def get_folder(self, name: str) -> FolderSettings:
        folder = self.find_folder(name)
        if folder is None:
            folder = FolderSettings(name=name)
            self.folders.append(folder)
        return folder

    def find_feed(self, feed_id: str) -> FeedSettings | None:
        return next((feed for feed in self.feeds if feed.id == feed_id), None)

    def get_feed(self, feed_id: str, name: str = "", folder: str = "") -> FeedSettings:
        feed = self.find_feed(feed_id)
        if feed is None:
            feed = FeedSettings(id=feed_id, name=name, folder=folder)
            self.feeds.append(feed)
        return feed


class ConfigStore:
    """Thread-safe access to config.yaml. Writes are atomic."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._mtime: float | None = None
        if path.exists():
            self.config = self._read()
        else:
            self.config = Config()
            self.save()

    def _read(self) -> Config:
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        self._mtime = self.path.stat().st_mtime
        return Config.model_validate(data)

    def reload(self) -> None:
        """Pick up manual edits of config.yaml made outside the GUI."""
        with self._lock:
            if self.path.exists() and self.path.stat().st_mtime != self._mtime:
                self.config = self._read()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            text = yaml.safe_dump(self.config.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
            fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".config-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp_name, self.path)
            self._mtime = self.path.stat().st_mtime

    def update(self, change: Callable[[Config], T]) -> T:
        with self._lock:
            self.reload()
            result = change(self.config)
            self.save()
            return result
