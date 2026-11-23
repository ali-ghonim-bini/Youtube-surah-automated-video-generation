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

from auto_queue import AutoQueue
import settings_path
from keep_awake import run_awake
from notify import notify
import todo_file as todo_file_module
from shared_log import append_event


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = settings_path.settings_file()  # SURAH_SETTINGS: tests / smoke runs
AUTOMATION_SCRIPT = SCRIPT_DIR / "surah_automation.py"
DEFAULT_TODO_FILE = Path("/path/to/Channel/سور/Todo.md")
DEFAULT_LOG_FILE = Path("/path/to/Channel/سور/Logs.json")
DEFAULT_SURAH_FILE = Path("/path/to/Channel/سور/Surhs.txt")
POLL_SECONDS = 2.0
SAVE_SETTLE_SECONDS = 0.5


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_settings() -> dict[str, object]:
    if not SETTINGS_FILE.exists():
        subprocess.run([sys.executable, str(AUTOMATION_SCRIPT)], cwd=str(Path.home()), check=False)
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {"todo_file": str(DEFAULT_TODO_FILE), "log_file": str(DEFAULT_LOG_FILE)}


def setting_path(settings: dict[str, object], key: str, default: Path) -> Path:
    value = settings.get(key)
    if isinstance(value, str) and value:
        return Path(value)
    return default


def log_event(log_file: Path, event: str, level: str = "INFO", **fields: object) -> None:
    status = "ERROR" if level == "ERROR" else "WATCHING"
    append_event(log_file, "trigger", event, level=level, run_id="watcher", status=status, **fields)




def ensure_todo_file(todo_file: Path, surah_file: Path, skip: set[int] = frozenset({18})) -> None:
    todo_file_module.create_if_missing(todo_file, "# TODO", todo_file_module.read_surah_names(surah_file), skip)


def todo_count(todo_file: Path) -> int:
    return todo_file_module.count(todo_file)


def current_mtime(path: Path) -> int | None:
    try:
        return todo_file_module.storage_path(path).stat().st_mtime_ns   # Queue.md when the two queues share one file
    except FileNotFoundError:
        return None


RETRY_AFTER_ERROR_SECONDS = 10 * 60   # a failed run is retried on its own (no need to touch the queue file)


def run_automation(log_file: Path, reason: str, todo_items: int) -> bool:
    log_event(log_file, "WATCHER_AUTOMATION_START", reason=reason, todo=todo_items)
    result = run_awake([sys.executable, str(AUTOMATION_SCRIPT)], "Rendering surah video", cwd=str(Path.home()))
    if result.returncode == 0:
        log_event(log_file, "WATCHER_AUTOMATION_DONE", todo=todo_items)
        return True
    if result.returncode == 3:   # network problem; the surah is still in TODO
        log_event(log_file, "WATCHER_AUTOMATION_DEFERRED", level="WARNING", retry_in_seconds=RETRY_AFTER_ERROR_SECONDS)
        return False
    else:
        log_event(log_file, "WATCHER_AUTOMATION_ERROR", level="ERROR", returncode=result.returncode, stderr=result.stderr[-4000:])
        last_line = (result.stderr.strip().splitlines() or ["see Logs.json"])[-1]
        notify("Video pipeline stopped", f"Exit code {result.returncode}: {last_line[:300]}", level="error", key=f"video-run-error-{last_line[:80]}")
        return False


def make_auto_queue(settings: dict[str, object], todo_file: Path, log_file: Path) -> AutoQueue | None:
    config = settings.get("auto_queue") if isinstance(settings.get("auto_queue"), dict) else {}
    if not config.get("enabled", False):
        return None
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    yt_todo = Path(str(youtube.get("todo_file") or todo_file.with_name("YT_Todo.md")))
    surah_dir = Path(str(settings.get("surah_dir") or todo_file.parent))
    exported_dir = Path(str(settings.get("exported_dir") or surah_dir / "Exported_Vids"))
    live_dir = Path(str(settings.get("live_exported_dir") or exported_dir / "Live"))
    return AutoQueue(
        todo_file, yt_todo, surah_dir / "Decisions.json",
        log=lambda event, level="INFO", **fields: log_event(log_file, event, level=level, **fields),
        exported_dir=exported_dir, live_dir=live_dir,
        keep_in_todo=int(config.get("keep_in_todo", 1)),
        ask_reminder_hours=float(config.get("ask_reminder_hours", 6)),
    )


def main() -> int:
    settings = load_settings()
    todo_file = setting_path(settings, "todo_file", DEFAULT_TODO_FILE)
    surah_file = setting_path(settings, "surah_file", DEFAULT_SURAH_FILE)
    log_file = setting_path(settings, "log_file", DEFAULT_LOG_FILE)
    ensure_todo_file(todo_file, surah_file, {int(n) for n in settings.get("skip_surah_numbers", [18])})
    last_mtime = current_mtime(todo_file)
    log_event(log_file, "WATCHER_START", todo_file=str(todo_file), poll_seconds=POLL_SECONDS)

    queue = make_auto_queue(settings, todo_file, log_file)

    retry_at: float | None = None   # set after a failed run; cleared by a successful one

    def attempt(reason: str, items: int) -> None:
        nonlocal retry_at, last_mtime
        ok = run_automation(log_file, reason, items)
        retry_at = None if ok else time.time() + RETRY_AFTER_ERROR_SECONDS
        last_mtime = current_mtime(todo_file)

    initial_todo = todo_count(todo_file)
    if initial_todo:
        attempt("initial_todo", initial_todo)

    while True:
        time.sleep(POLL_SECONDS)
        new_mtime = current_mtime(todo_file)
        if retry_at is not None and time.time() >= retry_at and new_mtime == last_mtime:
            items = todo_count(todo_file)
            if items:
                attempt("retry_after_error", items)
            else:
                retry_at = None
            continue
        if new_mtime == last_mtime:
            if queue is not None:
                try:
                    queue.tick()  # queues the next surah when idle; the file change is picked up next loop
                except Exception as exc:
                    log_event(log_file, "AUTO_QUEUE_ERROR", level="ERROR", message=str(exc))
            continue
        time.sleep(SAVE_SETTLE_SECONDS)
        last_mtime = current_mtime(todo_file)
        items = todo_count(todo_file)
        log_event(log_file, "WATCHER_TODO_CHANGED", todo=items)
        if items:
            attempt("todo_changed", items)


if __name__ == "__main__":
    raise SystemExit(main())
