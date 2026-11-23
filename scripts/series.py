#!/usr/bin/env python3
"""One series at a time.

A series is one set of videos: a reciter, a look, a YouTube playlist, and its own queue, audio,
videos and log. Everything about it is in one settings.json.

The two services work on exactly ONE series: the active one. scripts/active_series.json names its
settings file; when that file does not exist, the active series is scripts/settings.json.
A finished series is never touched again once another one is active, also after a restart of
the computer, because the services only ever read the active series' settings.

    python3 scripts/series.py new NAME      create series/NAME (settings, folders); starts nothing
    python3 scripts/series.py use NAME      make series/NAME the active series and restart the services
    python3 scripts/series.py use main      go back to scripts/settings.json
    python3 scripts/series.py which         show the active series

`new` writes nothing outside the new folder and never overwrites an existing one.
`use` refuses while the current series is in the middle of a render or an upload.
A single command can be pointed at another series with the SURAH_SETTINGS environment variable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SETTINGS = SCRIPT_DIR / "settings.json"
ACTIVE_FILE = SCRIPT_DIR / "active_series.json"   # {"settings": "/path/to/series/NAME/settings.json"}; absent = scripts/settings.json
SERVICES = ("surah-todo-watcher.service", "surah-yt-watcher.service")
EDIT_FIRST = ("audio.recitation_id", "audio.recitation_page_url", "audio.expected_sheikh_name", "youtube.playlist_id", "youtube.playlist_url",
              "youtube.title_format", "youtube.description_format", "youtube.tags", "video.background_image")


def active_settings() -> Path:
    """The settings file of the active series."""
    try:
        chosen = Path(str(json.loads(ACTIVE_FILE.read_text(encoding="utf-8"))["settings"]))
        if chosen.is_file():
            return chosen
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return DEFAULT_SETTINGS


def settings_file() -> Path:
    """The settings every script uses: SURAH_SETTINGS when set (tests, one-off commands), else the active series."""
    override = os.environ.get("SURAH_SETTINGS")
    return Path(override) if override else active_settings()


def temp_dir() -> Path:
    """Where temporary per-surah files go. Each series has its own; the main settings keep the plain name."""
    base = Path(tempfile.gettempdir()) / "surah_automation"
    current = settings_file().resolve()
    if current == DEFAULT_SETTINGS.resolve():
        return base
    return base.with_name(f"surah_automation-{hashlib.sha1(str(current).encode()).hexdigest()[:10]}")


def series_settings(base: dict, folder: Path) -> dict:
    """The base settings with every working path moved into `folder` and the playlist cleared."""
    s = json.loads(json.dumps(base))
    data = folder / "data"
    s.update({
        "surah_dir": str(data), "sound_dir": str(data / "Sound"), "exported_dir": str(data / "Exported_Vids"),
        "live_exported_dir": str(data / "Exported_Vids" / "Live"), "surah_file": str(folder / "Surhs.txt"),
        "queue_file": str(data / "Queue.md"), "todo_file": str(data / "Todo.md"), "log_file": str(data / "Logs.json"),
    })
    s.setdefault("thumbnail", {})["output_dir"] = str(data / "Thumbnails")
    captions = s.setdefault("captions", {})
    captions["output_dir"] = str(data / "Text" / "Translation")
    captions["languages_file"] = str(folder / "Caption_Languages.json")
    youtube = s.setdefault("youtube", {})
    youtube["todo_file"] = str(data / "YT_Todo.md")
    youtube["schedule_file"] = str(data / "YT_Schedule.json")
    youtube["publish_after_schedule_file"] = ""   # set to another series' YT_Schedule.json to start the day after it ends
    youtube["playlist_id"] = ""      # a new series needs its own playlist: fill these in
    youtube["playlist_url"] = ""
    return s


def create(name: str, folder: Path | None = None, base_file: Path | None = None) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name) or name == "main":
        raise SystemExit("The name must be lower-case letters, digits and dashes, and not \"main\".")
    folder = (folder or SCRIPT_DIR.parent / "series" / name).resolve()
    if folder.exists():
        raise SystemExit(f"{folder} already exists; nothing was changed.")
    base_file = base_file or settings_file()
    base = json.loads(base_file.read_text(encoding="utf-8"))
    moved = series_settings(base, folder)
    settings = {"series_name": name, **{key: value for key, value in moved.items() if key != "series_name"}}   # shown in front of every notification
    for sub in ("Exported_Vids/Live", "Sound", "Thumbnails"):
        (folder / "data" / sub).mkdir(parents=True)
    for key, target in (("surah_file", folder / "Surhs.txt"), ("languages_file", folder / "Caption_Languages.json")):
        source = Path(str(base.get(key) or (base.get("captions") or {}).get(key) or ""))
        if source.is_file():
            shutil.copy(source, target)
    (folder / "settings.json").write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return folder


def busy(settings: Path) -> list[str]:
    """What the series with these settings is in the middle of (empty when it is idle)."""
    saved = os.environ.get("SURAH_SETTINGS")
    os.environ["SURAH_SETTINGS"] = str(settings)
    try:
        import importlib
        import todo_file
        importlib.reload(todo_file)   # reads the queue paths of that series
        data = json.loads(settings.read_text(encoding="utf-8"))
        video = Path(str(data.get("todo_file") or ""))
        upload = Path(str((data.get("youtube") or {}).get("todo_file") or ""))
        doing = []
        if todo_file.numbers(video, {"STARTED"}):
            doing.append("rendering surah " + ", ".join(map(str, sorted(todo_file.numbers(video, {"STARTED"})))))
        if todo_file.numbers(upload, {"STARTED"}):
            doing.append("uploading surah " + ", ".join(map(str, sorted(todo_file.numbers(upload, {"STARTED"})))))
        return doing
    except (OSError, ValueError):
        return []
    finally:
        if saved is None:
            os.environ.pop("SURAH_SETTINGS", None)
        else:
            os.environ["SURAH_SETTINGS"] = saved


def use(name: str, restart: bool = True, force: bool = False, root: Path | None = None) -> Path:
    """Make a series the active one. The services are restarted so they start working on it at once."""
    root = root or SCRIPT_DIR.parent
    target = DEFAULT_SETTINGS if name == "main" else root / "series" / name / "settings.json"
    if not target.is_file():
        raise SystemExit(f"No such series: {target} does not exist. Create it with: series.py new {name}")
    json.loads(target.read_text(encoding="utf-8"))   # a broken settings file must not become the active one
    current = active_settings()
    if current.resolve() == target.resolve():
        print(f"{name} is already the active series.")
        return target
    doing = busy(current)
    if doing and not force:
        raise SystemExit(f"The current series is {' and '.join(doing)}. Wait until it is idle, or add --force to stop it and switch anyway.")
    if name == "main":
        ACTIVE_FILE.unlink(missing_ok=True)
    else:
        temp = ACTIVE_FILE.with_name(ACTIVE_FILE.name + ".tmp")
        temp.write_text(json.dumps({"settings": str(target.resolve())}) + "\n", encoding="utf-8")
        os.replace(temp, ACTIVE_FILE)
    print(f"Active series: {name} ({target})")
    if restart and shutil.which("systemctl"):
        result = subprocess.run(["systemctl", "--user", "restart", *SERVICES], capture_output=True, text=True)
        print("Services restarted; they now work on this series only." if result.returncode == 0
              else f"Could not restart the services ({result.stderr.strip()[:200]}). Restart them yourself: systemctl --user restart {' '.join(SERVICES)}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a series, or choose which one the services work on.")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="create series/NAME with its settings.json and folders; starts nothing")
    new.add_argument("name")
    new.add_argument("--folder", type=Path, help="where to create it (default: series/NAME)")
    new.add_argument("--from", dest="base", type=Path, help="settings.json to copy (default: the active one)")
    switch = commands.add_parser("use", help="make series/NAME (or \"main\") the active series and restart the services")
    switch.add_argument("name")
    switch.add_argument("--no-restart", action="store_true", help="only record the choice; the services pick it up at their next start")
    switch.add_argument("--force", action="store_true", help="switch even while the current series is rendering or uploading")
    commands.add_parser("which", help="show the active series")
    args = parser.parse_args()
    if args.command == "which":
        print(active_settings())
        return 0
    if args.command == "use":
        use(args.name, restart=not args.no_restart, force=args.force)
        return 0
    folder = create(args.name, args.folder, args.base)
    print(f"Created {folder}. Nothing was started.")
    print("Edit these in its settings.json first:")
    for key in EDIT_FIRST:
        print(f"  {key}")
    print("Look at it without starting anything:")
    print(f"  SURAH_SETTINGS={folder / 'settings.json'} python3 {SCRIPT_DIR / 'status.py'}")
    print(f"When it is ready, start it (the services then stop working on the current series for good):")
    print(f"  python3 {SCRIPT_DIR / 'series.py'} use {args.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
