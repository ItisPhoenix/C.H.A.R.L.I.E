from .core import Brain
from .config import config
from .voice import VoiceEngine
from .wake_word import WakeWordEngine
from .llm_router import LLMRouter
from .mcp_client import CharlieMCPClient
from .discovery import SystemDiscovery

__all__ = [
    "Brain",
    "config",
    "VoiceEngine",
    "WakeWordEngine",
    "LLMRouter",
    "CharlieMCPClient",
    "SystemDiscovery",
]

__version__ = "1.1.0"