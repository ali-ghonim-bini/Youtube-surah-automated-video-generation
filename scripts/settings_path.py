"""Which settings.json the scripts use, and where their temporary files go.

There is one settings file, scripts/settings.json. The SURAH_SETTINGS environment variable points a
single command (and the tests) at another one without touching the real files.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS = SCRIPT_DIR / "settings.json"


def settings_file() -> Path:
    override = os.environ.get("SURAH_SETTINGS")
    return Path(override) if override else DEFAULT_SETTINGS


def temp_dir() -> Path:
    """Temporary per-surah files. A run with SURAH_SETTINGS (tests) gets its own folder, so it never mixes with the real one."""
    base = Path(tempfile.gettempdir()) / "surah_automation"
    current = settings_file().resolve()
    if current == DEFAULT_SETTINGS.resolve():
        return base
    return base.with_name(f"surah_automation-{hashlib.sha1(str(current).encode()).hexdigest()[:10]}")
