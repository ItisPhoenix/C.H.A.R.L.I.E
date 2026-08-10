from unittest.mock import MagicMock

from charlie.attention import AttentionLevel, decide


def test_tool_call_is_silent():
    level, _ = decide({"type": "tool_call", "payload": {}})
    assert level == AttentionLevel.SILENT


def test_tool_approval_request_is_interrupt():
    level, reason = decide({"type": "tool_approval_request", "payload": {}})
    assert level == AttentionLevel.INTERRUPT
    assert reason


def test_extension_pending_is_interrupt():
    level, _ = decide({"type": "extension_pending", "payload": {}})
    assert level == AttentionLevel.INTERRUPT


def test_recovery_proposal_is_interrupt():
    level, _ = decide({"type": "recovery_proposal", "payload": {}})
    assert level == AttentionLevel.INTERRUPT


def test_alert_error_severity_is_attention():
    level, reason = decide({"type": "alert", "payload": {"severity": "error", "message": "disk full"}})
    assert level == AttentionLevel.ATTENTION
    assert reason == "disk full"


def test_alert_warning_severity_is_inform():
    level, _ = decide({"type": "alert", "payload": {"severity": "warning"}})
    assert level == AttentionLevel.INFORM


def test_alert_unknown_severity_is_passive():
    level, _ = decide({"type": "alert", "payload": {}})
    assert level == AttentionLevel.PASSIVE


def test_background_task_running_is_silent():
    level, _ = decide({"type": "background_task", "payload": {"status": "running"}})
    assert level == AttentionLevel.SILENT


def test_background_task_failed_is_attention():
    level, _ = decide({"type": "background_task", "payload": {"status": "failed"}})
    assert level == AttentionLevel.ATTENTION


def test_background_task_done_is_inform():
    level, _ = decide({"type": "background_task", "payload": {"status": "done"}})
    assert level == AttentionLevel.INFORM


def test_unmapped_event_type_defaults_silent():
    level, _ = decide({"type": "audio_level", "payload": {}})
    assert level == AttentionLevel.SILENT


def test_unknown_event_type_string_defaults_silent():
    level, _ = decide({"type": "not_a_real_event_type", "payload": {}})
    assert level == AttentionLevel.SILENT


def test_focus_mode_caps_attention_to_inform():
    ctx = MagicMock(focus_mode=True)
    level, _ = decide({"type": "alert", "payload": {"severity": "error", "message": "x"}}, ctx=ctx)
    assert level == AttentionLevel.INFORM


def test_focus_mode_does_not_cap_interrupt():
    ctx = MagicMock(focus_mode=True)
    level, _ = decide({"type": "tool_approval_request", "payload": {}}, ctx=ctx)
    assert level == AttentionLevel.INTERRUPT


def test_focus_mode_does_not_raise_already_low_level():
    ctx = MagicMock(focus_mode=True)
    level, _ = decide({"type": "tool_call", "payload": {}}, ctx=ctx)
    assert level == AttentionLevel.SILENT


def test_no_ctx_means_no_capping():
    level, _ = decide({"type": "alert", "payload": {"severity": "error", "message": "x"}}, ctx=None)
    assert level == AttentionLevel.ATTENTION


def test_cooldown_suppresses_repeat_within_window():
    cooldowns: dict = {}
    event = {"type": "alert", "payload": {"severity": "error", "message": "disk full"}}
    level1, _ = decide(event, cooldowns=cooldowns, now=100.0)
    level2, _ = decide(event, cooldowns=cooldowns, now=110.0)
    assert level1 == AttentionLevel.ATTENTION
    assert level2 == AttentionLevel.SILENT


def test_cooldown_allows_repeat_after_window():
    from charlie import attention
    cooldowns: dict = {}
    event = {"type": "alert", "payload": {"severity": "error", "message": "disk full"}}
    decide(event, cooldowns=cooldowns, now=100.0)
    level2, _ = decide(event, cooldowns=cooldowns, now=100.0 + attention._COOLDOWN_S + 1)
    assert level2 == AttentionLevel.ATTENTION


def test_cooldown_never_suppresses_interrupt():
    cooldowns: dict = {}
    event = {"type": "tool_approval_request", "payload": {}}
    decide(event, cooldowns=cooldowns, now=100.0)
    level2, _ = decide(event, cooldowns=cooldowns, now=100.1)
    assert level2 == AttentionLevel.INTERRUPT


def test_no_cooldown_dict_means_no_deduping():
    event = {"type": "alert", "payload": {"severity": "error", "message": "x"}}
    level1, _ = decide(event, now=100.0)
    level2, _ = decide(event, now=100.1)
    assert level1 == AttentionLevel.ATTENTION
    assert level2 == AttentionLevel.ATTENTION


def test_attention_level_is_ordered():
    assert (
        AttentionLevel.SILENT
        < AttentionLevel.PASSIVE
        < AttentionLevel.INFORM
        < AttentionLevel.ATTENTION
        < AttentionLevel.INTERRUPT
    )
