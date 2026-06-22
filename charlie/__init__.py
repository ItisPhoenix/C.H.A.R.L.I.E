from .config import config
from .llm_router import LLMRouter
from .mcp_client import CharlieMCPClient
from .discovery import SystemDiscovery
from .widget_bridge import WidgetBridge
from .screen_context import ScreenContextMonitor
from .proactive_remark import ProactiveRemarkEngine

def __getattr__(name: str):
    """Lazy imports for heavy modules (torch, PySide6) to avoid import during multiprocessing spawn."""
    if name == "Brain":
        from .core import Brain
        return Brain
    if name == "VoiceBrain":
        from .brain import VoiceBrain
        return VoiceBrain
    if name == "VoiceEngine":
        from .voice import VoiceEngine
        return VoiceEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "config",
    "LLMRouter",
    "CharlieMCPClient",
    "SystemDiscovery",
    "WidgetBridge",
    "ScreenContextMonitor",
    "ProactiveRemarkEngine",
    "Brain",
    "VoiceBrain",
    "VoiceEngine",
]

__version__ = "1.1.0"
