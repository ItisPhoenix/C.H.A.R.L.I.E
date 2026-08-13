import pytest

from charlie.telegram_bot import is_authorized, parse_callback_data, should_relay_approval


@pytest.mark.parametrize(
    ("user_id", "allowed", "expected"),
    [(42, 42, True), (7, 42, False), (None, 42, False)],
)
def test_telegram_owner_gate(user_id, allowed, expected):
    assert is_authorized(user_id, allowed) is expected


def test_telegram_approval_relay_requires_live_owner_channel():
    assert should_relay_approval(True, 42) is True
    assert should_relay_approval(False, 42) is False
    assert should_relay_approval(True, 0) is False


def test_telegram_callback_data_is_strictly_parsed():
    assert parse_callback_data("approve:req-1") == ("req-1", True)
    assert parse_callback_data("decline:req-1") == ("req-1", False)
    assert parse_callback_data("approve:") is None
    assert parse_callback_data("other:req-1") is None
