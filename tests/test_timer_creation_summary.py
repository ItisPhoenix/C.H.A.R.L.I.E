from charlie.core import _verified_requested_timer_creation_reply
from charlie.turn_contracts import ResultEnvelope, ResultStatus, VerificationStatus


def _verified_timer(delay_seconds):
    return ResultEnvelope(
        request="Create timer",
        capability="calendar",
        operation="calendar.timer.create",
        status=ResultStatus.COMPLETED,
        result="Timer set.",
        verification_status=VerificationStatus.VERIFIED_SUCCESS,
        data={"structured_data": {"verified": True}},
    )


def test_multiple_timer_reply_uses_only_verified_requested_delays():
    request = "Set two reminders, one for 10 minutes and the next for 18 minutes to take medicine."
    results = [
        ({"name": "timer_create", "arguments": {"delay_seconds": 600, "text": "Take medicine"}}, _verified_timer(600)),
        ({"name": "timer_create", "arguments": {"delay_seconds": 1080, "text": "Take medicine"}}, _verified_timer(1080)),
        (
            {"name": "timer_create", "arguments": {"delay_seconds": 1080, "text": "Take medicine"}},
            ResultEnvelope(
                request="duplicate",
                capability="calendar",
                operation="calendar.timer.create",
                status=ResultStatus.BLOCKED,
                result="Repeated call suppressed.",
            ),
        ),
    ]

    assert _verified_requested_timer_creation_reply(request, results) == (
        "I set the requested reminders for 10 minutes and 18 minutes."
    )


def test_multiple_timer_reply_reports_unverified_missing_delay():
    request = "Set reminders for 10 minutes and 18 minutes."
    results = [
        ({"name": "timer_create", "arguments": {"delay_seconds": 600}}, _verified_timer(600)),
    ]

    assert _verified_requested_timer_creation_reply(request, results) == (
        "I set the reminder for 10 minutes. I couldn't verify the one for 18 minutes."
    )
