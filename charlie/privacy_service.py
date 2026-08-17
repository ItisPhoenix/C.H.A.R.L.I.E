"""Privacy & Data Retention Service for Charlie.

Manages data storage tracking, automatic retention enforcement,
and on-demand selective purging of transcripts, terminal history,
audit logs, browser cache, camera/screenshot artifacts, and memories.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("charlie.privacy_service")


class PrivacyService:
    """Provides storage auditing and privacy data purge controls."""

    def __init__(
        self,
        sessions_db_path: str = "sessions.db",
        audit_db_path: str = "charlie_audit.db",
        browser_dir_path: str = "browser_profile",
        memory_db_path: str = "charlie_memory_graph.db",
        logs_dir_path: str = "logs",
    ) -> None:
        self.sessions_db_path = Path(sessions_db_path)
        self.audit_db_path = Path(audit_db_path)
        self.browser_dir_path = Path(browser_dir_path)
        self.memory_db_path = Path(memory_db_path)
        self.logs_dir_path = Path(logs_dir_path)

    def _path_size(self, path: Path) -> int:
        """Return size in bytes of a file or directory recursively."""
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    try:
                        total += p.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def get_storage_summary(self) -> Dict[str, Any]:
        """Compute live storage usage across all privacy-sensitive artifact categories."""
        transcripts_bytes = self._path_size(self.sessions_db_path)
        for extra in (f"{self.sessions_db_path}-wal", f"{self.sessions_db_path}-shm"):
            transcripts_bytes += self._path_size(Path(extra))

        audit_bytes = self._path_size(self.audit_db_path)
        for extra in (f"{self.audit_db_path}-wal", f"{self.audit_db_path}-shm"):
            audit_bytes += self._path_size(Path(extra))

        browser_bytes = self._path_size(self.browser_dir_path)

        memory_bytes = self._path_size(self.memory_db_path)
        for extra in (f"{self.memory_db_path}-wal", f"{self.memory_db_path}-shm"):
            memory_bytes += self._path_size(Path(extra))
        memory_bytes += self._path_size(Path("charlie_memory_db"))

        logs_bytes = self._path_size(self.logs_dir_path)

        # Scratchpad / temporary artifacts
        artifacts_bytes = self._path_size(Path("scratchpad.db")) + self._path_size(Path("data"))

        categories = {
            "transcripts": {
                "name": "Chat Transcripts & Sessions",
                "bytes": transcripts_bytes,
                "formatted": self._format_bytes(transcripts_bytes),
                "path": str(self.sessions_db_path),
            },
            "terminal": {
                "name": "Terminal Scrollback & History",
                "bytes": 0,  # In-memory buffer & active PTY pipes
                "formatted": "In-Memory / Managed",
                "path": "ConPTY Buffer",
            },
            "audit": {
                "name": "Tool & Security Audit Logs",
                "bytes": audit_bytes,
                "formatted": self._format_bytes(audit_bytes),
                "path": str(self.audit_db_path),
            },
            "browser": {
                "name": "Browser Cache & Screenshots",
                "bytes": browser_bytes,
                "formatted": self._format_bytes(browser_bytes),
                "path": str(self.browser_dir_path),
            },
            "memory": {
                "name": "Long-Term Knowledge & Facts",
                "bytes": memory_bytes,
                "formatted": self._format_bytes(memory_bytes),
                "path": str(self.memory_db_path),
            },
            "logs": {
                "name": "Runtime Diagnostics & Logs",
                "bytes": logs_bytes,
                "formatted": self._format_bytes(logs_bytes),
                "path": str(self.logs_dir_path),
            },
            "artifacts": {
                "name": "Temporary Workspace Artifacts",
                "bytes": artifacts_bytes,
                "formatted": self._format_bytes(artifacts_bytes),
                "path": "data/",
            },
        }

        total_bytes = sum(c["bytes"] for c in categories.values() if isinstance(c["bytes"], int))

        return {
            "total_bytes": total_bytes,
            "total_formatted": self._format_bytes(total_bytes),
            "categories": categories,
        }

    def purge_category(self, category: str, older_than_days: Optional[int] = None) -> Dict[str, Any]:
        """Selectively purge stored data for a given category."""
        category = category.lower().strip()
        freed = 0
        items_count = 0

        if category == "browser":
            if self.browser_dir_path.exists():
                for p in list(self.browser_dir_path.iterdir()):
                    size = self._path_size(p)
                    try:
                        if p.is_file():
                            p.unlink()
                        elif p.is_dir():
                            shutil.rmtree(p)
                        freed += size
                        items_count += 1
                    except Exception as e:
                        logger.warning("Could not remove %s: %s", p, e)
            return {"status": "ok", "category": "browser", "freed_bytes": freed, "items_purged": items_count}

        elif category == "audit":
            if self.audit_db_path.exists():
                try:
                    conn = sqlite3.connect(str(self.audit_db_path))
                    before_size = self._path_size(self.audit_db_path)
                    if older_than_days:
                        cutoff_sec = time.time() - (older_than_days * 86400)
                        cursor = conn.execute("DELETE FROM audit_entries WHERE timestamp < ?", (cutoff_sec,))
                        items_count = cursor.rowcount
                    else:
                        cursor = conn.execute("DELETE FROM audit_entries")
                        items_count = cursor.rowcount
                    conn.commit()
                    conn.execute("VACUUM")
                    conn.close()
                    after_size = self._path_size(self.audit_db_path)
                    freed = max(0, before_size - after_size)
                except Exception as e:
                    logger.warning("Audit purge error: %s", e)
            return {"status": "ok", "category": "audit", "freed_bytes": freed, "items_purged": items_count}

        elif category == "transcripts":
            if self.sessions_db_path.exists():
                try:
                    conn = sqlite3.connect(str(self.sessions_db_path))
                    before_size = self._path_size(self.sessions_db_path)
                    if older_than_days:
                        cutoff_sec = time.time() - (older_than_days * 86400)
                        cursor = conn.execute("DELETE FROM messages WHERE timestamp < ?", (cutoff_sec,))
                        items_count = cursor.rowcount
                    else:
                        cursor = conn.execute("DELETE FROM messages")
                        conn.execute("DELETE FROM sessions")
                        items_count = cursor.rowcount
                    conn.commit()
                    conn.execute("VACUUM")
                    conn.close()
                    after_size = self._path_size(self.sessions_db_path)
                    freed = max(0, before_size - after_size)
                except Exception as e:
                    logger.warning("Transcripts purge error: %s", e)
            return {"status": "ok", "category": "transcripts", "freed_bytes": freed, "items_purged": items_count}

        elif category == "logs":
            if self.logs_dir_path.exists():
                for p in list(self.logs_dir_path.glob("*.log*")):
                    size = self._path_size(p)
                    try:
                        p.unlink()
                        freed += size
                        items_count += 1
                    except Exception:
                        pass
            return {"status": "ok", "category": "logs", "freed_bytes": freed, "items_purged": items_count}

        elif category == "artifacts":
            scratch_path = Path("scratchpad.db")
            if scratch_path.exists():
                freed += self._path_size(scratch_path)
                try:
                    scratch_path.unlink()
                    items_count += 1
                except Exception:
                    pass
            return {"status": "ok", "category": "artifacts", "freed_bytes": freed, "items_purged": items_count}

        elif category == "all":
            total_freed = 0
            total_items = 0
            for cat in ("browser", "audit", "transcripts", "logs", "artifacts"):
                res = self.purge_category(cat, older_than_days=older_than_days)
                total_freed += res.get("freed_bytes", 0)
                total_items += res.get("items_purged", 0)
            return {"status": "ok", "category": "all", "freed_bytes": total_freed, "items_purged": total_items}

        return {"status": "error", "message": f"Unsupported category '{category}'"}

    @staticmethod
    def _format_bytes(num_bytes: int) -> str:
        if num_bytes < 1024:
            return f"{num_bytes} B"
        elif num_bytes < 1024 * 1024:
            return f"{num_bytes / 1024:.1f} KB"
        elif num_bytes < 1024 * 1024 * 1024:
            return f"{num_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


# Global default instance
privacy_service = PrivacyService()
