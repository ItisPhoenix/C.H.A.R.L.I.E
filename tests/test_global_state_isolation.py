"""Regression guard: one test's global mutations must not reach the next test.

The suite's intermittent, order-dependent failures were not one bug but two
distinct leaks of process-global state, each of which surfaced in files
unrelated to the test that caused it:

1. ``charlie.resource_locks._owners`` retained a capability lease acquired by a
   fixture that never released it. Because ``shell_execute`` declares
   ``required_leases=("terminal",)`` and ``timeout_sec=30.0``, every later
   shell call waited for a lease that was never granted and returned
   ``"Error: Tool 'shell_execute' timed out after 30.0s"``. That one leaked
   lease is what produced failures in six different files at once.
2. ``charlie.config._TRUST_ENV_RISK_WARNED`` is a deliberate warn-once latch, so
   the first test to build a risky ``Config`` consumed the only warning and any
   test asserting the warning *is* emitted lost it.

Because pytest runs everything in one process, ``tests/conftest.py`` resets both
before and after every test. These tests pin that contract from the outside:
they deliberately poison the globals and assert the *next* test cannot see the
poison, which is the property whose absence caused the flakiness. Asserting "the
global is empty" in isolation would pass on a green suite and prove nothing, so
the assertions below are split into an ordered poisoning/observing pair. Ordering
inside one file is pytest's file order and is not randomised by the plugins in
use, which is what makes the pair valid.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

# ``charlie/__init__.py`` does ``from .config import config``, so the ``config``
# attribute on the package is the Config singleton *instance*, not the module.
# Resolving through the import system is the only way to reach the module-level
# ``_TRUST_ENV_RISK_WARNED`` global this file resets and asserts on.
config_module = import_module("charlie.config")
resource_locks = import_module("charlie.resource_locks")

CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"

POISON_OWNER = "regression-poison-owner"
POISON_CAPABILITY = "terminal"


def test_zzz_a_prior_leaks_would_be_visible_without_the_reset():
    """Deliberately poison both globals. Fails loudly if the reset is removed.

    This is the poisoning half of the pair; ``test_zzz_b``/``test_zzz_d`` are the
    observing halves and are meaningless without it.
    """
    assert resource_locks.acquire(POISON_CAPABILITY, POISON_OWNER) is True
    assert resource_locks.current_owner(POISON_CAPABILITY) == POISON_OWNER

    # Latch the warn-once flag, exactly as any test that builds a risky Config does.
    config_module.Config(llm_trust_env=True, llm_key="sk-regression-key")
    assert config_module._TRUST_ENV_RISK_WARNED is True, (
        "precondition: this test must actually latch the flag for test_zzz_d to "
        "have something to observe"
    )


def test_zzz_b_the_next_test_cannot_see_that_leak():
    """The poisoned lease from the previous test must not survive into this one."""
    leaked_owner = resource_locks.current_owner(POISON_CAPABILITY)
    assert leaked_owner is None, (
        f"a capability lease leaked across a test boundary: {POISON_CAPABILITY!r} "
        f"is still owned by {leaked_owner!r}. That is what made unrelated "
        "shell_execute tests time out under a random test order."
    )
    # The ownership map must be genuinely usable, not just empty-but-fenced.
    assert resource_locks.acquire(POISON_CAPABILITY, POISON_OWNER) is True
    resource_locks.release(POISON_CAPABILITY, POISON_OWNER)


def test_zzz_c_the_next_test_cannot_see_a_leaked_takeover_fence():
    """A revocation fenced for a previous test's owner must not wedge the next one."""
    fenced = resource_locks.get_revocations()
    assert not fenced, (
        f"{POISON_CAPABILITY!r} is still fenced by a leftover takeover ({fenced}), "
        "so no holder can ever acquire it again"
    )


def test_zzz_d_a_warn_once_latch_left_set_by_a_previous_test_is_cleared():
    """``_TRUST_ENV_RISK_WARNED`` is a warn-once latch; it must reset per test."""
    assert config_module._TRUST_ENV_RISK_WARNED is False, (
        "the LLM_TRUST_ENV warn-once latch survived a test boundary, so the test "
        "asserting the warning is emitted can no longer observe it"
    )


def test_zzz_e_the_warn_once_latch_still_works_inside_the_test_that_triggers_it():
    """Resetting per test must not disable warn-once *within* a test."""
    first = config_module.Config(llm_trust_env=True, llm_key="sk-regression-key")
    assert config_module._TRUST_ENV_RISK_WARNED is True

    second = config_module.Config(llm_trust_env=True, llm_key="sk-regression-key")
    assert config_module._TRUST_ENV_RISK_WARNED is True

    # The latch only suppresses the repeated log line; the risk itself is still
    # resolved identically by both instances.
    assert first.llm_trust_env_report()["unsafe"] is True
    assert second.llm_trust_env_report()["unsafe"] is True


def test_zzz_f_the_reset_covers_every_process_global_it_claims_to_cover():
    """Pin the reset's *coverage*, so a newly added global cannot be forgotten."""
    source = CONFTEST_PATH.read_text(encoding="utf-8")

    for global_name in (
        "_owners",
        "_active_leases",
        "_lease_objects",
        "_revocations",
        "_takeover_listeners",
        "_waiters",
    ):
        assert f"resource_locks.{global_name}.clear()" in source, (
            f"resource_locks.{global_name} is process-global, but the conftest "
            "reset does not clear it"
        )

    assert "config_module._TRUST_ENV_RISK_WARNED = False" in source
    assert "@pytest.fixture(autouse=True)" in source, (
        "the reset must be autouse so no test can opt out of it by accident"
    )
