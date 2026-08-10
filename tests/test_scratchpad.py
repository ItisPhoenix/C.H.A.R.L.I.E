import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from charlie.scratchpad import MAX_CHARS, Scratchpad


@pytest.fixture
def pad():
    with tempfile.TemporaryDirectory() as d:
        sp = Scratchpad(str(Path(d) / "scratchpad.db"))
        yield sp
        sp.close()


def test_add_list_round_trip(pad):
    pad.add("first note")
    pad.add("second note")
    entries = pad.list()
    assert [(i, text) for i, text, _ in entries] == [(1, "first note"), (2, "second note")]


def test_edit_by_index(pad):
    pad.add("first note")
    pad.add("second note")
    assert pad.edit(1, "first note edited") is True
    entries = pad.list()
    assert entries[0][1] == "first note edited"
    assert entries[1][1] == "second note"


def test_edit_out_of_range_returns_false(pad):
    pad.add("only note")
    assert pad.edit(5, "nope") is False


def test_delete_by_index_shifts_positions(pad):
    pad.add("a")
    pad.add("b")
    pad.add("c")
    assert pad.delete(2) is True
    entries = pad.list()
    assert [(i, text) for i, text, _ in entries] == [(1, "a"), (2, "c")]


def test_delete_out_of_range_returns_false(pad):
    pad.add("only note")
    assert pad.delete(9) is False


def test_clear_removes_all(pad):
    pad.add("a")
    pad.add("b")
    pad.clear()
    assert pad.list() == []


def test_add_rejects_empty_text(pad):
    with pytest.raises(ValueError):
        pad.add("   ")


def test_add_rejects_oversized_single_entry(pad):
    with pytest.raises(ValueError):
        pad.add("x" * (MAX_CHARS + 1))


def test_cap_trims_oldest_entries(pad):
    pad.add("x" * (MAX_CHARS - 10))
    pad.add("y" * 20)
    entries = pad.list()
    total = sum(len(text) for _, text, _ in entries)
    assert total <= MAX_CHARS
    # the newest entry must have survived the trim, the oldest was dropped
    assert entries[-1][1] == "y" * 20
    assert entries[0][1] != "x" * (MAX_CHARS - 10)


def test_index_stability_recomputed_per_call(pad):
    pad.add("a")
    pad.add("b")
    pad.add("c")
    pad.delete(1)
    entries = pad.list()
    # positional indices are recomputed, not the old sqlite row ids
    assert entries[0] == (1, "b", entries[0][2])
    assert entries[1] == (2, "c", entries[1][2])
