import pytest

from charlie.background_task import BackgroundTask


def test_background_task_public_event_omits_raw_failure_text() -> None:
    task = BackgroundTask(
        id="task-1",
        text="Check deployment",
        steps=["Inspect logs", "Report result"],
        current_step=1,
        status="failed",
        error="ConnectionError: api-key=secret",
    )

    assert task.to_public_event() == {
        "id": "task-1",
        "title": "Check deployment",
        "status": "failed",
        "current_step": 1,
        "total_steps": 2,
    }


def test_web_server_replays_cached_tasks_and_runtime_activity() -> None:
    from charlie import web_server

    old_tasks = web_server._background_tasks
    old_state = web_server._charlie_state
    web_server._background_tasks = {
        "task-1": {
            "id": "task-1",
            "title": "Check deployment",
            "status": "running",
            "current_step": 1,
            "total_steps": 2,
        }
    }
    web_server._charlie_state = {"state": "working", "activities": ["background_task"]}
    try:
        event = next(event for event in web_server._initial_state_events() if event["type"] == "task_snapshot")
        assert event["payload"] == {"tasks": list(web_server._background_tasks.values())}
        assert event["replay"] is True
        state_event = next(event for event in web_server._initial_state_events() if event["type"] == "charlie_state")
        assert state_event["payload"] == web_server._charlie_state
        assert state_event["replay"] is True
    finally:
        web_server._background_tasks = old_tasks
        web_server._charlie_state = old_state


def test_task_snapshot_tracks_latest_task_event() -> None:
    from charlie import web_server

    cache = {}
    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "payload": {
                "id": "task-1",
                "title": "Check deployment",
                "status": "done",
                "current_step": 2,
                "total_steps": 2,
            },
        },
    )

    assert cache == {
        "task-1": {
            "id": "task-1",
            "title": "Check deployment",
            "status": "done",
            "current_step": 2,
            "total_steps": 2,
        }
    }


def test_task_snapshot_strips_untrusted_error_text() -> None:
    from charlie import web_server

    cache = {}
    web_server._apply_background_task_event(
        cache,
        {
            "type": "background_task",
            "payload": {
                "id": "task-1",
                "title": "Check deployment",
                "status": "failed",
                "current_step": 1,
                "total_steps": 2,
                "error": "ConnectionError: api-key=secret",
            },
        },
    )

    assert cache["task-1"] == {
        "id": "task-1",
        "title": "Check deployment",
        "status": "failed",
        "current_step": 1,
        "total_steps": 2,
    }


@pytest.mark.asyncio
async def test_tasks_api_reads_runtime_event_snapshot() -> None:
    from charlie import web_server

    old_tasks = web_server._background_tasks
    web_server._background_tasks = {
        "task-1": {
            "id": "task-1",
            "title": "Check deployment",
            "status": "done",
            "current_step": 2,
            "total_steps": 2,
        }
    }
    try:
        assert await web_server.list_tasks() == {"tasks": list(web_server._background_tasks.values())}
    finally:
        web_server._background_tasks = old_tasks


@pytest.mark.asyncio
async def test_legacy_task_status_api_omits_raw_failure_text() -> None:
    from charlie import background_task, web_server

    task = BackgroundTask(
        id="task-1",
        text="Check deployment",
        steps=["Inspect logs"],
        status="failed",
        error="ConnectionError: api-key=secret",
    )
    original = background_task.get_current_task
    background_task.get_current_task = lambda: task
    try:
        assert await web_server.background_task_status() == {
            "task": {
                "id": "task-1",
                "title": "Check deployment",
                "status": "failed",
                "current_step": 0,
                "total_steps": 1,
            }
        }
    finally:
        background_task.get_current_task = original
