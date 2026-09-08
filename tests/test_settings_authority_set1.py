"""Focused tests for main-owned settings persistence, projection, and reload."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

import main
from charlie import settings_service as settings_module
from charlie import web_server
from charlie.config import Config
from charlie.self_extension.models import ExtensionRequest
from charlie.self_extension.orchestrator import SelfExtensionOrchestrator
from charlie.settings_service import SettingsService, SettingValidationError, canonical_settings_request_fingerprint


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.commands: list[dict] = []

    async def emit(self, event_type, payload, meta=None):
        self.events.append((event_type, payload))

    async def send_command(self, command):
        self.commands.append(command)
        return True


def _service(tmp_path: Path) -> tuple[SettingsService, Config, Path]:
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_MODEL=before\nGPU_DEVICE=cpu\n", encoding="utf-8")
    cfg = Config()
    cfg.llm_model = "before"
    cfg.gpu_device = "cpu"
    return SettingsService(cfg, env_path=env_path), cfg, env_path


def _payload(operation: str, request_id: str, updates: dict | None = None) -> dict:
    updates = updates or {}
    return {
        "request_id": request_id,
        "operation": operation,
        "updates": updates,
        "request_fingerprint": canonical_settings_request_fingerprint(operation, updates),
    }


def _snapshot_event(snapshot: dict) -> dict:
    return {"type": "settings_snapshot", "payload": snapshot}


def _seed_web_projection(service: SettingsService) -> None:
    assert web_server._apply_settings_snapshot_event(_snapshot_event(service.snapshot())) is True


@pytest.fixture(autouse=True)
def _reset_web_settings_projection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(web_server, "event_bus", None)
    monkeypatch.setattr(web_server, "_settings_snapshot", None)
    monkeypatch.setattr(web_server, "_settings_snapshot_event", None)
    monkeypatch.setattr(web_server, "_pending_settings_operations", {})
    monkeypatch.setattr(web_server, "_pending_settings_fingerprints", {})
    yield


def test_settings_authority_has_no_module_default_or_web_writer():
    assert not hasattr(settings_module, "settings_service")
    web_source = inspect.getsource(web_server)
    assert "SettingsService(" not in web_source
    assert "_atomic_write_env" not in web_source
    assert "apply_env_updates" not in web_source


def test_settings_command_logging_excludes_secret_values(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.DEBUG, logger="charlie.main")
    main._log_received_web_command(
        {
            "type": "settings_operation",
            "payload": {
                "operation": "update",
                "request_id": "log-safe",
                "updates": {"LLM_API_KEY": "secret-value", "LLM_MODEL": "safe-model"},
            },
        }
    )

    assert "secret-value" not in caplog.text
    assert "LLM_API_KEY" in caplog.text
    assert "operation=update" in caplog.text


def test_main_constructs_one_service_and_injects_same_service_into_self_extension():
    source = inspect.getsource(main.main)
    assert source.count("SettingsService(config_instance=config)") == 1
    assert "settings_service=settings_service" in source
    assert "settings_snapshot_callback=_schedule_settings_snapshot" in source


def test_update_persists_before_runtime_config_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service, cfg, _ = _service(tmp_path)
    observed: list[str] = []

    def persist(_updates, *, source="unknown"):
        observed.append(cfg.llm_model)

    monkeypatch.setattr(service, "_atomic_write_env", persist)
    service.apply_updates({"LLM_MODEL": "after"})

    assert observed == ["before"]
    assert cfg.llm_model == "after"


def test_invalid_complete_update_does_not_mutate_runtime_or_disk(tmp_path: Path):
    service, cfg, env_path = _service(tmp_path)
    before = env_path.read_text(encoding="utf-8")

    with pytest.raises(SettingValidationError):
        service.apply_updates({"CONTEXT_WINDOW": "not-an-int", "LLM_MODEL": "after"})

    assert cfg.llm_model == "before"
    assert env_path.read_text(encoding="utf-8") == before


def test_persistence_failure_leaves_runtime_and_projection_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    service, cfg, env_path = _service(tmp_path)
    before = service.snapshot()

    def fail(*_args, **_kwargs):
        raise OSError("atomic write failed")

    monkeypatch.setattr(service, "_atomic_write_env", fail)
    with pytest.raises(OSError):
        service.apply_updates({"LLM_MODEL": "after"})

    assert cfg.llm_model == "before"
    assert service.snapshot() == before
    assert "LLM_MODEL=before" in env_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_concurrent_thread_updates_serialize_and_preserve_both_values(tmp_path: Path):
    service, cfg, env_path = _service(tmp_path)
    original_write = service._atomic_write_env
    first_entered = threading.Event()
    release_first = threading.Event()
    tracker_lock = threading.Lock()
    active_writes = 0
    max_active_writes = 0
    write_count = 0

    def tracked_write(updates, *, source="unknown"):
        nonlocal active_writes, max_active_writes, write_count
        with tracker_lock:
            write_count += 1
            call_number = write_count
            active_writes += 1
            max_active_writes = max(max_active_writes, active_writes)
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(2)
        try:
            original_write(updates, source=source)
        finally:
            with tracker_lock:
                active_writes -= 1

    service._atomic_write_env = tracked_write
    first = asyncio.create_task(asyncio.to_thread(service.apply_updates, {"LLM_MODEL": "thread-model"}))
    await asyncio.to_thread(first_entered.wait)
    second = asyncio.create_task(asyncio.to_thread(service.apply_updates, {"GPU_DEVICE": "cuda:0"}))
    await asyncio.sleep(0.05)
    assert not second.done()
    release_first.set()
    await asyncio.gather(first, second)

    content = env_path.read_text(encoding="utf-8")
    snapshot = service.snapshot()
    fields = {field["key"]: field for field in snapshot["fields"]}
    assert "LLM_MODEL=thread-model" in content
    assert "GPU_DEVICE=cuda:0" in content
    assert cfg.llm_model == "thread-model"
    assert fields["LLM_MODEL"]["saved_value"] == "thread-model"
    assert fields["LLM_MODEL"]["effective_value"] == "thread-model"
    assert fields["GPU_DEVICE"]["saved_value"] == "cuda:0"
    assert fields["GPU_DEVICE"]["effective_value"] == "cpu"
    assert snapshot["pending_reload"]["voice"] == ["GPU_DEVICE"]
    assert max_active_writes == 1


def test_atomic_replace_failure_is_truthful_and_does_not_apply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service, cfg, env_path = _service(tmp_path)
    monkeypatch.setattr(settings_module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace")))

    with pytest.raises(OSError):
        service.apply_updates({"LLM_MODEL": "after"})

    assert cfg.llm_model == "before"
    assert env_path.read_text(encoding="utf-8") == "LLM_MODEL=before\nGPU_DEVICE=cpu\n"


def test_restart_tier_update_is_saved_but_effective_state_waits_for_reload(tmp_path: Path):
    service, cfg, _ = _service(tmp_path)
    result = service.apply_updates({"GPU_DEVICE": "cuda:0"})
    snapshot = service.snapshot()

    assert cfg.gpu_device == "cuda:0"
    assert result["saved"] is True
    assert result["applied"] == []
    assert snapshot["pending_reload"]["voice"] == ["GPU_DEVICE"]
    gpu = next(field for field in snapshot["fields"] if field["key"] == "GPU_DEVICE")
    assert gpu["saved_value"] == "cuda:0"
    assert gpu["effective_value"] == "cpu"
    assert gpu["state"] == "pending_reload"


def test_process_tier_never_claims_runtime_application(tmp_path: Path):
    service, cfg, env_path = _service(tmp_path)
    result = service.apply_updates({"SESSION_DB_PATH": "new-sessions.db"})
    snapshot = service.snapshot()

    assert cfg.session_db_path != "new-sessions.db"
    assert "SESSION_DB_PATH=new-sessions.db" in env_path.read_text(encoding="utf-8")
    assert result["process_restart_required"] == ["SESSION_DB_PATH"]
    field = next(item for item in snapshot["fields"] if item["key"] == "SESSION_DB_PATH")
    assert field["state"] == "process_restart_required"
    assert field["effective_value"] != "new-sessions.db"


@pytest.mark.asyncio
async def test_main_settings_update_is_correlated_and_idempotent(tmp_path: Path):
    service, cfg, _ = _service(tmp_path)
    bus = _Bus()
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    writes: list[dict] = []
    original_write = service._atomic_write_env

    def counted_write(updates, *, source="unknown"):
        writes.append(dict(updates))
        original_write(updates, source=source)

    service._atomic_write_env = counted_write
    payload = _payload("update", "settings-1", {"LLM_MODEL": "after"})
    first = await main._handle_settings_operation_request(service, bus, payload, **caches)
    replay = await main._handle_settings_operation_request(service, bus, payload, **caches)
    conflict = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("update", "settings-1", {"LLM_MODEL": "different"}),
        **caches,
    )

    assert first["status"] == "completed"
    assert replay == first
    assert conflict["status"] == "request_id_conflict"
    assert cfg.llm_model == "after"
    assert [event[0] for event in bus.events].count("settings_operation_result") == 3
    assert caches["in_flight"] == {}
    assert writes == [{"LLM_MODEL": "after"}]


@pytest.mark.asyncio
async def test_duplicate_reload_executes_targeted_handler_once(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"GPU_DEVICE": "cuda:0"})
    bus = _Bus()
    calls: list[str] = []
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    handlers = {"voice": lambda: calls.append("voice") or True}
    payload = _payload("reload", "reload-1")

    first = await main._handle_settings_operation_request(service, bus, payload, reload_handlers=handlers, **caches)
    replay = await main._handle_settings_operation_request(service, bus, payload, reload_handlers=handlers, **caches)

    assert calls == ["voice"]
    assert first["status"] == "completed"
    assert replay == first
    assert service.snapshot()["pending_reload"] == {}


@pytest.mark.asyncio
async def test_reload_request_id_conflict_does_not_reload_again(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"GPU_DEVICE": "cuda:0"})
    bus = _Bus()
    calls: list[str] = []
    caches = {"result_cache": OrderedDict(), "in_flight": {}, "fingerprint_cache": {}}
    await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", "reload-2"),
        reload_handlers={"voice": lambda: calls.append("voice") or True},
        **caches,
    )
    conflict = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("update", "reload-2", {"LLM_MODEL": "other"}),
        reload_handlers={"voice": lambda: calls.append("wrong") or True},
        **caches,
    )
    assert conflict["status"] == "request_id_conflict"
    assert calls == ["voice"]


@pytest.mark.asyncio
async def test_failed_reload_keeps_pending_and_reports_partial(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"GPU_DEVICE": "cuda:0"})
    bus = _Bus()
    result = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", "reload-failed"),
        reload_handlers={"voice": lambda: (False, "voice unavailable")},
    )

    assert result["status"] == "partial"
    assert result["result"]["ok"] is False
    assert result["result"]["failed_tiers"] == {"voice": "voice unavailable"}
    assert service.snapshot()["pending_reload"]["voice"] == ["GPU_DEVICE"]


@pytest.mark.asyncio
async def test_concurrent_update_cannot_be_cleared_by_older_reload(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"GPU_DEVICE": "cuda:0"})
    reload_started = threading.Event()
    release_reload = threading.Event()

    async def reload_voice():
        reload_started.set()
        await asyncio.to_thread(release_reload.wait)
        return True

    reload_task = asyncio.create_task(
        main._handle_settings_operation_request(
            service,
            _Bus(),
            _payload("reload", "reload-race"),
            reload_handlers={"voice": reload_voice},
        )
    )
    await asyncio.to_thread(reload_started.wait)
    await asyncio.to_thread(service.apply_updates, {"GPU_DEVICE": "cuda:1"})
    release_reload.set()
    result = await reload_task

    snapshot = service.snapshot()
    gpu = next(field for field in snapshot["fields"] if field["key"] == "GPU_DEVICE")
    assert result["status"] == "partial"
    assert gpu["saved_value"] == "cuda:1"
    assert gpu["effective_value"] == "cpu"
    assert snapshot["pending_reload"]["voice"] == ["GPU_DEVICE"]


@pytest.mark.asyncio
async def test_reload_targets_only_pending_tiers(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"GPU_DEVICE": "cuda:0"})
    bus = _Bus()
    calls: list[str] = []

    result = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", "voice-only"),
        reload_handlers={
            "voice": lambda: calls.append("voice") or True,
            "mcp": lambda: calls.append("mcp") or True,
            "plugins": lambda: calls.append("plugins") or True,
        },
    )

    assert result["status"] == "completed"
    assert calls == ["voice"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("setting", "tier"),
    [("MCP_ENABLED", "mcp"), ("PLUGINS_ENABLED", "plugins")],
)
async def test_reload_targets_mcp_and_plugin_tier_independently(tmp_path: Path, setting: str, tier: str):
    service, _, _ = _service(tmp_path)
    service.apply_updates({setting: True})
    bus = _Bus()
    calls: list[str] = []
    result = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", f"{tier}-only"),
        reload_handlers={
            "voice": lambda: calls.append("voice") or True,
            "mcp": lambda: calls.append("mcp") or True,
            "plugins": lambda: calls.append("plugins") or True,
        },
    )

    assert result["status"] == "completed"
    assert calls == [tier]


@pytest.mark.asyncio
async def test_browser_reload_tier_uses_no_subsystem_restart(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"BROWSER_ENABLED": True})
    bus = _Bus()
    calls: list[str] = []
    result = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", "browser-reload"),
        reload_handlers={"voice": lambda: calls.append("voice") or True},
    )

    assert result["status"] == "completed"
    assert result["result"]["reloaded_tiers"] == ["reload"]
    assert calls == []


def test_plugin_reload_returns_canonical_manager_and_preserves_old_on_failure():
    class Registry:
        def __init__(self):
            self._tools = {"plugin_old": object(), "builtin": object()}
            self.unregistered: list[str] = []

        def unregister_tool(self, name):
            self.unregistered.append(name)
            self._tools.pop(name, None)

    health: list[tuple[str, object]] = []
    config = SimpleNamespace(plugins_enabled=True)
    old_manager = object()
    new_manager = object()
    registry = Registry()
    result = main._reload_plugin_tools_state(
        config,
        old_manager,
        registry=registry,
        register_plugin_tools=lambda _config: new_manager,
        empty_manager_factory=lambda: object(),
        set_health=lambda name, status: health.append((name, status)),
    )
    assert result[0] is True
    assert result[2] is new_manager
    assert registry.unregistered == ["plugin_old"]

    failed_registry = Registry()
    failed = main._reload_plugin_tools_state(
        config,
        old_manager,
        registry=failed_registry,
        register_plugin_tools=lambda _config: (_ for _ in ()).throw(RuntimeError("load failed")),
        empty_manager_factory=lambda: object(),
        set_health=lambda name, status: health.append((name, status)),
    )
    assert failed[0] is False
    assert failed[2] is old_manager

    disabled = main._reload_plugin_tools_state(
        SimpleNamespace(plugins_enabled=False),
        old_manager,
        registry=Registry(),
        register_plugin_tools=lambda _config: new_manager,
        empty_manager_factory=lambda: new_manager,
        set_health=lambda name, status: health.append((name, status)),
    )
    assert disabled[0] is True
    assert disabled[2] is new_manager


@pytest.mark.asyncio
async def test_self_extension_settings_update_publishes_final_snapshot(tmp_path: Path):
    service, cfg, _ = _service(tmp_path)
    snapshots: list[dict] = []
    orchestrator = SelfExtensionOrchestrator(
        settings_service=service,
        config=cfg,
        repo_root=tmp_path,
        tx_store_path=tmp_path / "transactions.json",
        settings_snapshot_callback=lambda _rationale: snapshots.append(service.snapshot()),
    )
    orchestrator._run_verification_gate = lambda *_args, **_kwargs: (True, "verified")
    result = orchestrator.execute_config_transaction(
        ExtensionRequest(
            user_prompt="set model",
            affected_settings={"LLM_MODEL": "self-extension-model"},
        )
    )

    assert result.success is True
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    field = next(item for item in snapshot["fields"] if item["key"] == "LLM_MODEL")
    assert field["value"] == "self-extension-model"
    assert web_server._apply_settings_snapshot_event({"type": "settings_snapshot", "payload": snapshot}) is True
    assert (await web_server.get_dashboard_config())["fields"]


@pytest.mark.asyncio
async def test_self_extension_rollback_publishes_truthful_final_snapshot(tmp_path: Path):
    service, cfg, _ = _service(tmp_path)
    snapshots: list[dict] = []
    orchestrator = SelfExtensionOrchestrator(
        settings_service=service,
        config=cfg,
        repo_root=tmp_path,
        tx_store_path=tmp_path / "transactions.json",
        settings_snapshot_callback=lambda _rationale: snapshots.append(service.snapshot()),
    )
    orchestrator._run_verification_gate = lambda *_args, **_kwargs: (False, "verification failed")
    result = orchestrator.execute_config_transaction(
        ExtensionRequest(
            user_prompt="set model",
            affected_settings={"LLM_MODEL": "temporary-model"},
        )
    )

    assert result.success is False
    assert len(snapshots) == 1
    field = next(item for item in snapshots[0]["fields"] if item["key"] == "LLM_MODEL")
    assert field["value"] == "before"


@pytest.mark.asyncio
async def test_settings_result_cache_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    service, _, _ = _service(tmp_path)
    bus = _Bus()
    monkeypatch.setattr(main, "_SETTINGS_RESULT_CACHE_MAX", 2)
    cache = OrderedDict()
    fingerprints: dict[str, str] = {}

    for index in range(3):
        await main._handle_settings_operation_request(
            service,
            bus,
            _payload("update", f"bounded-{index}", {"LLM_MODEL": f"model-{index}"}),
            result_cache=cache,
            in_flight={},
            fingerprint_cache=fingerprints,
        )

    assert list(cache) == ["bounded-1", "bounded-2"]
    assert set(fingerprints) == set(cache)


@pytest.mark.asyncio
async def test_process_pending_reload_is_not_run_or_claimed_effective(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    service.apply_updates({"SESSION_DB_PATH": "restart.db"})
    bus = _Bus()
    calls: list[str] = []
    result = await main._handle_settings_operation_request(
        service,
        bus,
        _payload("reload", "process-pending"),
        reload_handlers={"process": lambda: calls.append("process") or True},
    )

    assert result["status"] == "partial"
    assert result["result"]["ok"] is False
    assert result["result"]["process_restart_required"] == ["SESSION_DB_PATH"]
    assert calls == []


@pytest.mark.asyncio
async def test_main_runtime_state_replays_settings_snapshot(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    bus = _Bus()
    await main._publish_runtime_state(bus, settings_service=service)
    snapshots = [payload for event_type, payload in bus.events if event_type == "settings_snapshot"]
    assert len(snapshots) == 1
    assert snapshots[0]["authority"] == "main_runtime"


@pytest.mark.asyncio
async def test_web_post_forwards_and_waits_without_mutating_web_config(monkeypatch: pytest.MonkeyPatch):
    bus = _Bus()
    monkeypatch.setattr(web_server, "event_bus", bus)
    before = web_server.config.llm_model

    async def respond(command):
        payload = command["payload"]
        web_server._resolve_settings_operation_result(
            {
                "request_id": payload["request_id"],
                "request_fingerprint": payload["request_fingerprint"],
                "operation": "update",
                "status": "completed",
                "result": {"ok": True, "saved": True, "applied": ["LLM_MODEL"], "touched": []},
            }
        )

    async def send(command):
        bus.commands.append(command)
        await respond(command)
        return True

    bus.send_command = send
    result = await web_server.update_dashboard_config({"LLM_MODEL": "main-value", "request_id": "web-1"})

    assert result["status"] == "ok"
    assert result["authority_status"] == "completed"
    assert bus.commands[0]["type"] == "settings_operation"
    assert web_server.config.llm_model == before


@pytest.mark.asyncio
async def test_web_post_waits_for_main_result(monkeypatch: pytest.MonkeyPatch):
    bus = _Bus()
    seen = asyncio.Event()
    monkeypatch.setattr(web_server, "event_bus", bus)

    async def send(command):
        bus.commands.append(command)
        seen.set()
        return True

    bus.send_command = send
    operation = asyncio.create_task(
        web_server.update_dashboard_config({"LLM_MODEL": "main-value", "request_id": "web-wait"})
    )
    await seen.wait()
    await asyncio.sleep(0)
    assert not operation.done()
    payload = bus.commands[0]["payload"]
    web_server._resolve_settings_operation_result(
        {
            "request_id": payload["request_id"],
            "request_fingerprint": payload["request_fingerprint"],
            "operation": "update",
            "status": "completed",
            "result": {"ok": True, "saved": True, "applied": [], "touched": []},
        }
    )
    assert (await operation)["status"] == "ok"


@pytest.mark.asyncio
async def test_web_reload_waits_and_reports_timeout(monkeypatch: pytest.MonkeyPatch):
    bus = _Bus()
    monkeypatch.setattr(web_server, "event_bus", bus)
    monkeypatch.setattr(web_server, "SETTINGS_OPERATION_TIMEOUT_SECONDS", 0.001)
    result = await web_server.reload_engine_config()

    assert result["status"] == "error"
    assert result["authority_status"] == "timeout"


@pytest.mark.asyncio
async def test_web_get_uses_main_projection_and_not_local_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    service, _, _ = _service(tmp_path)
    _seed_web_projection(service)
    monkeypatch.setattr(web_server.config, "llm_model", "web-drift")

    result = await web_server.get_dashboard_config()
    field = next(item for item in result["fields"] if item["key"] == "LLM_MODEL")
    assert field["value"] == "before"
    assert result["authority"] == "main_runtime"


@pytest.mark.asyncio
async def test_web_get_is_unavailable_without_main_projection():
    response = await web_server.get_dashboard_config()
    assert response.status_code == 503


def test_settings_snapshot_masks_secrets_and_replays_to_late_web_client(tmp_path: Path):
    service, cfg, _ = _service(tmp_path)
    cfg.llm_key = "secret-value"
    snapshot = service.snapshot()
    secret = next(field for field in snapshot["fields"] if field["key"] == "LLM_API_KEY")

    assert secret["value"] is None
    assert secret["saved_value"] is None
    assert "secret-value" not in str(snapshot)
    assert "secret-value" not in canonical_settings_request_fingerprint("update", {"LLM_API_KEY": "secret-value"})
    assert web_server._apply_settings_snapshot_event(_snapshot_event(snapshot)) is True
    replay = next(event for event in web_server._initial_state_events() if event["type"] == "settings_snapshot")
    assert replay["payload"]["authority"] == "main_runtime"


def test_self_extension_reuses_injected_settings_service(tmp_path: Path):
    from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

    service, cfg, _ = _service(tmp_path)
    orchestrator = SelfExtensionOrchestrator(settings_service=service, config=cfg, repo_root=tmp_path)
    assert orchestrator._settings_service is service
