from charlie.stopwatch_service import Stopwatch


def test_stopwatch_start_stop_resume_and_reset(monkeypatch):
    ticks = iter([10.0, 13.5, 20.0, 21.25])
    monkeypatch.setattr("charlie.stopwatch_service.time.monotonic", lambda: next(ticks))
    stopwatch = Stopwatch()

    assert stopwatch.start() == 0.0
    assert stopwatch.stop() == 3.5
    assert stopwatch.start() == 3.5
    assert stopwatch.stop() == 4.75
    assert stopwatch.reset() == 0.0
    assert stopwatch.elapsed_seconds() == 0.0
