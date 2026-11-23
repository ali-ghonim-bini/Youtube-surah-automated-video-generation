"""Which surahs exist, which have audio/video, and what the generation queue should say."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports


def read_surahs() -> dict[int, str]:
    surahs: dict[int, str] = {}
    for line in SURAH_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(\d+)\s*-\s*(.*?)\s*$", line)
        if match:
            surahs[int(match.group(1))] = match.group(2)
    if len(surahs) != 114:
        raise RuntimeError(f"Expected 114 surahs in {SURAH_FILE}, found {len(surahs)}")
    return surahs


def read_todo_statuses() -> dict[int, str]:
    return todo_file.read_statuses(TODO_FILE)


def available_mp3s() -> dict[int, Path]:
    mp3s: dict[int, Path] = {}
    if AUDIO_SOURCE == "qul_ayah_by_ayah":
        return {number: SOUND_DIR / "Qul" / f"{number:03d}.mp3" for number in range(1, 115)}
    for path in SOUND_DIR.glob("*.mp3"):
        try:
            mp3s[int(path.stem)] = path
        except ValueError:
            log("MP3_SKIP_BAD_NAME", file=path)
    return mp3s


QUARANTINED: set[int] = set()  # surahs whose video was unreadable this run -> regenerate


def existing_video_for(number: int, arabic_name: str) -> Path | None:
    """A readable video for this surah, or None. Unreadable ones (e.g. a killed render) are
    renamed to *.corrupt-<time>.mp4 so they're kept but no longer count as generated."""
    for path in corrupt_videos(EXPORTED_DIR, LIVE_EXPORTED_DIR, number, arabic_name):
        target = path.with_name(f"{path.stem}.corrupt-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        path.rename(target)
        QUARANTINED.add(number)
        log("VIDEO_CORRUPT", level="ERROR", surah=number, file=path, moved_to=target)
        notify(f"Corrupt video: surah {number}", f"{path.name} could not be read; renamed to {target.name}.\nIt will be generated again.", level="warning", key=f"video-corrupt-{number}")
    return find_existing_video(EXPORTED_DIR, LIVE_EXPORTED_DIR, number, arabic_name)


def build_statuses(surahs: dict[int, str], mp3s: dict[int, Path], old_statuses: dict[int, str]) -> dict[int, str]:
    statuses: dict[int, str] = {}
    use_existing_queue = bool(old_statuses)
    for number in sorted(surahs, reverse=True):
        arabic_name = surahs[number]
        previous = old_statuses.get(number)
        if number in SKIP_SURAH_NUMBERS and previous in {None, "IGNORE"}:
            # skip_surah_numbers only sets the starting status; your own (or the auto-queue's
            # approved) change in the queue wins from then on.
            statuses[number] = "IGNORE"
        elif number in SKIP_SURAH_NUMBERS and previous in {"TODO", "STARTED"}:
            # Moved to TODO on purpose: generate it fresh, even if an old video exists.
            statuses[number] = "TODO"
        elif existing_video_for(number, arabic_name):
            statuses[number] = "DONE"
        elif number in QUARANTINED and previous in {"DONE", "STARTED", "TODO", None}:
            statuses[number] = "TODO"  # its video was corrupt: make it again
        elif previous in {"DONE", "ERROR", "IGNORE"}:
            # Keep DONE even if the mp4 was deleted later (e.g. already uploaded); move to TODO to regenerate.
            statuses[number] = previous
        elif use_existing_queue and previous in {"TODO", "WAITING", "STARTED"}:
            statuses[number] = "TODO" if previous == "STARTED" else previous
        elif number not in mp3s:
            statuses[number] = "NO_MP3"
        else:
            statuses[number] = "WAITING"

    # Enforce the work order (1, then 114 -> 2): later surahs wait until earlier available ones are done.
    if use_existing_queue:
        return statuses
    blocked = False
    for number in todo_file.in_order(mp3s):
        if number in SKIP_SURAH_NUMBERS:
            continue
        if statuses.get(number) in {"DONE", "ERROR", "IGNORE"}:
            if statuses[number] == "ERROR":
                blocked = True
            continue
        if blocked:
            statuses[number] = "WAITING"
        elif statuses.get(number) in {"TODO", "STARTED"}:
            blocked = True
    return statuses




def todo_count(statuses: dict[int, str]) -> int:
    return sum(1 for status in statuses.values() if status == "TODO")


def next_todo(statuses: dict[int, str], mp3s: dict[int, Path]) -> int | None:
    for number in todo_file.in_order(mp3s):
        if statuses.get(number) in {"STARTED", "TODO"}:
            return number
    return None
