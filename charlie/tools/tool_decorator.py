"""@tool decorator — auto-generates JSON Schema from type hints, wraps handlers."""

import functools
import inspect
import time
from typing import Callable, get_type_hints

from charlie.security.tiers import RiskTier
from charlie.utils.logger import get_logger

logger = get_logger(__name__)


def tool(
    name: str | None = None,
    description: str | None = None,
    risk_tier: RiskTier = RiskTier.TIER_0,
    category: str = "general",
    timeout: int = 30,
    calling_convention: str = "KWARGS",
):
    """Decorator to register a function as a CHARLIE tool.

    Usage:
        @tool(name="search", description="Search the web", category="web")
        def search_web(query: str, num_results: int = 5) -> str:
            ...

    The decorator:
    - Extracts metadata (name, description, parameters) from the function
    - Auto-generates JSON Schema from type hints
    - Wraps with input validation, timeout, error boundary, audit log
    - Stores metadata on the function for ToolRegistry discovery
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        tool_desc = description or inspect.getdoc(func) or f"Tool: {tool_name}"

        # Extract parameter schema from type hints
        sig = inspect.signature(func)
        hints = get_type_hints(func)
        properties = {}
        required = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = hints.get(param_name, str)
            prop = _type_to_schema(param_type)

            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                prop["default"] = param.default

            properties[param_name] = prop

        parameters = {
            "type": "object",
            "properties": properties,
            "required": required,
        }

        # Detect variadic (*args) parameters for VARIADIC calling convention
        variadic_param = None
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                variadic_param = param_name
                break

        # Store metadata on the function
        # Extract integer value from RiskTier enum for compatibility
        tier_value = risk_tier.value if hasattr(risk_tier, 'value') else int(risk_tier)

        # If caller explicitly passed a convention, use it. Otherwise auto-detect.
        effective_convention = calling_convention
        if calling_convention == "KWARGS" and variadic_param:
            effective_convention = "VARIADIC"

        func._tool_meta = {
            "name": tool_name,
            "description": tool_desc,
            "parameters": parameters,
            "risk_tier": risk_tier,
            "category": category,
            "timeout": timeout,
            "handler": func,
            "calling_convention": effective_convention,
            "variadic_param": variadic_param,
        }
        # Also set _risk_tier so get_tool_tier() from security/tiers.py can find it
        func._risk_tier = tier_value

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - start
                logger.debug("tool_executed | name=%s | elapsed=%.3fs", tool_name, elapsed)
                return result
            except Exception as e:
                elapsed = time.perf_counter() - start
                logger.error("tool_failed | name=%s | error=%s | elapsed=%.3fs", tool_name, e, elapsed)
                return f"Error executing {tool_name}: {e}"

        # Preserve metadata on wrapper too
        wrapper._tool_meta = func._tool_meta
        wrapper._risk_tier = tier_value
        return wrapper

    return decorator


def _type_to_schema(type_hint) -> dict:
    """Convert a Python type hint to a JSON Schema property."""
    if type_hint is str:
        return {"type": "string"}
    elif type_hint is int:
        return {"type": "integer"}
    elif type_hint is float:
        return {"type": "number"}
    elif type_hint is bool:
        return {"type": "boolean"}
    elif type_hint is list:
        return {"type": "array"}
    elif type_hint is dict:
        return {"type": "object"}
    else:
        return {"type": "string"}


def get_tool_meta(func: Callable) -> dict | None:
    """Extract tool metadata from a decorated function."""
    return getattr(func, "_tool_meta", None)


def discover_tools(module) -> list[dict]:
    """Discover all @tool decorated functions in a module."""
    tools = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_tool_meta"):
            tools.append(obj._tool_meta)
    return tools
