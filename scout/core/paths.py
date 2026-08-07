"""
Filesystem paths shared across the platform.
Kept free of environment/config dependencies so any module can import it
without triggering credential loading.
"""

from __future__ import annotations

from pathlib import Path

# scout/core/paths.py -> parents[2] is the repository root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
