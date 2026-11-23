"""Desktop (notify-send) and optional phone (ntfy) notifications, with de-duplication.

settings.json:
  "notifications": {
    "desktop": true,
    "ntfy_url": "",          # e.g. "https://ntfy.sh/<your-private-topic>"; empty = off
    "dedupe_hours": 6        # same `key` is not re-sent within this window
  }
  "series_name": "Alafasy"   # optional; put in front of every notification title, so two series are never confused
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
import series

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = series.settings_file()  # SURAH_SETTINGS: tests / smoke runs


def _settings() -> tuple[dict[str, object], Path]:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}
    config = settings.get("notifications") if isinstance(settings.get("notifications"), dict) else {}
    log_file = Path(str(settings.get("log_file", SCRIPT_DIR / "Logs.json")))
    return config, log_file.with_name(".notify_state.json")


def with_series(title: str) -> str:
    """The title with the series name in front, e.g. "[Alafasy] Uploaded surah 5" (unchanged when no name is set)."""
    try:
        name = str(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")).get("series_name") or "").strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        name = ""
    return f"[{name}] {title}" if name else title


def _seen_recently(state_file: Path, key: str, window_seconds: float) -> bool:
    lock_path = state_file.with_name(state_file.name + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            now = time.time()
            if now - float(state.get(key, 0)) < window_seconds:
                return True
            state = {k: v for k, v in state.items() if now - float(v) < 7 * 86400}
            state[key] = now
            state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
            return False
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def notify(title: str, message: str, level: str = "info", key: str | None = None) -> None:
    """Never raises: a failed notification must not break the pipeline.

    title should be ASCII (it goes into an HTTP header for ntfy); message may be any UTF-8.
    key: de-duplication key; repeated keys within dedupe_hours are dropped.
    """
    try:
        config, state_file = _settings()
        title = with_series(title)
        if key and _seen_recently(state_file, key, float(config.get("dedupe_hours", 6)) * 3600):
            return
        if config.get("desktop", True) and shutil.which("notify-send"):
            urgency = {"error": "critical", "warning": "normal"}.get(level, "low")
            subprocess.run(["notify-send", "-a", "Surah channel", "-u", urgency, title, message], timeout=10, check=False, capture_output=True)
        ntfy_url = str(config.get("ntfy_url") or "").strip()
        if ntfy_url.startswith("https://"):
            priority = {"error": "high", "warning": "default"}.get(level, "low")
            request = urllib.request.Request(
                ntfy_url,
                data=message.encode("utf-8"),
                headers={"Title": title.encode("ascii", "replace").decode("ascii"), "Priority": priority, "Tags": "warning" if level == "error" else "white_check_mark"},
                method="POST",
            )
            urllib.request.urlopen(request, timeout=15).close()
    except Exception:
        pass
