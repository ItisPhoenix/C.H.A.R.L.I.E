import pytest

from charlie.desktop import actions
from charlie.desktop.takeover import UserTakeoverDetector


@pytest.fixture(autouse=True)
def reset_halt_state():
    yield
    actions.clear_halt()


class TestUserTakeoverDetector:
    def test_session_lifecycle(self):
        detector = UserTakeoverDetector()
        assert detector.is_physical_control_active() is False

        detector.start_session("unit_test_session")
        assert detector.is_physical_control_active() is True
        status = detector.status()
        assert status.active is True
        assert status.session_owner == "unit_test_session"

        detector.end_session()
        assert detector.is_physical_control_active() is False

    def test_takeover_detection_on_tick_advance(self, monkeypatch):
        detector = UserTakeoverDetector()
        tick = [1000]

        monkeypatch.setattr(detector, "get_last_input_tick", lambda: tick[0])

        detector.start_session("unit_test")
        assert detector.check_takeover() is False

        # Simulate user physically moving the mouse 300ms later
        tick[0] = 1350
        assert detector.check_takeover() is True
        assert detector.is_physical_control_active() is False
        assert detector.status().takeover_detected is True
