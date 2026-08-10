import os

import pytest

from charlie.results import ResultsStore


@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "results_test.db")
    s = ResultsStore(db_path=db_path)
    yield s
    s.close()


def test_store_and_get_recent(store):
    store.store("t1", "did thing one", "full output one", attention_level=2)
    store.store("t2", "did thing two", "full output two", attention_level=1)

    recent = store.get_recent(limit=5)

    assert [r.task_id for r in recent] == ["t2", "t1"]
    assert recent[0].summary == "did thing two"
    assert recent[0].full_result == "full output two"
    assert recent[0].attention_level == 1
    assert recent[0].seen is False
    assert recent[0].created_at


def test_get_recent_respects_limit(store):
    for i in range(3):
        store.store(f"t{i}", f"summary {i}", "result", attention_level=2)

    assert len(store.get_recent(limit=2)) == 2


def test_consume_catchup_returns_none_when_nothing_stored(store):
    assert store.consume_catchup() is None


def test_consume_catchup_ignores_below_min_level(store):
    store.store("t1", "minor thing", "result", attention_level=1)

    assert store.consume_catchup(min_level=2) is None


def test_consume_catchup_returns_caption_for_single_unseen(store):
    store.store("t1", "found the bug", "result", attention_level=2)

    caption = store.consume_catchup(min_level=2)

    assert caption == "While you were away: found the bug"


def test_consume_catchup_combines_multiple_into_one_caption(store):
    store.store("t1", "found the bug", "result", attention_level=2)
    store.store("t2", "finished the report", "result", attention_level=3)

    caption = store.consume_catchup(min_level=2)

    assert caption == "While you were away, 2 things finished: found the bug; finished the report"


def test_consume_catchup_fires_only_once(store):
    store.store("t1", "found the bug", "result", attention_level=2)

    first = store.consume_catchup(min_level=2)
    second = store.consume_catchup(min_level=2)

    assert first == "While you were away: found the bug"
    assert second is None


def test_consume_catchup_does_not_resurface_after_new_seen_result_added(store):
    store.store("t1", "found the bug", "result", attention_level=2)
    store.consume_catchup(min_level=2)
    store.store("t2", "finished the report", "result", attention_level=2)

    caption = store.consume_catchup(min_level=2)

    assert caption == "While you were away: finished the report"


def test_results_survive_reopen(tmp_path):
    db_path = str(tmp_path / "results_persist.db")
    s1 = ResultsStore(db_path=db_path)
    s1.store("t1", "persisted thing", "result", attention_level=2)
    s1.close()

    s2 = ResultsStore(db_path=db_path)
    recent = s2.get_recent(limit=5)
    s2.close()

    assert len(recent) == 1
    assert recent[0].task_id == "t1"


def test_creates_db_parent_dir_if_missing(tmp_path):
    db_path = str(tmp_path / "nested" / "results.db")
    s = ResultsStore(db_path=db_path)
    s.store("t1", "x", "y", attention_level=2)
    s.close()
    assert os.path.exists(db_path)
