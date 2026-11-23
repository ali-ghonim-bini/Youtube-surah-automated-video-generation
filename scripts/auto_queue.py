"""Keep the video pipeline fed: queue the next surah in the queue's TODO (work order: 1 first, then 114 -> 2).

Ignored surahs (IGNORE in the queue, e.g. 18 via skip_surah_numbers) are never
passed silently. When the queue reaches the surah just before one, a desktop
notification asks:

  1. "Ignored surah N is next - make & upload it?"   Yes / No
       No  -> it stays ignored, the queue continues below it
       Yes -> if no video exists yet: generate it, then upload it
              if a video already exists, ask:
  2. "Surah N is already generated - generate it again?"   Yes / No
       Yes -> the old video is renamed aside (kept), a fresh one is generated and uploaded
       No  -> the existing video is uploaded as it is

No answer yet -> the queue pauses right before that surah and reminds you later.
Answers are stored in Decisions.json (you can also edit it by hand):

  {"ignored_surahs": {"18": "yes"}, "regenerate": {"18": "no"}}
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import todo_file
from notify import icon_args

BLOCKING = {"ERROR"}  # don't feed the pipeline while something is broken

QUESTIONS = {
    "ignored_surahs": ("Ignored surah {number} is next",
                       "سورة {name} ({number}) is set to IGNORE.\nMake and upload it in its turn?",
                       "Yes, make & upload it", "No, skip it"),
    "regenerate": ("Surah {number} is already generated",
                   "سورة {name} ({number}) already has a video:\n{video}\nGenerate it again?",
                   "Yes, generate again", "No, upload the existing video"),
}


def video_candidates(exported_dir: Path, live_dir: Path, number: int, arabic_name: str) -> list[Path]:
    names = [f"{number}.mp4", f"{number:03d}.mp4", f"{arabic_name}.mp4", f"{arabic_name} .mp4"]
    return [folder / name for folder in (exported_dir, live_dir) for name in names]


_VALID_CACHE: dict[tuple[str, int, int], bool] = {}


def video_is_valid(path: Path) -> bool:
    """True only if ffprobe can read a real duration (a killed render leaves a file with no index)."""
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _VALID_CACHE:
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                    capture_output=True, text=True, timeout=60)
            _VALID_CACHE[key] = result.returncode == 0 and float(result.stdout.strip() or 0) > 1
        except (OSError, ValueError, subprocess.TimeoutExpired):
            _VALID_CACHE[key] = False
    return _VALID_CACHE[key]


def find_existing_video(exported_dir: Path, live_dir: Path, number: int, arabic_name: str) -> Path | None:
    for path in video_candidates(exported_dir, live_dir, number, arabic_name):
        if video_is_valid(path):
            return path
    return None


def corrupt_videos(exported_dir: Path, live_dir: Path, number: int, arabic_name: str) -> list[Path]:
    return [path for path in video_candidates(exported_dir, live_dir, number, arabic_name)
            if path.exists() and path.stat().st_size > 0 and not video_is_valid(path)]


def read_decisions(decisions_file: Path, kind: str = "ignored_surahs") -> dict[int, str]:
    try:
        data = json.loads(decisions_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get(kind) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {int(k): str(v).lower() for k, v in raw.items() if str(k).isdigit() and str(v).lower() in {"yes", "no"}}


def approved_ignored(decisions_file: Path) -> set[int]:
    return {number for number, answer in read_decisions(decisions_file).items() if answer == "yes"}


class AutoQueue:
    def __init__(self, todo_path: Path, yt_todo_path: Path, decisions_path: Path, log: Callable[..., None],
                 exported_dir: Path, live_dir: Path, keep_in_todo: int = 1, ask_reminder_hours: float = 6) -> None:
        self.todo_path = todo_path
        self.yt_todo_path = yt_todo_path
        self.decisions_path = decisions_path
        self.log = log
        self.exported_dir = exported_dir
        self.live_dir = live_dir
        self.keep_in_todo = max(1, keep_in_todo)
        self.reminder_seconds = ask_reminder_hours * 3600
        self.asking: subprocess.Popen[str] | None = None
        self.asking_key: tuple[str, int] | None = None
        self.last_asked: dict[tuple[str, int], float] = {}
        self.last_pause: str | None = None

    # ----- decisions -------------------------------------------------------
    def record(self, kind: str, number: int, answer: str) -> None:
        try:
            data = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault(kind, {})[str(number)] = answer
        self.decisions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.log("AUTO_QUEUE_DECISION", surah=number, question=kind, answer=answer)

    def poll_answer(self) -> None:
        if self.asking is None or self.asking.poll() is None:
            return
        answer = (self.asking.stdout.read() if self.asking.stdout else "").strip()
        key = self.asking_key
        self.asking = None
        self.asking_key = None
        if key is None:
            return
        kind, number = key
        if answer in {"yes", "no"}:
            self.record(kind, number, answer)
        else:
            self.log("AUTO_QUEUE_ASK_DISMISSED", surah=number, question=kind, remind_in_hours=self.reminder_seconds / 3600)

    def ask(self, kind: str, number: int, name: str, video: Path | None = None) -> None:
        if self.asking is not None:
            return
        key = (kind, number)
        if time.time() - self.last_asked.get(key, 0) < self.reminder_seconds:
            return
        self.last_asked[key] = time.time()
        if not shutil.which("notify-send"):
            self.log("AUTO_QUEUE_ASK_UNAVAILABLE", level="ERROR", surah=number, question=kind, decisions_file=self.decisions_path)
            return
        title, body, yes_label, no_label = QUESTIONS[kind]
        body = body.format(number=number, name=name, video=video) + f"\n(You can also answer in {self.decisions_path.name})"
        self.asking = subprocess.Popen(
            ["notify-send", "-a", "Surah channel", "-u", "critical", *icon_args(), "-A", f"yes={yes_label}", "-A", f"no={no_label}",
             title.format(number=number), body],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.asking_key = key
        self.log("AUTO_QUEUE_ASK", surah=number, question=kind)

    # ----- queueing ----------------------------------------------------------
    def _pause(self, reason: str, **fields: object) -> bool:
        if self.last_pause != reason:
            self.log("AUTO_QUEUE_PAUSED", reason=reason, **fields)
            self.last_pause = reason
        return False

    def _lookahead(self, entries: dict[int, tuple[str, str]], below: int, decisions: dict[int, str]) -> None:
        """Ask early: if the next surah after `below` is an undecided ignored one, ask now."""
        for number in todo_file.in_order(n for n in entries if todo_file.comes_before(below, n)):
            name, status = entries[number]
            if status in {"DONE", "TODO"}:
                continue
            if status == "IGNORE" and number not in decisions:
                self.ask("ignored_surahs", number, name)
            return

    def _set_aside(self, video: Path) -> Path:
        """Rename the old video so the pipeline no longer sees it as done (it is kept, not deleted)."""
        target = video.with_name(f"{video.stem}.old-{datetime.now():%Y%m%d-%H%M%S}{video.suffix}")
        video.rename(target)
        return target

    def _approved(self, number: int, name: str, status: str) -> str:
        """Handle an ignored surah you said yes to. Returns 'queued', 'uploading' or 'waiting'."""
        video = find_existing_video(self.exported_dir, self.live_dir, number, name)
        if video is None:
            # Nothing generated yet: generate it; the hand-off queues the upload afterwards.
            todo_file.set_status(self.yt_todo_path, number, "WAITING", only_from={"IGNORE", None}, name=name)
            todo_file.set_status(self.todo_path, number, "TODO", only_from={status})
            self.log("AUTO_QUEUE", surah=number, previous=status, reason="approved_generate")
            return "queued"
        regenerate = read_decisions(self.decisions_path, "regenerate").get(number)
        if regenerate is None:
            self.ask("regenerate", number, name, video)
            return "waiting"
        if regenerate == "yes":
            old = self._set_aside(video)
            todo_file.set_status(self.yt_todo_path, number, "WAITING", only_from={"IGNORE", None}, name=name)
            todo_file.set_status(self.todo_path, number, "TODO", only_from={status})
            self.log("AUTO_QUEUE", surah=number, previous=status, reason="approved_regenerate", old_video=str(old))
            return "queued"
        # regenerate == "no": upload the existing video as it is.
        todo_file.set_status(self.todo_path, number, "DONE", only_from={status})
        todo_file.set_status(self.yt_todo_path, number, "TODO", only_from={"IGNORE", "WAITING", None}, name=name)
        self.log("AUTO_QUEUE_UPLOAD_EXISTING", surah=number, video=str(video))
        return "uploading"

    def tick(self) -> bool:
        """Call when the pipeline is idle. Returns True if a surah was queued for generation."""
        self.poll_answer()
        _title, entries = todo_file.read_entries(self.todo_path)
        # tick() only runs while the pipeline is idle, so STARTED here is left over from a killed run.
        orphans = sorted(n for n, (_name, s) in entries.items() if s == "STARTED")
        for number in orphans:
            todo_file.set_status(self.todo_path, number, "TODO", only_from={"STARTED"})
            self.log("AUTO_QUEUE_RESUME_INTERRUPTED", surah=number)
        if orphans:
            return True
        blocking = sorted(n for n, (_name, s) in entries.items() if s in BLOCKING)
        if blocking:
            return self._pause("error", surahs=blocking)
        if sum(1 for _name, s in entries.values() if s == "TODO") >= self.keep_in_todo:
            return False
        decisions = read_decisions(self.decisions_path)
        for number in todo_file.in_order(entries):
            name, status = entries[number]
            if status in {"DONE", "TODO"}:
                continue
            if status == "IGNORE":
                answer = decisions.get(number)
                if answer == "no":
                    # Never uploaded either, so it must not hold up the uploads below it.
                    if todo_file.set_status(self.yt_todo_path, number, "IGNORE", only_from={"WAITING", "TODO"}, name=name):
                        self.log("AUTO_QUEUE_DECLINED", surah=number, message="Upload set to IGNORE in the queue")
                    continue
                if answer != "yes":
                    self.ask("ignored_surahs", number, name)
                    return self._pause("waiting_for_answer", surah=number, question="ignored_surahs")
                result = self._approved(number, name, status)
                if result == "waiting":
                    return self._pause("waiting_for_answer", surah=number, question="regenerate")
                self.last_pause = None
                if result == "queued":
                    return True
                continue  # 'uploading': nothing to generate, move on to the next surah
            if status == "WAITING":
                todo_file.set_status(self.todo_path, number, "TODO", only_from={status})
                self.last_pause = None
                self.log("AUTO_QUEUE", surah=number, previous=status)
                self._lookahead(entries, number, decisions)
                return True
        return self._pause("nothing_left")
