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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "config",
    "Brain",
    "VoiceEngine",
    "SessionStore",
    "registry",
]

__version__ = "1.1.0"
