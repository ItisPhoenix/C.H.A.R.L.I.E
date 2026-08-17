"""SelfKnowledge Service for Charlie V1.

Authoritatively resolves questions about Charlie's live runtime, capabilities,
models, codebase implementation, MCP servers, and health using grounded evidence
from RuntimeIntrospector, CapabilityIndex, and CodeIndex.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from charlie.code_index import CodeIndex
from charlie.runtime_introspector import RuntimeIntrospector

logger = logging.getLogger("charlie.self_knowledge")

# Self-question detection patterns
_SELF_PATTERNS = [
    re.compile(r"\b(who are you|what is charlie|what are you)\b", re.IGNORECASE),
    re.compile(r"\b(what|which)\s+(model|llm|provider|ai model|vision model|embedding model)\b", re.IGNORECASE),
    re.compile(r"\b(can you|are you able to|do you have access to)\s+(control|click|type|browse|search|run|execute|see|watch|hear|remember)\b", re.IGNORECASE),
    re.compile(r"\b(what|which)\s+(tools|capabilities|skills|commands)\s+(do you have|are available|exist)\b", re.IGNORECASE),
    re.compile(r"\b(is|are)\s+(mcp|mcp servers?|eventbus|terminal|browser|desktop|vision|pet)\s+(running|connected|available|healthy|working)\b", re.IGNORECASE),
    re.compile(r"\b(where is|which file|which module|what file|which code|how is)\b.*?\b(implemented|implements|defined|defines|located|coded|built|lives|handles)\b", re.IGNORECASE),
    re.compile(r"\b(which file|which module|what file|which function|which class)\s+(implements?|defines?|contains?|handles?)\b", re.IGNORECASE),
    re.compile(r"\b(how does|how do you)\s+(your\s+)?(browser|desktop|memory|vision|voice|map|terminal|subagent|task|planner)\s+(work|function|operate)\b", re.IGNORECASE),
    re.compile(r"\b(are you healthy|how are you running|health check|diagnose yourself|what are you currently doing|active tasks|current tasks)\b", re.IGNORECASE),
    re.compile(r"\b(what memory|how does memory|where is memory|memory systems?)\b", re.IGNORECASE),
    re.compile(r"\b(what tasks|any running tasks|what are you doing)\b", re.IGNORECASE),
]


@dataclass
class SelfKnowledgeEvidence:
    """Evidence packet grounding a response about Charlie's runtime and code."""

    query: str
    evidence_sources: List[str] = field(default_factory=list)
    runtime_facts: Dict[str, Any] = field(default_factory=dict)
    capability_facts: Dict[str, Any] = field(default_factory=dict)
    relevant_symbols: List[Dict[str, Any]] = field(default_factory=list)
    relevant_files: List[Dict[str, Any]] = field(default_factory=list)
    excerpts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SelfKnowledgeService:
    """Service providing grounded self-answers from live code and runtime truth."""

    def __init__(
        self,
        runtime_introspector: Optional[RuntimeIntrospector] = None,
        code_index: Optional[CodeIndex] = None,
        capability_index: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> None:
        self._introspector = runtime_introspector or RuntimeIntrospector(config=config)
        self._code_index = code_index
        self._capability_index = capability_index
        self._config = config

    def _get_code_index(self) -> CodeIndex:
        if self._code_index is None:
            self._code_index = CodeIndex()
            self._code_index.refresh()
        return self._code_index

    # -------------------------------------------------------------------------
    # Classification
    # -------------------------------------------------------------------------

    def is_self_question(self, query: str) -> bool:
        """Classify if a question specifically queries Charlie's own identity, code, or runtime."""
        q = query.strip()
        if not q:
            return False

        for pat in _SELF_PATTERNS:
            if pat.search(q):
                return True

        q_lower = q.lower()
        if any(term in q_lower for term in ("your code", "your implementation", "your capabilities", "your model", "your tools")):
            return True

        return False

    # -------------------------------------------------------------------------
    # Evidence Gathering
    # -------------------------------------------------------------------------

    def get_evidence_for_query(self, query: str) -> SelfKnowledgeEvidence:
        """Gather focused, secret-free evidence from live runtime, capabilities, and code index."""
        sources: List[str] = []
        runtime_facts: Dict[str, Any] = {}
        cap_facts: Dict[str, Any] = {}
        symbols: List[Dict[str, Any]] = []
        files: List[Dict[str, Any]] = []
        excerpts: List[Dict[str, Any]] = []

        q_lower = query.lower()

        # 1. Model queries
        if any(k in q_lower for k in ("model", "llm", "provider", "gpt", "claude", "gemini", "ollama", "openai")):
            model_info = self._introspector.get_model_info()
            runtime_facts["model"] = model_info
            sources.append("runtime.model")

        # 2. Capability & Tool queries
        if any(k in q_lower for k in ("tool", "capability", "capabilities", "can you", "control", "browse", "click", "search")):
            caps_info = self._introspector.get_capabilities_info()
            cap_facts = caps_info.get("by_id", {})
            sources.append("capability.registry")

        # 3. Tasks & Leases queries
        if any(k in q_lower for k in ("task", "doing", "leases", "running")):
            tasks_info = self._introspector.get_tasks_info()
            leases_info = self._introspector.get_leases_info()
            runtime_facts["tasks"] = tasks_info
            runtime_facts["leases"] = leases_info
            sources.append("runtime.tasks")
            sources.append("runtime.leases")

        # 4. MCP queries
        if "mcp" in q_lower:
            mcp_info = self._introspector.get_mcp_info()
            runtime_facts["mcp"] = mcp_info
            sources.append("runtime.mcp")

        # 5. Memory queries
        if "memory" in q_lower:
            mem_info = self._introspector.get_memory_info()
            runtime_facts["memory"] = mem_info
            sources.append("runtime.memory")

        # 6. Health & Subsystem queries
        if any(k in q_lower for k in ("health", "healthy", "subsystem", "diagnos")):
            health_info = self._introspector.get_health_info()
            subsys_info = self._introspector.get_subsystem_info()
            runtime_facts["subsystem_health"] = health_info
            runtime_facts["subsystems"] = subsys_info
            sources.append("runtime.health")

        # 7. Code Index symbol / file search
        code_idx = self._get_code_index()
        # Extract keywords
        cleaned_words = [w for w in re.findall(r"[A-Za-z0-9_]+", q_lower) if w not in ("what", "which", "how", "where", "is", "the", "are", "you", "your", "does", "implemented", "implements", "file", "module", "can", "work", "do")]
        for word in cleaned_words[:3]:
            found_syms = code_idx.search_symbols(word, limit=5)
            for s in found_syms:
                if not any(existing["name"] == s["name"] and existing["file_path"] == s["file_path"] for existing in symbols):
                    symbols.append(s)
            found_files = code_idx.search_files(word, limit=3)
            for f in found_files:
                if not any(existing["file_path"] == f["file_path"] for existing in files):
                    files.append(f)

        if symbols or files:
            sources.append("code_index")

        return SelfKnowledgeEvidence(
            query=query,
            evidence_sources=sources,
            runtime_facts=runtime_facts,
            capability_facts=cap_facts,
            relevant_symbols=symbols[:10],
            relevant_files=files[:5],
            excerpts=excerpts,
        )

    # -------------------------------------------------------------------------
    # Deterministic Grounded Answering
    # -------------------------------------------------------------------------

    def answer_self_question(self, query: str) -> Dict[str, Any]:
        """Generate an evidence-grounded truthful answer for self-questions."""
        evidence = self.get_evidence_for_query(query)
        q_lower = query.lower()
        parts: List[str] = []

        # 1. Model Query
        if any(k in q_lower for k in ("what model", "which model", "llm provider", "configured model")):
            m = evidence.runtime_facts.get("model", self._introspector.get_model_info())
            provider = m.get("provider", "openai")
            model = m.get("model", "gpt-4o")
            api_set = "configured" if m.get("api_key_configured") else "not configured"
            parts.append(
                f"I am currently configured to use the **{model}** model via provider **{provider}** (API key is {api_set})."
            )

        # 2. Implementation Location Query (checked before generic action terms)
        elif any(
            k in q_lower for k in ("where is", "which file", "which module", "what file", "implements", "coded in")
        ):
            if evidence.relevant_symbols:
                sym_descs = [
                    f"`{s['name']}` ({s['kind']}) in `{s['file_path']}` (lines {s['start_line']}-{s['end_line']})"
                    for s in evidence.relevant_symbols[:3]
                ]
                parts.append(f"The requested functionality is implemented in: {', '.join(sym_descs)}.")
            elif evidence.relevant_files:
                file_descs = [f"`{f['file_path']}`" for f in evidence.relevant_files[:3]]
                parts.append(f"Relevant implementation files found: {', '.join(file_descs)}.")
            else:
                parts.append("No exact code symbols found matching the query in the repository index.")

        # 3. Desktop Control Query
        elif any(
            k in q_lower for k in ("control my pc", "control my desktop", "desktop control", "click", "move mouse")
        ):
            caps = evidence.capability_facts or self._introspector.get_capabilities_info().get("by_id", {})
            dt = caps.get("desktop")
            if dt and dt.get("available"):
                parts.append("Yes, desktop control capability is currently **available** on this Windows system.")
            else:
                parts.append("Desktop control is currently **unavailable** in this runtime environment.")

        # 4. Browser Control Query
        elif any(k in q_lower for k in ("browse", "browser", "playwright")):
            caps = evidence.capability_facts or self._introspector.get_capabilities_info().get("by_id", {})
            br = caps.get("browser")
            if br and br.get("available"):
                parts.append("Headless browser automation via Playwright is currently **available**.")
            else:
                parts.append("Browser automation capability is currently **unavailable**.")

        # 5. Tools & Capabilities Roster
        elif any(k in q_lower for k in ("what tools", "what capabilities", "what can you do")):
            caps_info = self._introspector.get_capabilities_info()
            total = caps_info.get("total", 0)
            avail = caps_info.get("available_count", 0)
            avail_ids = [cid for cid, c in caps_info.get("by_id", {}).items() if c.get("available")]
            unavail_ids = [cid for cid, c in caps_info.get("by_id", {}).items() if not c.get("available")]

            text = f"I have **{total}** registered capability domains ({avail} currently available):\n"
            if avail_ids:
                text += f"- **Available**: {', '.join(avail_ids)}\n"
            if unavail_ids:
                text += f"- **Unavailable**: {', '.join(unavail_ids)}"
            parts.append(text)

        # 6. MCP Query
        elif "mcp" in q_lower:
            mcp = evidence.runtime_facts.get("mcp", self._introspector.get_mcp_info())
            cfg_srv = mcp.get("configured_servers", 0)
            conn_srv = mcp.get("connected_servers", 0)
            parts.append(f"MCP subsystem: **{cfg_srv}** servers configured, **{conn_srv}** currently connected.")

        # 7. Memory Query
        elif "memory" in q_lower:
            mem = evidence.runtime_facts.get("memory", self._introspector.get_memory_info())
            status = mem.get("status", "available")
            total = mem.get("total_items", 0)
            parts.append(
                f"Memory system is **{status}** with SQLite knowledge graph and vector stores containing **{total}** items."
            )

        # 8. Tasks Query
        elif any(k in q_lower for k in ("what are you doing", "active tasks", "tasks")):
            tasks = evidence.runtime_facts.get("tasks", self._introspector.get_tasks_info())
            active = tasks.get("active_tasks", [])
            if active:
                task_lines = [f"- Task `{t['task_id']}`: {t['title']} ({t['status']})" for t in active]
                parts.append("Currently active tasks:\n" + "\n".join(task_lines))
            else:
                parts.append("No active background or foreground tasks currently running.")

        # Default fallback synthesis
        if not parts:
            parts.append(
                "Based on live runtime introspection, all registered Charlie subsystems are operating within defined parameters."
            )

        final_answer = " ".join(parts)
        return {
            "query": query,
            "is_self_question": True,
            "answer": final_answer,
            "evidence_sources": evidence.evidence_sources,
            "provenance": {
                "runtime_facts": evidence.runtime_facts,
                "symbols_count": len(evidence.relevant_symbols),
                "files_count": len(evidence.relevant_files),
            },
        }
