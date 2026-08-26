"""Regression tests for canonical semantic presentation control."""

import asyncio
from copy import deepcopy

from charlie.events import EventSource, normalize_event
from charlie.presentation_control import PresentationController, PresentationRequest
from charlie.presentation_registry import PresentationRegistry, get_presentation_registry


def _controller_with_events(registry=None):
    events = []
    return PresentationController(registry=registry, event_sink=events.append), events


def test_explicit_requests_resolve_through_registry_taxonomy_and_emit_typed_events():
    controller, events = _controller_with_events()

    probes = [
        ("show", "map", "map", "workspace", "presentation_intent"),
        ("show", "spatial", "map", "workspace", "presentation_intent"),
        ("show", "camera", "vision", "workspace", "presentation_intent"),
        ("show", "chat", "conversation", "workspace", "presentation_intent"),
        ("show", "settings", "settings", "overlay", "presentation_intent"),
        ("hide", "map", "map", "workspace", "presentation_dismiss"),
        ("hide", "settings", "settings", "overlay", "presentation_dismiss"),
    ]

    for action, raw, canonical, taxonomy, event_type in probes:
        result = controller.execute(PresentationRequest(action, raw, EventSource.VOICE))
        assert result.accepted is True
        assert result.raw_surface == raw
        assert result.canonical_surface == canonical
        assert result.taxonomy == taxonomy
        assert result.event is not None
        assert result.event["type"] == event_type
        normalize_event(result.event)

    assert events[-1]["type"] == "presentation_dismiss"

    rejected = controller.execute(PresentationRequest("show", "media", EventSource.VOICE))
    assert rejected.accepted is False
    assert "not implemented" in rejected.message.lower()


def test_explicit_prompt5_policy_parity_for_map_settings_and_media():
    controller, _ = _controller_with_events()
    expected = {
        "map": ("workspace", "core", True),
        "settings": ("overlay", "screen", False),
    }
    for raw, (taxonomy, anchor, replayable) in expected.items():
        result = controller.resolve(PresentationRequest("show", raw))
        assert result.accepted is True
        payload = result.event["payload"]
        assert payload["priority"] == 60
        assert payload["anchor"] == anchor
        assert payload["replayable"] is replayable
        assert payload["capability"] == "presentation"
        assert payload["operation"] == "presentation.show"
        assert payload["spoken_text"] == f"Showing {result.canonical_surface.replace('_', ' ')}."
        assert payload["replace_key"] == f"presentation:{taxonomy}:{result.canonical_surface}"

    rejected = controller.resolve(PresentationRequest("show", "media"))
    assert rejected.accepted is False
    assert "not implemented" in rejected.message.lower()


def test_registry_resolution_precedence_and_ambiguity_are_explicit():
    registry = get_presentation_registry()

    expected = {
        "map": ("resolved", "workspace", "map"),
        "spatial": ("resolved", "workspace", "map"),
        "system": ("resolved", "workspace", "system"),
        "media": ("resolved", "widget", "media_control"),
        "camera": ("resolved", "workspace", "vision"),
        "chat": ("resolved", "workspace", "conversation"),
        "settings": ("resolved", "overlay", "settings"),
        "file": ("ambiguous", None, None),
        "composed_surface": ("ambiguous", None, None),
        "calendar": ("unknown", None, None),
    }
    for target, (status, taxonomy, canonical) in expected.items():
        resolution = registry.resolve_surface(target)
        assert resolution.status == status
        assert resolution.taxonomy == taxonomy
        assert resolution.canonical == canonical

    controller, _ = _controller_with_events()
    ambiguous = controller.resolve(PresentationRequest("show", "file"))
    assert ambiguous.accepted is False
    assert "Ambiguous" in ambiguous.message


def test_dynamic_alias_collision_becomes_ambiguous_without_controller_changes():
    contract = deepcopy(get_presentation_registry().to_dict())
    contract["workspaces"]["future_workspace"] = {
        "aliases": ["media"],
        "implemented": True,
        "renderer": "FutureRenderer",
        "renderer_module": "frontend/src/workspaces/Future.tsx",
        "spatial": False,
        "core_position": "dock_bottom_right",
        "dismiss_policy": "persistent",
        "description": "Future workspace",
    }
    registry = PresentationRegistry.from_dict(contract)

    resolution = registry.resolve_surface("media")
    assert resolution.status == "ambiguous"
    assert {taxonomy for taxonomy, _ in resolution.matches} == {"workspace", "widget"}


def test_clear_screen_has_typed_semantics_without_magic_surface_id():
    controller, events = _controller_with_events()

    result = controller.execute(PresentationRequest("clear_screen", source=EventSource.BRAIN))

    assert result.accepted is True
    assert result.canonical_surface is None
    assert result.event is not None
    assert result.event["type"] == "presentation_command"
    assert result.event["payload"] == {"action": "clear_screen"}
    assert "*" not in str(result.event)
    normalize_event(result.event)
    assert events[0]["type"] == "presentation_command"


def test_lifecycle_identity_is_taxonomy_scoped_and_idempotent():
    controller, events = _controller_with_events()

    first_map = controller.execute(PresentationRequest("show", "map"))
    second_map = controller.execute(PresentationRequest("show", "map"))
    hide_map = controller.execute(PresentationRequest("hide", "map"))
    first_settings = controller.execute(PresentationRequest("show", "settings"))
    second_settings = controller.execute(PresentationRequest("show", "settings"))
    hide_settings = controller.execute(PresentationRequest("hide", "settings"))
    hidden_map = controller.execute(PresentationRequest("hide", "map"))

    assert first_map.event["payload"]["id"] == second_map.event["payload"]["id"]
    assert first_settings.event["payload"]["id"] == second_settings.event["payload"]["id"]
    assert first_map.event["payload"]["id"] != first_settings.event["payload"]["id"]
    assert hide_map.event["payload"]["id"] == first_map.event["payload"]["id"]
    assert hide_settings.event["payload"]["id"] == first_settings.event["payload"]["id"]
    assert hidden_map.event["payload"]["id"] == first_map.event["payload"]["id"]
    assert len(events) == 7


def test_invalid_targets_and_actions_are_rejected_without_fabrication():
    controller, events = _controller_with_events()

    for request in (
        PresentationRequest("show", "calendar"),
        PresentationRequest("show", "tools"),
        PresentationRequest("show", "mcp"),
        PresentationRequest("drag_widget", "map"),
        PresentationRequest("show"),
        PresentationRequest("clear_screen", "map"),
    ):
        result = controller.execute(request)
        assert result.accepted is False
        assert result.event is None

    assert events == []


def test_future_workspace_resolves_without_controller_or_parser_changes():
    contract = deepcopy(get_presentation_registry().to_dict())
    contract["workspaces"]["future_workspace"] = {
        "aliases": ["future"],
        "implemented": True,
        "renderer": "FutureRenderer",
        "renderer_module": "frontend/src/workspaces/Future.tsx",
        "spatial": False,
        "core_position": "dock_bottom_right",
        "dismiss_policy": "persistent",
        "description": "Future workspace",
    }
    registry = PresentationRegistry.from_dict(contract)
    controller, _ = _controller_with_events(registry)

    added = controller.resolve(PresentationRequest("show", "future"))
    assert added.accepted is True
    assert added.canonical_surface == "future_workspace"
    assert added.taxonomy == "workspace"

    del contract["workspaces"]["future_workspace"]
    removed = PresentationController(PresentationRegistry.from_dict(contract)).resolve(
        PresentationRequest("show", "future")
    )
    assert removed.accepted is False


def test_presentation_request_tool_is_registered_with_narrow_schema():
    from charlie.capabilities import capability_index
    from charlie.tools import registry

    definition = next(
        item
        for item in registry.get_tool_definitions()
        if item["function"]["name"] == "presentation_request"
    )
    schema = definition["function"]["parameters"]
    assert schema["required"] == ["action"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "surface"}
    assert "presentation_request" in registry.get_tool_names()
    presentation_capability = capability_index.get_capability("presentation")
    assert presentation_capability is not None
    assert any(op.name == "presentation_request" for op in presentation_capability.operations.values())
    assert registry.is_interactive("presentation_request") is True


def test_presentation_request_worker_thread_uses_injected_dispatcher():
    from charlie.presentation_control import get_presentation_controller
    from charlie.tools import registry

    events = []
    controller = get_presentation_controller()
    controller.set_event_sink(events.append)

    async def run_worker_topology():
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                registry.execute_tool,
                "presentation_request",
                {"action": "show", "surface": "map"},
            ),
            timeout=1.0,
        )

    try:
        result = asyncio.run(run_worker_topology())
    finally:
        controller.set_event_sink(None)

    assert result.startswith("Showing map")
    assert len(events) == 1
    assert events[0]["type"] == "presentation_intent"
    assert events[0]["payload"]["workspace_type"] == "map"


def test_interactive_presentation_requests_serialize_same_batch_order():
    from charlie.presentation_control import get_presentation_controller
    from charlie.tools import registry

    events = []
    controller = get_presentation_controller()
    controller.set_event_sink(events.append)

    async def run_batch():
        loop = asyncio.get_running_loop()
        lock = asyncio.Lock()

        async def call(arguments):
            async with lock if registry.is_interactive("presentation_request") else _null_async_context():
                return await loop.run_in_executor(None, registry.execute_tool, "presentation_request", arguments)

        return await asyncio.gather(
            call({"action": "show", "surface": "map"}),
            call({"action": "hide", "surface": "map"}),
        )

    class _null_async_context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    try:
        results = asyncio.run(asyncio.wait_for(run_batch(), timeout=1.0))
    finally:
        controller.set_event_sink(None)

    assert results[0].startswith("Showing map")
    assert results[1].startswith("Hidden map")
    assert [event["type"] for event in events] == ["presentation_intent", "presentation_dismiss"]
