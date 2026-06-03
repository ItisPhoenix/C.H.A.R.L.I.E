"""
charlie/utils/cleanup.py

Scans for legacy/stale/committed artifacts and produces a plan
that requires explicit owner approval before any deletion occurs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeletionItem:
    """A single file or directory proposed for deletion."""

    path: str
    reason: str
    size_bytes: int


@dataclass
class DeletionPlan:
    """Collection of proposed deletions. Nothing is removed until approved is True."""

    items: list[DeletionItem] = field(default_factory=list)
    approved: bool = False


def _get_dir_size(path: Path) -> int:
    """Recursively compute total size of a directory."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _get_file_size(path: Path) -> int:
    """Get size of a single file, returning 0 on error."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def generate_deletion_plan() -> DeletionPlan:
    """Scan the project root for artifacts eligible for cleanup.

    Returns a DeletionPlan with approved=False. Does NOT delete anything.

    Scanned categories:
      - charlie/dashboard/          → reason "legacy_dashboard"
      - charlie-daemon.py           → reason "duplicate_entrypoint"
      - .agents/council/2026-05-29-audit-* → reason "stale_audit"
      - *.db in charlie/memory/ and charlie/intelligence/ → reason "committed_db"
      - __pycache__/ directories    → reason "pycache"
      - *.bak files                 → reason "bak"
      - hotpatches/ directory       → reason "hotpatch"
    """
    root = Path(os.getcwd())
    items: list[DeletionItem] = []

    # 1. Legacy vanilla-JS dashboard
    dashboard_dir = root / "charlie" / "dashboard"
    if dashboard_dir.exists() and dashboard_dir.is_dir():
        items.append(
            DeletionItem(
                path=str(dashboard_dir.relative_to(root)),
                reason="legacy_dashboard",
                size_bytes=_get_dir_size(dashboard_dir),
            )
        )

    # 2. Duplicate entrypoint
    daemon_file = root / "charlie-daemon.py"
    if daemon_file.exists() and daemon_file.is_file():
        items.append(
            DeletionItem(
                path="charlie-daemon.py",
                reason="duplicate_entrypoint",
                size_bytes=_get_file_size(daemon_file),
            )
        )

    # 3. Stale audit files in .agents/council/
    council_dir = root / ".agents" / "council"
    if council_dir.exists() and council_dir.is_dir():
        for f in sorted(council_dir.iterdir()):
            if f.is_file() and f.name.startswith("2026-05-29-audit-charlie"):
                items.append(
                    DeletionItem(
                        path=str(f.relative_to(root)),
                        reason="stale_audit",
                        size_bytes=_get_file_size(f),
                    )
                )

    # 4. Committed .db files in charlie/memory/ and charlie/intelligence/
    for subdir in ("memory", "intelligence"):
        target = root / "charlie" / subdir
        if target.exists() and target.is_dir():
            for db_file in sorted(target.glob("*.db")):
                if db_file.is_file():
                    items.append(
                        DeletionItem(
                            path=str(db_file.relative_to(root)),
                            reason="committed_db",
                            size_bytes=_get_file_size(db_file),
                        )
                    )

    # 5. __pycache__/ directories (anywhere in project)
    for pycache in sorted(root.rglob("__pycache__")):
        if pycache.is_dir():
            items.append(
                DeletionItem(
                    path=str(pycache.relative_to(root)),
                    reason="pycache",
                    size_bytes=_get_dir_size(pycache),
                )
            )

    # 6. *.bak files (anywhere in project)
    for bak_file in sorted(root.rglob("*.bak")):
        if bak_file.is_file():
            items.append(
                DeletionItem(
                    path=str(bak_file.relative_to(root)),
                    reason="bak",
                    size_bytes=_get_file_size(bak_file),
                )
            )

    # 7. hotpatches/ directory
    hotpatches_dir = root / "hotpatches"
    if hotpatches_dir.exists() and hotpatches_dir.is_dir():
        items.append(
            DeletionItem(
                path="hotpatches",
                reason="hotpatch",
                size_bytes=_get_dir_size(hotpatches_dir),
            )
        )

    return DeletionPlan(items=items, approved=False)


def print_deletion_plan(plan: DeletionPlan) -> None:
    """Print the deletion plan in a human-readable format."""
    total_bytes = sum(item.size_bytes for item in plan.items)
    print(f"\n{'=' * 60}")
    print("  C.H.A.R.L.I.E. — Deletion Plan")
    print(f"  Status: {'APPROVED' if plan.approved else 'PENDING APPROVAL'}")
    print(f"  Items: {len(plan.items)} | Total size: {total_bytes:,} bytes")
    print(f"{'=' * 60}\n")

    if not plan.items:
        print("  (no items to delete)")
        return

    # Group by reason
    by_reason: dict[str, list[DeletionItem]] = {}
    for item in plan.items:
        by_reason.setdefault(item.reason, []).append(item)

    for reason, group in by_reason.items():
        group_size = sum(i.size_bytes for i in group)
        print(f"  [{reason}] ({len(group)} items, {group_size:,} bytes)")
        for item in group:
            print(f"    - {item.path} ({item.size_bytes:,} bytes)")
        print()


if __name__ == "__main__":
    plan = generate_deletion_plan()
    print_deletion_plan(plan)
