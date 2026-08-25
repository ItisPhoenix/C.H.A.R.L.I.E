"""Production watcher presentation routing regressions."""

import main
from charlie.attention import AttentionLevel
from charlie.presentation import DismissPolicy, PreferredZone, PresentationKind


def test_watcher_attention_surface_is_generic_and_non_actionable():
    kind, dismiss_policy, auto_dismiss_ms, preferred_zone = main._watcher_surface_kind(AttentionLevel.ATTENTION)

    assert kind is PresentationKind.ATTENTION
    assert dismiss_policy is DismissPolicy.MANUAL
    assert auto_dismiss_ms is None
    assert preferred_zone is PreferredZone.CENTER


def test_attention_levels_keep_tool_approval_as_the_only_interrupt_owner():
    kind, dismiss_policy, auto_dismiss_ms, preferred_zone = main._watcher_surface_kind(AttentionLevel.INFORM)

    assert kind is PresentationKind.NOTIFICATION
    assert dismiss_policy is DismissPolicy.TIMED
    assert auto_dismiss_ms == 8000
    assert preferred_zone is PreferredZone.TOP_RIGHT
