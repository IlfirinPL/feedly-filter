"""Rule matching and inheritance: global -> folder -> feed."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .config import Config, FeedSettings, Rule


@dataclass(frozen=True)
class Decision:
    mark: bool
    reason: str


@dataclass(frozen=True)
class Effective:
    rules: list[tuple[Rule, str]]  # (rule, source) where source is global / folder / feed
    whitelist: list[tuple[Rule, str]]
    aging_days: int  # 0 means aging is off


def entry_fields(item: dict) -> dict[str, str]:
    alternate = item.get("alternate") or []
    url = alternate[0].get("href", "") if alternate and isinstance(alternate[0], dict) else ""
    summary = item.get("summary") or {}
    content = summary.get("content", "") if isinstance(summary, dict) else ""
    return {
        "title": item.get("title") or "",
        "content": content or "",
        "author": item.get("author") or "",  # not present in the stream sample; empty for now
        "url": url or "",
    }


def is_saved(item: dict) -> bool:
    """Saved-for-later entries are never touched. Tag shape is not verified yet."""
    categories = item.get("categories") or []
    return any(str(category.get("id", "")).endswith("/tag/global.saved")
               for category in categories if isinstance(category, dict))


def published_at(item: dict) -> datetime | None:
    milliseconds = item.get("published")
    if not isinstance(milliseconds, int):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def matches(rule: Rule, fields: dict[str, str]) -> bool:
    if not rule.enabled:
        return False
    text = fields.get(rule.field, "")
    if rule.op == "regex":
        return re.search(rule.value, text, re.IGNORECASE) is not None
    haystack, needle = text.casefold(), rule.value.casefold()
    if rule.op == "contains":
        return needle in haystack
    if rule.op == "starts_with":
        return haystack.startswith(needle)
    return haystack == needle


def effective(config: Config, feed: FeedSettings | None, folder_name: str) -> Effective:
    folder = config.find_folder(folder_name) if folder_name else None
    if feed is not None and feed.override:
        rule_owner, rule_source = feed, "feed"
    elif folder is not None:
        rule_owner, rule_source = folder, "folder"
    else:
        rule_owner, rule_source = None, ""

    rules = [(rule, "global") for rule in config.global_rules]
    whitelist = [(rule, "global") for rule in config.global_whitelist]
    if rule_owner is not None:
        rules += [(rule, rule_source) for rule in rule_owner.rules]
        whitelist += [(rule, rule_source) for rule in rule_owner.whitelist]

    # Aging inherits independently of the rules: feed -> folder -> global.
    aging_days = next(
        (days for days in (feed.aging_days if feed else None, folder.aging_days if folder else None) if days is not None),
        None,
    )
    if aging_days is None:
        aging_days = config.aging.days if config.aging.enabled else 0
    return Effective(rules=rules, whitelist=whitelist, aging_days=aging_days)


def decide(item: dict, effective_settings: Effective, now: datetime) -> Decision:
    if is_saved(item):
        return Decision(False, "saved for later")
    fields = entry_fields(item)
    for rule, source in effective_settings.whitelist:
        if matches(rule, fields):
            return Decision(False, f"whitelist ({source})")
    published = published_at(item)
    days = effective_settings.aging_days
    if days > 0 and published is not None and published < now - timedelta(days=days):
        return Decision(True, f"aging older than {days} d")
    for rule, source in effective_settings.rules:
        if matches(rule, fields):
            return Decision(True, f"rule ({source}): {rule.field} {rule.op} {rule.value!r}")
    return Decision(False, "no match")
