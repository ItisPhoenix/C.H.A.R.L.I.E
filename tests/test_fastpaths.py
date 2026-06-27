"""Tests for charlie.text_utils -- normalize_app_list and KNOWN_APPS."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from charlie.text_utils import KNOWN_APPS, normalize_app_list


def test_single_app_unchanged():
    """Single app should not get 'and' inserted."""
    result = normalize_app_list("open chrome")
    assert "and" not in result
    assert "chrome" in result


def test_multi_app_gets_and():
    """Multiple known apps should get 'and' between them."""
    result = normalize_app_list("open chrome notepad")
    assert "chrome and notepad" in result


def test_three_apps():
    """Three known apps should join with 'and'."""
    result = normalize_app_list("open chrome notepad calculator")
    assert "chrome and notepad and calculator" in result


def test_mixed_known_and_unknown():
    """Unknown words after known apps are preserved."""
    result = normalize_app_list("open chrome foo")
    # 'chrome' is known, 'foo' is not -- no normalization (needs 2+ known)
    assert "and" not in result


def test_non_matching_prefix_unchanged():
    """Text without matching prefix is unchanged."""
    result = normalize_app_list("close chrome notepad")
    assert result == "close chrome notepad"


def test_launch_prefix():
    """'launch' prefix should also trigger normalization."""
    result = normalize_app_list("launch edge firefox")
    assert "edge and firefox" in result


def test_known_apps_contains_expected():
    """KNOWN_APPS set should contain expected entries."""
    assert "chrome" in KNOWN_APPS
    assert "notepad" in KNOWN_APPS
    assert "calculator" in KNOWN_APPS
    assert len(KNOWN_APPS) >= 20


def test_empty_string():
    """Empty string returns empty string."""
    result = normalize_app_list("")
    assert result == ""


def test_no_apps_in_command():
    """Command with no app names returns unchanged."""
    result = normalize_app_list("open the door")
    assert result == "open the door"
