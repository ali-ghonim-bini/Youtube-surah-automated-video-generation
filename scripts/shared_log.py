from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from pathlib import Path


SECTIONS = ("trigger", "video_generation", "image_generation", "youtube_upload")
# Logs.json is a summary (rewritten on every event), so it stays small: the newest events per section,
# long texts shortened. The complete history is appended to Logs/events-YYYY-MM.jsonl (one JSON per line).
SUMMARY_EVENTS = 200
SUMMARY_TEXT_LIMIT = 600
HISTORY_KEEP_MONTHS = 12
DONE_STATUSES = {"DONE"}
ACTIVE_STATUSES = {"RUNNING", "UPLOADING", "WATCHING"}


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def json_safe(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    return value


def default_log() -> dict[str, object]:
    return {
        "timestamp": None,
        "status": "IDLE",
        "done": False,
        "has_error": False,
        "trigger": default_section(),
        "video_generation": default_section(),
        "image_generation": default_section(),
        "youtube_upload": default_section(),
    }


def default_section() -> dict[str, object]:
    return {
        "status": "IDLE",
        "done": False,
        "has_error": False,
        "current_step": None,
        "last_error": None,
        "events": [],
    }


def read_log(log_file: Path) -> dict[str, object]:
    if not log_file.exists() or log_file.stat().st_size == 0:
        return default_log()
    try:
        loaded = json.loads(log_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default_log()
    if not isinstance(loaded, dict):
        return default_log()
    data = default_log()
    for key, value in loaded.items():
        if key in SECTIONS:
            continue
        data[key] = value
    for section in SECTIONS:
        existing = loaded.get(section)
        if isinstance(existing, dict):
            merged = data[section]
            assert isinstance(merged, dict)
            merged.update(existing)
            if not isinstance(merged.get("events"), list):
                merged["events"] = []
    old_events = loaded.get("events")
    if isinstance(old_events, list):
        video = data["video_generation"]
        assert isinstance(video, dict)
        video["events"] = old_events + list(video.get("events", []))
    return data


def update_root_status(data: dict[str, object]) -> None:
    statuses = []
    for section in SECTIONS:
        value = data.get(section)
        if isinstance(value, dict):
            statuses.append(str(value.get("status", "IDLE")))
    if any(status == "ERROR" for status in statuses):
        data["status"] = "ERROR"
    elif any(status in {"RUNNING", "UPLOADING"} for status in statuses):
        data["status"] = "RUNNING"
    elif statuses and all(status in {"IDLE", "DONE", "WATCHING"} for status in statuses):
        data["status"] = "DONE" if any(status == "DONE" for status in statuses) else "WATCHING"
    else:
        data["status"] = "IDLE"
    data["done"] = data["status"] == "DONE"
    data["has_error"] = any(status == "ERROR" for status in statuses)


def update_section_summary(section_data: dict[str, object], record: dict[str, object], status: str | None, level: str) -> None:
    current_status = str(section_data.get("status", "IDLE"))
    if status is not None:
        current_status = status
    section_data["done"] = current_status in DONE_STATUSES
    section_data["current_step"] = record.get("event")
    if current_status in ACTIVE_STATUSES:
        section_data["done"] = False
        section_data["has_error"] = False
        section_data["last_error"] = None
    if level == "ERROR" or current_status == "ERROR":
        section_data["done"] = False
        section_data["has_error"] = True
        section_data["last_error"] = record
    elif current_status in DONE_STATUSES:
        section_data["has_error"] = False
        section_data["last_error"] = None


def ordered_log(data: dict[str, object]) -> dict[str, object]:
    ordered: dict[str, object] = {}
    for key in ("timestamp", "status", "done", "has_error"):
        if key in data:
            ordered[key] = data[key]

    sections = []
    for section in SECTIONS:
        value = data.get(section)
        if isinstance(value, dict):
            timestamp = str(value.get("timestamp") or "")
            sections.append((timestamp, section, value))
    for _timestamp, section, value in sorted(sections, key=lambda item: item[0], reverse=True):
        ordered[section] = value

    for key, value in data.items():
        if key not in ordered and key not in SECTIONS:
            ordered[key] = value
    return ordered


def shorten(value: object) -> object:
    if isinstance(value, str) and len(value) > SUMMARY_TEXT_LIMIT:
        return value[:SUMMARY_TEXT_LIMIT] + f"... (+{len(value) - SUMMARY_TEXT_LIMIT} chars, full text in Logs/events-*.jsonl)"
    if isinstance(value, list):
        return [shorten(item) for item in value[:50]]
    if isinstance(value, dict):
        return {key: shorten(item) for key, item in value.items()}
    return value


def append_history(log_file: Path, section: str, record: dict[str, object]) -> None:
    """Complete, append-only history, one file per month. Never raises."""
    try:
        folder = log_file.with_name("Logs")
        folder.mkdir(exist_ok=True)
        month = str(record["ts"])[:7]
        path = folder / f"events-{month}.jsonl"
        is_new = not path.exists()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"section": section, **record}, ensure_ascii=False) + "\n")
        if is_new:
            for old in sorted(folder.glob("events-*.jsonl"))[:-HISTORY_KEEP_MONTHS]:
                old.unlink(missing_ok=True)
    except OSError:
        pass


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def append_event(log_file: Path, section: str, event: str, level: str = "INFO", run_id: str = "", status: str | None = None, **fields: object) -> None:
    if section not in SECTIONS:
        raise RuntimeError(f"Unknown log section: {section}")
    timestamp = now()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = log_file.with_name(log_file.name + ".lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            data = read_log(log_file)
            data["timestamp"] = timestamp
            section_data = data[section]
            assert isinstance(section_data, dict)
            if status is not None:
                section_data["status"] = status
            section_data.setdefault("started_at", timestamp)
            section_data["timestamp"] = timestamp
            record = {"ts": timestamp, "event": event, "level": level, "run_id": run_id or section}
            record.update({key: json_safe(value) for key, value in fields.items()})
            append_history(log_file, section, record)
            record = shorten(record)
            update_section_summary(section_data, record, status, level)
            events = section_data.setdefault("events", [])
            if not isinstance(events, list):
                events = []
            events.insert(0, record)
            section_data["events"] = events[:SUMMARY_EVENTS]
            update_root_status(data)
            write_atomic(log_file, json.dumps(ordered_log(data), ensure_ascii=False, indent=2) + "\n")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
