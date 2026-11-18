from datetime import UTC, datetime

from feedly_filter.history import History

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def test_prune_removes_oldest_runs_until_under_limit(tmp_path):
    history = History(tmp_path / "history.db")
    for index in range(3000):
        run_id = history.start_run(f"feed/{index % 3}", "name " * 20, False, NOW)
        history.add_marks(run_id, [(f"feed/{index}", "name", f"entry-{index}", "t" * 200, NOW.isoformat(),
                                    "rule", "marked")] * 5)
        history.finish_run(run_id, 5, 5, "", NOW)
    before = history.size_bytes()
    limit = before // 2
    removed = history.prune(limit)
    assert removed > 0
    assert history.size_bytes() <= limit
    remaining = history.recent_runs(10000)
    assert remaining and remaining[0][0] == 3000  # newest run kept
    history.close()


def test_prune_does_nothing_under_limit(tmp_path):
    history = History(tmp_path / "history.db")
    run_id = history.start_run("feed/1", "a", True, NOW)
    history.finish_run(run_id, 1, 0, "", NOW)
    assert history.prune(10 * 1024 * 1024) == 0
    assert len(history.recent_runs()) == 1
    history.close()


def test_marks_can_be_filtered_by_run(tmp_path):
    history = History(tmp_path / "history.db")
    first = history.start_run("feed/1", "a", False, NOW)
    second = history.start_run("feed/1", "a", False, NOW)
    history.add_marks(first, [("feed/1", "a", "e1", "one", NOW.isoformat(), "rule (global): x", "marked")])
    history.add_marks(second, [("feed/1", "a", "e2", "two", NOW.isoformat(), "aging", "marked")])
    rows = history.recent_marks(run_id=first)
    assert [row[1] for row in rows] == ["e1"]
    assert rows[0][4] == "rule (global): x"
    history.close()


def test_last_run_round_trip(tmp_path):
    history = History(tmp_path / "history.db")
    history.set_last_run("feed/1", NOW)
    assert history.last_runs() == {"feed/1": NOW}
    history.close()
