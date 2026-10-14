import ast
import asyncio
import json
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from charlie.core import Brain


def progress_loop(speak):
    module = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_speak_turn_work_progress"
    )
    namespace = {"asyncio": asyncio, "json": json, "logger": logging.getLogger(__name__), "_safe_speak": speak}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace[function.name]


def research_summary(report):
    module = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_spoken_research_summary"
    )
    namespace = {"re": __import__("re")}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace[function.name](report)


@pytest.mark.asyncio
async def test_spoken_update_uses_question_and_observed_state_without_consuming_answer_image():
    brain = object.__new__(Brain)
    brain.config = SimpleNamespace(llm_model="configured-model", llm_reasoning_enabled=False)
    brain._chat_generation = 1
    brain._pending_vision_image_url = "pending-answer-image"
    brain._stream_completion = AsyncMock(
        return_value=("I'm checking the retry documentation against your requirements.", [])
    )
    state = {"stage": "reading", "message": "Reading retry documentation", "current": 2, "total": 4}
    text = await brain.spoken_work_update("How does this API retry requests?", state)
    assert text == "I'm checking the retry documentation against your requirements."
    payload = brain._stream_completion.call_args.args[0]
    prompt = payload["messages"][0]["content"]
    assert "How does this API retry requests?" in prompt
    assert "Reading retry documentation" in prompt
    assert payload["model"] == "configured-model"
    assert "tools" not in payload
    assert brain._pending_vision_image_url == "pending-answer-image"


@pytest.mark.asyncio
async def test_update_error_or_invented_count_has_no_canned_fallback():
    brain = object.__new__(Brain)
    brain.config = SimpleNamespace(llm_model="configured-model")
    brain._chat_generation = 1
    brain._stream_completion = AsyncMock(side_effect=TimeoutError)
    assert await brain.spoken_work_update("Question", {"stage": "reading"}) == ""
    brain._stream_completion = AsyncMock(return_value=("I verified 99 sources.", []))
    assert await brain.spoken_work_update("Question", {"stage": "reading", "current": 2}) == ""


@pytest.mark.asyncio
async def test_model_identifier_from_question_is_allowed_in_update():
    brain = object.__new__(Brain)
    brain.config = SimpleNamespace(llm_model="configured-model")
    brain._chat_generation = 1
    brain._stream_completion = AsyncMock(return_value=("I'm checking the official details for Opus 5.5.", []))
    assert await brain.spoken_work_update("Tell me about Opus 5.5", {"stage": "reading"}) == (
        "I'm checking the official details for Opus 5.5."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["web", "telegram", "console"])
async def test_non_voice_turn_neither_speaks_nor_calls_progress_model(channel):
    brain = SimpleNamespace(spoken_work_update=AsyncMock())
    loop = progress_loop(lambda *args, **kwargs: pytest.fail("speech crossed channels"))
    await loop(brain, None, SimpleNamespace(channel=channel), lambda: {}, lambda: True, initial_delay=0, interval=0)
    brain.spoken_work_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_progress_delay_is_five_seconds():
    loop = progress_loop(lambda *args, **kwargs: None)
    assert inspect.signature(loop).parameters["initial_delay"].default == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["planning", "searching", "found", "reading", "answer_generation", "action_in_progress"]
)
async def test_confirmed_progress_message_is_spoken_without_generating_an_update(stage):
    brain = SimpleNamespace(spoken_work_update=AsyncMock())
    spoken = []
    checks = 0

    def is_current():
        nonlocal checks
        checks += 1
        return checks <= 2

    loop = progress_loop(lambda _voice, *args, **kwargs: spoken.append(args))
    await loop(
        brain,
        SimpleNamespace(is_speaking=asyncio.Event()),
        SimpleNamespace(input="Question", channel="voice", turn_id="t", session_id="s"),
        lambda: {
            "stage": stage,
            "message": "Reading the selected sources.",
            "query": "Question",
            "source_acquisition_finished": stage == "answer_generation",
            "operation": "stopwatch_control" if stage == "action_in_progress" else None,
        },
        is_current,
        initial_delay=0,
        interval=0,
    )
    assert spoken == [("Reading the selected sources.", "neutral", "work-progress")]
    brain.spoken_work_update.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state", [
        {"stage": "answer_generation", "source_acquisition_finished": True},
        {"stage": "action_in_progress", "operation": "stopwatch_control"},
    ]
)
async def test_synthesis_and_action_stages_can_use_grounded_generated_updates(state):
    brain = SimpleNamespace(spoken_work_update=AsyncMock(return_value="I’m preparing the result."))
    spoken = []
    checks = 0

    def is_current():
        nonlocal checks
        checks += 1
        return checks <= 2

    await progress_loop(lambda _voice, *args, **kwargs: spoken.append(args))(
        brain,
        SimpleNamespace(is_speaking=asyncio.Event()),
        SimpleNamespace(input="Question", channel="voice", turn_id="t", session_id="s"),
        lambda: state,
        is_current,
        initial_delay=0,
        interval=0,
    )
    brain.spoken_work_update.assert_awaited_once_with("Question", state)
    assert spoken == [("I’m preparing the result.", "neutral", "work-progress")]


@pytest.mark.asyncio
async def test_progress_is_not_spoken_after_turn_ends_during_generation():
    current = True
    spoken = []

    async def update(question, state):
        nonlocal current
        current = False
        return "A question-specific update."

    loop = progress_loop(lambda _voice, *args, **kwargs: spoken.append(args))
    voice = SimpleNamespace(is_speaking=asyncio.Event())
    request = SimpleNamespace(input="Question", channel="voice", turn_id="t", session_id="s")
    await loop(
        SimpleNamespace(spoken_work_update=update),
        voice,
        request,
        lambda: {"stage": "reading", "query": "Question"},
        lambda: current,
        initial_delay=0,
        interval=0,
    )
    assert spoken == []


@pytest.mark.asyncio
async def test_generated_update_is_not_spoken_after_work_stage_changes():
    state = {"stage": "searching", "query": "Question"}
    spoken = []

    async def update(question, observed_state):
        state.update(stage="reading", message="Reading selected sources")
        return "I found current documentation and am checking it."

    loop = progress_loop(lambda *args, **kwargs: spoken.append(args))
    checks = 0

    def is_current():
        nonlocal checks
        checks += 1
        return checks <= 2

    await loop(
        SimpleNamespace(spoken_work_update=update),
        SimpleNamespace(is_speaking=asyncio.Event()),
        SimpleNamespace(input="Question", channel="voice", turn_id="t", session_id="s"),
        lambda: state,
        is_current,
        initial_delay=0,
        interval=0,
    )
    assert spoken == []


@pytest.mark.asyncio
async def test_generated_update_survives_counter_change_within_same_stage():
    state = {"stage": "searching", "query": "Question", "current": 1}
    spoken = []

    async def update(question, observed_state):
        state["current"] = 2
        return "I found current documentation and am checking it."

    checks = 0

    def is_current():
        nonlocal checks
        checks += 1
        return checks <= 2

    await progress_loop(lambda _voice, *args, **kwargs: spoken.append(args))(
        SimpleNamespace(spoken_work_update=update),
        SimpleNamespace(is_speaking=asyncio.Event()),
        SimpleNamespace(input="Question", channel="voice", turn_id="t", session_id="s"),
        lambda: state,
        is_current,
        initial_delay=0,
        interval=0,
    )
    assert spoken == [("I found current documentation and am checking it.", "neutral", "work-progress")]


@pytest.mark.asyncio
async def test_unchanged_state_does_not_repeat_updates_and_cancellation_stops_them():
    spoken = []
    completed = asyncio.Event()

    def speak(*args, **kwargs):
        spoken.append(args)
        completed.set()

    brain = SimpleNamespace(spoken_work_update=AsyncMock(return_value="I'm checking the request limits."))
    request = SimpleNamespace(input="Compare request limits", channel="voice", turn_id="t", session_id="s")
    loop = progress_loop(speak)
    task = asyncio.create_task(
        loop(
            brain,
            SimpleNamespace(is_speaking=asyncio.Event()),
            request,
            lambda: {"stage": "reading", "query": "Compare request limits"},
            lambda: True,
            initial_delay=0,
            interval=0,
        )
    )
    await asyncio.wait_for(completed.wait(), 1)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert len(spoken) == 1
    assert brain.spoken_work_update.await_count == 1


@pytest.mark.asyncio
async def test_small_action_does_not_generate_a_spoken_progress_update():
    brain = SimpleNamespace(spoken_work_update=AsyncMock())
    loop = progress_loop(lambda *args, **kwargs: pytest.fail("small action reached speech"))
    await loop(
        brain,
        SimpleNamespace(is_speaking=asyncio.Event()),
        SimpleNamespace(input="Start stopwatch", channel="voice", turn_id="t", session_id="s"),
        lambda: {"stage": "action_in_progress", "operation": "stopwatch_control"},
        lambda: False,
        initial_delay=0,
        interval=0,
    )
    brain.spoken_work_update.assert_not_awaited()


@pytest.mark.parametrize(
    "section",
    [
        "Coverage notes: Rate-limit behavior remains unresolved.",
        "Bibliography\n[S1] https://example.test/docs",
        "Sources: [S1] https://example.test/docs",
    ],
)
def test_research_speech_uses_brief_answer_and_drops_report_sections(section):
    report = SimpleNamespace(
        answer=(
            "The API retries failed requests up to three times [S1]. It then uses exponential backoff.\n\n"
            f"{section}"
        )
    )
    summary = research_summary(report)
    assert summary == "The API retries failed requests up to three times. It then uses exponential backoff."
    assert "Coverage" not in summary
    assert "Sources" not in summary
    assert "https://" not in summary


def test_research_speech_stays_short_while_the_report_can_be_detailed():
    first = (
        "Urban tree canopies lower surface temperatures by shading streets, homes, "
        "sidewalks, and parking lots, limiting heat stored in the built environment."
    )
    second = (
        "Trees also cool nearby air through evapotranspiration, but benefits vary with "
        "humidity, wind, species, soil moisture, canopy coverage, and time of day."
    )
    report = SimpleNamespace(answer=" ".join((first, second, "More detail stays in the dashboard report.")))

    summary = research_summary(report)

    assert summary == first
    assert len(summary.split()) <= 30
    assert report.answer.startswith(f"{first} {second}")
