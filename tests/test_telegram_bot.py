from charlie.telegram_bot import is_authorized, parse_callback_data


class TestIsAuthorized:
    def test_matching_user_id_is_authorized(self):
        assert is_authorized(694903315, allowed_user_id=694903315) is True

    def test_other_user_id_is_not_authorized(self):
        assert is_authorized(111111111, allowed_user_id=694903315) is False

    def test_none_user_id_is_not_authorized(self):
        assert is_authorized(None, allowed_user_id=694903315) is False


class TestParseCallbackData:
    def test_approve_prefix_parses_true(self):
        assert parse_callback_data("approve:tool_abc123") == ("tool_abc123", True)

    def test_decline_prefix_parses_false(self):
        assert parse_callback_data("decline:tool_abc123") == ("tool_abc123", False)

    def test_malformed_data_returns_none(self):
        assert parse_callback_data("garbage") is None

    def test_unknown_action_returns_none(self):
        assert parse_callback_data("maybe:tool_abc123") is None
