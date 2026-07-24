import threading

from charlie.monitors import _MetricState, evaluate_sample, start_monitor_thread


def test_no_alert_below_threshold():
    state = _MetricState()
    assert evaluate_sample("CPU usage", 50.0, 95.0, state, now=0.0) is None
    assert state.consecutive_breaches == 0


def test_alert_after_sustained_breaches():
    state = _MetricState()
    assert evaluate_sample("CPU usage", 96.0, 95.0, state, now=0.0) is None
    assert evaluate_sample("CPU usage", 96.0, 95.0, state, now=60.0) is None
    msg = evaluate_sample("CPU usage", 96.0, 95.0, state, now=120.0)
    assert msg is not None
    assert "96" in msg


def test_breach_streak_resets_on_recovery():
    state = _MetricState()
    evaluate_sample("CPU usage", 96.0, 95.0, state, now=0.0)
    evaluate_sample("CPU usage", 96.0, 95.0, state, now=60.0)
    evaluate_sample("CPU usage", 50.0, 95.0, state, now=120.0)  # recovers, resets streak
    assert state.consecutive_breaches == 0
    # needs 3 fresh sustained breaches again, not just 1 more
    assert evaluate_sample("CPU usage", 96.0, 95.0, state, now=180.0) is None


def test_cooldown_suppresses_repeat_alert():
    state = _MetricState()
    msg = None
    for i in range(3):
        msg = evaluate_sample("CPU usage", 96.0, 95.0, state, now=i * 60.0)
    assert msg is not None  # third call alerted
    # immediately breaching again should NOT re-alert (cooldown)
    again = evaluate_sample("CPU usage", 97.0, 95.0, state, now=200.0)
    assert again is None


def test_cooldown_expires_and_realerts():
    state = _MetricState()
    for i in range(3):
        evaluate_sample("CPU usage", 96.0, 95.0, state, now=i * 60.0)
    # simulate breaches continuing every 60s for 40 minutes -- cooldown is 1800s (30min)
    # NOTE: checking only the *last* iteration's return would be wrong -- the
    # re-alert fires once cooldown lapses (~30 simulated minutes in) and then
    # the cooldown holds again, so later iterations correctly go back to None.
    # Track whether *any* iteration alerted instead.
    now = 180.0
    alerted = False
    for _ in range(40):
        now += 60.0
        msg = evaluate_sample("CPU usage", 96.0, 95.0, state, now=now)
        if msg is not None:
            alerted = True
    assert alerted  # eventually re-alerts once cooldown has passed


def test_start_monitor_thread_fires_alert():
    stop_event = threading.Event()
    calls = {"count": 0}
    alerts = []

    def fake_get_cpu_ram():
        calls["count"] += 1
        if calls["count"] >= 5:
            stop_event.set()
        return (96.0, 10.0)

    thread = start_monitor_thread(
        get_cpu_ram=fake_get_cpu_ram,
        on_alert=alerts.append,
        cpu_threshold_pct=95.0,
        ram_threshold_pct=92.0,
        poll_interval_s=0.01,
        stop_event=stop_event,
    )
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert len(alerts) == 1
    assert "CPU usage" in alerts[0]
