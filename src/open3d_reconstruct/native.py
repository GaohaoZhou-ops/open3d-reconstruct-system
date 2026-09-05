from __future__ import annotations

import ctypes
import os
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []


def load_library(path: Path) -> ctypes.CDLL:
    """Load a native library while keeping its Windows dependency path active."""
    resolved = path.expanduser().resolve()
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        handle = os.add_dll_directory(str(resolved.parent))
        _DLL_DIRECTORY_HANDLES.append(handle)
        return ctypes.CDLL(str(resolved))
    return ctypes.CDLL(str(resolved), mode=ctypes.RTLD_GLOBAL)
