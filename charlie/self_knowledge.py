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

_SUBSYSTEM_STATUS_PATTERN = re.compile(
    r"\b(is|are)\s+(mcp(?: servers?)?|eventbus|terminal|browser|desktop|vision|pet)\s+"
    r"(running|connected|available|healthy|working)\b",
    re.IGNORECASE,
)

# Self-question detection patterns. Presentation entity names stay in
# PresentationRegistry; these patterns only describe semantic query shapes.
_SELF_PATTERNS = [
    re.compile(r"\b(who are you|what is charlie|what are you)\b", re.IGNORECASE),
    re.compile(r"\b(what|which)\s+(model|llm|provider|ai model|vision model|embedding model)\b", re.IGNORECASE),
    re.compile(
        r"\b(can you|are you able to|do you have access to)\s+"
        r"(control|click|type|browse|search|run|execute|see|watch|hear|remember|render|display|show|open)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(what|which)\s+(tools|capabilities|skills|commands)\b", re.IGNORECASE),
    _SUBSYSTEM_STATUS_PATTERN,
    re.compile(
        r"\b(what|which|how|where)\b.*\b(hud|ui|workspace|widget|overlay|presentation|visual|surface|core|ring|surfacecomposer)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(can you|do you have|are you able to|is there)\b.*\b("
        r"show|render|display|open|support|workspace|widget|overlay|hud|ui|surface)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(where is|which file|which module|what file|which code|how is)\b.*?"
        r"\b(implemented|implements|defined|defines|located|coded|built|lives|handles)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(which file|which module|what file|which function|which class)\s+"
        r"(implements?|defines?|contains?|handles?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(how does|how do you)\s+(your\s+)?"
        r"(browser|desktop|memory|vision|voice|map|terminal|subagent|task|planner|core|hud)\s+"
        r"(work|function|operate|behave)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(are you healthy|how are you running|health check|diagnose yourself|"
        r"what are you currently doing|active tasks|current tasks)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(what memory|how does memory|where is memory|memory systems?)\b", re.IGNORECASE),
    re.compile(r"\b(what tasks|any running tasks|what are you doing)\b", re.IGNORECASE),
]

_PRESENTATION_TERMS = (
    "hud",
    "ui",
    "widget",
    "workspace",
    "overlay",
    "presentation",
    "visual",
    "surface",
    "primitive",
    "layout",
    "core",
    "ring",
    "surfacecomposer",
)


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
        presentation_registry: Optional[Any] = None,
    ) -> None:
        self._introspector = runtime_introspector or RuntimeIntrospector(
            config=config,
            capability_index=capability_index,
            presentation_registry=presentation_registry,
        )
        self._code_index = code_index
        if capability_index is None:
            capability_index = getattr(self._introspector, "_capability_index", None)
        if capability_index is None:
            from charlie.capabilities import get_capability_index

            capability_index = get_capability_index()
        self._capability_index = capability_index
        self._config = config
        self._presentation_registry = presentation_registry

    def _get_presentation_registry(self) -> Any:
        if self._presentation_registry is not None:
            return self._presentation_registry
        if hasattr(self._introspector, "_get_presentation_registry"):
            return self._introspector._get_presentation_registry()
        try:
            from charlie.presentation_registry import get_presentation_registry

            return get_presentation_registry()
        except Exception:
            return None

    def _get_code_index(self) -> CodeIndex:
        if self._code_index is None:
            self._code_index = CodeIndex()
            self._code_index.refresh()
        return self._code_index

    def _get_runtime_config(self) -> Any:
        """Return config through RuntimeIntrospector without exposing secrets."""
        if self._config is not None:
            return self._config
        getter = getattr(self._introspector, "_get_config", None)
        if getter is not None:
            try:
                return getter()
            except Exception:
                return None
        return None

    @staticmethod
    def _query_tokens(query: str) -> List[str]:
        """Return safe natural-language tokens used for registry lookup."""
        return [token.lower() for token in re.findall(r"[A-Za-z0-9_]+", query)]

    def _resolve_presentation_entity(
        self, query: str, registry: Optional[Any] = None
    ) -> Optional[Dict[str, str]]:
        """Resolve one canonical presentation entity or alias mentioned in query."""
        registry = registry or self._get_presentation_registry()
        if registry is None:
            return None

        for token in self._query_tokens(query):
            for kind, resolver in (
                ("workspace", registry.resolve_workspace_type),
                ("widget", registry.resolve_widget_type),
                ("overlay", registry.resolve_overlay_type),
            ):
                canonical = resolver(token)
                if canonical:
                    return {"kind": kind, "canonical": canonical, "queried": token}
        return None

    def _is_presentation_query(self, query: str) -> bool:
        """Recognize presentation questions without owning presentation names."""
        q_lower = query.lower()
        if _SUBSYSTEM_STATUS_PATTERN.search(q_lower):
            return False
        if any(re.search(rf"\b{re.escape(term)}s?\b", q_lower) for term in _PRESENTATION_TERMS):
            return True
        has_surface_context = bool(
            re.search(
                r"\b(show|render|display|open|support|hud|ui|workspace|widget|overlay|presentation|visual|surface)\b"
                r"|\b(can you|are you able to|is there)\b",
                q_lower,
            )
        )
        entity = self._resolve_presentation_entity(query)
        if entity is not None:
            # Canonical names are safe on their own. Aliases require a
            # presentation cue so ordinary words such as "file" do not win.
            if entity["queried"] == entity["canonical"] or has_surface_context:
                return True
        registry = self._get_presentation_registry()
        if registry is not None:
            tokens = self._query_tokens(query)
            for primitive in registry.list_surface_primitives():
                if primitive in tokens or f"{primitive}s" in tokens:
                    return True
        return False

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
        if any(
            term in q_lower
            for term in ("your code", "your implementation", "your capabilities", "your model", "your tools")
        ):
            return True

        if self._is_presentation_query(q):
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
        if any(
            k in q_lower
            for k in ("tool", "capability", "capabilities", "can you", "control", "browse", "click", "search")
        ):
            caps_info = self._introspector.get_capabilities_info()
            cap_facts = caps_info.get("by_id", {})
            sources.append("capability.registry")

        # 3. Tasks & Leases queries
        if not _SUBSYSTEM_STATUS_PATTERN.search(q_lower) and any(
            k in q_lower for k in ("task", "doing", "leases", "running")
        ):
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

        # 7. Direct subsystem-status queries. These are semantic SelfKnowledge
        # domains, not presentation entities.
        if _SUBSYSTEM_STATUS_PATTERN.search(q_lower):
            if not re.search(r"\b(mcp|eventbus|pet)\b", q_lower):
                runtime_facts["subsystems"] = self._introspector.get_subsystem_info()
                sources.append("runtime.subsystems")
            if "vision" in q_lower:
                vision_caps = self._introspector.get_capabilities_info()
                cap_facts = vision_caps.get("by_id", {})
                sources.append("capability.registry")
            if "pet" in q_lower:
                config = self._get_runtime_config()
                runtime_facts["config"] = {
                    "pet_enabled": getattr(config, "pet_enabled", "unknown") if config else "unknown"
                }
                sources.append("runtime.config")

        # 8. Presentation & HUD surfaces queries. RuntimeIntrospector owns the
        # inventory; SelfKnowledge only decides when to request that evidence.
        if self._is_presentation_query(query):
            pres_info = self._introspector.get_presentation_info()
            runtime_facts["presentation"] = pres_info
            sources.append("runtime.presentation")

        # 8. Code Index symbol / file search
        code_idx = self._get_code_index()
        # Extract keywords
        cleaned_words = [
            w
            for w in re.findall(r"[A-Za-z0-9_]+", q_lower)
            if w
            not in (
                "what",
                "which",
                "how",
                "where",
                "is",
                "the",
                "are",
                "you",
                "your",
                "does",
                "implemented",
                "implements",
                "file",
                "module",
                "can",
                "work",
                "do",
            )
        ]
        for word in cleaned_words[:3]:
            found_syms = code_idx.search_symbols(word, limit=5)
            for s in found_syms:
                if not any(
                    existing["name"] == s["name"] and existing["file_path"] == s["file_path"]
                    for existing in symbols
                ):
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

    def _answer_presentation_question(self, query: str, evidence: SelfKnowledgeEvidence) -> Optional[str]:
        """Answer presentation questions from RuntimeIntrospector evidence."""
        q_lower = query.lower()
        pres_info = evidence.runtime_facts.get("presentation")
        if not self._is_presentation_query(query) and "runtime.presentation" not in evidence.evidence_sources:
            return None
        if not pres_info:
            pres_info = self._introspector.get_presentation_info()
        if not isinstance(pres_info, dict):
            return "I couldn't inspect my presentation registry right now."
        if pres_info.get("status") == "error":
            message = pres_info.get("message", "presentation registry error")
            return f"I couldn't inspect my presentation registry right now ({message})."
        if pres_info.get("status") != "available":
            return "I couldn't inspect my presentation registry right now."

        def section(name: str) -> Dict[str, Any]:
            value = pres_info.get(name, {})
            return value if isinstance(value, dict) else {}

        def definitions(name: str) -> Dict[str, Dict[str, Any]]:
            value = section(name).get("definitions", {})
            return value if isinstance(value, dict) else {}

        def activity_note() -> str:
            activity = section("runtime").get("hud_runtime_active", "unknown")
            if activity == "unknown":
                return " I can't currently verify whether a HUD client is connected."
            return " A HUD client is currently connected." if activity else " No HUD client is currently connected."

        def alias_note(entity: Dict[str, str]) -> str:
            if entity["queried"] == entity["canonical"]:
                return ""
            return f" (the alias '{entity['queried']}' resolves to '{entity['canonical']}')"

        def widget_support_labels(supports: Any) -> List[str]:
            if not isinstance(supports, dict):
                return []
            labels = {"drag": "dragging", "resize": "resizing", "pin": "pinning", "auto_dismiss": "auto-dismiss"}
            return [labels.get(name, name.replace("_", "-")) for name, enabled in supports.items() if enabled]

        # Pet is intentionally outside the presentation registry contract.
        if "pet" in q_lower and any(term in q_lower for term in ("hud", "workspace", "part of", "separate")):
            return "The native floating Pet is a separate companion surface outside the HUD workspace system."

        # Settings must remain an overlay/modal, never be described as a workspace.
        if "settings" in q_lower and "workspace" in q_lower and any(
            word in q_lower for word in ("is", "type", "kind")
        ):
            settings = definitions("overlays").get("settings", {})
            dismiss = settings.get("dismiss_policy", "manual")
            anchor = settings.get("anchor", "screen")
            return (
                f"Settings is an implemented overlay modal (anchor: {anchor}, dismiss policy: {dismiss}), "
                "not a workspace."
            )

        rules = section("core").get("rules", {})
        if not isinstance(rules, dict):
            rules = {}
        if any(term in q_lower for term in ("core", "ring", "dock", "docking", "workspace opens")):
            idle = rules.get("no_workspace", {})
            active = rules.get("active_workspace", {})
            idle_pos = idle.get("position", "unknown")
            active_pos = active.get("position", "unknown")
            visible_flags = [key for key, value in active.items() if key.startswith("show_") and value]
            mode = "core-only mode" if not visible_flags else f"with {', '.join(visible_flags)}"
            return (
                f"With no workspace, the core is at **{idle_pos}**. "
                f"When a workspace opens, it docks to **{active_pos}** in {mode}."
            )

        entity = self._resolve_presentation_entity(query)
        asks_widgets = bool(re.search(r"\b(what|which|list)\b.*\bwidgets?\b|\bwidgets?\s+do you have\b", q_lower))
        asks_workspaces = bool(re.search(r"\b(what|which|list)\b.*\bworkspaces?\b|\bworkspaces?\s+(can|do)\b", q_lower))
        asks_overlays = bool(re.search(r"\b(what|which|list)\b.*\b(overlays?|modals?)\b", q_lower))
        query_tokens = self._query_tokens(query)
        known_primitives = [str(item) for item in pres_info.get("surface_primitives", [])]
        mentions_primitive = any(item in query_tokens or f"{item}s" in query_tokens for item in known_primitives)
        asks_primitives = bool(re.search(r"\b(primitives?|surfacecomposer|layout)\b", q_lower)) or mentions_primitive

        # Resolve canonical entity names and aliases through PresentationRegistry.
        if entity and not (asks_widgets or asks_workspaces or asks_overlays or asks_primitives):
            kind = entity["kind"]
            descriptor = definitions(f"{kind}s").get(entity["canonical"], {})
            implemented = bool(descriptor.get("implemented", True))
            status = "implemented" if implemented else "planned"
            description = descriptor.get("description", "")
            if kind == "workspace":
                spatial_note = " with spatial rendering" if descriptor.get("spatial") else ""
                return (
                    f"Yes, I have an **{status}** **{entity['canonical']}** workspace"
                    f"{alias_note(entity)}{spatial_note}: {description}."
                )
            if kind == "widget":
                supported = widget_support_labels(descriptor.get("supports", {}))
                behavior = f" Supports: {', '.join(supported)}." if supported else ""
                return (
                    f"Yes, I have an **{status}** **{entity['canonical']}** contextual widget"
                    f"{alias_note(entity)}: {description}.{behavior}"
                )
            return f"Yes, I have an **{status}** **{entity['canonical']}** overlay modal: {description}."

        if asks_widgets:
            widget_data = section("widgets")
            canonical = widget_data.get("canonical", [])
            widget_defs = definitions("widgets")
            entries = []
            for name in canonical:
                supported = widget_support_labels(widget_defs.get(name, {}).get("supports", {}))
                entries.append(f"{name} (supports: {', '.join(supported)})" if supported else str(name))
            count = widget_data.get("count", len(canonical))
            return f"I have **{count}** contextual HUD widgets: **{', '.join(entries)}**."

        if asks_workspaces:
            workspace_data = section("workspaces")
            canonical = workspace_data.get("canonical", [])
            count = workspace_data.get("count", len(canonical))
            return f"I support **{count}** canonical workspaces: **{', '.join(canonical)}**."

        if asks_overlays:
            overlay_data = section("overlays")
            canonical = overlay_data.get("canonical", [])
            count = overlay_data.get("count", len(canonical))
            return f"I support **{count}** overlay modals: **{', '.join(canonical)}**."

        if asks_primitives:
            primitives = known_primitives
            layouts = [str(item) for item in pres_info.get("layout_types", [])]
            requested = next(
                (primitive for primitive in primitives if primitive in query_tokens or f"{primitive}s" in query_tokens),
                None,
            )
            if requested and any(word in q_lower for word in ("render", "show", "display")):
                return (
                    f"Yes, SurfaceComposer supports **{requested}** primitives in these layout types: "
                    f"{', '.join(layouts)}."
                )
            return (
                f"SurfaceComposer supports **{len(primitives)}** visual primitives: {', '.join(primitives)}; "
                f"layout types: {', '.join(layouts)}."
            )

        overview = any(
            term in q_lower for term in ("hud", "presentation capabilities", "visual interface", "visual surface")
        )
        if overview:
            workspaces = section("workspaces").get("canonical", [])
            widgets = section("widgets").get("canonical", [])
            overlays = section("overlays").get("canonical", [])
            primitive_count = len(pres_info.get("surface_primitives", []))
            idle_position = rules.get("no_workspace", {}).get("position", "unknown")
            workspace_position = rules.get("active_workspace", {}).get("position", "unknown")
            return (
                f"HUD presentation supports **{len(workspaces)}** workspaces ({', '.join(workspaces)}), "
                f"**{len(widgets)}** widgets ({', '.join(widgets)}), "
                f"**{len(overlays)}** overlays ({', '.join(overlays)}), "
                f"and SurfaceComposer with **{primitive_count}** primitives."
                f" The core is at **{idle_position}** when idle and docks to **{workspace_position}** for a workspace."
                f"{activity_note()}"
            )

        return None

    def _answer_subsystem_status(self, query: str, evidence: SelfKnowledgeEvidence) -> Optional[str]:
        """Answer semantic subsystem status without confusing it with presentation."""
        if not _SUBSYSTEM_STATUS_PATTERN.search(query):
            return None
        q_lower = query.lower()

        if "mcp" in q_lower:
            mcp = evidence.runtime_facts.get("mcp", self._introspector.get_mcp_info())
            return (
                f"MCP subsystem: **{mcp.get('configured_servers', 0)}** servers configured, "
                f"**{mcp.get('connected_servers', 0)}** currently connected."
            )

        if "pet" in q_lower:
            configured = evidence.runtime_facts.get("config", {}).get("pet_enabled", "unknown")
            if configured is True:
                config_text = "configured enabled"
            elif configured is False:
                config_text = "configured disabled"
            else:
                config_text = "configuration unknown"
            return (
                f"The native Pet companion is implemented and {config_text}; "
                "active process/window status is unknown."
            )

        subsystem = next(
            (name for name in ("browser", "desktop", "terminal", "vision") if name in q_lower),
            None,
        )
        if subsystem is None:
            return None
        facts = evidence.runtime_facts.get("subsystems", {}).get(subsystem, {})
        if subsystem == "vision":
            facts = evidence.capability_facts.get("vision", facts)
        available = facts.get("available") if isinstance(facts, dict) else None
        availability = "available" if available is True else "unavailable" if available is False else "unknown"
        if "available" in q_lower:
            return f"{subsystem.capitalize()} subsystem is currently **{availability}**."
        return (
            f"{subsystem.capitalize()} subsystem availability is **{availability}**; "
            "active working state is not directly tracked by this runtime."
        )

    def answer_self_question(self, query: str) -> Dict[str, Any]:
        """Generate an evidence-grounded truthful answer for self-questions."""
        evidence = self.get_evidence_for_query(query)
        q_lower = query.lower()
        parts: List[str] = []

        # 1. Presentation & HUD Questions (checked early for specialized presentation queries)
        pres_ans = self._answer_presentation_question(query, evidence)
        subsystem_ans = self._answer_subsystem_status(query, evidence)
        if pres_ans:
            parts.append(pres_ans)

        # 2. Semantic subsystem status query
        elif subsystem_ans:
            parts.append(subsystem_ans)

        # 3. Model Query
        elif any(k in q_lower for k in ("what model", "which model", "llm provider", "configured model")):
            m = evidence.runtime_facts.get("model", self._introspector.get_model_info())
            provider = m.get("provider", "openai")
            model = m.get("model", "gpt-4o")
            api_set = "configured" if m.get("api_key_configured") else "not configured"
            parts.append(
                f"I am currently configured to use the **{model}** model via provider **{provider}** "
                f"(API key is {api_set})."
            )

        # 3. Implementation Location Query (checked before generic action terms)
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

        # 4. Desktop Control Query
        elif any(
            k in q_lower for k in ("control my pc", "control my desktop", "desktop control", "click", "move mouse")
        ):
            caps = evidence.capability_facts or self._introspector.get_capabilities_info().get("by_id", {})
            dt = caps.get("desktop")
            if dt and dt.get("available"):
                parts.append("Yes, desktop control capability is currently **available** on this Windows system.")
            else:
                parts.append("Desktop control is currently **unavailable** in this runtime environment.")

        # 5. Browser Control Query
        elif any(k in q_lower for k in ("browse", "browser", "playwright")):
            caps = evidence.capability_facts or self._introspector.get_capabilities_info().get("by_id", {})
            br = caps.get("browser")
            if br and br.get("available"):
                parts.append("Headless browser automation via Playwright is currently **available**.")
            else:
                parts.append("Browser automation capability is currently **unavailable**.")

        # 6. Tools & Capabilities Roster
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

        # 7. MCP Query
        elif "mcp" in q_lower:
            mcp = evidence.runtime_facts.get("mcp", self._introspector.get_mcp_info())
            cfg_srv = mcp.get("configured_servers", 0)
            conn_srv = mcp.get("connected_servers", 0)
            parts.append(f"MCP subsystem: **{cfg_srv}** servers configured, **{conn_srv}** currently connected.")

        # 8. Memory Query
        elif "memory" in q_lower:
            mem = evidence.runtime_facts.get("memory", self._introspector.get_memory_info())
            status = mem.get("status", "available")
            total = mem.get("total_items", 0)
            parts.append(
                f"Memory system is **{status}** with SQLite knowledge graph and vector stores "
                f"containing **{total}** items."
            )

        # 9. Tasks Query
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
                "Based on live runtime introspection, all registered Charlie subsystems are operating "
                "within defined parameters."
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
