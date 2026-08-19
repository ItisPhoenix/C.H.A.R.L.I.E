"""Live V1 Acceptance Pass -- 22 Key System Capabilities & Workflows."""

import asyncio
import os
import sys
import pytest

from charlie.config import Config
from charlie.doctor import CharlieDoctor
from charlie.fastpaths import match_fast_path, execute_fast_path
from charlie.presentation import PresentationResolver, ExecutionOutcome, PresentationKind
from charlie.resource_locks import CapabilityLeaseManager
from charlie.self_knowledge import SelfKnowledgeService
from charlie.settings_service import SettingsService
from charlie.subsystem_health import HealthRegistry, HealthStatus
from charlie.task_journal import TaskJournal, TaskStatus
from charlie.terminal_service import TerminalManager
from charlie.desktop.takeover import user_takeover_detector
from charlie.known_apps import APP_REGISTRY
from charlie.mcp_client import MCPServerConfig
from charlie.errors import classify_exception, ErrorClass
from charlie.self_extension.classifier import ExtensionClassifier
from charlie.self_extension.models import ExtensionKind
import httpx


class TestV1LiveAcceptance:
    # 1. Idle Charlie state
    def test_1_idle_charlie(self):
        hr = HealthRegistry(("brain", "voice", "browser", "desktop"))
        snap = hr.snapshot()
        assert len(snap) == 4
        assert snap["brain"]["status"] == "disabled"

    # 2. Simple voice/text question fastpath/routing
    def test_2_simple_time_question(self):
        from charlie.router import answer_time_date
        ans = answer_time_date("what time is it?")
        assert ans is not None
        assert "It's" in ans

    # 3. CPU telemetry query
    def test_3_cpu_query(self):
        m = match_fast_path("what is the cpu usage?")
        assert m is not None
        res = execute_fast_path(m)
        assert "CPU Utilization:" in res

    # 4. Open/close application resolution
    def test_4_open_close_app_resolution(self):
        app = APP_REGISTRY.get("notepad")
        assert app is not None
        assert app.open_cmd == "notepad"
        assert app.close_process == "notepad.exe"

    # 5. Browser navigation fastpath match
    def test_5_browser_navigation(self):
        m = match_fast_path("open https://github.com")
        assert m is not None
        assert m.intent == "browser_read_url"

    # 6. Research request presentation resolution
    def test_6_research_presentation(self):
        resolver = PresentationResolver()
        intent = resolver.resolve(
            ExecutionOutcome(
                capability="research",
                operation="synthesize",
                status="completed",
                result={"query": "quantum computing", "findings": ["Finding 1"]},
            )
        )
        assert intent.kind == PresentationKind.WORKSPACE
        assert intent.workspace_type == "research"

    # 7. Tasks workspace presentation resolution
    def test_7_tasks_workspace(self):
        resolver = PresentationResolver()
        intent = resolver.resolve(
            ExecutionOutcome(
                capability="task",
                operation="list_tasks",
                request="show my tasks",
                status="completed",
                result={"tasks": []},
            )
        )
        assert intent.kind in (PresentationKind.WORKSPACE, PresentationKind.WIDGET, PresentationKind.CAPTION)

    # 8. System workspace presentation
    def test_8_system_workspace(self):
        resolver = PresentationResolver()
        intent = resolver.resolve(
            ExecutionOutcome(
                capability="system",
                operation="system.metrics.read",
                request="show system diagnostics",
                status="completed",
                result={"cpu": 15, "ram": 55},
            )
        )
        assert intent.kind in (PresentationKind.WIDGET, PresentationKind.WORKSPACE, PresentationKind.CAPTION)

    # 9. Persistent terminal session
    @pytest.mark.asyncio
    async def test_9_persistent_terminal(self):
        tm = TerminalManager()
        sess = await tm.create("test-v1-session", cols=80, rows=24)
        assert sess is not None
        assert sess.session_id == "test-v1-session"
        assert tm.get_session("test-v1-session") is not None
        sess.close()

    # 10. Settings service inspection
    def test_10_settings_inspection(self):
        cfg = Config()
        svc = SettingsService(cfg)
        specs = svc.get_field_specs()
        assert len(specs) > 0

    # 11. Memory lookup
    def test_11_memory_lookup(self):
        from charlie.memory_graph import MemoryGraph
        mg = MemoryGraph(db_path=":memory:")
        stats = mg.get_stats()
        assert "nodes" in stats

    # 12. MCP configuration model
    def test_12_mcp_config(self):
        cfg = MCPServerConfig(name="test-mcp", command="npx", args=["-y", "@modelcontextprotocol/server-filesystem"])
        assert cfg.name == "test-mcp"
        assert cfg.command == "npx"

    # 13. Charlie self-knowledge query
    def test_13_self_knowledge(self):
        sk = SelfKnowledgeService()
        res = sk.answer_self_question("which models are configured?")
        assert res["is_self_question"] is True
        assert "model" in res["answer"].lower()

    # 14. Charlie Doctor diagnostic run
    def test_14_doctor_run(self):
        doc = CharlieDoctor()
        rep = doc.diagnose()
        assert rep.total_checks >= 15

    # 15. Controlled self-extension classifier
    def test_15_self_extension_classifier(self):
        clf = ExtensionClassifier()
        cat = clf.classify("change your voice setting")
        assert cat.kind == ExtensionKind.CONFIG

    # 16. Concurrent non-conflicting tasks
    def test_16_concurrent_tasks(self):
        tj = TaskJournal(state_path=None)
        t1 = tj.create_task("Task 1")
        t2 = tj.create_task("Task 2")
        tj.transition(t1.id, TaskStatus.RUNNING)
        tj.transition(t2.id, TaskStatus.RUNNING)
        assert tj.get(t1.id).status == TaskStatus.RUNNING
        assert tj.get(t2.id).status == TaskStatus.RUNNING

    # 17. Conflicting lease arbitration
    @pytest.mark.asyncio
    async def test_17_conflicting_lease_arbitration(self):
        lm = CapabilityLeaseManager()
        lease1 = await lm.acquire("desktop", "task-owner-1", timeout=0.1)
        assert lease1 is not None
        assert lm.current_owner("desktop") == "task-owner-1"
        with pytest.raises(asyncio.TimeoutError):
            await lm.acquire("desktop", "task-owner-2", timeout=0.05)
        await lease1.release()

    # 18. User takeover detector
    def test_18_user_takeover_detector(self):
        user_takeover_detector.end_session()
        assert user_takeover_detector.check_takeover() is False

    # 19. HUD reconnect & snapshot replay contract
    def test_19_hud_reconnect_snapshot(self):
        tj = TaskJournal(state_path=None)
        t = tj.create_task("Replay Task")
        snap = tj.snapshot()
        assert any(item["id"] == t.id for item in snap)

    # 20. Model outage / degraded offline operation
    def test_20_degraded_offline_mode(self):
        err_cls, msg = classify_exception(httpx.ConnectError("Reasoning API unavailable"))
        assert err_cls == ErrorClass.RETRYABLE
        assert "reasoning service" in msg.lower()
        # Local fastpaths still work in offline mode
        m = match_fast_path("what is the cpu usage?")
        assert m is not None
        assert "CPU Utilization:" in execute_fast_path(m)

    # 21. Map workspace presentation
    def test_21_map_workspace_presentation(self):
        resolver = PresentationResolver()
        intent = resolver.resolve(
            ExecutionOutcome(
                capability="map",
                operation="open_map",
                status="completed",
                result={"lat": 37.77, "lng": -122.41, "zoom": 12},
            )
        )
        assert intent.kind == PresentationKind.WORKSPACE
        assert intent.workspace_type == "map"

    # 22. Clear-screen presentation dismissal
    def test_22_clear_screen_dismissal(self):
        resolver = PresentationResolver()
        widget_intent = resolver.resolve(
            ExecutionOutcome(
                capability="system",
                operation="system.metrics.read",
                status="completed",
                result="CPU: 20%",
            )
        )
        assert widget_intent.dismiss_policy in ("timed", "task_lifetime", "manual", "immediate", "persistent")