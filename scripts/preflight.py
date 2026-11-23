"""Checks before an upload spends quota: metadata within YouTube's limits, files present and sane."""
from __future__ import annotations

from pathlib import Path

TITLE_MAX_CHARS = 100
DESCRIPTION_MAX_BYTES = 5000
TAGS_MAX_CHARS = 500
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
THUMBNAIL_TYPES = {".png", ".jpg", ".jpeg"}


def tags_length(tags: list[str]) -> int:
    """YouTube counts commas between tags, and quotes around tags that contain spaces."""
    return sum(len(tag) + (2 if " " in tag else 0) for tag in tags) + max(0, len(tags) - 1)


def upload_problems(title: str, description: str, tags: list[str], video_file: Path | None, thumbnail_file: Path | None,
                    publish_at: str | None = None, now_iso: str | None = None) -> list[str]:
    problems: list[str] = []
    if not title.strip():
        problems.append("title is empty")
    if len(title) > TITLE_MAX_CHARS:
        problems.append(f"title is {len(title)} characters (YouTube allows {TITLE_MAX_CHARS})")
    if len(description.encode("utf-8")) > DESCRIPTION_MAX_BYTES:
        problems.append(f"description is {len(description.encode('utf-8'))} bytes (YouTube allows {DESCRIPTION_MAX_BYTES})")
    for name, text in (("title", title), ("description", description)):
        if "<" in text or ">" in text:
            problems.append(f"{name} contains < or > (YouTube rejects them)")
    if tags_length(tags) > TAGS_MAX_CHARS:
        problems.append(f"tags are {tags_length(tags)} characters in total (YouTube allows {TAGS_MAX_CHARS})")
    if video_file is None or not video_file.exists() or video_file.stat().st_size == 0:
        problems.append("video file is missing or empty")
    if thumbnail_file is None or not thumbnail_file.exists():
        problems.append("thumbnail is missing")
    else:
        if thumbnail_file.stat().st_size > THUMBNAIL_MAX_BYTES:
            problems.append(f"thumbnail is {thumbnail_file.stat().st_size / 1024 / 1024:.1f} MB (YouTube allows 2 MB)")
        if thumbnail_file.suffix.lower() not in THUMBNAIL_TYPES:
            problems.append(f"thumbnail type {thumbnail_file.suffix} is not PNG/JPG")
    if publish_at and now_iso and publish_at <= now_iso:
        problems.append(f"publish time {publish_at} is in the past")
    return problems
