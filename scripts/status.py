#!/usr/bin/env python3
"""One-screen status of the whole pipeline:  python3 scripts/status.py

Also used by yt_watcher.py for the daily summary and the "laptop slept" notice.
Read-only: never changes a queue, the schedule or anything on YouTube.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import todo_file
import series

SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = series.settings_file()
SERVICES = ("surah-todo-watcher", "surah-yt-watcher")
LOGIN_VALID_DAYS = 7


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def paths(settings: dict) -> dict[str, Path]:
    surah_dir = Path(str(settings.get("surah_dir", SCRIPT_DIR.parent / "سور")))
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    return {
        "surah_dir": surah_dir,
        "todo": Path(str(settings.get("todo_file", surah_dir / "Todo.md"))),
        "yt_todo": Path(str(youtube.get("todo_file", surah_dir / "YT_Todo.md"))),
        "schedule": Path(str(youtube.get("schedule_file", surah_dir / "YT_Schedule.json"))),
        "token": Path(str(youtube.get("token_file", SCRIPT_DIR.parent / "auth_certification" / "token.json"))),
        "log": Path(str(settings.get("log_file", surah_dir / "Logs.json"))),
        "exported": Path(str(settings.get("exported_dir", surah_dir / "Exported_Vids"))),
    }


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, minutes = seconds // 3600, seconds % 3600 // 60
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def service_states() -> dict[str, str]:
    states = {}
    for name in SERVICES:
        try:
            states[name] = subprocess.run(["systemctl", "--user", "is-active", name], capture_output=True, text=True, timeout=10).stdout.strip() or "unknown"
        except (OSError, subprocess.TimeoutExpired):
            states[name] = "unknown"
    return states


def queue_summary(path: Path) -> dict[str, list[int]]:
    _title, entries = todo_file.read_entries(path)
    summary: dict[str, list[int]] = {status: [] for status in todo_file.SECTION_ORDER}
    for number in todo_file.in_order(entries):
        summary[entries[number][1]].append(number)
    return summary


def render_progress(surah_dir: Path, now: float | None = None) -> dict | None:
    """The render in progress: surah, percent done and seconds left (None when nothing is rendering)."""
    info = read_json(surah_dir / ".render_progress.json", None)
    if not isinstance(info, dict):
        return None
    try:
        os.kill(int(info["pid"]), 0)                     # is the pipeline that wrote it still running?
        text = Path(info["progress_file"]).read_text(encoding="utf-8", errors="replace")
        started = datetime.fromisoformat(info["started"]).timestamp()
        total = float(info["total_seconds"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    done_us, resumed_us, finished = 0, 0, False
    for line in text.splitlines():
        if line.startswith("resumed_time_us="):   # a continued render: this much was already done before this run
            try:
                resumed_us = int(line.split("=", 1)[1])
            except ValueError:
                pass
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                done_us = int(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("progress="):
            finished = line.strip() == "progress=end"
    if finished or total <= 0:
        return None
    done = min(total, done_us / 1_000_000)
    elapsed = (now or time.time()) - started
    done_now = done - resumed_us / 1_000_000
    left = (total - done) * elapsed / done_now if done_now > 1 else None
    return {"surah": int(info["surah"]), "percent": round(100 * done / total, 1), "seconds_left": left, "elapsed": elapsed}


def schedule_summary(path: Path, now: datetime | None = None) -> dict:
    videos = [v for v in read_json(path, {}).get("videos", []) if isinstance(v, dict) and v.get("publish_at")]
    now_text = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    future = sorted((v for v in videos if str(v["publish_at"]) > now_text), key=lambda v: str(v["publish_at"]))
    return {"published": len(videos) - len(future), "scheduled": len(future),
            "next": [(int(v["surah"]), str(v["publish_at"])[:10]) for v in future[:3]],
            "last": (int(future[-1]["surah"]), str(future[-1]["publish_at"])[:10]) if future else None}


def login_summary(token: Path, testing_mode: bool, now: datetime | None = None) -> str:
    if not token.exists():
        return "LOGIN REQUIRED (token.json is missing): run youtube_upload.py --auth-only"
    if not testing_mode:
        return "ok (app in production: no weekly expiry)"
    meta = read_json(token.with_name("token_meta.json"), {})
    try:
        expires = datetime.fromisoformat(meta["login_at"]) + timedelta(days=LOGIN_VALID_DAYS)
    except (KeyError, ValueError, TypeError):
        return "ok (login date unknown)"
    left = (expires - (now or datetime.now().astimezone())).total_seconds()
    if left <= 0:
        return f"EXPIRED on {expires:%a %d %b %H:%M}: run youtube_upload.py --auth-only"
    return f"ok, expires {expires:%a %d %b %H:%M} (in {left / 86400:.1f} days)"


def numbers_text(numbers: list[int], limit: int = 12) -> str:
    if not numbers:
        return "-"
    shown = ", ".join(str(n) for n in numbers[:limit])
    return shown + (f" … (+{len(numbers) - limit})" if len(numbers) > limit else "")


def report(settings: dict | None = None) -> str:
    settings = load_settings() if settings is None else settings
    p = paths(settings)
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    lines = ["Services:   " + "   ".join(f"{name}: {state}" for name, state in service_states().items())]
    for label, key in (("Generation", "todo"), ("Upload", "yt_todo")):
        q = queue_summary(p[key])
        lines.append(f"{label + ':':<11} now {numbers_text(q['STARTED'])} | next {numbers_text(q['TODO'])} | waiting {len(q['WAITING'])} | "
                     f"done {len(q['DONE'])} | ERROR {numbers_text(q['ERROR'])} | ignored {numbers_text(q['IGNORE'])}")
    progress = render_progress(p["surah_dir"])
    if progress:
        left = f", about {duration_text(progress['seconds_left'])} left" if progress["seconds_left"] is not None else ""
        lines.append(f"Render:     surah {progress['surah']}: {progress['percent']}%{left} (running {duration_text(progress['elapsed'])})")
    else:
        lines.append("Render:     nothing rendering")
    s = schedule_summary(p["schedule"])
    upcoming = ", ".join(f"{n} on {d}" for n, d in s["next"]) or "-"
    lines.append(f"YouTube:    {s['published']} published, {s['scheduled']} scheduled | next: {upcoming}"
                 + (f" | last: {s['last'][0]} on {s['last'][1]}" if s["last"] else ""))
    lines.append("Login:      " + login_summary(p["token"], bool(youtube.get("oauth_testing_mode", True))))
    try:
        free = shutil.disk_usage(p["exported"]).free / 1024**3
        lines.append(f"Disk:       {free:.0f} GB free (renders need {float(settings.get('min_free_gb', 5)):.0f} GB)")
    except OSError:
        pass
    log = read_json(p["log"], {})
    for section in ("video_generation", "youtube_upload"):
        error = (log.get(section) or {}).get("last_error") if isinstance(log.get(section), dict) else None
        if isinstance(error, dict):
            text = str(error.get("message") or error.get("api_message") or error.get("error") or error.get("event"))
            lines.append(f"Last error: [{section}] {error.get('ts', '')} {text[:160]}")
    return "\n".join(lines)


def history_events(log_file: Path, day: str) -> list[dict]:
    path = log_file.with_name("Logs") / f"events-{day[:7]}.jsonl"
    events = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if f'"ts": "{day}' in line:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return events


def daily_summary(settings: dict | None = None, now: datetime | None = None) -> tuple[str, str]:
    """(title, message) describing today: what was made, uploaded, and what is waiting on you."""
    settings = load_settings() if settings is None else settings
    p = paths(settings)
    now = now or datetime.now()
    events = history_events(p["log"], now.strftime("%Y-%m-%d"))
    made = todo_file.in_order({int(e["surah"]) for e in events if e.get("event") == "PROCESS_DONE" and "surah" in e})
    uploaded = todo_file.in_order({int(e["surah"]) for e in events if e.get("event") == "UPLOAD_OK" and "surah" in e})
    generation, upload = queue_summary(p["todo"]), queue_summary(p["yt_todo"])
    s = schedule_summary(p["schedule"])
    lines = [f"Made: {numbers_text(made)}", f"Uploaded: {numbers_text(uploaded)}",
             f"Left to make: {len(generation['WAITING']) + len(generation['TODO']) + len(generation['STARTED'])}",
             f"Scheduled on YouTube: {s['scheduled']}" + (f" (last on {s['last'][1]})" if s["last"] else "")]
    needs_you = []
    if generation["ERROR"]:
        needs_you.append(f"generation ERROR: {numbers_text(generation['ERROR'])}")
    if upload["ERROR"]:
        needs_you.append(f"upload ERROR: {numbers_text(upload['ERROR'])}")
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    login = login_summary(p["token"], bool(youtube.get("oauth_testing_mode", True)), now.astimezone())
    if not login.startswith("ok") or "in 0." in login or "in 1." in login:
        needs_you.append("login: " + login)
    lines.append("Needs you: " + ("; ".join(needs_you) if needs_you else "nothing"))
    return f"Surah videos: daily summary {now:%d %b}", "\n".join(lines)


class SleepDetector:
    """Notices that the laptop was suspended: the wall clock jumped ahead of the monotonic clock
    (which does not count time asleep). Call tick() regularly; it returns (slept_from, slept_until) or None."""

    def __init__(self, threshold_seconds: float = 120.0):
        self.threshold = threshold_seconds
        self.wall, self.mono = time.time(), time.monotonic()

    def tick(self, wall: float | None = None, mono: float | None = None) -> tuple[datetime, datetime] | None:
        wall = time.time() if wall is None else wall
        mono = time.monotonic() if mono is None else mono
        asleep = (wall - self.wall) - (mono - self.mono)
        awake_before_sleep = self.wall
        self.wall, self.mono = wall, mono
        if asleep >= self.threshold:
            return datetime.fromtimestamp(awake_before_sleep), datetime.fromtimestamp(wall)
        return None


if __name__ == "__main__":
    print(report())
