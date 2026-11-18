import pytest
import yaml

from feedly_filter.config import Config, ConfigStore, FeedSettings, Rule


def test_missing_file_creates_default_with_dry_run_on(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    assert path.exists()
    assert store.config.dry_run is True
    assert store.config.aging.days == 7


def test_round_trip_keeps_rules_and_inheritance(tmp_path):
    path = tmp_path / "config.yaml"
    store = ConfigStore(path)

    def change(config: Config):
        config.global_rules.append(Rule(value="spam"))
        config.get_folder("Tech").rules.append(Rule(op="regex", value=r"^\[ad\]"))
        config.get_feed("feed/1", "Example", "Tech").override = True

    store.update(change)
    reloaded = ConfigStore(path).config
    assert reloaded.global_rules[0].value == "spam"
    assert reloaded.find_folder("Tech").rules[0].op == "regex"
    assert reloaded.find_feed("feed/1").override is True


def test_saved_file_is_plain_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    ConfigStore(path).update(lambda config: config.get_feed("feed/1"))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["feeds"][0]["id"] == "feed/1"
    assert data["dry_run"] is True


def test_manual_edit_is_picked_up_on_reload(tmp_path):
    import os

    path = tmp_path / "config.yaml"
    store = ConfigStore(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["history_limit_mb"] = 42
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    store.reload()
    assert store.config.history_limit_mb == 42


def test_out_of_range_interval_in_file_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("feeds:\n  - id: x\n    interval_minutes: 1\n", encoding="utf-8")
    with pytest.raises(Exception):
        ConfigStore(path)


def test_example_config_stays_valid():
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / "data" / "config.example.yaml"
    config = Config.model_validate(yaml.safe_load(example.read_text(encoding="utf-8")))
    assert config.dry_run is True
    assert config.feeds[0].folder == "Tech"


def test_feed_interval_has_minimum():
    with pytest.raises(Exception):
        FeedSettings(id="x", interval_minutes=1)
