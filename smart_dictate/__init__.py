from __future__ import annotations

from importlib.metadata import version as _version

__all__ = ["__version__"]

try:
    __version__ = _version("smart-dictate")
except Exception:
    __version__ = "0.0.0"
