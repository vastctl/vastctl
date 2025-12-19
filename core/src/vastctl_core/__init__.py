"""VastLab Core - Professional GPU instance management library for Vast.ai

This is the core library package. It contains no CLI dependencies (click, rich)
and can be used as a standalone library for programmatic access to Vast.ai.
"""

__version__ = "0.1.0"
__author__ = "alerad"

from .config import Config
from .instance import Instance
from .registry import Registry
from .connection import ConnectionManager
from .storage import StorageManager
from .vast_api import VastAPI, VastApiError
from .environment import EnvironmentManager

__all__ = [
    # Core classes
    "Config",
    "Instance",
    "Registry",
    "ConnectionManager",
    "StorageManager",
    "VastAPI",
    "VastApiError",
    "EnvironmentManager",
    # Version
    "__version__",
]
