"""Desktop (notify-send) and optional phone (ntfy) notifications, with de-duplication.

settings.json:
  "notifications": {
    "desktop": true,
    "ntfy_url": "",          # e.g. "https://ntfy.sh/<your-private-topic>"; empty = off
    "dedupe_hours": 6,       # same `key` is not re-sent within this window
    "icon_dark_mode": "",    # picture shown on notifications while the desktop is in dark mode (a light drawing)
    "icon_light_mode": ""    # ... and while it is in light mode (a dark drawing); empty or missing file = no icon
  }
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
import settings_path

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = settings_path.settings_file()  # SURAH_SETTINGS: tests / smoke runs


def _settings() -> tuple[dict[str, object], Path]:
    try:
        settings = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        settings = {}
    config = settings.get("notifications") if isinstance(settings.get("notifications"), dict) else {}
    log_file = Path(str(settings.get("log_file", SCRIPT_DIR / "Logs.json")))
    return config, log_file.with_name(".notify_state.json")


def desktop_is_dark() -> bool:
    """Is the desktop in dark mode right now? Asked every time, so switching modes is followed at once.
    When it cannot be told (not GNOME, no session), dark is assumed."""
    try:
        answer = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    if answer.returncode != 0:
        return True
    return "dark" in answer.stdout.lower()


def icon_args(config: dict[str, object] | None = None) -> list[str]:
    """notify-send arguments for the icon that suits the desktop's light or dark mode ([] when there is none)."""
    if config is None:
        config, _state = _settings()
    path = str(config.get("icon_dark_mode" if desktop_is_dark() else "icon_light_mode") or "").strip()
    return ["-i", path] if path and Path(path).is_file() else []


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
        if key and _seen_recently(state_file, key, float(config.get("dedupe_hours", 6)) * 3600):
            return
        if config.get("desktop", True) and shutil.which("notify-send"):
            urgency = {"error": "critical", "warning": "normal"}.get(level, "low")
            subprocess.run(["notify-send", "-a", "Surah channel", "-u", urgency, *icon_args(config), title, message], timeout=10, check=False, capture_output=True)
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
