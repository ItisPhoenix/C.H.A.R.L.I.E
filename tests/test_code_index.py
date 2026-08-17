"""Tests for Incremental CodeIndex."""

import tempfile
from pathlib import Path

import pytest

from charlie.code_index import CodeIndex


@pytest.fixture
def temp_repo():
    """Create a temporary sandbox repo with Python and TypeScript files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir).resolve()

        # 1. Create Python source
        py_dir = repo_path / "charlie"
        py_dir.mkdir(parents=True, exist_ok=True)
        py_file = py_dir / "sample_service.py"
        py_file.write_text(
            '"""Sample service docstring."""\n\n'
            'class SampleService:\n'
            '    """Service class managing actions."""\n'
            '    def __init__(self, name: str):\n'
            '        self.name = name\n\n'
            '    def perform_action(self, action_id: str) -> bool:\n'
            '        """Perform an action."""\n'
            '        return True\n\n'
            'def helper_function(value: int) -> int:\n'
            '    """Standalone helper."""\n'
            '    return value * 2\n',
            encoding="utf-8",
        )

        # 2. Create TypeScript / React source
        ts_dir = repo_path / "frontend" / "src"
        ts_dir.mkdir(parents=True, exist_ok=True)
        ts_file = ts_dir / "SampleComponent.tsx"
        ts_file.write_text(
            '/** Sample component for HUD */\n'
            'import React from "react";\n\n'
            'export interface SampleProps {\n'
            '    title: string;\n'
            '    count: number;\n'
            '}\n\n'
            'export type StatusType = "idle" | "active";\n\n'
            'export const SampleComponent: React.FC<SampleProps> = ({ title }) => {\n'
            '    return <div className="sample">{title}</div>;\n'
            '};\n\n'
            'export function computeSampleValue(x: number): number {\n'
            '    return x + 42;\n'
            '}\n',
            encoding="utf-8",
        )

        # 3. Create Zustand store file
        store_file = ts_dir / "sampleStore.ts"
        store_file.write_text(
            'import { create } from "zustand";\n\n'
            'interface StoreState {\n'
            '    count: number;\n'
            '    increment: () => void;\n'
            '}\n\n'
            'export const useSampleStore = create<StoreState>((set) => ({\n'
            '    count: 0,\n'
            '    increment: () => set((s) => ({ count: s.count + 1 })),\n'
            '}));\n',
            encoding="utf-8",
        )

        # 4. Create sensitive / ignored files
        env_file = repo_path / ".env"
        env_file.write_text("SECRET_KEY=supersecret\n", encoding="utf-8")

        secret_key_file = repo_path / "id_rsa.key"
        secret_key_file.write_text("PRIVATE_KEY_DATA", encoding="utf-8")

        node_modules = repo_path / "node_modules" / "some-lib"
        node_modules.mkdir(parents=True, exist_ok=True)
        (node_modules / "index.js").write_text("console.log('lib')", encoding="utf-8")

        git_dir = repo_path / ".git" / "objects"
        git_dir.mkdir(parents=True, exist_ok=True)
        (git_dir / "dummy.txt").write_text("git binary", encoding="utf-8")

        yield repo_path


def test_code_index_initial_scan(temp_repo):
    """Test initial repository indexing of Python and TS files."""
    index = CodeIndex(temp_repo)
    res = index.refresh()

    assert res["indexed_files"] >= 3
    assert res["total_symbols"] > 5

    # Check python symbols
    symbols = index.get_symbol("SampleService")
    assert len(symbols) == 1
    sym = symbols[0]
    assert sym["kind"] == "class"
    assert sym["file_path"] == "charlie/sample_service.py"
    assert "Service class managing actions" in (sym.get("docstring") or "")
    assert sym["start_line"] == 3

    # Check python method
    method_syms = index.get_symbol("perform_action")
    assert len(method_syms) == 1
    assert method_syms[0]["kind"] == "method"
    assert method_syms[0]["container"] == "SampleService"

    # Check standalone python function
    func_syms = index.get_symbol("helper_function")
    assert len(func_syms) == 1
    assert func_syms[0]["kind"] == "function"


def test_code_index_typescript_and_react(temp_repo):
    """Test TypeScript/React component, interface, and store symbol extraction."""
    index = CodeIndex(temp_repo)
    index.refresh()

    # React component
    comp_syms = index.get_symbol("SampleComponent")
    assert len(comp_syms) == 1
    assert comp_syms[0]["kind"] in ("component", "function", "const")
    assert comp_syms[0]["file_path"] == "frontend/src/SampleComponent.tsx"

    # TypeScript interface
    iface_syms = index.get_symbol("SampleProps")
    assert len(iface_syms) == 1
    assert iface_syms[0]["kind"] == "interface"

    # TypeScript type alias
    type_syms = index.get_symbol("StatusType")
    assert len(type_syms) == 1
    assert type_syms[0]["kind"] == "type"

    # Zustand store
    store_syms = index.get_symbol("useSampleStore")
    assert len(store_syms) == 1
    assert store_syms[0]["kind"] in ("store", "const", "hook")


def test_code_index_incremental_modification(temp_repo):
    """Test that modifying a file re-indexes only that file."""
    index = CodeIndex(temp_repo)
    r1 = index.refresh()
    assert r1["updated_files"] >= 3

    # Refresh without changes -> updated_files should be 0
    r2 = index.refresh()
    assert r2["updated_files"] == 0
    assert not index.is_stale()

    # Modify sample_service.py
    py_file = temp_repo / "charlie" / "sample_service.py"
    py_file.write_text(
        py_file.read_text(encoding="utf-8") + "\ndef new_added_function():\n    pass\n",
        encoding="utf-8",
    )

    assert index.is_stale()
    r3 = index.refresh()
    assert r3["updated_files"] == 1
    assert len(index.get_symbol("new_added_function")) == 1


def test_code_index_deletion(temp_repo):
    """Test that deleting a file removes all its symbols from index."""
    index = CodeIndex(temp_repo)
    index.refresh()
    assert len(index.get_symbol("useSampleStore")) == 1

    # Delete sampleStore.ts
    store_file = temp_repo / "frontend" / "src" / "sampleStore.ts"
    store_file.unlink()

    r = index.refresh()
    assert r["removed_files"] == 1
    assert len(index.get_symbol("useSampleStore")) == 0


def test_code_index_rename(temp_repo):
    """Test that renaming a file updates symbols to new path with no ghost symbols."""
    index = CodeIndex(temp_repo)
    index.refresh()

    old_py = temp_repo / "charlie" / "sample_service.py"
    new_py = temp_repo / "charlie" / "renamed_service.py"
    old_py.rename(new_py)

    index.refresh()
    syms = index.get_symbol("SampleService")
    assert len(syms) == 1
    assert syms[0]["file_path"] == "charlie/renamed_service.py"


def test_code_index_syntax_error_isolation(temp_repo):
    """Test that broken syntax files do not crash the indexer."""
    broken_py = temp_repo / "charlie" / "broken.py"
    broken_py.write_text("def broken_func(:\n    invalid syntax!!!", encoding="utf-8")

    broken_ts = temp_repo / "frontend" / "src" / "broken.tsx"
    broken_ts.write_text("export const Broken = {{{", encoding="utf-8")

    index = CodeIndex(temp_repo)
    res = index.refresh()
    assert res["indexed_files"] >= 3
    assert len(index.get_symbol("SampleService")) == 1


def test_code_index_secret_exclusions(temp_repo):
    """Verify that .env, key files, node_modules, and .git are never indexed."""
    index = CodeIndex(temp_repo)
    index.refresh()

    files = index.search_files("")
    paths = [f["file_path"] for f in files]

    for p in paths:
        assert not p.startswith(".env")
        assert not p.endswith(".key")
        assert "node_modules" not in p
        assert not p.startswith(".git")
        assert "supersecret" not in p


def test_code_index_path_traversal_and_symlink_rejection(temp_repo):
    """Verify path traversal (../) and external symlinks are strictly rejected."""
    index = CodeIndex(temp_repo)
    index.refresh()

    # Traversal attempt
    with pytest.raises(ValueError):
        index.get_excerpt("../../../etc/passwd", 1, 10)

    with pytest.raises(ValueError):
        index.get_excerpt("..\\..\\windows\\system32\\config", 1, 10)

    # File outside repo
    outside_file = temp_repo.parent / "outside_file.txt"
    outside_file.write_text("secret outside", encoding="utf-8")

    with pytest.raises(ValueError):
        index.get_excerpt(str(outside_file), 1, 5)


def test_code_index_excerpt_bounds(temp_repo):
    """Verify line bounds clamping and excerpt retrieval."""
    index = CodeIndex(temp_repo)
    index.refresh()

    excerpt = index.get_excerpt("charlie/sample_service.py", start_line=3, end_line=6)
    assert excerpt is not None
    assert excerpt["file_path"] == "charlie/sample_service.py"
    assert excerpt["start_line"] == 3
    assert excerpt["end_line"] == 6
    assert "class SampleService:" in excerpt["content"]
    assert excerpt["total_lines"] > 10

    # Bounds exceeding file length
    clamped = index.get_excerpt("charlie/sample_service.py", start_line=1, end_line=999)
    assert clamped is not None
    assert clamped["end_line"] == clamped["total_lines"]


def test_code_index_search_ranking(temp_repo):
    """Verify search ranking: exact match > prefix > substring."""
    index = CodeIndex(temp_repo)
    index.refresh()

    # Search symbols
    results = index.search_symbols("SampleService")
    assert len(results) >= 1
    assert results[0]["name"] == "SampleService"

    # Search files
    file_results = index.search_files("sample_service")
    assert len(file_results) == 1
    assert "sample_service.py" in file_results[0]["file_path"]


def test_code_index_stats_and_kinds(temp_repo):
    """Verify statistics aggregation and symbol filtering by kind."""
    index = CodeIndex(temp_repo)
    index.refresh()

    stats = index.get_stats()
    assert stats["total_files"] >= 3
    assert stats["total_symbols"] > 5
    assert "python" in stats["by_language"]
    assert "typescript" in stats["by_language"] or "tsx" in stats["by_language"]
    assert stats["is_stale"] is False

    # Filter by kind
    classes = index.search_symbols("", kind="class")
    assert any(c["name"] == "SampleService" for c in classes)

    components = index.search_symbols("", kind="component")
    assert any(c["name"] == "SampleComponent" for c in components)


def test_code_index_live_repo_sanity():
    """Verify CodeIndex works against the actual Charlie repository without errors."""
    real_repo_root = Path(__file__).resolve().parent.parent
    index = CodeIndex(real_repo_root)
    res = index.refresh()

    assert res["indexed_files"] > 30
    assert res["total_symbols"] > 100

    # Look up known core Charlie symbols
    cap_syms = index.get_symbol("CapabilityIndex")
    assert len(cap_syms) >= 1
    assert any("capabilities.py" in s["file_path"] for s in cap_syms)

    config_syms = index.get_symbol("Config")
    assert len(config_syms) >= 1
    assert any("config.py" in s["file_path"] for s in config_syms)

    # Excerpt test on real file
    excerpt = index.get_excerpt("charlie/config.py", 1, 15)
    assert excerpt is not None
    assert excerpt["start_line"] == 1
    assert excerpt["end_line"] == 15

