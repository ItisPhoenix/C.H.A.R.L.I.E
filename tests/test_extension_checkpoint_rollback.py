"""Tests for CheckpointManager and Worktree-Safe Scoped Rollback Engine."""

import tempfile
from pathlib import Path

import pytest

from charlie.self_extension.checkpoint import CheckpointManager
from charlie.self_extension.models import ExtensionCheckpoint


@pytest.fixture
def mock_checkpoint_env():
    """Create isolated directory structure with existing files and unrelated dirty files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir).resolve()

        # Existing files
        src_dir = repo_root / "charlie"
        src_dir.mkdir(parents=True, exist_ok=True)
        file1 = src_dir / "utils.py"
        file1.write_text("def original_util(): return 1\n", encoding="utf-8")

        # Unrelated dirty file (should remain completely untouched)
        unrelated_file = repo_root / "unrelated_user_work.txt"
        unrelated_file.write_text("user notes: do not touch\n", encoding="utf-8")

        mgr = CheckpointManager(repo_root=repo_root)

        yield mgr, repo_root, file1, unrelated_file


def test_checkpoint_captures_preimages_for_affected_files_only(mock_checkpoint_env):
    """Verify checkpoint records exact byte content only for target files."""
    mgr, repo_root, file1, unrelated_file = mock_checkpoint_env

    cp = mgr.create_checkpoint(
        transaction_id="tx-101",
        files_to_modify=[file1],
        files_to_create=[repo_root / "charlie" / "new_helper.py"],
    )

    assert isinstance(cp, ExtensionCheckpoint)
    assert cp.checkpoint_id == "tx-101"
    assert str(file1) in cp.affected_files_preimage
    assert cp.affected_files_preimage[str(file1)] == file1.read_bytes()
    # Unrelated files not in preimage
    assert str(unrelated_file) not in cp.affected_files_preimage


def test_scoped_rollback_restores_modified_and_removes_new_files(mock_checkpoint_env):
    """Verify rollback restores modified files and deletes created files without touching unrelated files."""
    mgr, repo_root, file1, unrelated_file = mock_checkpoint_env
    new_file = repo_root / "charlie" / "new_helper.py"

    cp = mgr.create_checkpoint(
        transaction_id="tx-102",
        files_to_modify=[file1],
        files_to_create=[new_file],
    )

    # Mutate existing file and create new file
    file1.write_text("def broken_code(): raise Exception\n", encoding="utf-8")
    new_file.write_text("def new_code(): pass\n", encoding="utf-8")
    # Mutate unrelated user file
    unrelated_file.write_text("user notes: modified by user during transaction\n", encoding="utf-8")

    # Perform scoped rollback
    res = mgr.rollback(cp)
    assert res.success is True

    # 1. file1 should be restored to original
    assert file1.read_text(encoding="utf-8") == "def original_util(): return 1\n"
    # 2. new_file should be deleted
    assert not new_file.exists()
    # 3. unrelated_file should be preserved exactly as modified by user
    assert unrelated_file.read_text(encoding="utf-8") == "user notes: modified by user during transaction\n"


def test_checkpoint_rejects_path_traversal_escapes(mock_checkpoint_env):
    """Verify checkpoint manager rejects paths attempting directory traversal."""
    mgr, repo_root, _, _ = mock_checkpoint_env

    with pytest.raises(ValueError, match="outside repository boundary"):
        mgr.create_checkpoint(
            transaction_id="tx-103",
            files_to_modify=[repo_root / ".." / "outside.py"],
        )
