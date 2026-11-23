"""Safe read/modify/write of the queue.

Every change re-reads the file under an exclusive lock and touches only the
requested surah, so concurrent writers (uploader, video pipeline, your editor
saves) don't overwrite each other's edits with a stale in-memory copy.

One file or two
---------------
With `"queue_file"` in settings.json there is ONE file, Queue.md, where each surah moves through
one line of stages:

    WAITING -> TODO -> RENDERING -> READY -> UPLOADING -> DONE        (plus ERROR and IGNORE)

Inside, every surah still has two statuses, one for its video and one for its upload, each
STARTED / TODO / WAITING / DONE / ERROR / IGNORE. The rest of the code keeps asking for "the
generation queue" (the `todo_file` path) or "the upload queue" (the `youtube.todo_file` path) and
gets that side of Queue.md; those two paths are only names then, no such files exist.
The section a line is in says what happens next; a line whose two statuses are not the usual
ones for its section carries them in brackets, e.g. `- [!] 7 - name (video DONE, upload ERROR)`.
Moving a line to another section by hand sets the usual statuses of that section.

Without `"queue_file"` the two files Todo.md and YT_Todo.md are used as before.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SECTION_ORDER = ("STARTED", "TODO", "WAITING", "DONE", "ERROR", "IGNORE")
SYMBOLS = {"STARTED": "~", "TODO": " ", "WAITING": "?", "DONE": "x", "ERROR": "!", "IGNORE": "-"}
SYMBOL_STATUSES = {symbol: status for status, symbol in SYMBOLS.items()}
ENTRY_RE = re.compile(r"^\s*-\s*\[(?P<symbol>[ x?!~\-])\]\s*(?P<number>\d+)\s*-\s*(?P<name>.+?)\s*$")

# ---- the single Queue.md ----
STAGES = ("RENDERING", "UPLOADING", "TODO", "READY", "WAITING", "DONE", "ERROR", "IGNORE")
STAGE_SYMBOLS = {"RENDERING": "~", "UPLOADING": "^", "TODO": " ", "READY": ">", "WAITING": "?", "DONE": "x", "ERROR": "!", "IGNORE": "-"}
SYMBOL_STAGES = {symbol: stage for stage, symbol in STAGE_SYMBOLS.items()}
# the usual (video status, upload status) of each stage
USUAL = {"WAITING": ("WAITING", "WAITING"), "TODO": ("TODO", "WAITING"), "RENDERING": ("STARTED", "WAITING"), "READY": ("DONE", "TODO"),
         "UPLOADING": ("DONE", "STARTED"), "DONE": ("DONE", "DONE"), "ERROR": ("ERROR", "WAITING"), "IGNORE": ("IGNORE", "IGNORE")}
HEADER_STAGES = {**{stage: stage for stage in STAGES}, "STARTED": "RENDERING"}
QUEUE_TITLE = "# Queue"
QUEUE_ENTRY_RE = re.compile(r"^\s*-\s*\[(?P<symbol>[ x?!~\-^>])\]\s*(?P<number>\d+)\s*-\s*(?P<name>.+?)\s*$")
PAIR_RE = re.compile(r"\s*\(video (?P<video>[A-Z]+|-), upload (?P<upload>[A-Z]+|-)\)$")
Pair = tuple  # (video status or None, upload status or None)


def stage_of(video: str | None, upload: str | None) -> str:
    """The section a surah is listed under."""
    for stage, usual in USUAL.items():
        if usual == (video, upload):
            return stage
    if video == "STARTED":
        return "RENDERING"
    if upload == "STARTED":
        return "UPLOADING"
    if "ERROR" in (video, upload):
        return "ERROR"
    if video == "TODO":
        return "TODO"
    if upload == "TODO":
        return "READY"
    if upload == "DONE":
        return "DONE"
    if "IGNORE" in (video, upload):
        return "IGNORE"
    return "WAITING"


def parse_queue(text: str) -> dict[int, tuple[str, str | None, str | None]]:
    """Queue.md text -> {number: (name, video status, upload status)}."""
    entries: dict[int, tuple[str, str | None, str | None]] = {}
    section: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = HEADER_STAGES.get(line[3:].strip().upper())
            continue
        match = QUEUE_ENTRY_RE.match(line)
        if not match:
            continue
        stage = section or SYMBOL_STAGES[match.group("symbol")]
        name = match.group("name")
        pair = USUAL[stage]
        noted = PAIR_RE.search(name)
        if noted:
            name = name[:noted.start()].rstrip()
            video, upload = (None if value == "-" else value for value in (noted.group("video"), noted.group("upload")))
            if (video is None or video in SYMBOLS) and (upload is None or upload in SYMBOLS) and stage_of(video, upload) == stage:
                pair = (video, upload)   # still in the section the note belongs to; a line moved elsewhere takes that section's usual statuses
        entries[int(match.group("number"))] = (name, pair[0], pair[1])
    return entries


def format_queue(entries: dict[int, tuple[str, str | None, str | None]]) -> str:
    lines = [QUEUE_TITLE, ""]
    for stage in STAGES:
        lines.append(f"## {stage}")
        for number, (name, video, upload) in sorted(entries.items(), key=lambda item: order_key(item[0])):
            if (video is None and upload is None) or stage_of(video, upload) != stage:
                continue
            note = "" if USUAL[stage] == (video, upload) else f" (video {video or '-'}, upload {upload or '-'})"
            lines.append(f"- [{STAGE_SYMBOLS[stage]}] {number} - {name}{note}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_settings() -> dict:
    try:
        import settings_path
        data = json.loads(settings_path.settings_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


QUEUE_FILE: Path | None = None
SIDES: dict[Path, int] = {}   # the two "queue paths" the rest of the code uses -> 0 (video side) or 1 (upload side)
_lock_depth = 0


def configure(queue_file: Path | None, video_path: Path | None = None, upload_path: Path | None = None) -> None:
    """Use one Queue.md (or None: the two separate files)."""
    global QUEUE_FILE, SIDES
    QUEUE_FILE = Path(queue_file) if queue_file else None
    SIDES = {Path(video_path): 0, Path(upload_path): 1} if QUEUE_FILE and video_path and upload_path else {}


def _configure_from_settings() -> None:
    settings = _load_settings()
    queue = str(settings.get("queue_file") or "").strip()
    if not queue:
        return
    youtube = settings.get("youtube") if isinstance(settings.get("youtube"), dict) else {}
    surah_dir = Path(str(settings.get("surah_dir") or Path(queue).parent))
    configure(Path(queue), Path(str(settings.get("todo_file") or surah_dir / "Todo.md")), Path(str(youtube.get("todo_file") or surah_dir / "YT_Todo.md")))


def storage_path(todo_file: Path) -> Path:
    """The file that really holds this queue (watch this one for changes)."""
    return QUEUE_FILE if QUEUE_FILE is not None and Path(todo_file) in SIDES else Path(todo_file)


def display_name(todo_file: Path) -> str:
    return storage_path(todo_file).name


def _read_two_file(todo_file: Path) -> dict[int, tuple[str, str]]:
    entries: dict[int, tuple[str, str]] = {}
    if todo_file.exists():
        for line in todo_file.read_text(encoding="utf-8", errors="replace").splitlines():
            match = ENTRY_RE.match(line)
            if match:
                entries[int(match.group("number"))] = (match.group("name"), SYMBOL_STATUSES[match.group("symbol")])
    return entries


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def migrate_if_needed() -> bool:
    """First run with `queue_file`: build Queue.md from Todo.md + YT_Todo.md, which are then kept as *.migrated."""
    if QUEUE_FILE is None or QUEUE_FILE.exists():
        return False
    old = {side: path for path, side in SIDES.items() if path.exists()}
    if not old:
        return False
    with locked(QUEUE_FILE):
        if QUEUE_FILE.exists():
            return False
        video, upload = (_read_two_file(old[side]) if side in old else {} for side in (0, 1))
        merged = {number: ((video.get(number) or upload.get(number))[0], video.get(number, (None, None))[1], upload.get(number, (None, None))[1])
                  for number in set(video) | set(upload)}
        QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _write_text(QUEUE_FILE, format_queue(merged))
        for path in old.values():
            os.replace(path, path.with_name(path.name + ".migrated"))
    return True


def read_queue() -> dict[int, tuple[str, str | None, str | None]]:
    assert QUEUE_FILE is not None
    migrate_if_needed()
    if not QUEUE_FILE.exists():
        return {}
    return parse_queue(QUEUE_FILE.read_text(encoding="utf-8", errors="replace"))


@contextmanager
def locked(todo_file: Path) -> Iterator[None]:
    global _lock_depth
    todo_file = storage_path(todo_file)
    if todo_file == QUEUE_FILE and _lock_depth:   # already held by this process (e.g. the first-run migration inside a change)
        yield
        return
    lock_path = todo_file.with_name(todo_file.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if todo_file == QUEUE_FILE:
            _lock_depth += 1
        try:
            yield
        finally:
            if todo_file == QUEUE_FILE:
                _lock_depth -= 1
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def read_entries(todo_file: Path) -> tuple[str, dict[int, tuple[str, str]]]:
    """Return (title line, {number: (name, status)})."""
    title = "# TODO"
    entries: dict[int, tuple[str, str]] = {}
    side = SIDES.get(Path(todo_file)) if QUEUE_FILE is not None else None
    if side is not None:   # one side of Queue.md
        return QUEUE_TITLE, {number: (entry[0], entry[1 + side]) for number, entry in read_queue().items() if entry[1 + side] is not None}
    if not todo_file.exists():
        return title, entries
    for line in todo_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# "):
            title = line
            continue
        match = ENTRY_RE.match(line)
        if match:
            entries[int(match.group("number"))] = (match.group("name"), SYMBOL_STATUSES[match.group("symbol")])
    return title, entries


def write_entries(todo_file: Path, title: str, entries: dict[int, tuple[str, str]]) -> None:
    side = SIDES.get(Path(todo_file)) if QUEUE_FILE is not None else None
    if side is not None:   # replace this side of Queue.md, keep the other side
        with locked(todo_file):
            queue = read_queue()
            merged: dict[int, tuple[str, str | None, str | None]] = {}
            for number in set(queue) | set(entries):
                name = entries[number][0] if number in entries else queue[number][0]
                statuses = [queue[number][1], queue[number][2]] if number in queue else [None, None]
                statuses[side] = entries[number][1] if number in entries else None
                if statuses != [None, None]:
                    merged[number] = (name, statuses[0], statuses[1])
            QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _write_text(QUEUE_FILE, format_queue(merged))
        return
    lines = [title, ""]
    for status in SECTION_ORDER:
        lines.append(f"## {status}")
        lines.extend(f"- [{SYMBOLS[status]}] {number} - {name}" for number, (name, entry_status) in sorted(entries.items(), reverse=True) if entry_status == status)
        lines.append("")
    tmp = todo_file.with_name(f".{todo_file.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        os.replace(tmp, todo_file)
    finally:
        tmp.unlink(missing_ok=True)


def read_statuses(todo_file: Path) -> dict[int, str]:
    return {number: status for number, (_name, status) in read_entries(todo_file)[1].items()}


def set_status(todo_file: Path, number: int, status: str, name: str | None = None, only_from: set[str] | None = None) -> str | None:
    """Set one surah's status. Returns the previous status if it changed, else None.

    only_from: change it only when its current status is in this set (None = any).
    A surah missing from the file counts as status None and is added when `name` is given.
    """
    if status not in SYMBOLS:
        raise ValueError(f"Unknown status: {status}")
    with locked(todo_file):
        title, entries = read_entries(todo_file)
        current = entries.get(number)
        current_status = current[1] if current else None
        if only_from is not None and current_status not in only_from:
            return None
        if current_status == status:
            return None
        entry_name = current[0] if current else name
        if entry_name is None:
            return None
        entries[number] = (entry_name, status)
        write_entries(todo_file, title, entries)
        return current_status or "MISSING"


SURAH_LINE_RE = re.compile(r"^\s*(\d+)\s*-\s*(.+?)\s*$")


def read_surah_names(surah_file: Path) -> dict[int, str]:
    """Surhs.txt: one "<number> - <name>" per line."""
    surahs: dict[int, str] = {}
    if not surah_file.exists():
        return surahs
    for line in surah_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = SURAH_LINE_RE.match(line)
        if match:
            surahs[int(match.group(1))] = match.group(2)
    return surahs


def create_if_missing(todo_file: Path, title: str, surahs: dict[int, str], skip: set[int] | frozenset[int] = frozenset()) -> bool:
    """Create the queue (all WAITING, skipped surahs IGNORE) if it is missing or empty. Returns True if created."""
    todo_file.parent.mkdir(parents=True, exist_ok=True)
    with locked(todo_file):
        if Path(todo_file) in SIDES and QUEUE_FILE is not None:
            if read_entries(todo_file)[1]:
                return False
        elif todo_file.exists() and todo_file.stat().st_size > 0:
            return False
        write_entries(todo_file, title, {n: (name, "IGNORE" if n in skip else "WAITING") for n, name in surahs.items()})
        return True


def numbers(todo_file: Path, statuses: set[str] | frozenset[str] = frozenset({"TODO"})) -> set[int]:
    return {number for number, status in read_statuses(todo_file).items() if status in statuses}


def count(todo_file: Path, statuses: set[str] | frozenset[str] = frozenset({"TODO"})) -> int:
    return len(numbers(todo_file, statuses))


def apply_changes(todo_file: Path, wanted: dict[int, str], names: dict[int, str], before: dict[int, str]) -> dict[int, tuple[str | None, str]]:
    """Write only the surahs whose status differs from `before` (what the file said when we read it),
    and only if the file still says that: an edit made meanwhile (by you or the other service) wins.
    Statuses that are not queue sections (e.g. NO_MP3) are not written. Returns {number: (old, new)}."""
    changed: dict[int, tuple[str | None, str]] = {}
    for number, status in sorted(wanted.items(), reverse=True):
        old = before.get(number)
        if status == old or status not in SYMBOLS:
            continue
        if set_status(todo_file, number, status, name=names.get(number), only_from={old}):
            changed[number] = (old, status)
    return changed


def _first_surahs() -> tuple[int, ...]:
    try:
        return tuple(int(n) for n in _load_settings().get("first_surahs", [1]))
    except (ValueError, TypeError):
        return (1,)


# Order of work (generation, upload, publishing): these first (Al-Fatiha), then 114, 113, ... down.
FIRST_SURAHS = _first_surahs()


def order_key(number: int) -> tuple[int, int]:
    return (0, FIRST_SURAHS.index(number)) if number in FIRST_SURAHS else (1, -number)


def in_order(numbers) -> list[int]:
    """Surah numbers in work order: 1, then 114 -> 2."""
    return sorted(numbers, key=order_key)


def comes_before(a: int, b: int) -> bool:
    return order_key(a) < order_key(b)


_configure_from_settings()
