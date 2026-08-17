"""Phase 13 final correctness regression tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, create_autospec, patch

from charlie.events import EventMeta, EventSource, EventType
from charlie.ipc import EventBus
from charlie.mcp_client import MCPClient
from charlie.self_extension.adapters.code_adapter import CodeAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.code_worker import ASTAllowListError, run_worker, validate_ast_allow_list
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    ExtensionTransaction,
    TransactionStatus,
)
from charlie.self_extension.orchestrator import SelfExtensionOrchestrator
from charlie.self_extension.registry import ExtensionRegistry


def _verification_mocks(ext_id: str) -> dict:
    code_index = MagicMock()
    code_index.refresh = MagicMock()
    doctor = MagicMock()
    doctor.diagnose.return_value = MagicMock(is_healthy=True, errors=[])
    introspector = MagicMock()
    introspector.get_capabilities_info.return_value = {"by_id": {ext_id: {"id": ext_id}}}
    self_knowledge = MagicMock()
    self_knowledge.answer_self_question.return_value = {"answer": f"has {ext_id}"}
    return {
        "code_index": code_index,
        "doctor": doctor,
        "introspector": introspector,
        "self_knowledge": self_knowledge,
    }


class TestEventBusIntegration(unittest.TestCase):
    def test_real_event_bus_emit_interface(self) -> None:
        captured: list = []
        loop = asyncio.new_event_loop()

        async def _run() -> None:
            bus = EventBus(pub_port=0, pull_port=0)
            await bus.__aenter__()
            bus.set_state_listener(lambda envelope: captured.append(envelope) or None)
            await bus.emit(
                EventType.SELF_EXTENSION_REQUESTED.value,
                {"tx_id": "tx-test", "kind": "code_small"},
                meta=EventMeta(source=EventSource.BRAIN),
            )
            await bus.__aexit__(None, None, None)

        loop.run_until_complete(_run())
        loop.close()
        self.assertTrue(captured)
        self.assertEqual(captured[0]["type"], EventType.SELF_EXTENSION_REQUESTED.value)

    def test_orchestrator_bridges_sync_emit_to_async_bus(self) -> None:
        captured: list = []

        async def _run() -> None:
            bus = EventBus(pub_port=0, pull_port=0)
            await bus.__aenter__()
            bus.set_state_listener(lambda envelope: captured.append(envelope) or None)
            orch = SelfExtensionOrchestrator(
                repo_root=Path(tempfile.mkdtemp()),
                event_bus=bus,
                event_loop=asyncio.get_running_loop(),
            )
            orch._emit(
                EventType.SELF_EXTENSION_REQUESTED,
                {"tx_id": "tx-test", "kind": "code_small"},
            )
            await asyncio.sleep(0.05)
            await bus.__aexit__(None, None, None)

        asyncio.run(_run())
        self.assertEqual(captured[0]["type"], EventType.SELF_EXTENSION_REQUESTED.value)


class TestMCPAdapterCanonicalAPI(unittest.TestCase):
    def test_mock_uses_mcp_client_spec(self) -> None:
        client = create_autospec(MCPClient, instance=True)
        client.add_server.return_value = None
        client.enable_server.return_value = ["mcp_test_server_tool_a"]
        tool = MagicMock()
        tool.name = "tool_a"
        tool.server_name = "test_server"
        client.list_tools.return_value = [tool]
        client.health_check.return_value = {"test_server": True}
        with tempfile.TemporaryDirectory() as td:
            ext_registry = ExtensionRegistry(manifest_path=Path(td) / "m.json")
            tool_registry = MagicMock()
            adapter = MCPAdapter(registry=ext_registry, mcp_client=client, tool_registry=tool_registry)
            res = adapter.register_mcp_server("test_server", "npx", ["test-mcp"])
        self.assertTrue(res.success)
        client.enable_server.assert_called_once_with(tool_registry, "test_server")
        self.assertFalse(hasattr(MCPClient, "connect_server"))

    def test_nonexistent_mcp_methods_fail_on_spec_mock(self) -> None:
        client = create_autospec(MCPClient, instance=True)
        with self.assertRaises(AttributeError):
            client.connect_server("x")


class TestCodeSmallBoundary(unittest.TestCase):
    def test_pathlib_import_rejected(self) -> None:
        with self.assertRaises(ASTAllowListError):
            validate_ast_allow_list("from pathlib import Path\ndef f(): return 1\n", "f")

    def test_pathlib_read_text_rejected(self) -> None:
        src = (
            "from pathlib import Path\n"
            "def read_tool(p):\n"
            "    return Path(p).read_text()\n"
        )
        with self.assertRaises(ASTAllowListError):
            validate_ast_allow_list(src, "read_tool")

    def test_worker_reruns_validator_on_disk_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "evil.py"
            p.write_text("def evil():\n    return 1\n", encoding="utf-8")
            ok, _, err = run_worker(p, "evil", None)
            self.assertTrue(ok, err)
            p.write_text("import os\ndef evil():\n    return os.getcwd()\n", encoding="utf-8")
            ok2, _, err2 = run_worker(p, "evil", None)
            self.assertFalse(ok2)
            self.assertIn("validation", err2.lower())

    def test_worker_rejects_file_changed_after_parent_validation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "race.py"
            p.write_text("def race():\n    return 1\n", encoding="utf-8")
            real_run = __import__("subprocess").run
            mutated = False

            def mutate_before_child(*args: Any, **kwargs: Any) -> Any:
                nonlocal mutated
                if not mutated:
                    mutated = True
                    p.write_text(
                        "import os\ndef race():\n    return os.getcwd()\n",
                        encoding="utf-8",
                    )
                return real_run(*args, **kwargs)

            with patch("charlie.self_extension.code_worker.subprocess.run", side_effect=mutate_before_child):
                ok, _, err = run_worker(p, "race", None)
            self.assertFalse(ok)
            self.assertIn("validation", err.lower())


class TestCodeAdapterWorktreeGuard(unittest.TestCase):
    def test_apply_invokes_guard_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tools_dir = root / "tools"
            tools_dir.mkdir()
            target = tools_dir / "tool.py"
            target.write_bytes(b"def tool():\n    return 0\n")
            registry = ExtensionRegistry(manifest_path=root / "manifest.json")
            registry.register(
                __import__(
                    "charlie.self_extension.registry", fromlist=["ExtensionEntry"]
                ).ExtensionEntry(
                    extension_id="code_tool",
                    name="tool",
                    kind=ExtensionKind.CODE_SMALL,
                    source=str(target),
                    content_hash=hashlib.sha256(target.read_bytes()).hexdigest()[:16],
                    declared_tools=["tool"],
                )
            )
            adapter = CodeAdapter(repo_root=root, tools_dir=tools_dir, registry=registry)
            from charlie.self_extension.worktree_guard import WorktreeGuard

            with patch.object(WorktreeGuard, "check_before_write", wraps=WorktreeGuard.check_before_write) as guard:
                res = adapter.apply_code_extension(
                    name="tool",
                    code="def tool():\n    return 1\n",
                )
            guard.assert_called_once()
            self.assertTrue(res.success, res.message)

    def test_dirty_target_rejected_before_checkpoint_preimage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tools_dir = root / "tools"
            tools_dir.mkdir()
            target = tools_dir / "tool.py"
            baseline = b"def tool():\n    return 0\n"
            target.write_bytes(baseline)
            registry = ExtensionRegistry(manifest_path=root / "manifest.json")
            registry.register(
                __import__(
                    "charlie.self_extension.registry", fromlist=["ExtensionEntry"]
                ).ExtensionEntry(
                    extension_id="code_tool",
                    name="tool",
                    kind=ExtensionKind.CODE_SMALL,
                    source=str(target),
                    content_hash=hashlib.sha256(baseline).hexdigest()[:16],
                    declared_tools=["tool"],
                )
            )
            adapter = CodeAdapter(repo_root=root, tools_dir=tools_dir, registry=registry)
            target.write_bytes(b"def tool():\n    return 999  # user edit\n")
            res = adapter.apply_code_extension(name="tool", code="def tool():\n    return 1\n")
            self.assertFalse(res.success)
            self.assertIn("baseline", res.message.lower())

    def test_update_restores_previous_bytes_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tools_dir = root / "tools"
            tools_dir.mkdir()
            registry = ExtensionRegistry(manifest_path=root / "manifest.json")
            adapter = CodeAdapter(
                repo_root=root,
                tools_dir=tools_dir,
                registry=registry,
            )
            first = adapter.apply_code_extension(
                name="tool",
                code="def tool():\n    return 1\n",
            )
            self.assertTrue(first.success, first.message)
            original = (tools_dir / "tool.py").read_bytes()
            bad = adapter.apply_code_extension(
                name="tool",
                code="def tool():\n    raise ValueError('nope')\n",
            )
            self.assertFalse(bad.success)
            self.assertEqual((tools_dir / "tool.py").read_bytes(), original)


class TestOrchestratorVerificationAndResume(unittest.TestCase):
    def _make_orch(self, tmp: Path, ext_id: str, **extra) -> SelfExtensionOrchestrator:
        mocks = _verification_mocks(ext_id)
        kwargs = {
            "repo_root": tmp,
            "manifest_path": tmp / "manifest.json",
            "skills_dir": tmp / "skills",
            "tools_dir": tmp / "tools",
            **mocks,
            **extra,
        }
        (tmp / "skills").mkdir(parents=True, exist_ok=True)
        (tmp / "tools").mkdir(parents=True, exist_ok=True)
        return SelfExtensionOrchestrator(**kwargs)

    def test_skill_verification_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp, "skill_demo")
            orch._self_knowledge.answer_self_question.return_value = {"answer": "no match"}
            skill_md = """---
name: demo
description: Demo skill
scripts:
  - run.py
---
# Demo
Procedure.
"""
            req = ExtensionRequest(
                user_prompt="add skill",
                classification=ExtensionClassification(kind=ExtensionKind.SKILL),
                plan=ExtensionPlan(
                    plan_id="p1",
                    kind=ExtensionKind.SKILL,
                    description="d",
                    raw_text=skill_md,
                ),
            )
            res = orch.execute_skill_transaction(req, skill_name="demo", raw_text=skill_md)
            self.assertFalse(res.success)
            self.assertEqual(res.status, TransactionStatus.ROLLED_BACK)
            self.assertFalse((tmp / "skills" / "demo").exists())

    def test_missing_verification_dependency_cannot_complete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = SelfExtensionOrchestrator(
                repo_root=tmp,
                manifest_path=tmp / "manifest.json",
                tools_dir=tmp / "tools",
            )
            (tmp / "tools").mkdir(parents=True)
            ok, msg = orch._run_verification_gate("tx", affected_ext_ids=["code_x"])
            self.assertFalse(ok)
            self.assertIn("Introspector", msg)

    def test_unavailable_expected_capability_cannot_complete(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mocks = _verification_mocks("code_x")
            mocks["introspector"].get_capabilities_info.return_value = {
                "by_id": {"code_x": {"available": False}}
            }
            orch = self._make_orch(tmp, "code_x", **mocks)
            ok, msg = orch._run_verification_gate("tx", affected_ext_ids=["code_x"])
            self.assertFalse(ok)
            self.assertIn("unavailable", msg.lower())

    def test_transaction_roundtrip_preserves_plan(self) -> None:
        req = ExtensionRequest(
            user_prompt="extend",
            request_id="rid-1",
            classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
            plan=ExtensionPlan(
                plan_id="plan-1",
                kind=ExtensionKind.CODE_SMALL,
                description="d",
                code_source="def x():\n    return 1\n",
                tool_name="x",
            ),
            affected_capabilities=["code_x"],
        )
        tx = ExtensionTransaction(transaction_id="tx-1", request=req, plan=req.plan)
        tx.status = TransactionStatus.RESTARTING
        data = tx.to_dict()
        restored = ExtensionTransaction.from_dict(data)
        self.assertEqual(restored.request.user_prompt, "extend")
        self.assertEqual(restored.request.request_id, "rid-1")
        self.assertEqual(restored.plan.tool_name, "x")
        self.assertEqual(restored.request.classification.kind, ExtensionKind.CODE_SMALL)

    def test_resumed_verification_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tools = tmp / "tools"
            tools.mkdir()
            target = tools / "resume_tool.py"
            target.write_text("def resume_tool():\n    return 1\n", encoding="utf-8")
            tx_store = tmp / "tx.json"
            req = ExtensionRequest(
                user_prompt="extend",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=ExtensionPlan(
                    plan_id="p",
                    kind=ExtensionKind.CODE_SMALL,
                    description="d",
                    code_source="def resume_tool():\n    return 1\n",
                    tool_name="resume_tool",
                ),
            )
            from charlie.self_extension.checkpoint import CheckpointManager

            cp = CheckpointManager(repo_root=tmp).create_checkpoint(
                "tx-resume",
                files_to_modify=[target],
            )
            payload = {
                "tx-resume": {
                    "transaction_id": "tx-resume",
                    "status": TransactionStatus.RESTARTING.value,
                    "request": req.to_dict(),
                    "plan": req.plan.to_dict(),
                    "checkpoint": cp.to_dict(),
                }
            }
            tx_store.write_text(json.dumps(payload), encoding="utf-8")
            mocks = _verification_mocks("code_resume_tool")
            mocks["self_knowledge"].answer_self_question.return_value = {"answer": "nothing"}
            orch = SelfExtensionOrchestrator(
                repo_root=tmp,
                manifest_path=tmp / "manifest.json",
                tools_dir=tools,
                tx_store_path=tx_store,
                **mocks,
            )
            tx = orch._transactions.get("tx-resume")
            self.assertIsNotNone(tx)
            self.assertEqual(tx.status, TransactionStatus.ROLLED_BACK)
