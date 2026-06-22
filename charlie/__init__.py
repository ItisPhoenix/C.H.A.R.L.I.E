from .config import config

def __getattr__(name: str):
    """Lazy imports for heavy modules (torch) to avoid unnecessary imports."""
    if name == "Brain":
        from .core import Brain
        return Brain
    if name == "VoiceEngine":
        from .voice import VoiceEngine
        return VoiceEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "config",
    "Brain",
    "VoiceEngine",
]

__version__ = "1.1.0"
