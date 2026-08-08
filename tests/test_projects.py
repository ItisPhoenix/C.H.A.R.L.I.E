import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from charlie.projects import MAX_CHARS, Projects, slugify


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as d:
        yield Projects(str(Path(d) / "projects"))


def test_slugify():
    assert slugify("Charlie Dev") == "charlie-dev"
    assert slugify("  Job Search!! ") == "job-search"
    with pytest.raises(ValueError):
        slugify("   ")


def test_create_switch_add_list_round_trip(store):
    slug = store.create("Charlie Dev")
    assert slug == "charlie-dev"
    assert store.list() == ["charlie-dev"]
    assert store.get_active() is None

    store.set_active(slug)
    assert store.get_active() == slug

    store.add_entry(slug, "prefers TDD")
    assert store.read_entries(slug) == ["prefers TDD"]


def test_create_duplicate_rejected(store):
    store.create("Charlie Dev")
    with pytest.raises(ValueError):
        store.create("Charlie Dev")


def test_switch_to_unknown_project_rejected(store):
    with pytest.raises(ValueError):
        store.set_active("does-not-exist")


def test_switch_to_none_clears_active(store):
    slug = store.create("Charlie Dev")
    store.set_active(slug)
    store.set_active(None)
    assert store.get_active() is None


def test_add_entry_requires_existing_project(store):
    with pytest.raises(ValueError):
        store.add_entry("nope", "some fact")


def test_add_entry_rejects_empty_text(store):
    slug = store.create("Charlie Dev")
    with pytest.raises(ValueError):
        store.add_entry(slug, "   ")


def test_add_entry_cap_enforced(store):
    slug = store.create("Charlie Dev")
    store.add_entry(slug, "x" * (MAX_CHARS - 10))
    with pytest.raises(ValueError):
        store.add_entry(slug, "y" * 20)


def test_no_active_project_is_none_by_default(store):
    store.create("Charlie Dev")
    assert store.get_active() is None
