"""Charlie computer-use capability layer (Cua-backed).

Charlie keeps its own tool names, schemas, policy, approval, lease, result-envelope
and verification contracts. Cua is an implementation behind this layer, never an
alternative contract the model sees directly.
"""

from __future__ import annotations

__all__ = ["cua_bridge"]


def __getattr__(name: str):
    """Expose submodules lazily so importing the package stays cheap."""
    if name in __all__:
        import importlib

        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
