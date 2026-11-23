#!/usr/bin/env python3
"""Video generation pipeline: entry point (run by todo_watcher.py).

The work is in the pipeline_*.py modules; everything is re-exported here.
"""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403
from pipeline_fonts import *  # noqa: F401,F403
from pipeline_queue import *  # noqa: F401,F403
from pipeline_sources import *  # noqa: F401,F403
from pipeline_audio import *  # noqa: F401,F403
from pipeline_subtitles import *  # noqa: F401,F403
from pipeline_render import *  # noqa: F401,F403
from pipeline_checks import *  # noqa: F401,F403


def verify_mp4(number: int, mp4: Path) -> None:
    if not mp4.exists() or mp4.stat().st_size <= 0:
        raise RuntimeError(f"MP4 missing or empty: {mp4}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-show_entries",
            "format=duration,size",
            "-of",
            "json",
            str(mp4),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise RuntimeError(f"No video stream found in {mp4}")
    log("VERIFY_OK", surah=number, file=mp4, probe=data)


def uploaded_surahs() -> set[int]:
    """Surahs of this reciter that have a video on YouTube, from YT_Schedule.json."""
    schedule = Path(str(setting_value("youtube.schedule_file") or SURAH_DIR / "YT_Schedule.json"))
    try:
        videos = json.loads(schedule.read_text(encoding="utf-8")).get("videos", [])
    except (OSError, ValueError, AttributeError):
        return set()
    reciter = str(setting_value("youtube.sheikh_english") or "").strip()   # the schedule is shared by every reciter; only his entries count
    return {int(video["surah"]) for video in videos if isinstance(video, dict) and video.get("video_id") and isinstance(video.get("surah"), int)
            and str(video.get("reciter", reciter)) == reciter}


def queue_for_upload(number: int, arabic_name: str) -> None:
    """Hand-off: a freshly generated video goes to READY in the queue (upload TODO) so yt_watcher uploads it."""
    if not AUTO_QUEUE_UPLOAD:
        return
    try:
        if number in uploaded_surahs():
            # Made again after it was uploaded (you moved it back to TODO): keep the new video, do not upload a second copy.
            previous = todo_file.set_status(YT_TODO_FILE, number, "DONE", name=arabic_name, only_from={"WAITING", None})
            log("UPLOAD_QUEUE_SKIP", surah=number, reason="already on YouTube; move it to READY to upload the new video too", previous=previous)
            return
        # Only from WAITING/missing: never re-queue something already uploaded, in progress, failed or ignored.
        previous = todo_file.set_status(YT_TODO_FILE, number, "TODO", name=arabic_name, only_from={"WAITING", None})
        if previous:
            log("UPLOAD_QUEUED", surah=number, yt_todo_file=YT_TODO_FILE, previous=previous)
        else:
            log("UPLOAD_QUEUE_SKIP", surah=number, yt_todo_file=YT_TODO_FILE, current=todo_file.read_statuses(YT_TODO_FILE).get(number))
    except Exception as exc:
        log("UPLOAD_QUEUE_ERROR", level="ERROR", surah=number, message=str(exc))


def mark_status(surahs: dict[int, str], statuses: dict[int, str], number: int, status: str) -> None:
    statuses[number] = status
    todo_file.set_status(TODO_FILE, number, status, name=surahs.get(number))
    log("STATUS", surah=number, status=status)


def save_statuses(surahs: dict[int, str], statuses: dict[int, str], before: dict[int, str]) -> dict[int, str]:
    """Write only what changed since `before` (one surah at a time, under the queue lock), then
    return what the file says now, so edits made meanwhile by you or the other service are kept."""
    changed = todo_file.apply_changes(TODO_FILE, statuses, surahs, before)
    if changed:
        log("QUEUE_UPDATED", changes={str(n): f"{old or 'MISSING'}->{new}" for n, (old, new) in changed.items()})
    current = read_todo_statuses()
    return {**statuses, **current}


def parse_force_arg() -> int | None:
    if len(sys.argv) == 3 and sys.argv[1] == "--force":
        return int(sys.argv[2])
    if len(sys.argv) != 1:
        raise RuntimeError("Usage: surah_automation.py [--force SURAH_NUMBER]")
    return None


def main() -> int:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPORTED_DIR.mkdir(parents=True, exist_ok=True)   # a brand-new series has no videos folder yet
    log("RUN_START", argv=sys.argv)
    processed = 0
    try:
        force_number = parse_force_arg()
        for tool in ["ffmpeg", "ffprobe"]:
            if not shutil.which(tool):
                raise RuntimeError(f"Missing required tool: {tool}")
        validate_fonts()
        if AUDIO_SOURCE == "qul_ayah_by_ayah":
            validate_audio_source_page()
        surahs = read_surahs()
        mp3s = available_mp3s()
        on_file = read_todo_statuses()
        statuses = build_statuses(surahs, mp3s, on_file)
        if force_number is not None:
            if force_number not in surahs:
                raise RuntimeError(f"Unknown surah number: {force_number}")
            if force_number not in mp3s:
                raise RuntimeError(f"Cannot force surah {force_number}: MP3 is missing")
            statuses[force_number] = "STARTED"
            log("FORCE", surah=force_number)
        statuses = save_statuses(surahs, statuses, on_file)
        current_todo_count = todo_count(statuses)
        log("SCAN_OK", mp3_count=len(mp3s), todo=current_todo_count, done=sum(1 for v in statuses.values() if v == "DONE"), ignored=sum(1 for v in statuses.values() if v == "IGNORE"), run_until_todo_empty=RUN_UNTIL_TODO_EMPTY)
        if force_number is None and current_todo_count == 0:
            log("SERVICE_IDLE", message=f"{todo_file.display_name(TODO_FILE)}: nothing in TODO")
            log("RUN_END", status="DONE", processed=processed, message=f"{todo_file.display_name(TODO_FILE)}: nothing in TODO")
            return 0
        log("SERVICE_START", todo=current_todo_count, force=force_number)

        while True:
            number = force_number if force_number is not None and processed == 0 else next_todo(statuses, mp3s)
            if number is None:
                log("SERVICE_STOP", message=f"{todo_file.display_name(TODO_FILE)}: nothing in TODO")
                log("RUN_END", status="DONE", processed=processed, message=f"{todo_file.display_name(TODO_FILE)}: nothing in TODO")
                return 0
            arabic_name = surahs[number]
            mp3 = mp3s[number]
            try:
                mark_status(surahs, statuses, number, "STARTED")
                log("PROCESS_START", surah=number, arabic=arabic_name, mp3=mp3)
                page = fetch_page(number)
                english_name, ayat, translations = extract_surah_data(number, page)
                mp3, duration, basmalah_timing, ayah_timings = prepare_audio(number, mp3, len(ayat))
                _title_end, _intro_end, _usable_start, _usable_end, _per_ayah, outro_start, total_duration = timeline(duration, len(ayat))
                log(
                    "TIMING_OK",
                    surah=number,
                    intro_credit_seconds=INTRO_CREDIT_SECONDS,
                    recitation_duration=duration,
                    basmalah_timing=basmalah_timing,
                    outro_start=outro_start,
                    outro_credit_seconds=OUTRO_CREDIT_SECONDS,
                    total_duration=total_duration,
                )
                ass_file = write_ass(number, arabic_name, english_name, ayat, translations, duration, basmalah_timing, ayah_timings)
                if GENERATE_MLT:
                    generate_mlt(number, arabic_name, english_name, mp3, duration, basmalah_timing, ayat, translations, ayah_timings)
                else:
                    log("MLT_SKIP", surah=number, reason="generate_mlt disabled")
                check_disk_space(number, total_duration)
                mp4 = render_validated(number, arabic_name, ayat, translations, mp3, ass_file, total_duration, basmalah_timing, ayah_timings)
                verify_mp4(number, mp4)
                thumbnail = generate_thumbnail(number, arabic_name, english_name)
                mark_status(surahs, statuses, number, "DONE")
                queue_for_upload(number, arabic_name)
                notify(f"Video ready: surah {number}", f"سورة {arabic_name}\n{mp4}", key=f"video-ok-{number}")
                on_file = read_todo_statuses()
                statuses = save_statuses(surahs, build_statuses(surahs, mp3s, on_file), on_file)
                log("STATUS_REFRESH", next_todo=next_todo(statuses, mp3s))
                processed += 1
                log("PROCESS_DONE", surah=number, file=mp4, thumbnail=thumbnail)
            except Exception as exc:
                if isinstance(exc, LowDiskError):   # not the surah's fault: keep it queued, the watcher retries every 10 min
                    mark_status(surahs, statuses, number, "TODO")
                    log("DISK_LOW_DEFERRED", level="WARNING", surah=number, message=str(exc))
                    notify(f"Video generation postponed: surah {number}", str(exc), level="warning", key=f"video-disk-{number}")
                    log("RUN_END", level="WARNING", status="DEFERRED", processed=processed, error=str(exc))
                    return EXIT_NETWORK_DEFERRED
                if is_network_error(exc):   # internet hiccup: keep it queued, the watcher retries in a few minutes
                    mark_status(surahs, statuses, number, "TODO")
                    log("NETWORK_DEFERRED", level="WARNING", surah=number, message=str(exc))
                    notify(f"Video generation postponed: surah {number}", f"Network problem, will retry: {str(exc)[:200]}", level="warning", key=f"video-network-{number}")
                    log("RUN_END", level="WARNING", status="DEFERRED", processed=processed, error=str(exc))
                    return EXIT_NETWORK_DEFERRED
                mark_status(surahs, statuses, number, "ERROR")
                log("ERROR", level="ERROR", surah=number, message=str(exc))
                notify(f"Video generation failed: surah {number}", str(exc)[:300], level="error", key=f"video-error-{number}")
                log("RUN_END", level="ERROR", status="ERROR", processed=processed, error=str(exc))
                return 1
            if PROCESS_ONE_PER_RUN or processed >= 1:
                if force_number is None and RUN_UNTIL_TODO_EMPTY and todo_count(statuses) > 0:
                    continue
                if force_number is None:
                    log("SERVICE_STOP", message=f"{todo_file.display_name(TODO_FILE)}: nothing in TODO")
                log("RUN_END", status="DONE", processed=processed)
                return 0
    except Exception as exc:
        log("FATAL", level="ERROR", message=str(exc))
        log("RUN_END", level="ERROR", status="ERROR", processed=processed, error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
