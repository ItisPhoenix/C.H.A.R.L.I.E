"""Main-owned privacy and data-lifecycle operations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from charlie.audit_store import AuditStore
from charlie.backup_service import export_snapshot
from charlie.session_store import SessionStore

logger = logging.getLogger("charlie.privacy_service")

PRIVACY_OPERATIONS = frozenset({"summary", "purge", "backup_export", "audit_list", "audit_export"})
PRIVACY_PURGE_CATEGORIES = frozenset({"browser", "audit", "transcripts", "logs", "artifacts", "all"})


class UnsafePrivacyPathError(ValueError):
    """A configured destructive target is too broad or path-indirected."""


def _path_contains(path: Path, possible_child: Path) -> bool:
    return path == possible_child or path in possible_child.parents


def validate_browser_profile_path(
    path: str | Path,
    *,
    project_root: str | Path | None = None,
) -> Path:
    """Resolve one configured profile path and reject broad or indirect targets."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        absolute = candidate.absolute()
        for component in (absolute, *absolute.parents):
            is_junction = getattr(component, "is_junction", lambda: False)
            if component.is_symlink() or is_junction():
                raise UnsafePrivacyPathError("Browser profile path cannot contain symlink or junction components")
        resolved = candidate.resolve(strict=False)
        home = Path.home().resolve()
        project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    except UnsafePrivacyPathError:
        raise
    except (OSError, RuntimeError) as exc:
        raise UnsafePrivacyPathError("Browser profile path could not be resolved safely") from exc

    anchor = Path(resolved.anchor)
    if resolved == anchor:
        raise UnsafePrivacyPathError("Filesystem root is not a valid browser profile target")
    if _path_contains(resolved, home):
        raise UnsafePrivacyPathError("User home or an ancestor of user home is not a valid browser profile target")
    if _path_contains(resolved, project):
        raise UnsafePrivacyPathError(
            "Project root or an ancestor of project root is not a valid browser profile target"
        )
    if len(resolved.relative_to(anchor).parts) <= 1:
        raise UnsafePrivacyPathError("Broad filesystem ancestor is not a valid browser profile target")
    return resolved


def canonical_privacy_request_fingerprint(operation: str, payload: Mapping[str, Any]) -> str:
    """Return stable, secret-safe identity for one privacy request."""
    operation = str(operation or "invalid").strip()
    if operation == "purge":
        identity = {
            "operation": operation,
            "category": str(payload.get("category") or "").strip().lower(),
            "older_than_days": payload.get("older_than_days"),
            "confirmed": payload.get("confirmed") is True,
        }
    elif operation in {"audit_list", "audit_export"}:
        identity = {"operation": operation, "limit": payload.get("limit")}
    elif operation == "backup_export":
        passphrase = payload.get("passphrase")
        identity = {
            "operation": operation,
            "passphrase_sha256": (
                hashlib.sha256(passphrase.encode("utf-8")).hexdigest()
                if isinstance(passphrase, str) and passphrase
                else None
            ),
        }
    else:
        identity = {"operation": operation}
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


class PrivacyService:
    """Execute privacy operations against explicitly composed runtime owners."""

    def __init__(
        self,
        sessions_db_path: str = "sessions.db",
        audit_db_path: str = "charlie_audit.db",
        browser_dir_path: str = "browser_profile",
        memory_db_path: str = "charlie_memory_graph.db",
        logs_dir_path: str = "logs",
        *,
        session_store: SessionStore | None = None,
        audit_store: AuditStore | None = None,
        artifact_paths: Iterable[str | Path] | None = None,
        memory_paths: Iterable[str | Path] | None = None,
        active_log_path: str | Path | None = None,
        log_handler: logging.Handler | None = None,
        active_artifact_paths: Callable[[], Iterable[str | Path]] | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.sessions_db_path = Path(sessions_db_path)
        self.audit_db_path = Path(audit_db_path)
        self.browser_dir_path = Path(browser_dir_path)
        self.logs_dir_path = Path(logs_dir_path)
        self.session_store = session_store
        self.audit_store = audit_store
        self.artifact_paths = tuple(Path(path) for path in (artifact_paths or (Path("scratchpad.db"),)))
        self.memory_paths = tuple(Path(path) for path in (memory_paths or (Path(memory_db_path),)))
        self.active_log_path = (
            Path(active_log_path) if active_log_path is not None else self.logs_dir_path / "charlie.log"
        )
        self.log_handler = log_handler
        self.active_artifact_paths = active_artifact_paths
        self.project_root = (
            Path(project_root).resolve() if project_root is not None else Path(__file__).resolve().parents[1]
        )

    @staticmethod
    def _path_size(path: Path) -> int:
        """Return size in bytes without following a directory symlink."""
        try:
            if path.is_symlink():
                return path.stat().st_size
            if path.is_file():
                return path.stat().st_size
            if not path.is_dir():
                return 0
            return sum(
                item.stat().st_size
                for item in path.rglob("*")
                if item.is_file() and not item.is_symlink()
            )
        except OSError:
            return 0

    @classmethod
    def _sqlite_storage_size(cls, path: Path) -> int:
        return sum(
            cls._path_size(Path(candidate))
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
        )

    @staticmethod
    def _same_path(left: Path, right: Path) -> bool:
        try:
            return left.resolve() == right.resolve()
        except OSError:
            return left.absolute() == right.absolute()

    def _unique_storage_size(self, paths: Iterable[Path]) -> int:
        seen: set[Path] = set()
        total = 0
        for path in paths:
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                try:
                    key = candidate.resolve()
                except OSError:
                    key = candidate.absolute()
                if key in seen:
                    continue
                seen.add(key)
                total += self._path_size(candidate)
        return total

    def get_storage_summary(self) -> dict[str, Any]:
        """Compute storage truth from the composed runtime paths."""
        transcripts_bytes = self._sqlite_storage_size(self.sessions_db_path)
        audit_bytes = self._sqlite_storage_size(self.audit_db_path)
        browser_bytes = self._path_size(self.browser_dir_path)
        memory_bytes = sum(self._path_size(path) for path in self.memory_paths)
        logs_bytes = self._path_size(self.logs_dir_path)
        artifacts_bytes = sum(self._path_size(path) for path in self.artifact_paths)

        memory_path: str | list[str] = (
            str(self.memory_paths[0])
            if len(self.memory_paths) == 1
            else [str(path) for path in self.memory_paths]
        )
        artifact_path: str | list[str] = (
            str(self.artifact_paths[0])
            if len(self.artifact_paths) == 1
            else [str(path) for path in self.artifact_paths]
        )
        categories = {
            "transcripts": {
                "name": "Chat Transcripts & Sessions",
                "bytes": transcripts_bytes,
                "formatted": self._format_bytes(transcripts_bytes),
                "path": str(self.sessions_db_path),
                "purgeable": self.session_store is not None,
            },
            "terminal": {
                "name": "Terminal Scrollback & History",
                "bytes": 0,
                "formatted": "In-Memory / Managed",
                "path": "ConPTY Buffer",
                "purgeable": False,
                "reason": "Terminal scrollback is in-memory and not persisted by this authority.",
            },
            "audit": {
                "name": "Tool & Security Audit Logs",
                "bytes": audit_bytes,
                "formatted": self._format_bytes(audit_bytes),
                "path": str(self.audit_db_path),
                "purgeable": self.audit_store is not None,
                "shared_with": (
                    "transcripts" if self._same_path(self.audit_db_path, self.sessions_db_path) else None
                ),
            },
            "browser": {
                "name": "Browser Cache & Screenshots",
                "bytes": browser_bytes,
                "formatted": self._format_bytes(browser_bytes),
                "path": str(self.browser_dir_path),
                "purgeable": True,
            },
            "memory": {
                "name": "Long-Term Knowledge & Facts",
                "bytes": memory_bytes,
                "formatted": self._format_bytes(memory_bytes),
                "path": memory_path,
                "paths": [str(path) for path in self.memory_paths],
                "purgeable": False,
                "reason": "Memory deletion remains outside this authority and under the frozen memory authority.",
            },
            "logs": {
                "name": "Runtime Diagnostics & Logs",
                "bytes": logs_bytes,
                "formatted": self._format_bytes(logs_bytes),
                "path": str(self.logs_dir_path),
                "purgeable": True,
                "active_path": str(self.active_log_path),
            },
            "artifacts": {
                "name": "Temporary Runtime Artifacts",
                "bytes": artifacts_bytes,
                "formatted": self._format_bytes(artifacts_bytes),
                "path": artifact_path,
                "paths": [str(path) for path in self.artifact_paths],
                "purgeable": True,
            },
        }
        total_bytes = self._unique_storage_size(
            (
                self.sessions_db_path,
                self.audit_db_path,
                *self.memory_paths,
                self.browser_dir_path,
                self.logs_dir_path,
            )
        ) + sum(self._path_size(path) for path in self.artifact_paths)
        return {
            "authority": "main_runtime",
            "total_bytes": total_bytes,
            "total_formatted": self._format_bytes(total_bytes),
            "categories": categories,
        }

    def purge_category(
        self,
        category: str,
        older_than_days: Optional[int] = None,
        *,
        protected_session_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Purge one explicit category through composed main-owned authorities."""
        category = str(category or "").strip().lower()
        if category not in PRIVACY_PURGE_CATEGORIES:
            return {
                "status": "error",
                "failure_kind": "unsupported",
                "message": f"Unsupported category '{category}'",
            }
        if older_than_days is not None and (
            isinstance(older_than_days, bool) or not isinstance(older_than_days, int) or older_than_days < 0
        ):
            return {
                "status": "error",
                "failure_kind": "invalid_request",
                "message": "older_than_days must be a non-negative integer",
            }

        if category == "all":
            results: list[dict[str, Any]] = []
            for child in ("browser", "audit", "transcripts", "logs", "artifacts"):
                try:
                    results.append(
                        self._purge_one(child, older_than_days, protected_session_ids=protected_session_ids)
                    )
                except Exception as exc:
                    logger.warning("Privacy purge failed for %s: %s", child, type(exc).__name__)
                    results.append(
                        {
                            "status": "error",
                            "category": child,
                            "failure_kind": "exception",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    )
            if any(result.get("status") != "ok" for result in results):
                return {
                    "status": "partial",
                    "failure_kind": "partial_purge",
                    "category": "all",
                    "results": results,
                    "freed_bytes": sum(int(result.get("freed_bytes", 0)) for result in results),
                    "items_purged": sum(int(result.get("items_purged", 0)) for result in results),
                    "message": "Privacy purge completed only partially; inspect per-category results.",
                }
            return {
                "status": "ok",
                "category": "all",
                "results": results,
                "freed_bytes": sum(int(result.get("freed_bytes", 0)) for result in results),
                "items_purged": sum(int(result.get("items_purged", 0)) for result in results),
            }
        return self._purge_one(category, older_than_days, protected_session_ids=protected_session_ids)

    def _purge_one(
        self,
        category: str,
        older_than_days: Optional[int],
        *,
        protected_session_ids: Iterable[str],
    ) -> dict[str, Any]:
        if category == "browser":
            return self._purge_browser()
        if category == "audit":
            return self._purge_audit(older_than_days)
        if category == "transcripts":
            return self._purge_transcripts(older_than_days, protected_session_ids)
        if category == "logs":
            return self._purge_logs(older_than_days)
        return self._purge_artifacts(older_than_days)

    def _purge_browser(self) -> dict[str, Any]:
        path = validate_browser_profile_path(self.browser_dir_path, project_root=self.project_root)
        if not path.exists() and not path.is_symlink():
            return {"status": "ok", "category": "browser", "freed_bytes": 0, "items_purged": 0}
        freed = self._path_size(path)
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        return {"status": "ok", "category": "browser", "freed_bytes": freed, "items_purged": 1}

    def _purge_audit(self, older_than_days: Optional[int]) -> dict[str, Any]:
        if self.audit_store is None:
            return {
                "status": "error",
                "category": "audit",
                "failure_kind": "authority_unavailable",
                "message": "Canonical AuditStore is unavailable; audit data was not purged.",
                "freed_bytes": 0,
                "items_purged": 0,
            }
        before = self._sqlite_storage_size(self.audit_db_path)
        result = self.audit_store.purge(older_than_days=older_than_days)
        after = self._sqlite_storage_size(self.audit_db_path)
        return {
            "status": "ok",
            "category": "audit",
            "freed_bytes": max(0, before - after),
            "items_purged": int(result.get("items_purged", 0)),
            "older_than_days": older_than_days,
        }

    def list_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read audit data through the composed canonical AuditStore."""
        if self.audit_store is None:
            raise RuntimeError("Canonical AuditStore is unavailable.")
        return self.audit_store.list(limit)

    def _purge_transcripts(
        self,
        older_than_days: Optional[int],
        protected_session_ids: Iterable[str],
    ) -> dict[str, Any]:
        if self.session_store is None:
            return {
                "status": "error",
                "category": "transcripts",
                "failure_kind": "authority_unavailable",
                "message": "Canonical SessionStore is unavailable; transcript data was not purged.",
                "freed_bytes": 0,
                "items_purged": 0,
            }
        before = self._sqlite_storage_size(self.sessions_db_path)
        result = self.session_store.purge_transcripts(
            older_than_days=older_than_days,
            protected_session_ids=tuple(protected_session_ids),
        )
        after = self._sqlite_storage_size(self.sessions_db_path)
        return {
            "status": "ok",
            "category": "transcripts",
            "freed_bytes": max(0, before - after),
            "items_purged": int(result.get("items_purged", 0)),
            "messages_purged": int(result.get("messages_purged", 0)),
            "sessions_purged": int(result.get("sessions_purged", 0)),
            "tool_events_purged": int(result.get("tool_events_purged", 0)),
            "older_than_days": older_than_days,
        }

    def _purge_logs(self, older_than_days: Optional[int]) -> dict[str, Any]:
        if not self.logs_dir_path.exists():
            return {"status": "ok", "category": "logs", "freed_bytes": 0, "items_purged": 0}
        cutoff = (
            None
            if older_than_days is None
            else datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        )
        handler = self.log_handler
        if handler is not None:
            handler.acquire()
        try:
            freed = 0
            items = 0
            errors: list[str] = []
            for path in self.logs_dir_path.glob("*.log*"):
                if not path.is_file() or self._same_path(path, self.active_log_path):
                    continue
                try:
                    if cutoff is not None and path.stat().st_mtime >= cutoff:
                        continue
                    freed += self._path_size(path)
                    path.unlink()
                    items += 1
                except OSError as exc:
                    errors.append(f"{path.name}: {exc}")
            result: dict[str, Any] = {
                "status": "error" if errors else "ok",
                "category": "logs",
                "freed_bytes": freed,
                "items_purged": items,
                "active_log_preserved": True,
            }
            if errors:
                result.update(failure_kind="partial_purge", message="; ".join(errors)[:500])
            return result
        finally:
            if handler is not None:
                handler.release()

    def _purge_artifacts(self, older_than_days: Optional[int]) -> dict[str, Any]:
        cutoff = (
            None
            if older_than_days is None
            else datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        )
        active: set[Path] = set()
        if self.active_artifact_paths is not None:
            active = {Path(path).resolve() for path in self.active_artifact_paths()}
        freed = 0
        items = 0
        skipped = 0
        for path in self.artifact_paths:
            resolved = path.resolve()
            if any(resolved == item or resolved in item.parents or item in resolved.parents for item in active):
                skipped += 1
                continue
            if not path.exists() and not path.is_symlink():
                continue
            if cutoff is not None and path.stat().st_mtime >= cutoff:
                continue
            freed += self._path_size(path)
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
            items += 1
        result: dict[str, Any] = {
            "status": "partial" if skipped else "ok",
            "category": "artifacts",
            "freed_bytes": freed,
            "items_purged": items,
            "items_skipped": skipped,
        }
        if skipped:
            result.update(failure_kind="active_artifact", message="Active-task artifacts were preserved.")
        return result

    def export_backup(self, target: str | Path, passphrase: str | None = None) -> dict[str, Any]:
        """Create an archive from a consistent snapshot made by SessionStore."""
        if self.session_store is None:
            raise RuntimeError("Canonical SessionStore is unavailable; backup was not created.")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized_passphrase = passphrase or None
        snapshot_path: Path | None = None
        archive_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{target.name}.", suffix=".sqlite3", dir=target.parent, delete=False
            ) as snapshot_file:
                snapshot_path = Path(snapshot_file.name)
            with tempfile.NamedTemporaryFile(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent, delete=False
            ) as archive_file:
                archive_path = Path(archive_file.name)
            self.session_store.backup_to(snapshot_path)
            manifest = export_snapshot(
                archive_path,
                {"sessions.sqlite3": snapshot_path},
                passphrase=normalized_passphrase,
            )
            os.replace(archive_path, target)
            archive_path = None
            scope = ["sessions"]
            if self._same_path(self.sessions_db_path, self.audit_db_path):
                scope.append("audit")
            return {
                "path": str(target),
                "manifest": manifest,
                "encrypted": bool(normalized_passphrase),
                "scope": scope,
            }
        finally:
            for path in (snapshot_path, archive_path):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Could not clean temporary privacy backup file: %s", path)

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        if num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        if num_bytes < 1024 * 1024 * 1024:
            return f"{num_bytes / (1024 * 1024):.1f} MB"
        return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"
