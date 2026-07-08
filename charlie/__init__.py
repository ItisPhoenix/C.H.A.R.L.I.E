from .config import config
from .tools import registry


def __getattr__(name: str):
    """Lazy imports for heavy modules (torch) to avoid unnecessary imports."""
    if name == "Brain":
        from .core import Brain

        return Brain
    if name == "VoiceEngine":
        from .voice import VoiceEngine

        return VoiceEngine
    if name == "SessionStore":
        from .session_store import SessionStore

        return SessionStore
    if name == "Blackboard":
        from .blackboard import Blackboard

        return Blackboard
    if name == "SwarmOrchestrator":
        from .swarm import SwarmOrchestrator

        return SwarmOrchestrator
    if name == "MemoryGraph":
        from .memory_graph import MemoryGraph

        return MemoryGraph
    if name == "ReflectionEngine":
        from .reflection import ReflectionEngine

        return ReflectionEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "config",
    "Brain",
    "VoiceEngine",
    "SessionStore",
    "Blackboard",
    "SwarmOrchestrator",
    "MemoryGraph",
    "ReflectionEngine",
    "registry",
]

__version__ = "2.0.0-alpha.1"
