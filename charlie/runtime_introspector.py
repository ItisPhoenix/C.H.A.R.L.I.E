"""Unified live runtime introspection layer for Charlie V1.

Queries authoritative state across process, models, capabilities, task journal,
capability leases, subsystem health, MCP servers, memory systems, and terminal/desktop/browser
subsystems with strict secret masking.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("charlie.runtime_introspector")

_START_TIME = time.time()


@dataclass
class RuntimeSnapshot:
    """Structured, secret-safe snapshot of Charlie's live runtime state."""

    timestamp: float
    process: Dict[str, Any]
    model: Dict[str, Any]
    capabilities: Dict[str, Any]
    tasks: Dict[str, Any]
    leases: Dict[str, Any]
    subsystem_health: Dict[str, Any]
    mcp: Dict[str, Any]
    memory: Dict[str, Any]
    subsystems: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RuntimeIntrospector:
    """Collects and aggregates live runtime truth from authoritative subsystem owners."""

    def __init__(
        self,
        config: Optional[Any] = None,
        capability_index: Optional[Any] = None,
        health_registry: Optional[Any] = None,
        task_journal: Optional[Any] = None,
        lease_manager: Optional[Any] = None,
        mcp_client: Optional[Any] = None,
        memory_service: Optional[Any] = None,
        terminal_manager: Optional[Any] = None,
    ) -> None:
        self._config = config
        self._capability_index = capability_index
        self._health_registry = health_registry
        self._task_journal = task_journal
        self._lease_manager = lease_manager
        self._mcp_client = mcp_client
        self._memory_service = memory_service
        self._terminal_manager = terminal_manager

    # -------------------------------------------------------------------------
    # Lazy Singletons
    # -------------------------------------------------------------------------

    def _get_config(self) -> Any:
        if self._config is not None:
            return self._config
        try:
            from charlie.config import config
            return config
        except Exception:
            return None

    def _get_capability_index(self) -> Any:
        if self._capability_index is not None:
            return self._capability_index
        try:
            from charlie.capabilities import get_capability_index
            return get_capability_index()
        except Exception:
            return None

    def _get_health_registry(self) -> Any:
        if self._health_registry is not None:
            return self._health_registry
        try:
            from charlie.core import _subsystem_health
            return _subsystem_health
        except Exception:
            return None

    def _get_task_journal(self) -> Any:
        if self._task_journal is not None:
            return self._task_journal
        try:
            from charlie.task_journal import get_task_journal
            return get_task_journal()
        except Exception:
            return None

    def _get_lease_manager(self) -> Any:
        if self._lease_manager is not None:
            return self._lease_manager
        try:
            from charlie.resource_locks import get_capability_lease_manager
            return get_capability_lease_manager()
        except Exception:
            return None

    def _get_mcp_client(self) -> Any:
        if self._mcp_client is not None:
            return self._mcp_client
        try:
            from charlie.mcp_client import get_mcp_client
            return get_mcp_client()
        except Exception:
            return None

    def _get_memory_service(self) -> Any:
        if self._memory_service is not None:
            return self._memory_service
        try:
            from charlie.memory_service import get_memory_service
            return get_memory_service()
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Subsystem Introspection Methods
    # -------------------------------------------------------------------------

    def get_process_info(self) -> Dict[str, Any]:
        """Return process, runtime, and OS environment information."""
        uptime = round(time.time() - _START_TIME, 2)
        return {
            "pid": os.getpid(),
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "system": platform.system(),
            "cwd": os.getcwd(),
            "uptime_seconds": uptime,
        }

    def get_model_info(self) -> Dict[str, Any]:
        """Return model and provider configuration with strictly masked secrets."""
        cfg = self._get_config()
        if cfg is None:
            return {
                "provider": "unknown",
                "model": "unknown",
                "api_key_configured": False,
            }

        api_key = getattr(cfg, "llm_api_key", None)
        return {
            "provider": getattr(cfg, "llm_provider", "openai"),
            "model": getattr(cfg, "llm_model", "gpt-4o"),
            "api_base_url": getattr(cfg, "llm_base_url", None) or getattr(cfg, "llm_url", None),
            "api_key_configured": bool(api_key),
            "vision_model": getattr(cfg, "vision_model", "local"),
            "embedding_model": getattr(cfg, "embedding_model", "local"),
            "context_window": getattr(cfg, "context_window", 8000),
            "gpu_device": getattr(cfg, "gpu_device", "cpu"),
        }

    def get_capabilities_info(self) -> Dict[str, Any]:
        """Query live CapabilityIndex for registered capabilities, operations, and health."""
        cap_idx = self._get_capability_index()
        if cap_idx is None:
            return {"total": 0, "available_count": 0, "by_id": {}}

        caps = cap_idx.list_capabilities()
        by_id: Dict[str, Any] = {}
        available_count = 0

        for c in caps:
            cap_id = getattr(c, "id", getattr(c, "capability_id", "unknown"))
            is_avail = c.is_available() if hasattr(c, "is_available") else True
            if is_avail:
                available_count += 1
            ops_iter = c.operations.values() if isinstance(c.operations, dict) else c.operations
            by_id[cap_id] = {
                "capability_id": cap_id,
                "owner": getattr(c, "owner", "unknown"),
                "available": is_avail,
                "provenance": getattr(c, "provenance", "builtin"),
                "operations": [
                    {
                        "name": getattr(op, "name", str(op)),
                        "description": getattr(op, "description", ""),
                        "risk_class": getattr(op, "risk_class", "safe"),
                        "required_leases": list(getattr(op, "required_leases", ())),
                        "timeout": getattr(op, "timeout_sec", getattr(op, "timeout", 15.0)),
                    }
                    for op in ops_iter
                ],
            }

        return {
            "total": len(caps),
            "available_count": available_count,
            "by_id": by_id,
        }

    def get_tasks_info(self) -> Dict[str, Any]:
        """Query TaskJournal for active, queued, and completed tasks."""
        journal = self._get_task_journal()
        if journal is None:
            return {
                "counts": {"running": 0, "queued": 0, "completed": 0, "failed": 0},
                "active_tasks": [],
                "total_tasks": 0,
            }

        raw_tasks = journal.snapshot() if hasattr(journal, "snapshot") else []
        if isinstance(raw_tasks, dict):
            task_list = raw_tasks.get("tasks", [])
        else:
            task_list = raw_tasks

        counts: Dict[str, int] = {}
        active: List[Dict[str, Any]] = []
        for t in task_list:
            st = str(t.get("status", "unknown")).lower()
            counts[st] = counts.get(st, 0) + 1
            if st in ("running", "queued", "planning", "verifying", "waiting", "approval_required"):
                active.append({
                    "task_id": t.get("id", t.get("task_id", "")),
                    "title": t.get("title", ""),
                    "status": st,
                    "priority": t.get("priority", "normal"),
                    "origin": t.get("origin", "foreground"),
                })

        return {
            "counts": counts,
            "active_tasks": active,
            "total_tasks": len(task_list),
        }

    def get_leases_info(self) -> Dict[str, Any]:
        """Query CapabilityLeaseManager for active resource leases."""
        lease_mgr = self._get_lease_manager()
        active_leases: Dict[str, str] = {}
        if lease_mgr is not None and hasattr(lease_mgr, "snapshot"):
            active_leases = lease_mgr.snapshot()
        elif lease_mgr is not None and hasattr(lease_mgr, "get_all_leases"):
            active_leases = lease_mgr.get_all_leases()
        else:
            try:
                from charlie.resource_locks import get_all_leases
                active_leases = get_all_leases()
            except Exception:
                pass

        return {
            "active_leases": active_leases,
            "leased_resources_count": len(active_leases),
        }

    def get_health_info(self) -> Dict[str, Any]:
        """Query HealthRegistry for subsystem statuses."""
        health_reg = self._get_health_registry()
        if health_reg is None:
            return {}

        return health_reg.snapshot()

    def get_mcp_info(self) -> Dict[str, Any]:
        """Query MCPClient for configured servers and tool roster."""
        mcp = self._get_mcp_client()
        if mcp is None:
            return {"configured_servers": 0, "connected_servers": 0, "servers": []}

        detailed = mcp.list_servers_detailed()
        connected = sum(1 for s in detailed if s.get("status") == "connected")
        return {
            "configured_servers": len(detailed),
            "connected_servers": connected,
            "servers": detailed,
        }

    def get_memory_info(self) -> Dict[str, Any]:
        """Query MemoryService for memory graph and store stats."""
        mem = self._get_memory_service()
        if mem is None:
            return {"status": "unavailable", "total_items": 0}

        try:
            stats = mem.get_stats()
            stats["status"] = "available"
            return stats
        except Exception as e:
            return {"status": "error", "error": str(e), "total_items": 0}

    def get_subsystem_info(self) -> Dict[str, Any]:
        """Inspect Desktop, Browser, Terminal, Voice, and Telemetry availability."""
        # 1. Desktop
        desktop_avail = False
        try:
            from charlie.desktop import DESKTOP_AVAILABLE
            desktop_avail = bool(DESKTOP_AVAILABLE)
        except Exception:
            pass

        # 2. Browser
        browser_avail = False
        try:
            from charlie.browser import BROWSER_AVAILABLE
            browser_avail = bool(BROWSER_AVAILABLE)
        except Exception:
            pass

        # 3. Terminal
        terminal_avail = False
        try:
            from charlie.terminal_service import _HAS_CONPTY
            terminal_avail = True
            conpty = bool(_HAS_CONPTY)
        except Exception:
            conpty = False

        # 4. Voice / Wake word
        voice_avail = False
        wake_word_model = "charlie.onnx"
        wake_word_path = Path(__file__).resolve().parent / wake_word_model
        wake_word_present = wake_word_path.exists()

        # 5. Telemetry
        telemetry_stats: Dict[str, Any] = {}
        try:
            from charlie.telemetry import telemetry
            telemetry_stats = telemetry.get_stats()
        except Exception:
            pass

        return {
            "desktop": {
                "available": desktop_avail,
                "platform": sys.platform,
            },
            "browser": {
                "available": browser_avail,
                "playwright_supported": browser_avail,
            },
            "terminal": {
                "available": terminal_avail,
                "has_conpty": conpty,
            },
            "voice": {
                "wake_word_model_present": wake_word_present,
                "wake_word_model": wake_word_model,
            },
            "telemetry": telemetry_stats,
        }

    # -------------------------------------------------------------------------
    # Snapshot & Aggregation
    # -------------------------------------------------------------------------

    def get_snapshot(self) -> Dict[str, Any]:
        """Return the comprehensive, secret-safe runtime snapshot."""
        return {
            "timestamp": time.time(),
            "process": self.get_process_info(),
            "model": self.get_model_info(),
            "capabilities": self.get_capabilities_info(),
            "tasks": self.get_tasks_info(),
            "leases": self.get_leases_info(),
            "subsystem_health": self.get_health_info(),
            "mcp": self.get_mcp_info(),
            "memory": self.get_memory_info(),
            "subsystems": self.get_subsystem_info(),
        }
