"""Unified ToolRegistry — single catalog for native, MCP, and agent tools."""

import importlib
import pkgutil
from pathlib import Path
from typing import Any

from charlie.security.tiers import RiskTier, get_tool_tier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


# Canonical tier seed table (Req 3.2, 3.3). These specific *logical* tool names
# have an authoritative Risk_Tier that always wins at registration time,
# regardless of what tier the caller passes — so a destructive/high-risk tool
# can never be registered under a downgraded tier. Keyed by the original
# requested tool name (before any source-prefix collision rename), since the
# seeds refer to logical tool identities, not storage keys.
_CANONICAL_TIER_SEEDS: dict[str, RiskTier] = {
    "delete_file": RiskTier.TIER_3,
    "self_modify": RiskTier.TIER_3,
    "apply_edit": RiskTier.TIER_3,
    "shutdown": RiskTier.TIER_2,
    "sleep_pc": RiskTier.TIER_2,
    "hibernate_pc": RiskTier.TIER_2,
}


# Offensive-security tool tier FLOOR (Req 18.1, 18.2, 18.3). These tools stay
# registered and enabled by default, but must ALWAYS route through the
# Confirmation_Gate — so their *effective* tier is floored at TIER_2 (TIER_2+
# routes to confirmation). The floor only ever RAISES a tier: a TIER_0/TIER_1
# offensive tool is lifted to TIER_2, while a tool already at TIER_2/TIER_3
# keeps its higher tier. The canonical seed table above still takes precedence
# for its specific names (and seeds are all >= TIER_2, so the floor never
# affects them). The explicit name set covers the offensive tools defined in
# redteam_tools.py, recon_tools.py, and intrusion_patrol.py; the
# ``category == "security"`` condition (applied in ``register``) additionally
# covers any future security tool automatically.
_OFFENSIVE_TOOL_NAMES: set[str] = {
    # redteam_tools.py
    "scan_target",
    "fuzz_dirs",
    "analyze_vuln",
    "generate_payload",
    "write_report",
    "ctf_hint",
    "explain_exploit",
    # recon_tools.py
    "whois_lookup",
    "dns_enum",
    "subdomain_scan",
    "tech_fingerprint",
    "google_dork",
    "caller_lookup",
    # intrusion_patrol.py
    "audit_network_connections",
}


def _coerce_risk_tier(value: "RiskTier | str | int | None") -> RiskTier:
    """Normalize any tier representation to the canonical ``RiskTier`` enum.

    Accepts a ``RiskTier`` (returned as-is), an enum name string such as
    ``"TIER_2"``, a numeric string such as ``"2"``, or an int such as ``2``.
    Anything unrecognized or missing fails closed to ``RiskTier.TIER_3`` — the
    most restrictive tier — consistent with the fail-closed default used by
    ``charlie.security.tiers.get_tool_tier`` (Req 3.5).
    """
    if isinstance(value, RiskTier):
        return value
    # ``bool`` is a subclass of ``int`` — treat it as unknown rather than 0/1.
    if isinstance(value, bool):
        return RiskTier.TIER_3
    if isinstance(value, int):
        try:
            return RiskTier(value)
        except ValueError:
            return RiskTier.TIER_3
    if isinstance(value, str):
        # Prefer the enum name ("TIER_2"); fall back to a numeric string ("2").
        try:
            return RiskTier[value]
        except KeyError:
            pass
        try:
            return RiskTier(int(value))
        except (ValueError, KeyError):
            return RiskTier.TIER_3
    return RiskTier.TIER_3


class ToolEntry:
    """A single tool entry in the registry."""

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: callable,
        risk_tier: "RiskTier | str | int" = RiskTier.TIER_0,
        category: str = "general",
        source: str = "native",
        timeout: int = 30,
        calling_convention: str = "KWARGS",
        variadic_param: str | None = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        # Always store the canonical enum, regardless of how callers pass it
        # (str/int/enum). Existing callers that still pass strings keep working
        # until tasks 1.3–1.5 update them.
        self.risk_tier: RiskTier = _coerce_risk_tier(risk_tier)
        self.category = category
        self.source = source
        self.timeout = timeout
        self.calling_convention = calling_convention
        self.variadic_param = variadic_param

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function-calling format.

        The inner ``function`` object is kept to the strict OpenAI schema
        (name/description/parameters). ``risk_tier`` is exposed as a top-level
        sibling string (``risk_tier.name``) so consumers never receive a raw,
        non-JSON-serializable enum.
        """
        return {
            "type": "function",
            "risk_tier": self.risk_tier.name,
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_tier": self.risk_tier.name,
            "category": self.category,
            "source": self.source,
        }


class ToolRegistry:
    """Single unified tool catalog for ALL tools (native, MCP, agent)."""

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: callable,
        risk_tier: "RiskTier | str | int | None" = None,
        category: str = "general",
        source: str = "native",
        timeout: int = 30,
        calling_convention: str = "KWARGS",
        variadic_param: str | None = None,
    ) -> None:
        """Register a tool in the registry.

        ``risk_tier`` may be a canonical :class:`RiskTier` enum (preferred), an
        enum name string, or an int; ``ToolEntry`` coerces it via
        ``_coerce_risk_tier`` (fail-closed to ``TIER_3``).

        Tier resolution precedence:

        1. **Canonical seed (Req 3.2, 3.3)** — if the *original* requested
           ``name`` is in ``_CANONICAL_TIER_SEEDS``, its authoritative tier
           always wins, overriding whatever the caller passed. This keyes off
           the logical name *before* any collision rename.
        2. **Explicit caller value** — used as-is when provided.
        3. **Fail-closed default (Req 3.5)** — when no tier is provided
           (``risk_tier is None``) and the name is not seeded, the tool is
           assigned ``RiskTier.TIER_3`` and a warning naming the tool is logged.

        After precedence resolution, an **offensive-security tier floor**
        (Req 18.1, 18.2, 18.3) is applied: a tool that is offensive (either in
        ``_OFFENSIVE_TOOL_NAMES`` or registered with ``category == "security"``)
        always routes through the Confirmation_Gate, so a resolved tier below
        ``TIER_2`` is raised to ``TIER_2``. The floor only ever raises — it
        never lowers a tool already at ``TIER_2``/``TIER_3`` — so the canonical
        seed table still wins (its names already resolve ``>= TIER_2``).
        """
        # The seed table is keyed by the logical tool name as requested by the
        # caller, before any collision-driven rename below.
        original_name = name
        seeded = original_name in _CANONICAL_TIER_SEEDS

        # Unknown/missing tier on a non-seeded tool: the caller relied on the
        # default. Fail closed to TIER_3 and warn naming the tool (Req 3.5).
        # Seeded names skip this — their tier is authoritative, not defaulted.
        if risk_tier is None and not seeded:
            risk_tier = RiskTier.TIER_3
            logger.warning("tool_registered_without_tier | name=%s | defaulted=TIER_3", name)

        # Collision handling: if name exists and source differs, prefix
        if name in self._tools:
            existing = self._tools[name]
            if existing.source != source:
                # Prefix with source
                prefixed = f"{source.replace(':', '_')}_{name}"
                logger.warning("tool_name_collision | name=%s | renamed=%s", name, prefixed)
                name = prefixed
            else:
                # Same source, overwrite
                logger.info("tool_overwritten | name=%s | source=%s", name, source)

        # Canonical seed override (Req 3.2, 3.3): authoritative tier wins,
        # keyed by the ORIGINAL requested name so it still applies even when a
        # collision rename changed the storage key above.
        if seeded:
            risk_tier = _CANONICAL_TIER_SEEDS[original_name]

        # Resolve the tier to the canonical enum so we can compare/floor it.
        # ``ToolEntry`` would coerce again, but we need the enum here to apply
        # the offensive floor below; coercion is idempotent on a RiskTier.
        resolved_tier = _coerce_risk_tier(risk_tier)

        # Offensive-security tier FLOOR (Req 18.1, 18.2, 18.3): tools that are
        # offensive — either by explicit name or by ``category == "security"``
        # — must always route through the Confirmation_Gate, so their effective
        # tier is floored at TIER_2. The floor only RAISES: a TIER_0/TIER_1
        # offensive tool is lifted to TIER_2; a tool already at TIER_2/TIER_3
        # is left untouched. The canonical seed table still takes precedence
        # (its names resolve >= TIER_2 already, so the floor is a no-op there).
        is_offensive = (original_name in _OFFENSIVE_TOOL_NAMES) or (category == "security")
        if is_offensive and resolved_tier.value < RiskTier.TIER_2.value:
            logger.info(
                "offensive_tool_tier_floor | name=%s | raised_to=TIER_2", original_name
            )
            resolved_tier = RiskTier.TIER_2

        self._tools[name] = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            risk_tier=resolved_tier,
            category=category,
            source=source,
            timeout=timeout,
            calling_convention=calling_convention,
            variadic_param=variadic_param,
        )
        logger.debug("tool_registered | name=%s | source=%s", name, source)

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> ToolEntry | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_tier(self, name: str) -> RiskTier:
        """Return the recorded ``RiskTier`` of a registered tool.

        Fails closed: an unknown tool resolves to ``RiskTier.TIER_3`` (the most
        restrictive tier) so a name that was never registered is never silently
        under-gated (Req 3.5).
        """
        entry = self._tools.get(name)
        if entry is None:
            return RiskTier.TIER_3
        return entry.risk_tier

    def execute(self, name: str, args: dict) -> Any:
        """Execute a tool by name with arguments.

        The call form is selected from ``entry.calling_convention``:

        - ``"KWARGS"`` — ``handler(**args)`` (Pattern A ``@tool`` functions).
        - ``"ARGS_DICT"`` — ``handler(args)`` (Pattern B ``_tool_*`` methods and
          MCP wrappers, which take a single positional ``dict``).
        - ``"VARIADIC"`` — read the declared ``variadic_param`` (e.g. ``"keys"``)
          from ``args``; if it is a list/tuple, call ``handler(*value)``;
          otherwise fall back to ``handler(**args)`` defensively.

        Schema validation interacts with the convention: required-param checks
        and unknown-param stripping only apply when the schema actually declares
        non-empty ``properties``. Stripping is further restricted to ``KWARGS``
        tools — ``ARGS_DICT``/``VARIADIC`` handlers receive the whole args dict
        and must not have keys removed (Pattern B schemas are the empty
        ``{"type": "object", "properties": {}}`` and would otherwise strip
        everything).
        """
        entry = self._tools.get(name)
        if not entry:
            return f"Error: Tool '{name}' not found"

        convention = entry.calling_convention

        # Validate args against registered schema. Only act when the schema
        # actually declares properties; an empty ``properties`` map (used by
        # ARGS_DICT Pattern B tools) declares nothing to validate or strip.
        if entry.parameters:
            schema_props = entry.parameters.get("properties", {})
            if schema_props:
                schema_required = entry.parameters.get("required", [])
                for req in schema_required:
                    if req not in args:
                        return f"Error: Missing required parameter '{req}' for tool '{name}'"
                # Strip unknown params to prevent injection — KWARGS only, since
                # ARGS_DICT/VARIADIC handlers consume the whole args dict.
                if convention == "KWARGS":
                    unknown = set(args) - set(schema_props)
                    if unknown:
                        logger.warning("tool_unknown_params | name=%s | dropped=%s", name, unknown)
                        args = {k: v for k, v in args.items() if k not in unknown}

        try:
            if convention == "ARGS_DICT":
                result = entry.handler(args)
            elif convention == "VARIADIC":
                value = args.get(entry.variadic_param) if entry.variadic_param else None
                if isinstance(value, (list, tuple)):
                    result = entry.handler(*value)
                else:
                    result = entry.handler(**args)
            else:  # KWARGS (default; Pattern A)
                result = entry.handler(**args)
            return result
        except Exception as e:
            logger.error("tool_execution_failed | name=%s | error=%s", name, e)
            return f"Error executing {name}: {e}"

    def get_tools_for_llm(
        self,
        categories: list[str] | None = None,
        sources: list[str] | None = None,
        tool_list: list[str] | None = None,
    ) -> list[dict]:
        """Get tools in OpenAI function-calling format with optional filtering."""
        tools = []
        for name, entry in self._tools.items():
            # Filter by explicit tool list
            if tool_list and name not in tool_list:
                continue
            # Filter by category
            if categories and entry.category not in categories:
                continue
            # Filter by source
            if sources and entry.source not in sources:
                continue
            tools.append(entry.to_openai_format())
        return tools

    def get_tools_for_agent(self, agent_spec) -> list[dict]:
        """Get tools scoped to an agent's declared tool list."""
        if not agent_spec:
            return self.get_tools_for_llm()

        agent_tools = getattr(agent_spec, "tools", [])
        return self.get_tools_for_llm(tool_list=agent_tools)

    def get_tool_names(self) -> list[str]:
        """Get all registered tool names."""
        return list(self._tools.keys())

    def get_tools_text(self) -> str:
        """Get all tools as a simple text block for system prompt injection.

        Format: "- tool_name: description"
        """
        lines = []
        for name, entry in sorted(self._tools.items()):
            lines.append(f"- {name}: {entry.description}")
        return "\n".join(lines)

    def get_tool_count(self) -> int:
        """Get the number of registered tools."""
        return len(self._tools)

    def get_tools_by_category(self, category: str) -> list[ToolEntry]:
        """Get all tools in a category."""
        return [e for e in self._tools.values() if e.category == category]

    def get_tools_by_source(self, source: str) -> list[ToolEntry]:
        """Get all tools from a source."""
        return [e for e in self._tools.values() if e.source == source]

    def clear(self) -> None:
        """Clear all tools (for testing)."""
        self._tools.clear()

    def auto_discover(self, tools_package: str = "charlie.tools") -> int:
        """Auto-discover @tool decorated functions in a package.

        Returns the number of tools discovered.
        """
        count = 0
        try:
            package = importlib.import_module(tools_package)
            package_file = getattr(package, "__file__", None)
            if not package_file:
                logger.warning("auto_discover_no_file | package=%s", tools_package)
                return 0
            package_path = Path(package_file).parent

            for importer, modname, ispkg in pkgutil.iter_modules([str(package_path)]):
                if modname.startswith("_"):
                    continue

                try:
                    module = importlib.import_module(f"{tools_package}.{modname}")
                    tools = _discover_module_tools(module)
                    for tool_meta in tools:
                        # ``_tool_meta["risk_tier"]`` is already a canonical
                        # RiskTier enum (set by the @tool decorator); pass it
                        # through directly. Pattern A @tool functions take
                        # keyword args → KWARGS calling convention (the default),
                        # unless the decorator detected a variadic parameter.
                        self.register(
                            name=tool_meta["name"],
                            description=tool_meta["description"],
                            parameters=tool_meta["parameters"],
                            handler=tool_meta["handler"],
                            risk_tier=tool_meta.get("risk_tier", RiskTier.TIER_3),
                            category=tool_meta.get("category", "general"),
                            source="native",
                            timeout=tool_meta.get("timeout", 30),
                            calling_convention=tool_meta.get("calling_convention", "KWARGS"),
                            variadic_param=tool_meta.get("variadic_param"),
                        )
                        count += 1
                except Exception as e:
                    logger.warning("module_discovery_failed | module=%s | error=%s", modname, e)

        except Exception as e:
            logger.error("auto_discover_failed | package=%s | error=%s", tools_package, e)

        logger.info("auto_discover_complete | tools_found=%d", count)
        return count

    def register_from_handler(self, handler_obj) -> int:
        """Register tools from an existing ToolHandler object (backward compat).

        Scans for _tool_* methods and registers them with proper risk tiers.

        Pattern B ``_tool_*`` methods take a single positional ``dict`` of
        arguments, so they are registered with ``calling_convention="ARGS_DICT"``.
        The wrapper used to register them preserves the resolved tier
        (``wrapper._risk_tier = tier.value``) so that a later
        ``get_tool_tier(entry.handler)`` still resolves to the correct tier
        rather than failing closed to TIER_3 (the original bug).
        """
        count = 0
        for attr_name in dir(handler_obj):
            if not attr_name.startswith("_tool_"):
                continue
            method = getattr(handler_obj, attr_name)
            if not callable(method):
                continue

            tool_name = attr_name[6:]  # Remove _tool_ prefix
            doc = method.__doc__ or f"Tool: {tool_name}"

            # Resolve the bound method's tier via the canonical helper.
            tier = get_tool_tier(method)

            # Pattern B methods take a single positional dict. Build a wrapper
            # that forwards a dict positionally, and preserve the tier on the
            # wrapper so any later get_tool_tier(entry.handler) still resolves.
            def _make_wrapper(m):
                def wrapper(args):
                    return m(args)
                return wrapper

            wrapper = _make_wrapper(method)
            # Preserve both the enum (for get_tool_tier lookups) and the
            # int value (for backward-compat callers that expect an int).
            wrapper._risk_tier = tier
            wrapper._risk_tier_value = tier.value

            self.register(
                name=tool_name,
                description=doc.strip().split("\n")[0],
                parameters={"type": "object", "properties": {}},
                handler=wrapper,
                risk_tier=tier,
                source="native",
                calling_convention="ARGS_DICT",
            )
            count += 1

        logger.info("registered_from_handler | tools=%d", count)
        return count


def _discover_module_tools(module) -> list[dict]:
    """Discover @tool decorated functions in a module."""
    tools = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_tool_meta"):
            tools.append(obj._tool_meta)
    return tools
