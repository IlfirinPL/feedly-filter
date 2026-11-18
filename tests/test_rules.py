from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from feedly_filter.config import Config, FeedSettings, FolderSettings, Rule
from feedly_filter.rules import Decision, decide, effective, matches

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def item(title="Hello", published_days_ago=0, categories=None, summary="", url="https://x.test/a"):
    return {
        "id": f"entry-{title}",
        "title": title,
        "published": int((NOW - timedelta(days=published_days_ago)).timestamp() * 1000),
        "alternate": [{"href": url}],
        "summary": {"content": summary},
        "categories": categories or [],
    }


def test_contains_is_case_insensitive():
    assert matches(Rule(value="sponsored"), {"title": "SPONSORED post"})
    assert not matches(Rule(value="sponsored"), {"title": "news"})


def test_starts_with_equals_and_regex():
    assert matches(Rule(op="starts_with", value="[ad]"), {"title": "[AD] buy"})
    assert matches(Rule(op="equals", value="news"), {"title": "NEWS"})
    assert matches(Rule(op="regex", value=r"^\[ad\]"), {"title": "[Ad] buy"})


def test_disabled_rule_never_matches():
    assert not matches(Rule(value="x", enabled=False), {"title": "x"})


def test_rule_field_selects_text():
    rule = Rule(field="url", value="tracker")
    assert matches(rule, {"title": "", "url": "https://tracker.example"})
    assert not matches(rule, {"title": "tracker", "url": ""})


def test_invalid_regex_is_rejected():
    with pytest.raises(ValidationError):
        Rule(op="regex", value="(unclosed")


def test_blank_value_is_rejected():
    with pytest.raises(ValidationError):
        Rule(value="   ")


def test_feed_without_override_inherits_folder_rules():
    config = Config(folders=[FolderSettings(name="Tech", rules=[Rule(value="ad")])],
                    feeds=[FeedSettings(id="f1", folder="Tech")])
    eff = effective(config, config.find_feed("f1"), "Tech")
    assert [source for _, source in eff.rules] == ["folder"]
    assert decide(item("an ad here"), eff, NOW).mark


def test_feed_override_replaces_folder_rules_without_merging():
    config = Config(
        folders=[FolderSettings(name="Tech", rules=[Rule(value="ad")])],
        feeds=[FeedSettings(id="f1", folder="Tech", override=True, rules=[Rule(value="crypto")])],
    )
    eff = effective(config, config.find_feed("f1"), "Tech")
    assert [rule.value for rule, _ in eff.rules] == ["crypto"]
    assert not decide(item("an ad here"), eff, NOW).mark


def test_global_rules_always_apply():
    config = Config(global_rules=[Rule(value="spam")],
                    folders=[FolderSettings(name="Tech")],
                    feeds=[FeedSettings(id="f1", folder="Tech", override=True)])
    eff = effective(config, config.find_feed("f1"), "Tech")
    assert decide(item("spam offer"), eff, NOW).mark


def test_whitelist_beats_rule_and_aging():
    config = Config(
        aging={"enabled": True, "days": 7},
        global_rules=[Rule(value="weekly")],
        global_whitelist=[Rule(value="keep")],
    )
    eff = effective(config, None, "")
    assert not decide(item("keep this weekly", published_days_ago=30), eff, NOW).mark


def test_aging_uses_publication_date_and_feed_overrides_global():
    config = Config(aging={"enabled": True, "days": 7},
                    feeds=[FeedSettings(id="f1", aging_days=2)])
    old = item(published_days_ago=3)
    assert decide(old, effective(config, config.find_feed("f1"), ""), NOW).mark
    assert not decide(old, effective(config, None, ""), NOW).mark


def test_aging_off_when_disabled_globally_and_zero_on_feed():
    config = Config(aging={"enabled": False, "days": 7})
    assert effective(config, None, "").aging_days == 0
    config.feeds.append(FeedSettings(id="f1", aging_days=0))
    assert not decide(item(published_days_ago=100), effective(config, config.find_feed("f1"), ""), NOW).mark


def test_saved_entries_are_never_marked():
    config = Config(aging={"enabled": True, "days": 1}, global_rules=[Rule(value="hello")])
    saved = item(published_days_ago=30, categories=[{"id": "user/x/tag/global.saved"}])
    decision = decide(saved, effective(config, None, ""), NOW)
    assert decision == Decision(False, "saved for later")


def test_no_match_keeps_entry_unread():
    config = Config(global_rules=[Rule(value="nomatch")])
    assert not decide(item("fresh news"), effective(config, None, ""), NOW).mark
