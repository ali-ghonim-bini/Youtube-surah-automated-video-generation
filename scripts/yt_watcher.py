#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import series
from keep_awake import run_awake
from notify import notify
import status as pipeline_status
import todo_file as todo_file_module
from shared_log import append_event


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = series.settings_file()  # SURAH_SETTINGS: tests / smoke runs
UPLOAD_SCRIPT = SCRIPT_DIR / "youtube_upload.py"
DEFAULT_TODO_FILE = Path("/path/to/Channel/سور/YT_Todo.md")
DEFAULT_LOG_FILE = Path("/path/to/Channel/سور/Logs.json")
DEFAULT_SURAH_FILE = Path("/path/to/Channel/سور/Surhs.txt")
DEFAULT_SCHEDULE_FILE = Path("/path/to/Channel/سور/YT_Schedule.json")
POLL_SECONDS = 2.0
SAVE_SETTLE_SECONDS = 0.5
BETWEEN_UPLOADS_SECONDS = 30.0
RETRY_SECONDS = 30 * 60  # paused uploads (login/quota/outage) are retried this often
OFFLINE_RETRY_SECONDS = 5 * 60  # no internet: try again sooner
SYNC_RETRY_SECONDS = 10 * 60  # deleted-video check skipped (offline/login): try again in 10 min
UPLOAD_TIMEOUT_SECONDS = 3 * 60 * 60
SYNC_DELETED_SECONDS = 60 * 60  # check for videos you deleted on YouTube (1 quota unit)
LOGIN_CHECK_SECONDS = 60 * 60
LOGIN_VALID_DAYS = 7  # Google ends a Testing-mode login 7 days after the browser login
LOGIN_REMIND_HOURS = 24
DEFAULT_TOKEN_FILE = Path("/path/to/Channel/auth_certification/token.json")
# Must match youtube_upload.py
EXIT_OK, EXIT_ERROR, EXIT_AUTH, EXIT_DEFERRED, EXIT_WAITING_ORDER = 0, 1, 2, 3, 4
PAUSE_EXIT_CODES = (EXIT_AUTH, EXIT_DEFERRED)


def load_settings() -> dict[str, object]:
    if SETTINGS_FILE.exists():
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {"youtube": {"todo_file": str(DEFAULT_TODO_FILE)}, "log_file": str(DEFAULT_LOG_FILE)}


def setting_path(settings: dict[str, object], key: str, default: Path) -> Path:
    value = settings.get(key)
    if isinstance(value, str) and value:
        return Path(value)
    return default


def youtube_todo_path(settings: dict[str, object]) -> Path:
    youtube = settings.get("youtube")
    if isinstance(youtube, dict) and isinstance(youtube.get("todo_file"), str):
        return Path(str(youtube["todo_file"]))
    return DEFAULT_TODO_FILE


def nested_youtube_path(settings: dict[str, object], key: str, default: Path) -> Path:
    youtube = settings.get("youtube")
    if isinstance(youtube, dict) and isinstance(youtube.get(key), str):
        return Path(str(youtube[key]))
    return default




def ensure_todo_file(todo_file: Path, surah_file: Path, skip: set[int] = frozenset({18})) -> None:
    todo_file_module.create_if_missing(todo_file, "# YouTube TODO", todo_file_module.read_surah_names(surah_file), skip)


def ensure_schedule_file(schedule_file: Path) -> None:
    if schedule_file.exists() and schedule_file.stat().st_size > 0:
        return
    schedule_file.parent.mkdir(parents=True, exist_ok=True)
    schedule_file.write_text(json.dumps({"latest_publish_at": None, "videos": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def log_event(log_file: Path, event: str, level: str = "INFO", **fields: object) -> None:
    status = "ERROR" if level == "ERROR" else "WATCHING"
    append_event(log_file, "trigger", event, level=level, run_id="yt_watcher", status=status, **fields)


def latest_upload_error(log_file: Path) -> dict[str, object]:
    if not log_file.exists() or log_file.stat().st_size == 0:
        return {}
    try:
        data = json.loads(log_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    section = data.get("youtube_upload")
    if not isinstance(section, dict):
        return {}
    events = section.get("events")
    if not isinstance(events, list):
        return {}
    for event in events:
        if isinstance(event, dict) and event.get("level") == "ERROR":
            return {key: value for key, value in event.items() if key not in {"ts", "level", "run_id"}}
    return {}


def todo_numbers(todo_file: Path) -> set[int]:
    # STARTED = interrupted upload; the uploader resumes it first, so it counts as pending.
    return todo_file_module.numbers(todo_file, {"TODO", "STARTED"})


def todo_count(todo_file: Path) -> int:
    return len(todo_numbers(todo_file))


def current_mtime(path: Path) -> int | None:
    try:
        return todo_file_module.storage_path(path).stat().st_mtime_ns   # Queue.md when the two queues share one file
    except FileNotFoundError:
        return None


def last_deferral_was_offline(log_file: Path) -> bool:
    """True if the uploader's latest event is a postponement because the internet was down."""
    try:
        events = json.loads(log_file.read_text(encoding="utf-8")).get("youtube_upload", {}).get("events", [])
    except (OSError, ValueError, AttributeError):
        return False
    for event in events[:3]:
        if isinstance(event, dict) and event.get("event") in {"UPLOAD_DEFERRED", "RUN_END"} and "offline" in event:
            return bool(event.get("offline"))
    return False


def retry_delay(log_file: Path) -> float:
    return OFFLINE_RETRY_SECONDS if last_deferral_was_offline(log_file) else RETRY_SECONDS


def run_until_empty(log_file: Path, todo_file: Path, reason: str, todo_items: int) -> bool:
    """Upload one surah per run until TODO is empty.

    Returns True when a retry later is needed (paused for login/quota/outage, or something unexpected).
    A single surah that fails permanently goes to ERROR and the loop moves on to the next one.
    """
    while todo_items:
        before = todo_numbers(todo_file)
        returncode = run_upload(log_file, reason, todo_items)
        if returncode == EXIT_WAITING_ORDER:
            return False  # a higher surah isn't uploaded yet; the hand-off's file change wakes us up
        if returncode in PAUSE_EXIT_CODES:
            return True
        if returncode not in (EXIT_OK, EXIT_ERROR):
            notify("YouTube uploader crashed", f"Exit code {returncode}. See Logs.json (trigger section).", level="error", key=f"yt-crash-{returncode}")
            return True
        after = todo_numbers(todo_file)
        # Progress = something that was queued left TODO. New surahs may be added meanwhile (hand-off, edits);
        # that's fine and must not stop the loop. Only stop if nothing left (the uploader didn't take any).
        if before and before <= after:
            log_event(log_file, "YT_WATCHER_TODO_NOT_SHRINKING", level="ERROR", before=sorted(before), after=sorted(after))
            return True
        todo_items = len(after)
        reason = "todo_remaining"
        if todo_items:
            time.sleep(BETWEEN_UPLOADS_SECONDS)
    return False


def run_upload(log_file: Path, reason: str, todo_items: int) -> int:
    log_event(log_file, "YT_WATCHER_UPLOAD_START", reason=reason, todo=todo_items)
    # stdin=DEVNULL: the uploader must never wait for an interactive OAuth login it can't show.
    try:
        result = run_awake([sys.executable, str(UPLOAD_SCRIPT)], "Uploading surah video", timeout=UPLOAD_TIMEOUT_SECONDS, cwd=str(Path.home()), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        log_event(log_file, "YT_WATCHER_UPLOAD_TIMEOUT", level="ERROR", timeout_seconds=UPLOAD_TIMEOUT_SECONDS)
        return -1
    if result.returncode == EXIT_OK:
        log_event(log_file, "YT_WATCHER_UPLOAD_DONE", todo=todo_items)
        return EXIT_OK
    if result.returncode == EXIT_WAITING_ORDER:
        log_event(log_file, "YT_WATCHER_WAITING_FOR_ORDER", todo=todo_items)
        return result.returncode
    if result.returncode in PAUSE_EXIT_CODES:
        offline = result.returncode == EXIT_DEFERRED and last_deferral_was_offline(log_file)
        log_event(log_file, "YT_WATCHER_UPLOAD_PAUSED", level="WARNING", returncode=result.returncode,
                  retry_in_seconds=OFFLINE_RETRY_SECONDS if offline else RETRY_SECONDS,
                  reason="auth_required" if result.returncode == EXIT_AUTH else ("offline" if offline else "deferred"))
        return result.returncode
    else:
        upload_error = latest_upload_error(log_file)
        upload_error_message = upload_error.get("api_message") or upload_error.get("message") if upload_error else ""
        upload_error_reason = upload_error.get("api_reason") if upload_error else ""
        log_event(
            log_file,
            "YT_WATCHER_UPLOAD_ERROR",
            level="ERROR",
            returncode=result.returncode,
            stderr=result.stderr[-4000:],
            stdout=result.stdout[-4000:],
            upload_error_message=upload_error_message,
            upload_error_reason=upload_error_reason,
            upload_error=upload_error,
        )
        return result.returncode


def sync_deleted(log_file: Path) -> bool:
    """Ask the uploader to requeue surahs whose YouTube video you deleted; its queue edit wakes the loop."""
    try:
        result = subprocess.run([sys.executable, str(UPLOAD_SCRIPT), "--sync-deleted"], cwd=str(Path.home()), text=True,
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
    except subprocess.TimeoutExpired:
        log_event(log_file, "YT_WATCHER_SYNC_ERROR", level="WARNING", message="timeout")
        return False
    if result.returncode in PAUSE_EXIT_CODES:
        # offline or logged out: one quiet line, try again soon
        log_event(log_file, "YT_WATCHER_SYNC_SKIPPED", reason="login_required" if result.returncode == EXIT_AUTH else "offline_or_deferred",
                  message=result.stderr.strip()[-300:])
        return False
    if result.returncode != 0:
        log_event(log_file, "YT_WATCHER_SYNC_ERROR", level="WARNING", returncode=result.returncode, stderr=result.stderr[-1000:])
        return False
    if "none" not in result.stdout:
        log_event(log_file, "YT_WATCHER_SYNC_DELETED", output=result.stdout.strip()[-500:])
    return True


def series_finished(upload_queue: Path, video_queue: Path) -> bool:
    """Every surah is uploaded (or ignored) and nothing is waiting to be made: the series is complete."""
    upload = todo_file_module.read_statuses(upload_queue)
    video = todo_file_module.read_statuses(video_queue)
    return bool(upload) and all(status in {"DONE", "IGNORE"} for status in [*upload.values(), *video.values()])


def cleanup_uploaded(log_file: Path) -> bool:
    """Ask the uploader to check uploaded videos on YouTube and remove the local files of the confirmed ones.
    Returns True when no local files are left to check."""
    try:
        result = subprocess.run([sys.executable, str(UPLOAD_SCRIPT), "--cleanup-uploaded"], cwd=str(Path.home()), text=True,
                                capture_output=True, stdin=subprocess.DEVNULL, timeout=600)
    except subprocess.TimeoutExpired:
        log_event(log_file, "YT_WATCHER_CLEANUP_ERROR", level="WARNING", message="timeout")
        return False
    if result.returncode not in PAUSE_EXIT_CODES and result.returncode != 0:
        log_event(log_file, "YT_WATCHER_CLEANUP_ERROR", level="WARNING", returncode=result.returncode, stderr=result.stderr[-1000:])
    return result.returncode == 0 and "Nothing local left" in result.stdout


_LOGIN_WARNED: set[str] = set()


def check_login_expiry(log_file: Path, token_file: Path, testing_mode: bool, now: datetime | None = None) -> str | None:
    """Warn (once a day) when the YouTube login is within a day of Google's 7-day Testing-mode limit.
    Returns "soon", "expired" or None."""
    if not testing_mode:
        return None
    try:
        login_at = datetime.fromisoformat(json.loads(token_file.with_name("token_meta.json").read_text(encoding="utf-8"))["login_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    now = now or datetime.now().astimezone()
    expires = login_at + timedelta(days=LOGIN_VALID_DAYS)
    if now < expires - timedelta(hours=LOGIN_REMIND_HOURS):
        return None
    state = "expired" if now >= expires else "soon"
    fix = f"Run in a terminal:\npython3 {UPLOAD_SCRIPT} --auth-only"
    if state == "soon":
        title, message = "YouTube login expires soon", f"It stops working around {expires:%a %d %b %H:%M}. Log in again to avoid paused uploads.\n{fix}"
    else:
        title, message = "YouTube login has expired", f"It ran out on {expires:%a %d %b %H:%M}. Uploads pause until you log in again.\n{fix}"
    key = f"yt-login-{state}-{login_at:%Y%m%d%H%M}-{now:%Y%m%d}"  # at most once a day per login
    if key not in _LOGIN_WARNED:
        _LOGIN_WARNED.add(key)
        notify(title, message, level="warning", key=key)
        log_event(log_file, "YT_WATCHER_LOGIN_EXPIRY", level="WARNING", state=state, login_at=login_at.isoformat(), expires=expires.isoformat())
    return state


_SUMMARY_SENT: set[str] = set()


def send_daily_summary(log_file: Path, settings: dict[str, object], now: datetime | None = None) -> bool:
    """Once a day, from notifications.daily_summary_hour (default 21; null turns it off)."""
    config = settings.get("notifications") if isinstance(settings.get("notifications"), dict) else {}
    hour = config.get("daily_summary_hour", 21)
    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    if hour is None or hour is False or now.hour < int(hour) or day in _SUMMARY_SENT:
        return False
    _SUMMARY_SENT.add(day)
    try:
        title, message = pipeline_status.daily_summary(settings, now)
    except Exception as exc:   # a summary must never stop uploads
        log_event(log_file, "YT_WATCHER_SUMMARY_ERROR", level="WARNING", message=str(exc)[:300])
        return False
    notify(title, message, key=f"daily-summary-{day}")
    log_event(log_file, "YT_WATCHER_DAILY_SUMMARY", message=message)
    return True


def report_sleep(log_file: Path, settings: dict[str, object], gap: tuple[datetime, datetime]) -> None:
    """The laptop was suspended: say so if work was in progress (it paused and has resumed by itself)."""
    slept_from, slept_until = gap
    paths = pipeline_status.paths(settings)
    busy = []
    for label, key in (("render of surah", "todo"), ("upload of surah", "yt_todo")):
        started = pipeline_status.queue_summary(paths[key])["STARTED"]
        if started:
            busy.append(f"{label} {started[0]}")
    hours = (slept_until - slept_from).total_seconds() / 3600
    log_event(log_file, "YT_WATCHER_SLEPT", slept_from=slept_from.isoformat(timespec="seconds"), slept_until=slept_until.isoformat(timespec="seconds"),
              hours=round(hours, 2), in_progress=busy)
    if busy:
        notify("Laptop was asleep", f"Asleep {slept_from:%a %H:%M} -> {slept_until:%a %H:%M} ({hours:.1f} h).\nPaused meanwhile: {', '.join(busy)}. It continues by itself now.",
               level="warning", key=f"slept-{slept_until:%Y%m%d%H%M}")


def main() -> int:
    settings = load_settings()
    todo_file = youtube_todo_path(settings)
    surah_file = setting_path(settings, "surah_file", DEFAULT_SURAH_FILE)
    schedule_file = nested_youtube_path(settings, "schedule_file", DEFAULT_SCHEDULE_FILE)
    log_file = setting_path(settings, "log_file", DEFAULT_LOG_FILE)
    token_file = nested_youtube_path(settings, "token_file", DEFAULT_TOKEN_FILE)
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    testing_mode = bool(youtube.get("oauth_testing_mode", True))
    cleanup_settings = settings.get("cleanup") if isinstance(settings.get("cleanup"), dict) else {}
    cleanup_enabled = bool(cleanup_settings.get("delete_local_after_upload", False))
    ensure_todo_file(todo_file, surah_file, {int(n) for n in settings.get("skip_surah_numbers", [18])})
    ensure_schedule_file(schedule_file)
    last_mtime = current_mtime(todo_file)
    log_event(log_file, "YT_WATCHER_START", todo_file=str(todo_file), poll_seconds=POLL_SECONDS)

    retry_at: float | None = None
    video_queue = setting_path(settings, "todo_file", todo_file.with_name("Todo.md"))
    # A series whose every surah is uploaded has nothing left to do: no hourly questions to YouTube, no login reminder.
    finished = series_finished(todo_file, video_queue)
    cleanup_left = cleanup_enabled   # still has local files that wait for YouTube's confirmation
    if finished:
        log_event(log_file, "YT_WATCHER_SERIES_FINISHED", message="every surah is uploaded; hourly checks are off until the queue changes")
        last_sync = time.time() - SYNC_DELETED_SECONDS   # so the one remaining job (local cleanup) runs on the first idle round
    else:
        last_sync = time.time() if sync_deleted(log_file) else time.time() - SYNC_DELETED_SECONDS + SYNC_RETRY_SECONDS
        check_login_expiry(log_file, token_file, testing_mode)
    last_login_check = time.time()
    initial_todo = todo_count(todo_file)
    if initial_todo:
        if run_until_empty(log_file, todo_file, "initial_todo", initial_todo):
            retry_at = time.time() + retry_delay(log_file)
        last_mtime = current_mtime(todo_file)

    sleeper = pipeline_status.SleepDetector()
    while True:
        time.sleep(POLL_SECONDS)
        gap = sleeper.tick()
        if gap:
            report_sleep(log_file, settings, gap)
        new_mtime = current_mtime(todo_file)
        if new_mtime != last_mtime:
            time.sleep(SAVE_SETTLE_SECONDS)
            reason = "todo_changed"
            items = todo_count(todo_file)
            log_event(log_file, "YT_WATCHER_TODO_CHANGED", todo=items)
        elif retry_at is not None and time.time() >= retry_at:
            reason = "retry"
            items = todo_count(todo_file)
        else:
            if time.time() - last_sync >= SYNC_DELETED_SECONDS:
                now_finished = series_finished(todo_file, video_queue)
                if now_finished and not finished:
                    log_event(log_file, "YT_WATCHER_SERIES_FINISHED", message="every surah is uploaded; hourly checks are off until the queue changes")
                    cleanup_left = cleanup_enabled
                finished = now_finished
                if finished:
                    last_sync = time.time()
                    if cleanup_left:   # only until the last uploaded surah's files are confirmed and removed
                        cleanup_left = not cleanup_uploaded(log_file)
                else:
                    synced = sync_deleted(log_file)
                    last_sync = time.time() if synced else time.time() - SYNC_DELETED_SECONDS + SYNC_RETRY_SECONDS
                    if synced and cleanup_enabled:   # same hourly round, and only when YouTube was reachable
                        cleanup_uploaded(log_file)
            if not finished and time.time() - last_login_check >= LOGIN_CHECK_SECONDS:
                last_login_check = time.time()
                check_login_expiry(log_file, token_file, testing_mode)
            send_daily_summary(log_file, settings)
            continue
        retry_at = None
        if items and run_until_empty(log_file, todo_file, reason, items):
            retry_at = time.time() + retry_delay(log_file)
        last_mtime = current_mtime(todo_file)


if __name__ == "__main__":
    raise SystemExit(main())
