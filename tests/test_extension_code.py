"""Tests for Small Code Extension Adapter."""

import tempfile
from pathlib import Path
import pytest

from charlie.capabilities import CapabilityIndex
from charlie.self_extension.registry import ExtensionRegistry
from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.adapters.code_adapter import CodeAdapter, CodeAdapterResult


@pytest.fixture
def mock_code_env():
    """Create isolated directory for code extension testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()
        tools_dir = repo_root / "data" / "generated_tools"
        tools_dir.mkdir(parents=True, exist_ok=True)

        cap_idx = CapabilityIndex()
        registry = ExtensionRegistry(manifest_path=repo_root / "extensions.json", capability_index=cap_idx)
        checkpoint_mgr = CheckpointManager(repo_root=repo_root)

        adapter = CodeAdapter(
            repo_root=repo_root,
            tools_dir=tools_dir,
            registry=registry,
            capability_index=cap_idx,
            checkpoint_mgr=checkpoint_mgr,
        )

        yield adapter, repo_root, tools_dir, cap_idx, registry


def test_code_adapter_validates_and_registers_tool(mock_code_env):
    """Verify CodeAdapter parses AST, saves module, tests execution, and registers capability."""
    adapter, repo_root, tools_dir, cap_idx, registry = mock_code_env

    tool_code = """
def fibonacci(n: int) -> int:
    \"\"\"Compute the nth Fibonacci number.\"\"\"
    n = int(n)
    if n <= 0:
        return 0
    elif n == 1:
        return 1
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b
"""
    res = adapter.apply_code_extension(
        name="fibonacci",
        code=tool_code,
        test_inputs={"n": 5},
        expected_output=5,
    )

    assert isinstance(res, CodeAdapterResult)
    assert res.success is True
    assert (tools_dir / "fibonacci.py").exists()

    # Capability registered
    cap = cap_idx.get_capability("code_fibonacci")
    assert cap is not None
    assert cap.name == "fibonacci"
    assert "fibonacci" in cap.operations


def test_code_adapter_rejects_invalid_syntax_and_unsafe_ast(mock_code_env):
    """Verify CodeAdapter catches syntax errors and disallowed imports before writing."""
    adapter, _, tools_dir, _, _ = mock_code_env

    # 1. Syntax Error
    bad_code = "def broken(:\n    pass"
    res1 = adapter.apply_code_extension(name="broken", code=bad_code)
    assert res1.success is False
    assert "Syntax error" in res1.message

    # 2. Blocked unsafe import (ctypes)
    unsafe_code = """
import ctypes
def evil():
    \"\"\"Unsafe function.\"\"\"
    return ctypes.string_at(0)
"""
    res2 = adapter.apply_code_extension(name="evil", code=unsafe_code)
    assert res2.success is False
    assert "Disallowed import" in res2.message
    assert not (tools_dir / "evil.py").exists()


def test_code_adapter_rolls_back_on_test_failure(mock_code_env):
    """Verify CodeAdapter rolls back file creation if test invocation fails or raises."""
    adapter, _, tools_dir, cap_idx, _ = mock_code_env

    failing_code = """
def failing_calc(x: int) -> int:
    \"\"\"Calculator that throws an error.\"\"\"
    raise RuntimeError("Calculation failed internally")
"""
    res = adapter.apply_code_extension(
        name="failing_calc",
        code=failing_code,
        test_inputs={"x": 10},
    )

    assert res.success is False
    assert not (tools_dir / "failing_calc.py").exists()
    assert cap_idx.get_capability("code_failing_calc") is None
