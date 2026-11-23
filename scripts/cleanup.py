"""After a confirmed upload: check the video on YouTube, then remove the surah's local files.

Nothing here ever deletes or changes anything on YouTube. A surah's files are removed only when
  - its video and its upload are both DONE in the queue,
  - YouTube says the video is fully processed, and
  - the length YouTube reports matches the local video (when the local video is still there).
What is removed: the video (and leftovers of failed renders), unfinished render pieces, the ayah
audio clips and the combined audio, the thumbnail, the caption/translation text files and the
temporary subtitle/overlay files. Shared files (basmalah audio, fonts, background, queue, logs,
schedule, layout overrides) are never touched.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def iso_duration_seconds(value: str) -> float | None:
    """YouTube's "PT2H47M1S" -> seconds (None if it is not a duration)."""
    match = _ISO_DURATION.match(str(value or ""))
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (float(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def local_duration(video: Path) -> float | None:
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        return float(probe.stdout.strip())
    except ValueError:
        return None


def youtube_verdict(item: dict | None, local_seconds: float | None, tolerance: float = 2.0) -> tuple[str, str]:
    """('ok' | 'wait' | 'problem', why) for one video as YouTube reports it (videos.list item, or None if absent)."""
    if item is None:
        return "problem", "YouTube does not list this video"
    status = item.get("status") or {}
    upload = str(status.get("uploadStatus") or "")
    if upload in {"failed", "rejected", "deleted"}:
        reason = status.get("failureReason") or status.get("rejectionReason") or ""
        return "problem", f"YouTube marks the upload as {upload}" + (f" ({reason})" if reason else "")
    processing = str((item.get("processingDetails") or {}).get("processingStatus") or "")
    if processing == "failed":
        return "problem", "YouTube could not process the video"
    if upload != "processed":
        return "wait", f"YouTube is still processing it (upload status: {upload or 'unknown'})"
    seconds = iso_duration_seconds((item.get("contentDetails") or {}).get("duration", ""))
    if seconds is None or seconds <= 0:
        return "wait", "YouTube has not reported the video's length yet"
    if local_seconds is not None and abs(seconds - local_seconds) > tolerance:
        return "problem", f"length differs: YouTube {seconds:.0f} s, local video {local_seconds:.0f} s"
    return "ok", f"processed, {seconds:.0f} s"


def surah_files(number: int, arabic_name: str, exported_dir: Path, live_dir: Path, sound_dir: Path, thumbnail_dir: Path,
                captions_dir: Path, channel_dir: Path | None = None, temp_dir: Path | None = None) -> list[Path]:
    """Every local file or folder that belongs to this one surah and exists."""
    temp_dir = temp_dir or Path(tempfile.gettempdir()) / "surah_automation"
    wanted: list[Path] = []
    for folder in (exported_dir, live_dir):
        wanted += [folder / name for name in (f"{number}.mp4", f"{number:03d}.mp4", f"{arabic_name}.mp4", f"{arabic_name} .mp4")]
        if folder.is_dir():
            wanted += sorted(folder.glob(f"{number}.*.mp4"))            # 7.rendering.mp4, 7.quality-failed.mp4, 7.old-<time>.mp4 ...
            wanted += sorted(folder.glob(f"{number:03d}.*.mp4"))
    pieces = exported_dir / ".pieces"
    if pieces.is_dir():
        wanted += sorted(pieces.glob(f"{number}-*"))
    wanted += [sound_dir / str(number), sound_dir / "Qul" / f"{number:03d}.mp3", thumbnail_dir / f"{number}.png", captions_dir / str(number)]
    if channel_dir is not None:
        wanted.append(channel_dir / f"{number}.mlt")
    wanted += [temp_dir / name for name in (f"{number}.ass", f"{number}.progress", f"{number}_concat.txt", f"{number}_pango_arabic.tsv")]
    seen: set[Path] = set()
    found = []
    for path in wanted:
        if path not in seen and (path.exists() or path.is_symlink()):
            seen.add(path)
            found.append(path)
    return found


def size_of(path: Path) -> int:
    if path.is_dir() and not path.is_symlink():
        return sum(child.stat().st_size for child in path.rglob("*") if child.is_file() and not child.is_symlink())
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def remove_files(paths: list[Path], roots: list[Path]) -> tuple[list[str], int]:
    """Delete the given files/folders. Refuses anything that is not strictly inside one of `roots`
    (so a wrong setting can never delete a whole folder of yours). Returns (what was removed, bytes freed)."""
    resolved_roots = [root.resolve() for root in roots]
    removed, freed = [], 0
    for path in paths:
        parent = path.parent.resolve()
        if not any(parent == root or root in parent.parents for root in resolved_roots) or path.resolve() in resolved_roots:
            raise RuntimeError(f"Refusing to delete {path}: it is not inside the surah folders")
        size = size_of(path)
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path))
        freed += size
    return removed, freed
