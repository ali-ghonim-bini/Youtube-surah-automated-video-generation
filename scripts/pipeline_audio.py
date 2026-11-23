"""QUL ayah-by-ayah audio: download, combine, and per-ayah timing."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports
from pipeline_sources import download_file, fetch_json
from pipeline_subtitles import fallback_ayah_timings


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def detect_voice_start(path: Path, fallback_start: float, duration: float) -> float:
    if not VOICE_TIMING_ENABLED:
        return fallback_start
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={VOICE_START_THRESHOLD_DB}dB:d=0.02",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    text = result.stderr
    silence_start = re.search(r"silence_start:\s*0(?:\.0+)?", text)
    silence_end = re.search(r"silence_end:\s*([0-9.]+)", text)
    if silence_start and silence_end:
        voice_start = min(max(0.0, float(silence_end.group(1))), duration)
        log("VOICE_TIMING_OK", file=path, threshold_db=VOICE_START_THRESHOLD_DB, fallback_start=fallback_start, voice_start=voice_start)
        return voice_start
    if result.returncode == 0:
        log("VOICE_TIMING_OK", file=path, threshold_db=VOICE_START_THRESHOLD_DB, fallback_start=fallback_start, voice_start=0.0, reason="no initial silence detected")
        return 0.0
    log("VOICE_TIMING_FALLBACK", level="WARNING", file=path, threshold_db=VOICE_START_THRESHOLD_DB, fallback_start=fallback_start, reason=result.stderr[-1000:])
    return fallback_start


def audio_segments_url(number: int, from_ayah: int = 1) -> str:
    url = AUDIO_SEGMENTS_URL_TEMPLATE.format(recitation_id=AUDIO_RECITATION_ID, surah_number=number, from_ayah=from_ayah)
    if "{from_ayah}" in AUDIO_SEGMENTS_URL_TEMPLATE:
        return url
    if re.search(r"([?&])from=\d+", url):
        return re.sub(r"([?&])from=\d+", lambda match: f"{match.group(1)}from={from_ayah}", url, count=1)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}from={from_ayah}"


def segment_window(item: dict[str, object], duration: float) -> tuple[float, float]:
    rows = item.get("segments")
    starts: list[float] = []
    ends: list[float] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list) and len(row) >= 4:
                try:
                    starts.append(max(0.0, float(row[2]) / 1000.0))
                    ends.append(min(duration, float(row[3]) / 1000.0))
                except (TypeError, ValueError):
                    continue
    if not starts or not ends:
        return 0.0, duration
    start = min(starts)
    end = max(ends)
    if end <= start:
        return 0.0, duration
    return start, end


def fetch_audio_segments(number: int, ayah_count: int) -> dict[str, object]:
    segments: dict[str, object] = {}
    attempted_from: set[int] = set()
    from_ayah = 1

    while True:
        if from_ayah in attempted_from:
            missing = [ayah for ayah in range(1, ayah_count + 1) if f"{number}:{ayah}" not in segments]
            raise RuntimeError(f"QUL response missing audio segments for surah {number} ayahs: {', '.join(map(str, missing))}")
        attempted_from.add(from_ayah)

        url = audio_segments_url(number, from_ayah)
        data = fetch_json(url)
        batch = data.get("segments")
        if not isinstance(batch, dict):
            raise RuntimeError(f"Audio source response missing segments for surah {number}")
        before = len(segments)
        for key, value in batch.items():
            if isinstance(key, str) and key.startswith(f"{number}:"):
                segments[key] = value
        log("AUDIO_SEGMENTS_FETCH_OK", surah=number, url=url, from_ayah=from_ayah, ayat=len(batch), total_ayat=len(segments), recitation_id=AUDIO_RECITATION_ID)

        missing = [ayah for ayah in range(1, ayah_count + 1) if f"{number}:{ayah}" not in segments]
        if not missing:
            return segments
        if len(segments) == before:
            raise RuntimeError(f"QUL response missing audio segments for surah {number} ayahs: {', '.join(map(str, missing))}")
        from_ayah = missing[0]


def prepare_qul_audio(number: int, ayah_count: int) -> tuple[Path, tuple[float, float] | None, list[tuple[float, float]]]:
    segments = fetch_audio_segments(number, ayah_count)

    ayah_dir = SOUND_DIR / str(number)
    combined_dir = SOUND_DIR / "Qul"
    combined_dir.mkdir(parents=True, exist_ok=True)
    combined = combined_dir / f"{number:03d}.mp3"
    ayah_files: list[Path] = []
    clip_windows: list[tuple[float, float]] = []
    basmalah_timing: tuple[float, float] | None = None

    if number not in {1, 9}:
        basmalah_segments = fetch_audio_segments(1, 1)
        basmalah_item = basmalah_segments.get("1:1")
        if not isinstance(basmalah_item, dict) or not isinstance(basmalah_item.get("audio_url"), str):
            raise RuntimeError("QUL response missing basmalah audio URL from Fatiha ayah 1")
        basmalah_file = SOUND_DIR / "Basmalah" / "001001.mp3"
        download_file(str(basmalah_item["audio_url"]), basmalah_file)
        basmalah_duration = ffprobe_duration(basmalah_file)
        ayah_files.append(basmalah_file)
        clip_windows.append(segment_window(basmalah_item, basmalah_duration))

    for ayah_number in range(1, ayah_count + 1):
        item = segments.get(f"{number}:{ayah_number}")
        if not isinstance(item, dict) or not isinstance(item.get("audio_url"), str):
            raise RuntimeError(f"QUL response missing audio URL for surah {number} ayah {ayah_number}")
        ayah_file = ayah_dir / f"{number:03d}{ayah_number:03d}.mp3"
        download_file(str(item["audio_url"]), ayah_file)
        duration = ffprobe_duration(ayah_file)
        ayah_files.append(ayah_file)
        clip_windows.append(segment_window(item, duration))
        rows = item.get("segments")
        if isinstance(rows, list):
            WORD_SEGMENTS[(number, ayah_number)] = [row for row in rows if isinstance(row, list) and len(row) >= 4]

    list_file = TEMP_DIR / f"{number}_concat.txt"
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in ayah_files), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(combined)]
    log("AUDIO_COMBINE_START", surah=number, output=combined, ayah_files=len(ayah_files))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])

    cursor = INTRO_CREDIT_SECONDS
    ayah_timings: list[tuple[float, float]] = []
    for index, ayah_file in enumerate(ayah_files):
        duration = ffprobe_duration(ayah_file)
        window_start, window_end = clip_windows[index]
        window_length = max(0.1, window_end - window_start)
        detected_start = detect_voice_start(ayah_file, window_start, duration)
        timing_end = min(duration, detected_start + window_length)
        if timing_end <= detected_start:
            detected_start, timing_end = window_start, window_end
        log(
            "AYAH_TIMING_OK",
            surah=number,
            clip_index=index,
            file=ayah_file,
            qul_start=window_start,
            qul_end=window_end,
            voice_start=detected_start,
            final_start=cursor + detected_start,
            final_end=cursor + timing_end,
        )
        timing = (cursor + detected_start, cursor + timing_end)
        if number not in {1, 9} and index == 0:
            basmalah_timing = timing
        else:
            ayah_timings.append(timing)
        cursor += duration
    log("AUDIO_COMBINE_OK", surah=number, output=combined, duration=ffprobe_duration(combined), basmalah=basmalah_timing, ayat=len(ayah_timings))
    return combined, basmalah_timing, ayah_timings


def prepare_audio(number: int, mp3: Path, ayah_count: int) -> tuple[Path, float, tuple[float, float] | None, list[tuple[float, float]]]:
    if AUDIO_SOURCE == "qul_ayah_by_ayah":
        audio, basmalah_timing, ayah_timings = prepare_qul_audio(number, ayah_count)
        return audio, ffprobe_duration(audio), basmalah_timing, ayah_timings
    duration = ffprobe_duration(mp3)
    return mp3, duration, None, fallback_ayah_timings(duration, ayah_count)
