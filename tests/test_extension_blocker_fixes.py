"""
Release-blocker regression tests for controlled self-extension.

Validates:
  1. Generic execute_transaction never treats raw user_prompt as executable code.
  2. Generic execute_transaction never falls back to hardcoded MCP server name/command.
  3. CODE_SMALL subprocess isolation (execution in child process, not in-process exec_module).
  4. Extended AST deny-list: exec(), eval(), subprocess, socket, ctypes, etc.
  5. MCP_TOOL generic path requires validated ExtensionPlan with mcp_name + mcp_command.
  6. SKILL generic path requires validated ExtensionPlan with raw_text.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from charlie.capabilities import CapabilityIndex
from charlie.config import Config
from charlie.self_extension.adapters.code_adapter import (
    ASTValidationError,
    CodeAdapter,
    _validate_ast_safety,
)
from charlie.self_extension.models import (
    ExtensionClassification,
    ExtensionKind,
    ExtensionPlan,
    ExtensionRequest,
    TransactionStatus,
)
from charlie.self_extension.orchestrator import SelfExtensionOrchestrator
from charlie.settings_service import SettingsService

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_env():
    """Create a fully isolated environment for orchestrator testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()
        env_file = repo_root / ".env"
        env_file.write_text("LLM_MODEL=gpt-4o-mini\n", encoding="utf-8")

        manifest_path = repo_root / "extensions.json"
        skills_dir = repo_root / "data" / "skills"
        tools_dir = repo_root / "data" / "generated_tools"
        skills_dir.mkdir(parents=True, exist_ok=True)
        tools_dir.mkdir(parents=True, exist_ok=True)

        cfg = Config()
        cfg.llm_model = "gpt-4o-mini"
        settings_service = SettingsService(config_instance=cfg, env_path=env_file)
        cap_idx = CapabilityIndex()

        orchestrator = SelfExtensionOrchestrator(
            repo_root=repo_root,
            manifest_path=manifest_path,
            skills_dir=skills_dir,
            tools_dir=tools_dir,
            settings_service=settings_service,
            config=cfg,
            capability_index=cap_idx,
            doctor=MagicMock(diagnose=MagicMock(return_value=MagicMock(is_healthy=True, errors=[]))),
            code_index=MagicMock(refresh=MagicMock()),
            introspector=MagicMock(
                get_capabilities_info=MagicMock(return_value={"by_id": {"code_add_two": {}}})
            ),
            self_knowledge=MagicMock(
                answer_self_question=MagicMock(return_value={"answer": "code_add_two"})
            ),
        )

        yield orchestrator, repo_root, cap_idx, tools_dir


@pytest.fixture
def code_adapter_env():
    """Isolated CodeAdapter for low-level adapter tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()
        tools_dir = repo_root / "generated_tools"
        tools_dir.mkdir()
        from charlie.self_extension.checkpoint import CheckpointManager
        from charlie.self_extension.registry import ExtensionRegistry

        cap_idx = CapabilityIndex()
        registry = ExtensionRegistry(manifest_path=repo_root / "ext.json", capability_index=cap_idx)
        cp_mgr = CheckpointManager(repo_root=repo_root)
        adapter = CodeAdapter(
            repo_root=repo_root,
            tools_dir=tools_dir,
            registry=registry,
            capability_index=cap_idx,
            checkpoint_mgr=cp_mgr,
        )
        yield adapter, tools_dir, cap_idx


# ---------------------------------------------------------------------------
# Blocker 1 — Generic path must NOT execute raw user_prompt as CODE_SMALL
# ---------------------------------------------------------------------------

class TestGenericPathRequiresPlanForCodeSmall:
    def test_code_small_without_plan_is_rejected(self, isolated_env):
        """Generic execute_transaction must reject CODE_SMALL if no plan is provided."""
        orchestrator, _, _, tools_dir = isolated_env

        req = ExtensionRequest(
            user_prompt="def malicious(): import os; os.system('rm -rf /')",
            explicit_user_request=True,
            classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
        )
        # No plan attached — must fail with a clear error, not execute the prompt
        res = orchestrator.execute_transaction(req)

        assert res.success is False
        assert res.status == TransactionStatus.FAILED
        assert "code_source" in res.message.lower() or "plan" in res.message.lower()
        # Crucially: no .py file must have been written
        assert list(tools_dir.glob("*.py")) == [], "Raw prompt must never be written as executable code"

    def test_code_small_with_plan_succeeds(self, isolated_env):
        """Generic execute_transaction executes CODE_SMALL when a validated plan is provided."""
        orchestrator, _, cap_idx, tools_dir = isolated_env

        code = """\
def add_two(a: int, b: int) -> int:
    \"\"\"Add two integers.\"\"\"
    return int(a) + int(b)
"""
        plan = ExtensionPlan(
            plan_id="plan-add-two",
            kind=ExtensionKind.CODE_SMALL,
            description="Add helper: add_two",
            code_source=code,
            tool_name="add_two",
            test_inputs={"a": 2, "b": 3},
            expected_output=5,
        )
        req = ExtensionRequest(
            user_prompt="Add a function to add two numbers",
            explicit_user_request=True,
            classification=ExtensionClassification(kind=ExtensionKind.CODE_SMALL),
            plan=plan,
        )
        res = orchestrator.execute_transaction(req)

        assert res.success is True, res.message
        assert res.status == TransactionStatus.COMPLETED
        assert (tools_dir / "add_two.py").exists()
        assert cap_idx.get_capability("code_add_two") is not None


# ---------------------------------------------------------------------------
# Blocker 2 — Generic path must NOT hardcode MCP server name/command
# ---------------------------------------------------------------------------

class TestGenericPathRequiresPlanForMCPTool:
    def test_mcp_tool_without_plan_is_rejected(self, isolated_env):
        """Generic execute_transaction must reject MCP_TOOL if no validated plan is provided."""
        orchestrator, _, _, _ = isolated_env

        req = ExtensionRequest(
            user_prompt="Add the GitHub MCP server",
            explicit_user_request=True,
            classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL),
            # No plan — mcp_name/mcp_command not specified
        )
        res = orchestrator.execute_transaction(req)

        assert res.success is False
        assert res.status == TransactionStatus.FAILED
        assert "mcp_name" in res.message.lower() or "plan" in res.message.lower()

    def test_mcp_tool_with_plan_succeeds(self, isolated_env):
        """Generic execute_transaction executes MCP_TOOL when plan specifies mcp_name/mcp_command."""
        orchestrator, _, cap_idx, _ = isolated_env
        orchestrator._introspector.get_capabilities_info.return_value = {"by_id": {"mcp_github": {}}}
        orchestrator._self_knowledge.answer_self_question.return_value = {"answer": "mcp_github"}

        plan = ExtensionPlan(
            plan_id="plan-github-mcp",
            kind=ExtensionKind.MCP_TOOL,
            description="GitHub MCP server",
            mcp_name="github",
            mcp_command="npx",
            mcp_args=["-y", "@modelcontextprotocol/server-github"],
            mcp_declared_tools=["create_issue", "list_prs"],
        )
        req = ExtensionRequest(
            user_prompt="Add the GitHub MCP server",
            explicit_user_request=True,
            classification=ExtensionClassification(kind=ExtensionKind.MCP_TOOL),
            plan=plan,
        )
        res = orchestrator.execute_transaction(req)

        assert res.success is True, res.message
        cap = cap_idx.get_capability("mcp_github")
        assert cap is not None
        assert "create_issue" in cap.operations


# ---------------------------------------------------------------------------
# Blocker 3 — Generic path must NOT accept SKILL without raw_text in plan
# ---------------------------------------------------------------------------

class TestGenericPathRequiresPlanForSkill:
    def test_skill_without_plan_raw_text_is_rejected(self, isolated_env):
        """Generic execute_transaction must reject SKILL if plan has no raw_text."""
        orchestrator, _, _, _ = isolated_env

        req = ExtensionRequest(
            user_prompt="Learn this procedure",
            explicit_user_request=True,
            classification=ExtensionClassification(kind=ExtensionKind.SKILL),
            affected_capabilities=["my_skill"],
            # No plan
        )
        res = orchestrator.execute_transaction(req)

        assert res.success is False
        assert res.status == TransactionStatus.FAILED
        assert "raw_text" in res.message.lower() or "plan" in res.message.lower()


# ---------------------------------------------------------------------------
# Blocker 4 — Extended AST deny-list (exec, eval, subprocess, socket, ctypes)
# ---------------------------------------------------------------------------

class TestExtendedASTDenyList:
    @pytest.mark.parametrize("dangerous_code,expected_fragment", [
        # exec() call
        ('def f(): exec("import os")', "Disallowed call 'exec'"),
        # eval() call
        ('def f(): return eval("1+1")', "Disallowed call 'eval'"),
        # compile() call
        ('def f(): compile("x=1","<>","exec")', "Disallowed call 'compile'"),
        # subprocess import
        ("import subprocess\ndef f(): pass", "Disallowed import 'subprocess'"),
        # socket import
        ("import socket\ndef f(): pass", "Disallowed import 'socket'"),
        # ctypes import (original)
        ("import ctypes\ndef f(): pass", "Disallowed import 'ctypes'"),
        # winreg (Windows platform internals)
        ("import winreg\ndef f(): pass", "Disallowed import 'winreg'"),
        # importlib
        ("import importlib\ndef f(): pass", "Disallowed import 'importlib'"),
        # from subprocess import run
        ("from subprocess import run\ndef f(): pass", "Disallowed import 'subprocess'"),
    ])
    def test_blocked_patterns(self, dangerous_code, expected_fragment):
        with pytest.raises(ASTValidationError) as exc_info:
            _validate_ast_safety(dangerous_code, "test")
        assert expected_fragment in str(exc_info.value)

    def test_safe_code_passes_validation(self):
        """Standard library math and basic operations must not be blocked."""
        safe_code = """\
import math
import json

def safe_fn(x: float) -> float:
    \"\"\"Compute square root.\"\"\"
    return math.sqrt(x)
"""
        tree = _validate_ast_safety(safe_code, "safe_fn")
        assert tree is not None


# ---------------------------------------------------------------------------
# Blocker 5 — Subprocess isolation: execution test runs in a child process
# ---------------------------------------------------------------------------

class TestSubprocessIsolation:
    def test_code_executes_in_subprocess_not_in_process(self, code_adapter_env):
        """Verify CodeAdapter subprocess verification runs without polluting host sys.modules."""
        adapter, tools_dir, cap_idx = code_adapter_env

        pure_code = """\
import math

def calc_hypot(a: float, b: float) -> float:
    \"\"\"Calculate hypotenuse.\"\"\"
    return math.hypot(a, b)
"""
        res = adapter.apply_code_extension(
            name="calc_hypot",
            code=pure_code,
            test_inputs={"a": 3.0, "b": 4.0},
            expected_output=5.0,
        )
        import sys
        assert res.success is True, res.message
        assert (tools_dir / "calc_hypot.py").exists()
        # Verify module was not imported directly into current host process namespace
        assert "calc_hypot" not in sys.modules

    def test_code_with_side_effect_fails_in_subprocess_and_rolls_back(self, code_adapter_env):
        """Verify that if subprocess execution raises, the file is rolled back."""
        adapter, tools_dir, _ = code_adapter_env

        boom_code = """\
def bomber():
    \"\"\"Intentional boom.\"\"\"
    raise RuntimeError("Boom from subprocess")
"""
        res = adapter.apply_code_extension(
            name="bomber",
            code=boom_code,
            test_inputs={},
        )
        assert res.success is False
        assert not (tools_dir / "bomber.py").exists(), "Rolled-back file must not exist"

    def test_timeout_respected(self, code_adapter_env):
        """A function that hangs must fail within the subprocess timeout."""
        adapter, tools_dir, _ = code_adapter_env

        # Override timeout to 1 second for the test
        from charlie.self_extension.adapters.code_adapter import _run_subprocess_verification

        slow_code = """\
import time

def slow_fn() -> str:
    \"\"\"Sleeps forever.\"\"\"
    time.sleep(60)
    return "done"
"""
        module_path = tools_dir / "slow_fn.py"
        module_path.write_text(slow_code, encoding="utf-8")

        ok, _, error_msg = _run_subprocess_verification(
            module_path=module_path,
            func_name="slow_fn",
            test_inputs={},
            expected_output=None,
            timeout=1,
        )
        assert ok is False
        assert "timed out" in error_msg.lower()
