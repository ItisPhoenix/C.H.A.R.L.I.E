"""
charlie/tools/template.py

Tool template utilities for dynamic tool creation.
Provides ToolResult and tool_metadata used by the dynamic builder.
"""

import functools
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ToolResult:
    """Standard result type returned by dynamic tools."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }


def tool_metadata(name: str, description: str, category: str = "custom"):
    """Decorator to attach metadata to a dynamic tool function."""
    def decorator(func):
        func._tool_name = name
        func._tool_description = description
        func._tool_category = category

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        wrapper._tool_name = name
        wrapper._tool_description = description
        wrapper._tool_category = category
        return wrapper

    return decorator
