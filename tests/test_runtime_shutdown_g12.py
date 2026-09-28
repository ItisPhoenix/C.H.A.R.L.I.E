"""Focused runtime shutdown contracts for EventBus submissions."""

import asyncio
import threading

import pytest

import main


@pytest.mark.asyncio
async def test_eventbus_close_rejects_late_work_and_drains_started_submission():
    registry = main._EventBusSubmissionRegistry()
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = asyncio.Event()
    late_started = asyncio.Event()
    submitted = []

    async def pending_submission():
        started.set()
        await release.wait()

    async def late_submission():
        late_started.set()

    drain = None
    try:
        submitter = threading.Thread(
            target=lambda: submitted.append(registry.submit_threadsafe(pending_submission(), loop))
        )
        submitter.start()
        await asyncio.to_thread(submitter.join, 1.0)
        assert not submitter.is_alive()
        assert len(submitted) == 1
        assert submitted[0] is not None
        await asyncio.wait_for(started.wait(), 1.0)

        registry.close()
        assert registry.submit_threadsafe(late_submission(), loop) is None
        drain = asyncio.create_task(main._drain_event_bus_submissions(registry, loop=loop, timeout=0.5))
        await asyncio.sleep(0)
        assert not drain.done()
    finally:
        release.set()
    if drain is not None:
        await asyncio.wait_for(drain, 1.0)

    assert await asyncio.wrap_future(submitted[0]) is None
    assert registry.is_empty()
    assert not late_started.is_set()
