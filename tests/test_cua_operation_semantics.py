"""Outcome mapping must follow operation semantics, not just payload shape.

Cua reports many browser and window results as a bare ``status="ok"`` with no action
effect. Treating that uniformly is wrong in both directions: calling it a failure
breaks working reads, and calling it success would report unverified mutations as
done. These tests pin the distinction.
"""

from __future__ import annotations

import pytest

from charlie.computer.cua_adapter import (
    NEVER_SUCCESS,
    CuaOperation,
    CuaOutcome,
    normalise,
)

# ------------------------------------------------------------------ reads


def test_read_with_content_is_confirmed():
    result = normalise(
        {"status": "ok", "text": "semantic snapshot p1 of https://example.com/"},
        operation=CuaOperation.READ,
    )
    assert result.outcome is CuaOutcome.CONFIRMED
    assert result.is_success


def test_read_with_structured_content_is_confirmed():
    result = normalise(
        {"status": "ok", "tabs": [{"tab_id": "t1", "url": "https://example.com"}]},
        operation=CuaOperation.READ,
    )
    assert result.outcome is CuaOutcome.CONFIRMED


def test_read_ok_but_empty_is_unverified():
    """An ok with nothing to read has not retrieved anything."""
    result = normalise({"status": "ok"}, operation=CuaOperation.READ)
    assert result.outcome is CuaOutcome.UNVERIFIED
    assert not result.is_success


def test_read_with_error_flag_fails():
    result = normalise(
        {"status": "ok", "is_error": True, "text": "Missing required field"},
        operation=CuaOperation.READ,
    )
    assert result.outcome is CuaOutcome.FAILED


# --------------------------------------------------------------- mutations


def test_mutation_ok_is_dispatch_only():
    result = normalise(
        {"status": "ok", "text": "dispatched"}, operation=CuaOperation.MUTATION
    )
    assert result.outcome is CuaOutcome.EXECUTED_UNVERIFIED
    assert not result.is_success, "dispatch is not a verified postcondition"
    assert result.outcome in NEVER_SUCCESS


def test_mutation_confirmed_effect_is_success():
    result = normalise(
        {"effect": "confirmed", "verification_status": "satisfied"},
        operation=CuaOperation.MUTATION,
    )
    assert result.outcome is CuaOutcome.CONFIRMED


def test_mutation_confirmed_but_unverifiable_downgrades():
    result = normalise(
        {"effect": "confirmed", "verification_status": "unknown"},
        operation=CuaOperation.MUTATION,
    )
    assert result.outcome is CuaOutcome.UNVERIFIED
    assert not result.is_success


def test_mutation_refusal_is_never_success():
    result = normalise(
        {"status": "ok", "effect": "refused"}, operation=CuaOperation.MUTATION
    )
    assert result.outcome is CuaOutcome.REFUSED
    assert result.outcome in NEVER_SUCCESS


def test_mutation_partial_is_preserved():
    result = normalise({"effect": "partial"}, operation=CuaOperation.MUTATION)
    assert result.outcome is CuaOutcome.PARTIAL
    assert result.outcome not in NEVER_SUCCESS


def test_suspected_noop_is_preserved():
    result = normalise({"effect": "suspected_noop"}, operation=CuaOperation.MUTATION)
    assert result.outcome is CuaOutcome.SUSPECTED_NOOP
    assert result.outcome in NEVER_SUCCESS


def test_unverifiable_is_preserved():
    result = normalise({"effect": "unverifiable"}, operation=CuaOperation.MUTATION)
    assert result.outcome is CuaOutcome.UNVERIFIED


# ------------------------------------------------------------------ setup


def test_setup_ok_is_confirmed():
    result = normalise(
        {"status": "ok", "prepared": True, "prepared_pid": 4321},
        operation=CuaOperation.SETUP,
    )
    assert result.outcome is CuaOutcome.CONFIRMED


# --------------------------------------------------------------- malformed


def test_empty_payload_is_unknown():
    for payload in ({}, None, [], ""):
        result = normalise(payload, operation=CuaOperation.READ)
        assert result.outcome is CuaOutcome.UNVERIFIED
        assert not result.is_success


def test_unrecognised_payload_never_infers_success():
    result = normalise(
        {"unexpected": "shape", "colour": "blue"}, operation=CuaOperation.MUTATION
    )
    assert result.outcome is CuaOutcome.FAILED
    assert not result.is_success


def test_status_refused_wins_over_ok_text():
    result = normalise(
        {"status": "refused", "text": "browser_consent_required"},
        operation=CuaOperation.READ,
    )
    assert result.outcome is CuaOutcome.REFUSED


def test_refusal_block_wins_over_ok_status():
    result = normalise(
        {
            "status": "ok",
            "refusal": {"code": "browser_consent_required", "message": "no"},
        },
        operation=CuaOperation.MUTATION,
    )
    assert result.outcome is CuaOutcome.REFUSED


def test_non_ok_status_does_not_infer_success():
    result = normalise({"status": "pending"}, operation=CuaOperation.READ)
    assert result.outcome is CuaOutcome.FAILED


@pytest.mark.parametrize("operation", list(CuaOperation))
def test_never_success_outcomes_are_never_success(operation):
    for payload in (
        {"effect": "refused"},
        {"effect": "suspected_noop"},
        {"effect": "unverifiable"},
        {"status": "ok", "is_error": True},
    ):
        assert normalise(payload, operation=operation).outcome in NEVER_SUCCESS
