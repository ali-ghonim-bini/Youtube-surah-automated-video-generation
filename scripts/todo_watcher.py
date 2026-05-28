#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from shared_log import append_event


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_DIR / "settings.json"
AUTOMATION_SCRIPT = SCRIPT_DIR / "surah_automation.py"
POLL_SECONDS = 2.0
SAVE_SETTLE_SECONDS = 0.5
SECTION_ORDER = ("STARTED", "TODO", "WAITING", "DONE", "ERROR", "IGNORE")
SYMBOLS = {"STARTED": "~", "TODO": " ", "WAITING": "?", "DONE": "x", "ERROR": "!", "IGNORE": "-"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_settings() -> dict[str, object]:
    if not SETTINGS_FILE.exists():
        raise RuntimeError(f"Missing settings file: {SETTINGS_FILE}")
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def setting_path(settings: dict[str, object], key: str) -> Path:
    value = settings.get(key)
    if isinstance(value, str) and value:
        return Path(value)
    raise RuntimeError(f"Missing or invalid setting: {key}")


def log_event(log_file: Path, event: str, level: str = "INFO", **fields: object) -> None:
    status = "ERROR" if level == "ERROR" else "WATCHING"
    append_event(log_file, "trigger", event, level=level, run_id="watcher", status=status, **fields)


def read_surahs(surah_file: Path) -> dict[int, str]:
    surahs: dict[int, str] = {}
    if not surah_file.exists():
        return surahs
    for line in surah_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*(\d+)\s*-\s*(.+?)\s*$", line)
        if match:
            surahs[int(match.group(1))] = match.group(2)
    return surahs


def ensure_todo_file(todo_file: Path, surah_file: Path) -> None:
    if todo_file.exists() and todo_file.stat().st_size > 0:
        return
    surahs = read_surahs(surah_file)
    statuses = {number: "WAITING" for number in surahs}
    lines = ["# TODO", ""]
    for status in SECTION_ORDER:
        lines.append(f"## {status}")
        for number in sorted(surahs, reverse=True):
            if statuses.get(number) == status:
                lines.append(f"- [{SYMBOLS[status]}] {number} - {surahs[number]}")
        lines.append("")
    todo_file.parent.mkdir(parents=True, exist_ok=True)
    todo_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def todo_count(todo_file: Path) -> int:
    if not todo_file.exists():
        return 0
    in_todo = False
    count = 0
    for line in todo_file.read_text(encoding="utf-8", errors="replace").splitlines():
        section = re.match(r"^\s*##\s+(.+?)\s*$", line)
        if section:
            in_todo = section.group(1).strip().upper() == "TODO"
            continue
        if in_todo and re.match(r"^\s*-\s*\[\s*\]\s*\d+\s*-", line):
            count += 1
    return count


def current_mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def run_automation(log_file: Path, reason: str, todo_items: int) -> None:
    log_event(log_file, "WATCHER_AUTOMATION_START", reason=reason, todo=todo_items)
    result = subprocess.run([sys.executable, str(AUTOMATION_SCRIPT)], cwd=str(Path.home()), text=True, capture_output=True)
    if result.returncode == 0:
        log_event(log_file, "WATCHER_AUTOMATION_DONE", todo=todo_items)
    else:
        log_event(log_file, "WATCHER_AUTOMATION_ERROR", level="ERROR", returncode=result.returncode, stderr=result.stderr[-4000:])


def main() -> int:
    settings = load_settings()
    todo_file = setting_path(settings, "todo_file")
    surah_file = setting_path(settings, "surah_file")
    log_file = setting_path(settings, "log_file")
    ensure_todo_file(todo_file, surah_file)
    last_mtime = current_mtime(todo_file)
    log_event(log_file, "WATCHER_START", todo_file=str(todo_file), poll_seconds=POLL_SECONDS)

    initial_todo = todo_count(todo_file)
    if initial_todo:
        run_automation(log_file, "initial_todo", initial_todo)
        last_mtime = current_mtime(todo_file)

    while True:
        time.sleep(POLL_SECONDS)
        new_mtime = current_mtime(todo_file)
        if new_mtime == last_mtime:
            continue
        time.sleep(SAVE_SETTLE_SECONDS)
        last_mtime = current_mtime(todo_file)
        items = todo_count(todo_file)
        log_event(log_file, "WATCHER_TODO_CHANGED", todo=items)
        if items:
            run_automation(log_file, "todo_changed", items)
            last_mtime = current_mtime(todo_file)


if __name__ == "__main__":
    raise SystemExit(main())
