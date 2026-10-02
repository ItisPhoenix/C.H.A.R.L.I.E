"""The log redactor must be a fixed point on query-string credentials.

Rule 2 (query string) rewrote ``?token=<secret>`` to ``?token=[REDACTED]``; rule 3
(assignment form) then re-matched ``token=`` and its value class stopped *before*
``]``, so it consumed ``[REDACTED`` and emitted ``[REDACTED]``, leaving the
original closing bracket behind: ``[REDACTED]]``.

The leaked bracket is a stable artifact -- re-applying the redactor never removes
it -- so any log line carrying a redacted query token is permanently malformed and
cannot be matched against a clean expectation downstream.

Evidence class: TEST/MOCK.
"""

from __future__ import annotations

import pytest

from charlie.log_redaction import contains_sensitive_secret, redact_sensitive_text


class TestTheQueryStringRedactionIsExact:
    def test_the_documented_example(self):
        assert (
            redact_sensitive_text("http://127.0.0.1:8001/?token=abc123")
            == "http://127.0.0.1:8001/?token=[REDACTED]"
        )

    def test_no_stray_bracket_survives(self):
        assert "]]" not in redact_sensitive_text("http://127.0.0.1:8001/?token=abc123")

    @pytest.mark.parametrize(
        "query",
        [
            "?token=abc123",
            "?api_key=abc123",
            "?api-key=abc123",
            "?access_token=abc123",
            "?auth_token=abc123",
            "&token=abc123",
            "?secret=abc123",
            "?password=abc123",
            "?key=abc123",
        ],
    )
    def test_every_query_rule_is_exact(self, query):
        assert redact_sensitive_text(f"http://127.0.0.1:8001/{query}") == (
            f"http://127.0.0.1:8001/{query.rsplit('=', 1)[0]}=[REDACTED]"
        )

    def test_a_query_value_that_itself_contains_a_secret_is_fully_removed(self):
        cleaned = redact_sensitive_text("http://127.0.0.1:8001/?token=sk-proj-abcdefghijklmnopqrstuvwx")

        assert "sk-proj-abcdefghijklmnopqrstuvwx" not in cleaned
        assert "]]" not in cleaned


class TestRedactionIsAFixedPoint:
    @pytest.mark.parametrize(
        "text",
        [
            "http://127.0.0.1:8001/?token=abc123",
            "?api_key=abc123&x=1",
            "api_key=abc123",
            "password: hunter2",
            "Authorization: Bearer abc.def.ghi",
            "user sk-proj-abcdefghijklmnopqrstuvwx here",
            "/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
            "aws key AKIAIOSFODNN7EXAMPLE here",
            "no secrets in this line at all",
        ],
    )
    def test_reapplying_the_redactor_changes_nothing(self, text):
        once = redact_sensitive_text(text)

        assert redact_sensitive_text(once) == once

    def test_the_documented_example_is_a_fixed_point(self):
        once = redact_sensitive_text("http://127.0.0.1:8001/?token=abc123")

        assert redact_sensitive_text(once) == "http://127.0.0.1:8001/?token=[REDACTED]"

    def test_three_applications_are_stable(self):
        text = "http://127.0.0.1:8001/?token=abc123"

        once = redact_sensitive_text(text)
        twice = redact_sensitive_text(once)

        assert redact_sensitive_text(twice) == once == twice


class TestTheAssignmentRuleIsAlsoIdempotent:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("api_key=abc123", "api_key=[REDACTED]"),
            ("api_key=[REDACTED]", "api_key=[REDACTED]"),
            ("client_secret=abc123", "client_secret=[REDACTED]"),
            ("session_token: abc123", "session_token: [REDACTED]"),
            ("private_key=abc123", "private_key=[REDACTED]"),
            ("access_token=abc123", "access_token=[REDACTED]"),
        ],
    )
    def test_already_redacted_values_are_left_alone(self, text, expected):
        assert redact_sensitive_text(text) == expected


class TestTheDetectorIsUnaffected:
    def test_the_predicate_still_detects_a_real_query_token(self):
        assert contains_sensitive_secret("http://127.0.0.1:8001/?token=abc123") is True

    @pytest.mark.parametrize(
        "text",
        [
            "http://127.0.0.1:8001/?token=abc123",
            "?api_key=abc123",
            "api_key=abc123",
            "password: hunter2",
            "Authorization: Bearer abc.def.ghi",
            "sk-proj-abcdefghijklmnopqrstuvwx",
            "AKIAIOSFODNN7EXAMPLE",
            "/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        ],
    )
    def test_sensitive_text_is_still_refused(self, text):
        assert contains_sensitive_secret(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "the weather is nice",
            "http://127.0.0.1:8001/health",
            "order 1234567890123 shipped",
            "tokenizer settings look fine",
        ],
    )
    def test_benign_text_is_still_allowed(self, text):
        assert contains_sensitive_secret(text) is False

    def test_an_already_redacted_line_is_not_a_secret(self):
        assert contains_sensitive_secret("?token=[REDACTED]") is False


class TestEmptyAndOddInput:
    def test_empty_input_is_returned_unchanged(self):
        assert redact_sensitive_text("") == ""

    def test_none_values_are_ignored_by_the_predicate(self):
        assert contains_sensitive_secret(None, "", "nothing here") is False
