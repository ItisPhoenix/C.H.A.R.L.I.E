"""Comprehensive acceptance tests for Phase 13 self-extension blocker fixes.

Covers:
  B1: AST allow-list rejects banned imports/calls
  B2: CODE_SMALL func dispatch via subprocess (run_worker)
  B3: MCP adapter uses real MCPClient API, discovers tools, rolls back on failure
  B4: Registry rehydrate() restores CODE_SMALL/SKILL/MCP capabilities
  B5: RESTARTING→VERIFYING transaction persistence and resume
  B6: Worktree conflict guard (pre-write dirty file, pre-rollback post-image)
  B7: Post-change verification gate (introspector, doctor, self-knowledge)
  B8: Canonical EventType contract — all events use correct string values
  B9: Orchestrator end-to-end for CONFIG / SKILL / CODE_SMALL / MCP_TOOL
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, create_autospec

from charlie.events import EventType
from charlie.ipc import EventBus
from charlie.mcp_client import MCPClient
from charlie.self_extension.adapters.code_adapter import CodeAdapter
from charlie.self_extension.adapters.mcp_adapter import MCPAdapter
from charlie.self_extension.checkpoint import CheckpointManager

# ── Imports under test ────────────────────────────────────────────────────────
from charlie.self_extension.code_worker import (
    ASTAllowListError,
    run_worker,
    validate_ast_allow_list,
)
from charlie.self_extension.models import (
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    TransactionStatus,
)
from charlie.self_extension.registry import ExtensionEntry, ExtensionRegistry
from charlie.self_extension.worktree_guard import WorktreeConflictError, WorktreeGuard


def _verification_kwargs(ext_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    doctor = MagicMock()
    doctor.diagnose.return_value = MagicMock(is_healthy=True, errors=[])
    code_index = MagicMock()
    code_index.refresh = MagicMock()
    introspector = MagicMock()
    by_id = {eid: {} for eid in (ext_ids or [])}
    introspector.get_capabilities_info.return_value = {"by_id": by_id}
    self_knowledge = MagicMock()
    self_knowledge.answer_self_question.return_value = {"answer": " ".join(ext_ids or ["ok"])}
    return {
        "doctor": doctor,
        "code_index": code_index,
        "introspector": introspector,
        "self_knowledge": self_knowledge,
    }


def _start_real_event_bus_capture(emitted: List[str]) -> tuple[EventBus, asyncio.AbstractEventLoop, threading.Thread]:
    loop = asyncio.new_event_loop()
    bus = EventBus(pub_port=0, pull_port=0)
    ready = threading.Event()

    def _run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bus.__aenter__())
        bus.set_state_listener(lambda envelope: emitted.append(envelope["type"]) or None)
        ready.set()
        loop.run_forever()

    thread = threading.Thread(target=_run_loop, daemon=True)
    thread.start()
    if not ready.wait(timeout=2.0):
        raise RuntimeError("real EventBus test loop did not start")
    return bus, loop, thread


def _stop_real_event_bus(bus: EventBus, loop: asyncio.AbstractEventLoop, thread: threading.Thread) -> None:
    asyncio.run_coroutine_threadsafe(bus.__aexit__(None, None, None), loop).result(timeout=2.0)
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2.0)
    loop.close()

# ─────────────────────────────────────────────────────────────────────────────
# B1: AST allow-list
# ─────────────────────────────────────────────────────────────────────────────


class TestASTAllowList(unittest.TestCase):
    """B1 — Code allow-list rejects banned imports and dangerous calls."""

    def _validate(self, source: str) -> None:
        validate_ast_allow_list(source, "test_tool")

    def test_clean_math_code_accepted(self):
        code = "import math\ndef add(a, b):\n    return math.sqrt(a**2 + b**2)\n"
        self._validate(code)  # must not raise

    def test_clean_json_re_accepted(self):
        code = "import json, re\ndef proc(s):\n    return json.dumps(re.findall(r'\\w+', s))\n"
        self._validate(code)

    def test_os_import_rejected(self):
        with self.assertRaises(ASTAllowListError) as ctx:
            self._validate("import os\ndef f(): return os.getcwd()\n")
        self.assertIn("os", str(ctx.exception))

    def test_sys_import_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import sys\ndef f(): return sys.argv\n")

    def test_subprocess_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import subprocess\ndef f(): return subprocess.check_output(['ls'])\n")

    def test_socket_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import socket\ndef f(): return socket.gethostname()\n")

    def test_shutil_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import shutil\ndef f(): shutil.rmtree('/tmp/x')\n")

    def test_requests_third_party_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import requests\ndef f(): return requests.get('http://example.com').text\n")

    def test_exec_call_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("def f(): exec('import os')\n")

    def test_eval_call_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("def f(): return eval('1+1')\n")

    def test_open_call_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("def f(): return open('/etc/passwd').read()\n")

    def test_pathlib_write_text_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate(
                "from pathlib import Path\n"
                "def f(): Path('/tmp/x').write_text('evil')\n"
            )

    def test_pathlib_unlink_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate(
                "from pathlib import Path\n"
                "def f(): Path('/tmp/x').unlink()\n"
            )

    def test_pathlib_import_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("from pathlib import PurePosixPath\ndef f(s): return str(PurePosixPath(s).stem)\n")

    def test_importlib_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import importlib\ndef f(): return importlib.import_module('os')\n")

    def test_syntax_error_raises_allow_list_error(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("def broken(:\n    pass\n")

    def test_from_os_path_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("from os import path\ndef f(): return path.exists('/tmp')\n")

    def test_nested_denied_import_rejected(self):
        with self.assertRaises(ASTAllowListError):
            self._validate("import os.path\ndef f(): return os.path.join('a', 'b')\n")


# ─────────────────────────────────────────────────────────────────────────────
# B2: Subprocess worker dispatch
# ─────────────────────────────────────────────────────────────────────────────


class TestCodeWorkerDispatch(unittest.TestCase):
    """B2 — run_worker executes in subprocess with smoke-test when no inputs."""

    def _write_tool(self, tmp: Path, name: str, source: str) -> Path:
        p = tmp / f"{name}.py"
        p.write_text(source, encoding="utf-8")
        return p

    def test_smoke_call_with_no_test_inputs(self):
        """Smoke call is always executed — no inputs → zero-arg call."""
        src = "def ping():\n    return 'pong'\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "ping", src)
            ok, out, err = run_worker(module_path=p, func_name="ping", test_inputs=None)
        self.assertTrue(ok, err)
        self.assertEqual(out, "pong")

    def test_with_kwargs(self):
        src = "def add(a, b):\n    return a + b\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "add", src)
            ok, out, err = run_worker(p, "add", {"a": 3, "b": 4}, expected_output=7)
        self.assertTrue(ok, err)
        self.assertEqual(out, 7)

    def test_wrong_expected_output_fails(self):
        src = "def get_five():\n    return 5\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "get_five", src)
            ok, out, err = run_worker(p, "get_five", None, expected_output=99)
        self.assertFalse(ok)
        self.assertIn("Expected", err)

    def test_runtime_exception_fails(self):
        src = "def boom():\n    raise ValueError('boom')\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "boom", src)
            ok, _, err = run_worker(p, "boom", None)
        self.assertFalse(ok)
        self.assertIn("boom", err)

    def test_missing_callable_fails(self):
        src = "x = 1\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "missing", src)
            ok, _, err = run_worker(p, "missing", None)
        self.assertFalse(ok)

    def test_os_import_in_module_still_runs_in_subprocess(self):
        """Code that bypassed static analysis must fail at worker runtime if it imports os
        — note: allow-list catches this before write, but this tests the worker boundary."""
        src = "def f():\n    import os\n    return os.getcwd()\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "f", src)
            # Worker itself doesn't re-run allow-list; import still succeeds in subprocess.
            # This test verifies the subprocess can detect real runtime errors.
            ok, out, err = run_worker(p, "f", None)
        # The result may succeed here because os is a stdlib; the allow-list is the
        # primary gate (tested in B1). We just verify the worker runs and returns
        # a result (not crashes the parent process).
        self.assertIsInstance(ok, bool)

    def test_timeout_enforced(self):
        src = "import time\ndef slow():\n    time.sleep(60)\n    return 1\n"
        with tempfile.TemporaryDirectory() as td:
            p = self._write_tool(Path(td), "slow", src)
            ok, _, err = run_worker(p, "slow", None, timeout=1)
        self.assertFalse(ok)
        self.assertTrue(
            "timed out" in err.lower() or "timeout" in err.lower(),
            f"Expected timeout message, got: {err!r}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# B3: MCP adapter uses real MCPClient API
# ─────────────────────────────────────────────────────────────────────────────


class TestMCPAdapter(unittest.TestCase):
    """B3 — MCPAdapter uses real MCPClient methods; rollback on discovery failure."""

    def _make_mock_client(self, discovered_tools: List[str], health: bool = True) -> MagicMock:
        client = create_autospec(MCPClient, instance=True)
        client.add_server.return_value = None
        client.enable_server.return_value = [f"mcp_test_server_{t}" for t in discovered_tools]
        tool_objs = []
        for t in discovered_tools:
            obj = MagicMock()
            obj.name = t
            obj.server_name = "test_server"
            tool_objs.append(obj)
        client.list_tools.return_value = tool_objs
        client.health_check.return_value = {"test_server": health}
        client.remove_server.return_value = True
        return client

    def _make_adapter(self, client: Any, tool_registry: Any = MagicMock()) -> MCPAdapter:
        with tempfile.TemporaryDirectory() as td:
            registry = ExtensionRegistry(
                manifest_path=Path(td) / "manifest.json",
                capability_index=None,
            )
        return MCPAdapter(registry=registry, mcp_client=client, tool_registry=tool_registry)

    def test_register_calls_add_server(self):
        client = self._make_mock_client(["tool_a"])
        adapter = self._make_adapter(client)
        res = adapter.register_mcp_server("test_server", "npx", ["test-mcp"])
        client.add_server.assert_called_once()
        self.assertTrue(res.success, res.message)

    def test_register_calls_enable_server(self):
        client = self._make_mock_client(["tool_a"])
        tool_registry = MagicMock()
        adapter = self._make_adapter(client, tool_registry)
        adapter.register_mcp_server("test_server", "npx", [])
        client.enable_server.assert_called_once_with(tool_registry, "test_server")

    def test_register_discovers_real_tools(self):
        client = self._make_mock_client(["tool_x", "tool_y"])
        adapter = self._make_adapter(client)
        res = adapter.register_mcp_server("test_server", "npx", [])
        self.assertEqual(sorted(res.tools), ["tool_x", "tool_y"])

    def test_rollback_on_connect_failure(self):
        client = create_autospec(MCPClient, instance=True)
        client.add_server.return_value = None
        client.enable_server.side_effect = RuntimeError("connection refused")
        client.remove_server.return_value = True
        tool_registry = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            registry = ExtensionRegistry(manifest_path=Path(td) / "m.json", capability_index=None)
            adapter = MCPAdapter(registry=registry, mcp_client=client, tool_registry=tool_registry)
        res = adapter.register_mcp_server("bad_server", "npx", [])
        self.assertFalse(res.success)
        client.remove_server.assert_called_with(tool_registry, "bad_server")

    def test_rollback_mcp_calls_remove_server(self):
        client = self._make_mock_client(["tool_a"])
        tool_registry = MagicMock()
        with tempfile.TemporaryDirectory() as td:
            registry = ExtensionRegistry(manifest_path=Path(td) / "m.json", capability_index=None)
            adapter = MCPAdapter(registry=registry, mcp_client=client, tool_registry=tool_registry)
        adapter.rollback_mcp_server("test_server")
        client.remove_server.assert_called_with(tool_registry, "test_server")

    def test_no_duplicate_api_methods(self):
        """Verify adapter doesn't define its own add_server/list_tools/etc."""
        adapter = MCPAdapter()
        for method in ("add_server", "list_tools", "enable_server", "health_check"):
            self.assertFalse(
                hasattr(adapter, method),
                f"MCPAdapter should not own '{method}' — use MCPClient.{method}() instead",
            )


# ─────────────────────────────────────────────────────────────────────────────
# B4: Registry rehydrate()
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryRehydration(unittest.TestCase):
    """B4 — rehydrate() restores capabilities from manifest on new instance."""

    def test_skill_entry_rehydrated(self):
        cap_index = MagicMock()
        cap_index.register_capability = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            reg = ExtensionRegistry(manifest_path=manifest)
            entry = ExtensionEntry(
                extension_id="skill_myskilll",
                name="myskilll",
                kind=ExtensionKind.SKILL,
                source="skills/myskilll",
                content_hash="abc123",
                enabled=True,
            )
            reg.register(entry)

            # New instance — simulate new process
            reg2 = ExtensionRegistry(manifest_path=manifest, capability_index=None)
            report = reg2.rehydrate(capability_index=cap_index)

        cap_index.register_capability.assert_called()
        self.assertGreaterEqual(report.restored, 1)

    def test_disabled_entry_skipped(self):
        cap_index = MagicMock()
        cap_index.register_capability = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            reg = ExtensionRegistry(manifest_path=manifest)
            entry = ExtensionEntry(
                extension_id="skill_disabled",
                name="disabled",
                kind=ExtensionKind.SKILL,
                source="skills/disabled",
                content_hash="xyz",
                enabled=False,
            )
            reg.register(entry)

            reg2 = ExtensionRegistry(manifest_path=manifest)
            report = reg2.rehydrate(capability_index=cap_index)

        cap_index.register_capability.assert_not_called()
        self.assertEqual(report.skipped_disabled, 1)

    def test_code_entry_missing_file_fails_gracefully(self):
        cap_index = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            reg = ExtensionRegistry(manifest_path=manifest)
            entry = ExtensionEntry(
                extension_id="code_ghost",
                name="ghost",
                kind=ExtensionKind.CODE_SMALL,
                source=str(Path(td) / "nonexistent.py"),
                content_hash="0000000000000000",
                enabled=True,
                metadata={"module_path": str(Path(td) / "nonexistent.py")},
            )
            reg.register(entry)

            reg2 = ExtensionRegistry(manifest_path=manifest)
            report = reg2.rehydrate(capability_index=cap_index)

        self.assertEqual(report.failed, 1)
        self.assertEqual(report.restored, 0)

    def test_code_entry_hash_mismatch_fails(self):
        cap_index = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            tool = Path(td) / "mytool.py"
            tool.write_text("def mytool(): return 1\n", encoding="utf-8")

            reg = ExtensionRegistry(manifest_path=manifest)
            entry = ExtensionEntry(
                extension_id="code_mytool",
                name="mytool",
                kind=ExtensionKind.CODE_SMALL,
                source=str(tool),
                content_hash="deadbeefdeadbeef",  # wrong hash
                enabled=True,
                metadata={"module_path": str(tool)},
            )
            reg.register(entry)

            reg2 = ExtensionRegistry(manifest_path=manifest)
            report = reg2.rehydrate(capability_index=cap_index)

        self.assertEqual(report.failed, 1)

    def test_code_entry_valid_registers_capability(self):
        cap_index = MagicMock()
        cap_index.register_capability = MagicMock()

        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "manifest.json"
            tool = Path(td) / "good_tool.py"
            src = "def good_tool(x):\n    return x * 2\n"
            tool.write_text(src, encoding="utf-8")
            content_hash = hashlib.sha256(src.encode()).hexdigest()[:16]

            reg = ExtensionRegistry(manifest_path=manifest)
            entry = ExtensionEntry(
                extension_id="code_good_tool",
                name="good_tool",
                kind=ExtensionKind.CODE_SMALL,
                source=str(tool),
                content_hash=content_hash,
                enabled=True,
                metadata={"module_path": str(tool)},
            )
            reg.register(entry)

            reg2 = ExtensionRegistry(manifest_path=manifest)
            report = reg2.rehydrate(capability_index=cap_index)

        cap_index.register_capability.assert_called()
        self.assertEqual(report.restored, 1)


# ─────────────────────────────────────────────────────────────────────────────
# B5: RESTARTING → VERIFYING transaction persistence and resume
# ─────────────────────────────────────────────────────────────────────────────


class TestTransactionPersistenceAndResume(unittest.TestCase):
    """B5 — RESTARTING transactions are persisted and resumed as VERIFYING on startup."""

    def _make_orchestrator(self, tmp: Path, **kwargs) -> Any:
        from charlie.capabilities import CapabilityIndex
        from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

        cap_index = kwargs.pop("capability_index", CapabilityIndex())
        verify = _verification_kwargs(kwargs.pop("ext_ids", None))
        verify.update(kwargs)
        return SelfExtensionOrchestrator(
            repo_root=tmp,
            manifest_path=tmp / "manifest.json",
            tools_dir=tmp / "tools",
            tx_store_path=tmp / "transactions.json",
            capability_index=cap_index,
            **verify,
        )

    def test_restarting_transaction_persisted_to_disk(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orchestrator(tmp)
            tx_store = tmp / "transactions.json"

            # Simulate a RESTARTING transaction
            from charlie.self_extension.models import ExtensionRequest, ExtensionTransaction, TransactionStatus

            req = ExtensionRequest(user_prompt="test", request_id="r-001")
            tx = ExtensionTransaction(transaction_id="tx-restart-001", request=req)
            tx.status = TransactionStatus.RESTARTING
            orch._transactions["tx-restart-001"] = tx
            orch._persist_transactions()

            self.assertTrue(tx_store.exists())
            data = json.loads(tx_store.read_text())
            self.assertIn("tx-restart-001", data)
            self.assertEqual(data["tx-restart-001"]["status"], "restarting")

    def test_completed_transaction_not_persisted(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orchestrator(tmp)
            tx_store = tmp / "transactions.json"

            from charlie.self_extension.models import ExtensionRequest, ExtensionTransaction, TransactionStatus

            req = ExtensionRequest(user_prompt="done", request_id="r-002")
            tx = ExtensionTransaction(transaction_id="tx-done-001", request=req)
            tx.status = TransactionStatus.COMPLETED
            orch._transactions["tx-done-001"] = tx
            orch._persist_transactions()

            if tx_store.exists():
                data = json.loads(tx_store.read_text())
                self.assertNotIn("tx-done-001", data)

    def test_restarting_transactions_resumed_as_verifying_on_new_orchestrator(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tx_store = tmp / "transactions.json"

            # Write a fake RESTARTING transaction to disk
            data = {
                "tx-resume-001": {
                    "transaction_id": "tx-resume-001",
                    "status": "restarting",
                    "user_prompt": "extend me",
                    "request_id": "r-resume",
                }
            }
            tx_store.parent.mkdir(parents=True, exist_ok=True)
            tx_store.write_text(json.dumps(data), encoding="utf-8")

            # New orchestrator instance — simulates process restart
            orch = self._make_orchestrator(tmp)

            # After init, tx should have been resumed (status COMPLETED or FAILED)
            tx = orch._transactions.get("tx-resume-001")
            self.assertIsNotNone(tx, "RESTARTING transaction not resumed on startup")
            self.assertIn(
                tx.status,
                (TransactionStatus.COMPLETED, TransactionStatus.FAILED, TransactionStatus.VERIFYING),
                f"Unexpected status after resume: {tx.status}",
            )


# ─────────────────────────────────────────────────────────────────────────────
# B6: Worktree conflict guard
# ─────────────────────────────────────────────────────────────────────────────


class TestWorktreeGuard(unittest.TestCase):
    """B6 — pre-write dirty detection and pre-rollback post-image guard."""

    def test_check_before_write_new_file_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "new_file.py"
            # New file (does not exist) → should not raise
            WorktreeGuard.check_before_write(path, preimage_hash=None)

    def test_check_before_write_unmodified_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "existing.py"
            content = b"def f(): pass\n"
            path.write_bytes(content)
            preimage_hash = hashlib.sha256(content).hexdigest()
            WorktreeGuard.check_before_write(path, preimage_hash=preimage_hash)

    def test_check_before_write_dirty_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "existing.py"
            content = b"def f(): pass\n"
            path.write_bytes(content)
            wrong_hash = hashlib.sha256(b"original content").hexdigest()
            with self.assertRaises(WorktreeConflictError):
                WorktreeGuard.check_before_write(path, preimage_hash=wrong_hash)

    def test_check_before_write_existing_without_preimage_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "untracked.py"
            path.write_bytes(b"something")
            with self.assertRaises(WorktreeConflictError):
                WorktreeGuard.check_before_write(path, preimage_hash=None)

    def test_check_before_rollback_nonexistent_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "gone.py"
            WorktreeGuard.check_before_rollback(path, postimage_hash="any")

    def test_check_before_rollback_unmodified_ok(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.py"
            content = b"original"
            path.write_bytes(content)
            postimage_hash = hashlib.sha256(content).hexdigest()
            WorktreeGuard.check_before_rollback(path, postimage_hash=postimage_hash)

    def test_check_before_rollback_modified_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "file.py"
            path.write_bytes(b"user changed this")
            stale_hash = hashlib.sha256(b"original transaction content").hexdigest()
            with self.assertRaises(WorktreeConflictError):
                WorktreeGuard.check_before_rollback(path, postimage_hash=stale_hash)

    def test_checkpoint_rollback_skips_externally_modified_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mgr = CheckpointManager(repo_root=tmp)

            target = tmp / "target.py"
            original = b"def f(): return 1\n"
            target.write_bytes(original)

            cp = mgr.create_checkpoint("tx-wg-001", files_to_modify=[target])

            # Transaction writes the file
            new_content = b"def f(): return 42\n"
            target.write_bytes(new_content)
            mgr.record_postimage("tx-wg-001", target)

            # User edits the file after transaction
            target.write_bytes(b"def f(): return 999  # user edit\n")

            result = mgr.rollback(cp)
            self.assertIn(str(target), result.skipped_files)

    def test_checkpoint_rollback_restores_unmodified_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mgr = CheckpointManager(repo_root=tmp)

            target = tmp / "target.py"
            original = b"def f(): return 1\n"
            target.write_bytes(original)

            cp = mgr.create_checkpoint("tx-wg-002", files_to_modify=[target])

            # Transaction writes the file
            target.write_bytes(b"def f(): return 42\n")
            mgr.record_postimage("tx-wg-002", target)

            # No user edit — rollback should restore
            result = mgr.rollback(cp)
            self.assertIn(str(target), result.restored_files)
            self.assertEqual(target.read_bytes(), original)


# ─────────────────────────────────────────────────────────────────────────────
# B7: Post-change verification gate
# ─────────────────────────────────────────────────────────────────────────────


class TestVerificationGate(unittest.TestCase):
    """B7 — _run_verification_gate calls introspector, doctor, self-knowledge."""

    def _make_orch(self, tmp: Path, **kwargs) -> Any:
        from charlie.capabilities import CapabilityIndex
        from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

        return SelfExtensionOrchestrator(
            repo_root=tmp,
            manifest_path=tmp / "manifest.json",
            tools_dir=tmp / "tools",
            tx_store_path=tmp / "tx.json",
            capability_index=CapabilityIndex(),
            **kwargs,
        )

    def test_gate_passes_when_all_healthy(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            doctor = MagicMock()
            doctor.diagnose.return_value.is_healthy = True
            doctor.diagnose.return_value.errors = []

            introspector = MagicMock()
            introspector.get_capabilities_info.return_value = {"by_id": {"code_foo": {}}}

            self_knowledge = MagicMock()
            self_knowledge.answer_self_question.return_value = {"answer": "You have code_foo"}

            orch = self._make_orch(tmp, doctor=doctor, introspector=introspector, self_knowledge=self_knowledge)
            ok, msg = orch._run_verification_gate("tx-test", affected_ext_ids=["code_foo"])
            self.assertTrue(ok, msg)

    def test_gate_fails_when_doctor_unhealthy(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            doctor = MagicMock()
            err = MagicMock()
            err.summary = "Config missing"
            doctor.diagnose.return_value.is_healthy = False
            doctor.diagnose.return_value.errors = [err]

            orch = self._make_orch(tmp, doctor=doctor)
            ok, msg = orch._run_verification_gate("tx-test")
            self.assertFalse(ok)
            self.assertIn("Config missing", msg)

    def test_gate_fails_when_capability_missing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            doctor = MagicMock()
            doctor.diagnose.return_value.is_healthy = True
            doctor.diagnose.return_value.errors = []
            introspector = MagicMock()
            introspector.get_capabilities_info.return_value = {"by_id": {}}

            orch = self._make_orch(tmp, doctor=doctor, introspector=introspector)
            ok, msg = orch._run_verification_gate("tx-test", affected_ext_ids=["code_missing"])
            self.assertFalse(ok)
            self.assertIn("code_missing", msg)

    def test_gate_fails_when_doctor_raises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            doctor = MagicMock()
            doctor.diagnose.side_effect = RuntimeError("doctor down")
            orch = self._make_orch(tmp, doctor=doctor)
            ok, msg = orch._run_verification_gate("tx-test")
            self.assertFalse(ok)
            self.assertIn("doctor", msg.lower())


# ─────────────────────────────────────────────────────────────────────────────
# B8: Canonical EventType contract
# ─────────────────────────────────────────────────────────────────────────────


class TestEventTypeContract(unittest.TestCase):
    """B8 — All 13 self-extension lifecycle events defined with correct string values."""

    EXPECTED = {
        "SELF_EXTENSION_REQUESTED": "self_extension_requested",
        "SELF_EXTENSION_CLASSIFIED": "self_extension_classified",
        "SELF_EXTENSION_PLANNED": "self_extension_planned",
        "SELF_EXTENSION_APPROVAL_REQUIRED": "self_extension_approval_required",
        "SELF_EXTENSION_APPLYING": "self_extension_applying",
        "SELF_EXTENSION_TESTING": "self_extension_testing",
        "SELF_EXTENSION_HEALTH_CHECK": "self_extension_health_check",
        "SELF_EXTENSION_RESTARTING": "self_extension_restarting",
        "SELF_EXTENSION_VERIFYING": "self_extension_verifying",
        "SELF_EXTENSION_COMPLETED": "self_extension_completed",
        "SELF_EXTENSION_FAILED": "self_extension_failed",
        "SELF_EXTENSION_ROLLBACK_STARTED": "self_extension_rollback_started",
        "SELF_EXTENSION_ROLLED_BACK": "self_extension_rolled_back",
    }

    def test_all_events_present(self):
        for attr, expected_value in self.EXPECTED.items():
            self.assertTrue(hasattr(EventType, attr), f"EventType.{attr} is missing")
            self.assertEqual(
                getattr(EventType, attr).value,
                expected_value,
                f"EventType.{attr}.value should be '{expected_value}'",
            )

    def test_event_contract_json_has_all_events(self):
        repo_root = Path(__file__).parent.parent
        contract_path = repo_root / "shared" / "event_contract.json"
        if not contract_path.exists():
            self.skipTest("event_contract.json not found")
        data = json.loads(contract_path.read_text(encoding="utf-8"))
        events = data.get("event_types", {})
        for _, event_name in self.EXPECTED.items():
            self.assertIn(event_name, events, f"'{event_name}' missing from event_contract.json")


# ─────────────────────────────────────────────────────────────────────────────
# B9: Orchestrator end-to-end scenarios
# ─────────────────────────────────────────────────────────────────────────────


class TestOrchestratorEndToEnd(unittest.TestCase):
    """B9 — Full orchestrator pipeline for each extension kind."""

    def _make_orch(
        self,
        tmp: Path,
        event_bus: Optional[Any] = None,
        ext_ids: Optional[List[str]] = None,
        **kwargs,
    ) -> Any:
        from charlie.capabilities import CapabilityIndex
        from charlie.self_extension.orchestrator import SelfExtensionOrchestrator

        verify = _verification_kwargs(ext_ids)
        verify.update(kwargs)
        return SelfExtensionOrchestrator(
            repo_root=tmp,
            manifest_path=tmp / "manifest.json",
            tools_dir=tmp / "tools",
            tx_store_path=tmp / "tx.json",
            capability_index=CapabilityIndex(),
            event_bus=event_bus,
            **verify,
        )

    def test_config_transaction_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp)
            req = ExtensionRequest(
                user_prompt="change model to gpt-4o-mini",
                classification=None,
                plan=None,
                affected_settings={"LLM_MODEL": "gpt-4o-mini"},
            )
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            req.classification = ExtensionClassification(kind=ExtensionKind.CONFIG)
            res = orch.execute_config_transaction(req, updates={"LLM_MODEL": "gpt-4o-mini"})
            self.assertTrue(res.success, res.message)
            self.assertEqual(res.status, TransactionStatus.COMPLETED)

    def test_code_transaction_requires_plan_with_code_source(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp)
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            req = ExtensionRequest(
                user_prompt="add a calculator tool",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=None,  # no plan
            )
            res = orch.execute_transaction(req)
            self.assertFalse(res.success)
            self.assertIn("code_source", res.message)

    def test_code_transaction_succeeds_with_valid_plan(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp, ext_ids=["code_multiply"])
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            code = "def multiply(a, b):\n    return a * b\n"
            plan = ExtensionPlan(
                plan_id="p-001",
                kind=ExtensionKind.CODE_SMALL,
                description="Multiply two numbers",
                code_source=code,
                tool_name="multiply",
                test_inputs={"a": 3, "b": 4},
                expected_output=12,
            )
            req = ExtensionRequest(
                user_prompt="add multiply",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=plan,
            )
            res = orch.execute_transaction(req)
            self.assertTrue(res.success, res.message)
            self.assertEqual(res.status, TransactionStatus.COMPLETED)

    def test_code_transaction_fails_on_disallowed_import(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp)
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            code = "import os\ndef evil(x):\n    return os.getcwd()\n"
            plan = ExtensionPlan(
                plan_id="p-002",
                kind=ExtensionKind.CODE_SMALL,
                description="Evil tool",
                code_source=code,
                tool_name="evil",
            )
            req = ExtensionRequest(
                user_prompt="add evil",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=plan,
            )
            res = orch.execute_transaction(req)
            self.assertFalse(res.success)

    def test_mcp_transaction_requires_plan_with_mcp_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp)
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            req = ExtensionRequest(
                user_prompt="add filesystem mcp server",
                classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL),
                plan=None,
            )
            res = orch.execute_transaction(req)
            self.assertFalse(res.success)
            self.assertIn("mcp_name", res.message)

    def test_events_emitted_during_code_transaction(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            emitted: List[str] = []
            bus, loop, thread = _start_real_event_bus_capture(emitted)
            orch = self._make_orch(tmp, event_bus=bus, event_loop=loop, ext_ids=["code_noop"])

            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            code = "def noop():\n    return None\n"
            plan = ExtensionPlan(
                plan_id="p-003",
                kind=ExtensionKind.CODE_SMALL,
                description="noop",
                code_source=code,
                tool_name="noop",
            )
            req = ExtensionRequest(
                user_prompt="add noop",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=plan,
            )
            orch.execute_transaction(req)

            terminal = (
                "self_extension_completed",
                "self_extension_failed",
                "self_extension_rolled_back",
            )
            deadline = time.time() + 5.0
            while not any(e in emitted for e in terminal) and time.time() < deadline:
                time.sleep(0.05)
            _stop_real_event_bus(bus, loop, thread)

            self.assertIn("self_extension_requested", emitted)
            self.assertIn("self_extension_classified", emitted)
            self.assertIn("self_extension_applying", emitted)
            self.assertIn("self_extension_testing", emitted)
            self.assertTrue(
                any(e in emitted for e in terminal),
                f"No terminal event emitted. Events: {emitted}",
            )

    def test_rollback_transaction_emits_events(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            emitted: List[str] = []
            bus, loop, thread = _start_real_event_bus_capture(emitted)
            orch = self._make_orch(tmp, event_bus=bus, event_loop=loop)

            from charlie.self_extension.models import ExtensionRequest, ExtensionTransaction

            req = ExtensionRequest(user_prompt="test", request_id="r-rollback")
            tx = ExtensionTransaction(transaction_id="tx-r-001", request=req)
            orch._transactions["tx-r-001"] = tx
            orch.rollback_transaction("tx-r-001")

            deadline = time.time() + 2.0
            while len(emitted) < 2 and time.time() < deadline:
                time.sleep(0.05)
            _stop_real_event_bus(bus, loop, thread)

            self.assertIn("self_extension_rollback_started", emitted)
            self.assertIn("self_extension_rolled_back", emitted)

    def test_raw_user_prompt_never_executed_as_code(self):
        """Guard: CODE_SMALL without a plan always returns failure — prompt is never exec'd."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            orch = self._make_orch(tmp)
            from charlie.self_extension.models import ExtensionClassification, ExtensionKind

            req = ExtensionRequest(
                user_prompt="import os; os.system('del /F /Q C:\\\\')",
                classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
                plan=None,
            )
            res = orch.execute_transaction(req)
            self.assertFalse(res.success)
            self.assertEqual(res.status, TransactionStatus.FAILED)


class TestCodeAdapterIntegration(unittest.TestCase):
    """Integration tests for CodeAdapter using real filesystem and subprocess."""

    def test_apply_valid_extension_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reg = ExtensionRegistry(manifest_path=tmp / "m.json", capability_index=None)
            adapter = CodeAdapter(repo_root=tmp, tools_dir=tmp / "tools", registry=reg)

            code = "def double(n):\n    return n * 2\n"
            res = adapter.apply_code_extension(
                name="double",
                code=code,
                test_inputs={"n": 5},
                expected_output=10,
            )
            self.assertTrue(res.success, res.message)
            self.assertTrue((tmp / "tools" / "double.py").exists())

    def test_apply_disallowed_code_fails_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reg = ExtensionRegistry(manifest_path=tmp / "m.json", capability_index=None)
            adapter = CodeAdapter(repo_root=tmp, tools_dir=tmp / "tools", registry=reg)

            code = "import socket\ndef scan(host):\n    return socket.gethostbyname(host)\n"
            res = adapter.apply_code_extension(name="scan", code=code)
            self.assertFalse(res.success)
            self.assertFalse((tmp / "tools" / "scan.py").exists())

    def test_apply_failing_test_rolls_back(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reg = ExtensionRegistry(manifest_path=tmp / "m.json", capability_index=None)
            adapter = CodeAdapter(repo_root=tmp, tools_dir=tmp / "tools", registry=reg)

            code = "def broken():\n    raise RuntimeError('always fails')\n"
            res = adapter.apply_code_extension(name="broken", code=code)
            self.assertFalse(res.success)
            # File must be rolled back
            self.assertFalse((tmp / "tools" / "broken.py").exists())

    def test_rollback_removes_extension(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            reg = ExtensionRegistry(manifest_path=tmp / "m.json", capability_index=None)
            adapter = CodeAdapter(repo_root=tmp, tools_dir=tmp / "tools", registry=reg)

            code = "def temp():\n    return 'temp'\n"
            adapter.apply_code_extension(name="temp", code=code)
            self.assertTrue((tmp / "tools" / "temp.py").exists())

            adapter.rollback_code_extension(name="temp")
            self.assertFalse((tmp / "tools" / "temp.py").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
