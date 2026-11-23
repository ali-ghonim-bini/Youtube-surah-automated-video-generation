#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import subprocess
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
import urllib.request

from auto_queue import find_existing_video
from notify import notify
from shared_log import append_event
import cleanup
import settings_path
import preflight
import todo_file


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = settings_path.settings_file()  # SURAH_SETTINGS: tests / smoke runs
SCOPES = ["https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/youtube.force-ssl"]
ENGLISH_NAMES = {
    1: "Al-Fatihah", 2: "Al-Baqarah", 3: "Ali 'Imran", 4: "An-Nisa", 5: "Al-Ma'idah", 6: "Al-An'am",
    7: "Al-A'raf", 8: "Al-Anfal", 9: "At-Tawbah", 10: "Yunus", 11: "Hud", 12: "Yusuf", 13: "Ar-Ra'd",
    14: "Ibrahim", 15: "Al-Hijr", 16: "An-Nahl", 17: "Al-Isra", 18: "Al-Kahf", 19: "Maryam", 20: "Taha",
    21: "Al-Anbiya", 22: "Al-Hajj", 23: "Al-Mu'minun", 24: "An-Nur", 25: "Al-Furqan", 26: "Ash-Shu'ara",
    27: "An-Naml", 28: "Al-Qasas", 29: "Al-'Ankabut", 30: "Ar-Rum", 31: "Luqman", 32: "As-Sajdah",
    33: "Al-Ahzab", 34: "Saba", 35: "Fatir", 36: "Ya-Sin", 37: "As-Saffat", 38: "Sad", 39: "Az-Zumar",
    40: "Ghafir", 41: "Fussilat", 42: "Ash-Shuraa", 43: "Az-Zukhruf", 44: "Ad-Dukhan", 45: "Al-Jathiyah",
    46: "Al-Ahqaf", 47: "Muhammad", 48: "Al-Fath", 49: "Al-Hujurat", 50: "Qaf", 51: "Adh-Dhariyat",
    52: "At-Tur", 53: "An-Najm", 54: "Al-Qamar", 55: "Ar-Rahman", 56: "Al-Waqi'ah", 57: "Al-Hadid",
    58: "Al-Mujadilah", 59: "Al-Hashr", 60: "Al-Mumtahanah", 61: "As-Saff", 62: "Al-Jumu'ah", 63: "Al-Munafiqun",
    64: "At-Taghabun", 65: "At-Talaq", 66: "At-Tahrim", 67: "Al-Mulk", 68: "Al-Qalam", 69: "Al-Haqqah",
    70: "Al-Ma'arij", 71: "Nuh", 72: "Al-Jinn", 73: "Al-Muzzammil", 74: "Al-Muddaththir", 75: "Al-Qiyamah",
    76: "Al-Insan", 77: "Al-Mursalat", 78: "An-Naba", 79: "An-Nazi'at", 80: "'Abasa", 81: "At-Takwir",
    82: "Al-Infitar", 83: "Al-Mutaffifin", 84: "Al-Inshiqaq", 85: "Al-Buruj", 86: "At-Tariq", 87: "Al-A'la",
    88: "Al-Ghashiyah", 89: "Al-Fajr", 90: "Al-Balad", 91: "Ash-Shams", 92: "Al-Layl", 93: "Ad-Duhaa",
    94: "Ash-Sharh", 95: "At-Tin", 96: "Al-'Alaq", 97: "Al-Qadr", 98: "Al-Bayyinah", 99: "Az-Zalzalah",
    100: "Al-'Adiyat", 101: "Al-Qari'ah", 102: "At-Takathur", 103: "Al-'Asr", 104: "Al-Humazah", 105: "Al-Fil",
    106: "Quraysh", 107: "Al-Ma'un", 108: "Al-Kawthar", 109: "Al-Kafirun", 110: "An-Nasr", 111: "Al-Masad",
    112: "Al-Ikhlas", 113: "Al-Falaq", 114: "An-Nas",
}

ENGLISH_TITLE_NAMES = {number: re.sub(r"[^A-Za-z0-9]+", " ", name).strip() for number, name in ENGLISH_NAMES.items()}


def load_settings() -> dict[str, object]:
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


SETTINGS = load_settings()
YOUTUBE = SETTINGS.get("youtube", {}) if isinstance(SETTINGS.get("youtube"), dict) else {}
SURAH_DIR = Path(str(SETTINGS.get("surah_dir", "/path/to/Channel/سور")))
SOUND_DIR = Path(str(SETTINGS.get("sound_dir", SURAH_DIR / "Sound")))
EXPORTED_DIR = Path(str(SETTINGS.get("exported_dir", SURAH_DIR / "Exported_Vids")))
LIVE_EXPORTED_DIR = Path(str(SETTINGS.get("live_exported_dir", EXPORTED_DIR / "Live")))
THUMBNAIL_DIR = Path(str((SETTINGS.get("thumbnail") or {}).get("output_dir", "/path/to/Channel/Visual/Thumbnails"))) if isinstance(SETTINGS.get("thumbnail"), dict) else Path("/path/to/Channel/Visual/Thumbnails")
SURAH_FILE = Path(str(SETTINGS.get("surah_file", SURAH_DIR / "Surhs.txt")))
LOG_FILE = Path(str(SETTINGS.get("log_file", SURAH_DIR / "Logs.json")))
TODO_FILE = Path(str(YOUTUBE.get("todo_file", SURAH_DIR / "YT_Todo.md")))
CLIENT_SECRET_FILE = Path(str(YOUTUBE.get("client_secret_file", "/path/to/Channel/auth_certification/client_secret.json")))
GENERATION_TODO_FILE = Path(str(SETTINGS.get("todo_file", SURAH_DIR / "Todo.md")))
DECISIONS_FILE = SURAH_DIR / "Decisions.json"
SKIP_SURAH_NUMBERS = {int(n) for n in SETTINGS.get("skip_surah_numbers", [18])}
TOKEN_FILE = Path(str(YOUTUBE.get("token_file", "/path/to/Channel/auth_certification/token.json")))
# When you last logged in through the browser (no secrets). yt_watcher.py uses it to remind you
# before a Testing-mode login runs out (Google ends those 7 days after the browser login).
TOKEN_META_FILE = TOKEN_FILE.with_name("token_meta.json")
SCHEDULE_FILE = Path(str(YOUTUBE.get("schedule_file", SURAH_DIR / "YT_Schedule.json")))
# One schedule file is shared by every reciter you ever upload. Each entry is marked with its reciter, so the same
# surah number under two reciters never gets mixed up; publish dates continue from the latest date in the whole file.
RECITER = str(YOUTUBE.get("sheikh_english") or "").strip()


def mine(video: object) -> bool:
    """Is this schedule entry a video of the reciter in settings.json? (Entries from before the mark existed count as his.)"""
    return isinstance(video, dict) and str(video.get("reciter", RECITER)) == RECITER
CAPTIONS = SETTINGS.get("captions", {}) if isinstance(SETTINGS.get("captions"), dict) else {}
CAPTIONS_ENABLED = bool(CAPTIONS.get("enabled", True))
CAPTIONS_OUTPUT_DIR = Path(str(CAPTIONS.get("output_dir", SURAH_DIR / "Captions")))
CAPTIONS_LANGUAGES_FILE = Path(str(CAPTIONS.get("languages_file", SURAH_DIR / "Caption_Languages.md")))
CAPTIONS_UPLOAD_ON_NEW_VIDEOS = bool(CAPTIONS.get("upload_on_new_videos", True))
AUDIO = SETTINGS.get("audio", {}) if isinstance(SETTINGS.get("audio"), dict) else {}
AUDIO_RECITATION_ID = int(AUDIO.get("recitation_id", 21))
AUDIO_SEGMENTS_URL_TEMPLATE = str(AUDIO.get("segments_url_template", "https://qul.tarteel.ai/api/v1/audio/ayah_segments/{recitation_id}?surah={surah_number}&from=1"))
VOICE_TIMING_ENABLED = bool(SETTINGS.get("voice_timing_enabled", True))
VOICE_START_THRESHOLD_DB = float(SETTINGS.get("voice_start_threshold_db", -25))
INTRO_CREDIT_SECONDS = float(((SETTINGS.get("video") or {}).get("intro") or {}).get("duration_seconds", 3.56)) if isinstance(SETTINGS.get("video"), dict) and isinstance((SETTINGS.get("video") or {}).get("intro"), dict) else 3.56
RUN_ID = datetime.now().strftime("yt-%Y%m%d-%H%M%S")
# googleapiclient retries 5xx, 429, rate-limit 403s and connection errors with exponential backoff.
API_RETRIES = 6
# Videos are uploaded in pieces, so a dropped connection costs one piece, not the whole file.
UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024          # must be a multiple of 256 KiB
UPLOAD_RETRY_WAITS = (5, 15, 30, 60, 60, 120, 120, 300)   # seconds between tries of the same piece (about 12 min in all)
CLEANUP = SETTINGS.get("cleanup", {}) if isinstance(SETTINGS.get("cleanup"), dict) else {}
CLEANUP_ENABLED = bool(CLEANUP.get("delete_local_after_upload", False))   # remove a surah's local files once YouTube has confirmed its video
CLEANUP_DURATION_TOLERANCE_SECONDS = float(CLEANUP.get("duration_tolerance_seconds", 2))
SURAH_TEMP_DIR = settings_path.temp_dir()   # where the pipeline keeps a surah's temporary subtitle/overlay files
UPLOAD_SESSION_FILE = SURAH_DIR / ".upload_session.json"   # where an unfinished upload can be picked up after a restart
UPLOAD_SESSION_MAX_AGE_HOURS = 144                        # YouTube keeps an unfinished upload for about a week
# Exit codes understood by yt_watcher.py
EXIT_OK, EXIT_ERROR, EXIT_AUTH, EXIT_DEFERRED, EXIT_WAITING_ORDER = 0, 1, 2, 3, 4
# Errors worth retrying later (surah stays in TODO); everything else goes to ERROR.
DEFERRABLE_API_REASONS = {"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
DEFERRABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}


def is_deferrable(exc: Exception, details: dict[str, object]) -> bool:
    if details.get("api_reason") in DEFERRABLE_API_REASONS:
        return True
    status = details.get("http_status")
    if isinstance(status, int) and status in DEFERRABLE_HTTP_STATUSES:
        return True
    import socket
    import ssl
    if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError)):
        return True
    return type(exc).__module__.startswith("httplib2") or type(exc).__name__ in {"ServerNotFoundError", "TransportError"}


def log(event: str, level: str = "INFO", **fields: object) -> None:
    event_fields = dict(fields)
    section_status = None
    if event in {"RUN_START", "UPLOAD_START"}:
        section_status = "UPLOADING"
    elif event == "RUN_END":
        section_status = str(event_fields.pop("status", "DONE"))
    elif level == "ERROR" or event.endswith("ERROR"):
        section_status = "ERROR"
    append_event(LOG_FILE, "youtube_upload", event, level=level, run_id=RUN_ID, status=section_status, **event_fields)


def youtube_error_details(exc: Exception) -> dict[str, object]:
    details: dict[str, object] = {"error_type": type(exc).__name__, "message": str(exc)}
    resp = getattr(exc, "resp", None)
    if resp is not None:
        details["http_status"] = getattr(resp, "status", None)
        details["http_reason"] = getattr(resp, "reason", None)
    content = getattr(exc, "content", None)
    if isinstance(content, bytes):
        content_text = content.decode("utf-8", errors="replace")
    elif content is None:
        content_text = ""
    else:
        content_text = str(content)
    if content_text:
        details["api_response"] = content_text[:4000]
        try:
            parsed = json.loads(content_text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                details["api_message"] = error.get("message")
                errors = error.get("errors")
                if isinstance(errors, list) and errors:
                    first = errors[0]
                    if isinstance(first, dict):
                        details["api_reason"] = first.get("reason")
                        details["api_domain"] = first.get("domain")
                        details["api_location"] = first.get("location")
    return details


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_json(url: str) -> dict[str, object]:
    return json.loads(fetch_text(url))


def download_file(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        dest.write_bytes(response.read())


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value))).strip()


def load_caption_languages() -> tuple[list[dict[str, object]], int]:
    if not CAPTIONS_LANGUAGES_FILE.exists():
        raise RuntimeError(f"Missing caption languages file: {CAPTIONS_LANGUAGES_FILE}")
    log("CAPTIONS_LANGUAGE_FILE_START", caption_languages_file=CAPTIONS_LANGUAGES_FILE)
    data = json.loads(CAPTIONS_LANGUAGES_FILE.read_text(encoding="utf-8"))
    rows = data.get("languages") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"Caption languages file must contain a languages array: {CAPTIONS_LANGUAGES_FILE}")
    languages: list[dict[str, object]] = []
    skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("enabled", False)):
            skipped += 1
            continue
        number = row.get("surahquran_language_number")
        code = row.get("youtube_language_code")
        name = row.get("language_name")
        if not isinstance(number, int) or not isinstance(code, str) or not code or not isinstance(name, str) or not name:
            skipped += 1
            continue
        languages.append({"number": number, "code": code, "name": name})
    if not languages:
        raise RuntimeError(f"No enabled caption languages found in: {CAPTIONS_LANGUAGES_FILE}")
    log("CAPTIONS_LANGUAGE_FILE_OK", caption_languages_file=CAPTIONS_LANGUAGES_FILE, enabled=len(languages), skipped=skipped)
    return languages, skipped


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
        f"silencedetect=noise={VOICE_START_THRESHOLD_DB}dB:d=0.03",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    for line in result.stderr.splitlines():
        match = re.search(r"silence_end:\s*([0-9.]+)", line)
        if match:
            value = float(match.group(1))
            if 0 <= value < duration:
                return value
    return fallback_start


def audio_segments_url(number: int, from_ayah: int = 1) -> str:
    url = AUDIO_SEGMENTS_URL_TEMPLATE.format(recitation_id=AUDIO_RECITATION_ID, surah_number=number, from_ayah=from_ayah)
    if "{from_ayah}" in AUDIO_SEGMENTS_URL_TEMPLATE:
        return url
    if re.search(r"([?&])from=\d+", url):
        return re.sub(r"([?&])from=\d+", lambda match: f"{match.group(1)}from={from_ayah}", url, count=1)
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}from={from_ayah}"


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
        log("CAPTIONS_AUDIO_SEGMENTS_FETCH_START", surah=number, url=url, from_ayah=from_ayah)
        data = fetch_json(url)
        batch = data.get("segments")
        if not isinstance(batch, dict):
            raise RuntimeError(f"Audio source response missing segments for surah {number}")
        before = len(segments)
        for key, value in batch.items():
            if isinstance(key, str) and key.startswith(f"{number}:"):
                segments[key] = value
        log("CAPTIONS_AUDIO_SEGMENTS_FETCH_OK", surah=number, url=url, from_ayah=from_ayah, ayat=len(batch), total_ayat=len(segments))

        missing = [ayah for ayah in range(1, ayah_count + 1) if f"{number}:{ayah}" not in segments]
        if not missing:
            return segments
        if len(segments) == before:
            raise RuntimeError(f"QUL response missing audio segments for surah {number} ayahs: {', '.join(map(str, missing))}")
        from_ayah = missing[0]


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
    return (start, end) if end > start else (0.0, duration)


def caption_ayah_timings(number: int, ayah_count: int) -> list[tuple[float, float]]:
    log("CAPTIONS_TIMING_START", surah=number)
    segments = fetch_audio_segments(number, ayah_count)

    items: list[tuple[Path, tuple[float, float], bool]] = []
    if number not in {1, 9}:
        basmalah_segments = fetch_audio_segments(1, 1)
        basmalah_item = basmalah_segments.get("1:1")
        if not isinstance(basmalah_item, dict) or not isinstance(basmalah_item.get("audio_url"), str):
            raise RuntimeError("QUL response missing basmalah audio URL from Fatiha ayah 1")
        basmalah_file = SOUND_DIR / "Basmalah" / "001001.mp3"
        download_file(str(basmalah_item["audio_url"]), basmalah_file)
        basmalah_duration = ffprobe_duration(basmalah_file)
        items.append((basmalah_file, segment_window(basmalah_item, basmalah_duration), True))

    ayah_dir = SOUND_DIR / str(number)
    for ayah_number in range(1, ayah_count + 1):
        item = segments.get(f"{number}:{ayah_number}")
        if not isinstance(item, dict) or not isinstance(item.get("audio_url"), str):
            raise RuntimeError(f"QUL response missing audio URL for surah {number} ayah {ayah_number}")
        ayah_file = ayah_dir / f"{number:03d}{ayah_number:03d}.mp3"
        download_file(str(item["audio_url"]), ayah_file)
        duration = ffprobe_duration(ayah_file)
        items.append((ayah_file, segment_window(item, duration), False))

    cursor = INTRO_CREDIT_SECONDS
    timings: list[tuple[float, float]] = []
    for audio_file, window, is_basmalah in items:
        duration = ffprobe_duration(audio_file)
        window_start, window_end = window
        window_length = max(0.1, window_end - window_start)
        detected_start = detect_voice_start(audio_file, window_start, duration)
        timing_end = min(duration, detected_start + window_length)
        if timing_end <= detected_start:
            detected_start, timing_end = window_start, window_end
        if not is_basmalah:
            timings.append((cursor + detected_start, cursor + timing_end))
        cursor += duration
    log("CAPTIONS_TIMING_OK", surah=number, ayat=len(timings), intro_seconds=INTRO_CREDIT_SECONDS)
    return timings


def caption_url(language_number: int, surah: int) -> str:
    return f"https://surahquran.com/Surah-translation/Arabic-language-{language_number}-surah-{surah}.html"


def fetch_caption_translations(number: int, language: dict[str, object]) -> list[str]:
    language_number = int(language["number"])
    url = caption_url(language_number, number)
    log("CAPTIONS_FETCH_START", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url)
    try:
        text = fetch_text(url)
    except Exception as exc:
        log("CAPTIONS_FETCH_ERROR", level="ERROR", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url, **youtube_error_details(exc))
        raise
    log("CAPTIONS_FETCH_OK", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url, bytes=len(text))
    try:
        translations = [strip_tags(match) for match in re.findall(r"<a\s+class=[\"']?her[\"']?[^>]*>(.*?)</a>", text, re.DOTALL | re.IGNORECASE)]
        translations = [translation for translation in translations if translation]
    except Exception as exc:
        log("CAPTIONS_EXTRACT_ERROR", level="ERROR", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url, **youtube_error_details(exc))
        raise
    if not translations:
        exc = RuntimeError(f"No caption translations extracted from {url}")
        log("CAPTIONS_EXTRACT_ERROR", level="ERROR", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url, **youtube_error_details(exc))
        raise exc
    log("CAPTIONS_EXTRACT_OK", surah=number, language_number=language_number, language_code=language["code"], language_name=language["name"], url=url, ayat=len(translations))
    return translations


def srt_time(seconds: float) -> str:
    milliseconds = int(round(max(0.0, seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_caption_file(number: int, language: dict[str, object], timings: list[tuple[float, float]], translations: list[str]) -> Path:
    language_number = int(language["number"])
    language_code = str(language["code"])
    out = CAPTIONS_OUTPUT_DIR / str(number) / f"{number}-{language_number}-{language_code}.srt"
    log("CAPTIONS_FILE_START", surah=number, language_number=language_number, language_code=language_code, language_name=language["name"], caption_file=out)
    if len(translations) != len(timings):
        raise RuntimeError(f"Caption ayah count mismatch: translations={len(translations)} timings={len(timings)}")
    out.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for index, ((start, end), text) in enumerate(zip(timings, translations), start=1):
        blocks.append(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}")
    out.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    log("CAPTIONS_FILE_OK", surah=number, language_number=language_number, language_code=language_code, language_name=language["name"], caption_file=out, cues=len(timings))
    return out


def verify_captions(youtube, video_id: str, number: int, languages: list[dict[str, object]], summary: dict[str, int]) -> None:
    log("CAPTIONS_VERIFY_START", surah=number, video_id=video_id)
    try:
        response = youtube.captions().list(part="snippet", videoId=video_id).execute(num_retries=API_RETRIES)
    except Exception as exc:
        log("CAPTIONS_VERIFY_ERROR", level="ERROR", surah=number, video_id=video_id, **youtube_error_details(exc))
        return
    items = response.get("items", []) if isinstance(response, dict) else []
    uploaded_codes: set[str] = set()
    if isinstance(items, list):
        for item in items:
            snippet = item.get("snippet") if isinstance(item, dict) else None
            language = snippet.get("language") if isinstance(snippet, dict) else None
            if isinstance(language, str) and language:
                uploaded_codes.add(language)
    expected_codes = {str(language["code"]) for language in languages}
    verified_codes = sorted(expected_codes & uploaded_codes)
    missing_codes = sorted(expected_codes - uploaded_codes)
    summary["verified"] = len(verified_codes)
    summary["missing"] = len(missing_codes)
    if verified_codes:
        log("CAPTIONS_VERIFY_OK", surah=number, video_id=video_id, verified=len(verified_codes), language_codes=verified_codes)
    if missing_codes:
        log("CAPTIONS_VERIFY_MISSING", level="WARNING", surah=number, video_id=video_id, missing=len(missing_codes), language_codes=missing_codes)
    log("CAPTIONS_VERIFY_DONE", surah=number, video_id=video_id, total_tracks=len(uploaded_codes), verified=len(verified_codes), missing=len(missing_codes))


def upload_captions(youtube, MediaFileUpload, video_id: str, number: int) -> dict[str, int]:
    summary = {"attempted": 0, "uploaded": 0, "failed": 0, "skipped": 0, "verified": 0, "missing": 0}
    if not CAPTIONS_ENABLED:
        log("CAPTIONS_SKIPPED", surah=number, video_id=video_id, reason="disabled")
        return summary
    try:
        caption_languages, skipped = load_caption_languages()
        summary["skipped"] = skipped
    except Exception as exc:
        summary["failed"] = 1
        log("CAPTIONS_LANGUAGE_FILE_ERROR", level="ERROR", surah=number, video_id=video_id, caption_languages_file=CAPTIONS_LANGUAGES_FILE, **youtube_error_details(exc))
        log("CAPTIONS_DONE", surah=number, video_id=video_id, **summary)
        return summary
    log("CAPTIONS_START", surah=number, video_id=video_id, languages=len(caption_languages), skipped=summary["skipped"], caption_languages_file=CAPTIONS_LANGUAGES_FILE)
    timings: list[tuple[float, float]] | None = None

    for language in caption_languages:
        summary["attempted"] += 1
        language_number = int(language["number"])
        language_code = str(language["code"])
        language_name = str(language["name"])
        log("CAPTIONS_LANGUAGE_START", surah=number, video_id=video_id, language_number=language_number, language_code=language_code, language_name=language_name)
        try:
            translations = fetch_caption_translations(number, language)
            if timings is None:
                timings = caption_ayah_timings(number, len(translations))
            try:
                caption_file = write_caption_file(number, language, timings, translations)
            except Exception as exc:
                log("CAPTIONS_FILE_ERROR", level="ERROR", surah=number, video_id=video_id, language_number=language_number, language_code=language_code, language_name=language_name, **youtube_error_details(exc))
                raise
            log("CAPTIONS_UPLOAD_START", surah=number, video_id=video_id, language_number=language_number, language_code=language_code, language_name=language_name, caption_file=caption_file)
            youtube.captions().insert(
                part="snippet",
                body={"snippet": {"videoId": video_id, "language": language_code, "name": language_name, "isDraft": False}},
                media_body=MediaFileUpload(str(caption_file), mimetype="application/octet-stream"),
            ).execute(num_retries=API_RETRIES)
            summary["uploaded"] += 1
            log("CAPTIONS_UPLOAD_OK", surah=number, video_id=video_id, language_number=language_number, language_code=language_code, language_name=language_name, caption_file=caption_file)
        except Exception as exc:
            summary["failed"] += 1
            log("CAPTIONS_UPLOAD_ERROR", level="ERROR", surah=number, video_id=video_id, language_number=language_number, language_code=language_code, language_name=language_name, **youtube_error_details(exc))
            continue
    verify_captions(youtube, video_id, number, caption_languages, summary)
    log("CAPTIONS_DONE", surah=number, video_id=video_id, **summary)
    return summary


def read_surahs() -> dict[int, str]:
    surahs: dict[int, str] = {}
    for line in SURAH_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(\d+)\s*-\s*(.+?)\s*$", line)
        if match:
            surahs[int(match.group(1))] = match.group(2)
    return surahs


def parse_todo() -> dict[int, str]:
    return todo_file.read_statuses(TODO_FILE)




def ensure_schedule_file() -> None:
    if SCHEDULE_FILE.exists() and SCHEDULE_FILE.stat().st_size > 0:
        return
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps({"latest_publish_at": None, "videos": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_todo(surahs: dict[int, str]) -> dict[int, str]:
    todo_file.create_if_missing(TODO_FILE, "# YouTube TODO", surahs, SKIP_SURAH_NUMBERS)
    return parse_todo()


def next_todo(statuses: dict[int, str]) -> int | None:
    for number in todo_file.in_order(statuses):
        if statuses[number] in {"STARTED", "TODO"}:
            return number
    return None


def import_youtube_libs():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise RuntimeError(
            "Missing YouTube upload dependencies. Install: "
            "python3 -m pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build, MediaFileUpload


def is_offline(exc: Exception) -> bool:
    """No internet / DNS / connection reset (not quota, not an API error)."""
    import socket
    import ssl
    if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout, socket.gaierror, ssl.SSLError)):
        return True
    return type(exc).__module__.startswith("httplib2") or type(exc).__name__ in {"ServerNotFoundError", "TransportError"}


def short_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:200] if str(exc) else ''}"


def run_side_task(name: str, action) -> int:
    """--sync-deleted / --reorder-schedule: one clean log line instead of a traceback when offline or logged out."""
    try:
        return action()
    except AuthRequiredError as exc:
        log("RUN_END", level="WARNING", status="SKIPPED", processed=0, task=name, reason="login_required", message=str(exc))
        print(f"{name}: skipped, YouTube login required", file=sys.stderr)
        return EXIT_AUTH
    except Exception as exc:
        if not is_deferrable(exc, youtube_error_details(exc)):
            raise
        reason = "offline" if is_offline(exc) else "deferred"
        log("RUN_END", level="WARNING", status="SKIPPED", processed=0, task=name, reason=reason, message=short_error(exc))
        print(f"{name}: skipped ({reason}): {short_error(exc)}", file=sys.stderr)
        return EXIT_DEFERRED


def ignored_and_declined(number: int) -> tuple[bool, str | None]:
    """(is it IGNORE in the generation queue, your answer in Decisions.json or None)."""
    try:
        generation_status = todo_file.read_statuses(GENERATION_TODO_FILE).get(number)
    except OSError:
        generation_status = None
    try:
        raw = json.loads(DECISIONS_FILE.read_text(encoding="utf-8")).get("ignored_surahs", {})
        answer = str(raw.get(str(number), "")).lower() or None
    except (OSError, ValueError, AttributeError):
        answer = None
    return generation_status == "IGNORE", answer


class AuthRequiredError(RuntimeError):
    pass


AUTH_FIX_HINT = f"Run in a terminal: python3 {Path(__file__).resolve()} --auth-only"


def youtube_client(allow_browser_auth: bool | None = None, force_login: bool = False):
    """force_login: always log in through the browser (resets Google's 7-day Testing-mode limit).
    The old token.json is only replaced once the new login succeeds."""
    from google.auth.exceptions import RefreshError

    if allow_browser_auth is None:
        allow_browser_auth = sys.stdin.isatty()

    Request, Credentials, InstalledAppFlow, build, _ = import_youtube_libs()
    creds = None
    if TOKEN_FILE.exists() and not force_login:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                backup = TOKEN_FILE.with_name(f"token.bad-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
                TOKEN_FILE.replace(backup)
                log("TOKEN_REFRESH_FAILED", level="ERROR", backup=str(backup), error=str(exc))
                creds = None
        if not creds or not creds.valid:
            if not CLIENT_SECRET_FILE.exists():
                raise RuntimeError(f"Missing client secret file: {CLIENT_SECRET_FILE}")
            if not allow_browser_auth:
                reason = "token.json is missing" if not TOKEN_FILE.exists() else "token.json is invalid or expired"
                raise AuthRequiredError(f"YouTube login required ({reason}: {TOKEN_FILE}). {AUTH_FIX_HINT}")
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
            try:
                creds = flow.run_local_server(
                    port=0,
                    open_browser=bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
                    timeout_seconds=300,
                )
            except AttributeError as exc:
                # google-auth-oauthlib crashes with "'NoneType' object has no attribute 'replace'" on timeout
                raise AuthRequiredError(f"YouTube login timed out after 300s without approval. {AUTH_FIX_HINT}") from exc
            save_credentials(creds)
            save_login_time()
        else:
            save_credentials(creds)
        log("TOKEN_OK", file=TOKEN_FILE)
    return build("youtube", "v3", credentials=creds)


def save_login_time() -> None:
    TOKEN_META_FILE.write_text(json.dumps({"login_at": datetime.now().astimezone().isoformat(timespec="seconds")}) + "\n", encoding="utf-8")
    TOKEN_META_FILE.chmod(0o600)
    log("LOGIN_TIME_SAVED", file=TOKEN_META_FILE)


def client_secret_values() -> tuple[str, str, str]:
    data = json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
    config = data.get("installed") or data.get("web")
    if not isinstance(config, dict):
        raise RuntimeError(f"Invalid client secret file: {CLIENT_SECRET_FILE}")
    client_id = str(config.get("client_id", ""))
    client_secret = str(config.get("client_secret", ""))
    token_uri = str(config.get("token_uri", "https://oauth2.googleapis.com/token"))
    if not client_id or not client_secret:
        raise RuntimeError(f"Missing OAuth client id/secret in: {CLIENT_SECRET_FILE}")
    return client_id, client_secret, token_uri


def save_credentials(creds) -> None:
    if hasattr(creds, "to_json"):
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
        return

    client_id, client_secret, token_uri = client_secret_values()
    expiry = getattr(creds, "expiry", None)
    if isinstance(expiry, datetime):
        expiry_text = expiry.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if expiry.tzinfo else expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        expiry_text = None
    data = {
        "token": getattr(creds, "token", None) or getattr(creds, "access_token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": getattr(creds, "token_uri", None) or token_uri,
        "client_id": getattr(creds, "client_id", None) or client_id,
        "client_secret": getattr(creds, "client_secret", None) or client_secret,
        "scopes": list(getattr(creds, "scopes", None) or SCOPES),
    }
    if expiry_text:
        data["expiry"] = expiry_text
    if not data["token"] or not data["refresh_token"]:
        raise RuntimeError("OAuth completed, but credentials did not include a token and refresh_token")
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    TOKEN_FILE.chmod(0o600)


def publish_at(video_file: Path) -> str:
    latest_publish = latest_scheduled_publish_at()   # the latest date in the whole schedule, whichever reciter it belongs to
    if latest_publish is not None:
        return next_spaced_publish_at(latest_publish)
    return tomorrow_publish_at()


def initial_publish_at(video_file: Path) -> str:
    publish_time = str(YOUTUBE.get("publish_time", "10:00"))
    hour, minute = [int(part) for part in publish_time.split(":", 1)]
    gmt_offset_hours = float(YOUTUBE.get("gmt_offset_hours", 2))
    tz = timezone(timedelta(hours=gmt_offset_hours))
    days = int(YOUTUBE.get("publish_days_after_video_done", 0))
    base_date = datetime.fromtimestamp(video_file.stat().st_mtime, tz=tz).date() + timedelta(days=days)
    local_dt = datetime.combine(base_date, time(hour, minute), tzinfo=tz)
    now_local = datetime.now(tz)
    if local_dt <= now_local:
        local_dt = datetime.combine(now_local.date(), time(hour, minute), tzinfo=tz)
        if local_dt <= now_local:
            local_dt += timedelta(days=1)
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def tomorrow_publish_at() -> str:
    publish_time = str(YOUTUBE.get("publish_time", "10:00"))
    hour, minute = [int(part) for part in publish_time.split(":", 1)]
    gmt_offset_hours = float(YOUTUBE.get("gmt_offset_hours", 2))
    tz = timezone(timedelta(hours=gmt_offset_hours))
    now_local = datetime.now(tz)
    local_dt = datetime.combine(now_local.date() + timedelta(days=1), time(hour, minute), tzinfo=tz)
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def next_spaced_publish_at(latest_publish_at: str) -> str:
    publish_time = str(YOUTUBE.get("publish_time", "10:00"))
    hour, minute = [int(part) for part in publish_time.split(":", 1)]
    gmt_offset_hours = float(YOUTUBE.get("gmt_offset_hours", 2))
    tz = timezone(timedelta(hours=gmt_offset_hours))
    between_days = int(YOUTUBE.get("publish_between_scheduled_days", 1))
    latest_local = parse_utc_datetime(latest_publish_at).astimezone(tz)
    now_local = datetime.now(tz)
    if (now_local.date() - latest_local.date()).days > between_days:
        local_dt = datetime.combine(now_local.date() + timedelta(days=1), time(hour, minute), tzinfo=tz)
    else:
        local_dt = datetime.combine(latest_local.date() + timedelta(days=between_days), time(hour, minute), tzinfo=tz)
    while local_dt <= now_local:
        local_dt += timedelta(days=max(between_days, 1))
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_datetime(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_scheduled_publish_at() -> str | None:
    schedule_value = latest_from_schedule_file()
    if schedule_value:
        return schedule_value
    return latest_from_logs()


def latest_from_schedule_file() -> str | None:
    if not SCHEDULE_FILE.exists() or SCHEDULE_FILE.stat().st_size == 0:
        return None
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    dates = [str(video["publish_at"]) for video in data.get("videos") or [] if isinstance(video, dict) and video.get("publish_at")]
    if data.get("latest_publish_at"):
        dates.append(str(data["latest_publish_at"]))
    return max(dates, key=parse_utc_datetime) if dates else None


def latest_from_logs() -> str | None:
    if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
        return None
    try:
        data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    section = data.get("youtube_upload") if isinstance(data, dict) else None
    events = section.get("events") if isinstance(section, dict) else None
    if not isinstance(events, list):
        return None
    latest: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        publish_value = event.get("publish_at")
        if not isinstance(publish_value, str) or not publish_value:
            continue
        if latest is None or parse_utc_datetime(publish_value) > parse_utc_datetime(latest):
            latest = publish_value
    return latest


def save_schedule(data: dict[str, object]) -> None:
    """Write YT_Schedule.json with the videos in publishing order (earliest date first)."""
    videos = data.get("videos")
    if isinstance(videos, list):
        data["videos"] = sorted(videos, key=lambda video: str(video.get("publish_at") or "") if isinstance(video, dict) else "")
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def record_schedule(number: int, video_id: str, publish_at_value: str) -> None:
    data: dict[str, object] = {"latest_publish_at": None, "videos": []}
    if SCHEDULE_FILE.exists() and SCHEDULE_FILE.stat().st_size > 0:
        try:
            loaded = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except json.JSONDecodeError:
            pass
    videos = data.get("videos")
    if not isinstance(videos, list):
        videos = []
    # replace this reciter's earlier entry for the surah; another reciter's entry for the same surah stays
    videos = [video for video in videos if not (isinstance(video, dict) and ((mine(video) and video.get("surah") == number) or video.get("video_id") == video_id))]
    record = {"surah": number, "video_id": video_id, "publish_at": publish_at_value, "recorded_at": datetime.now().isoformat(timespec="seconds"), "reciter": RECITER}
    videos.insert(0, record)
    data["videos"] = videos
    publish_values = [str(video.get("publish_at")) for video in videos if isinstance(video, dict) and video.get("publish_at")]
    data["latest_publish_at"] = max(publish_values, key=parse_utc_datetime) if publish_values else publish_at_value
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_schedule(data)
    log("SCHEDULE_RECORDED", surah=number, video_id=video_id, publish_at=publish_at_value, schedule_file=SCHEDULE_FILE)


DELETED_GRACE_HOURS = 6


def channel_upload_ids(youtube) -> set[str]:
    """Every video in the channel's uploads list (includes videos still processing). ~1 quota unit per 50."""
    channel = youtube.channels().list(part="contentDetails", mine=True).execute(num_retries=API_RETRIES)
    uploads = channel["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids: set[str] = set()
    token = None
    while True:
        page = youtube.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50, pageToken=token).execute(num_retries=API_RETRIES)
        ids |= {item["contentDetails"]["videoId"] for item in page.get("items", [])}
        token = page.get("nextPageToken")
        if not token:
            return ids


def cleanup_candidates() -> list[tuple[int, str, list[Path]]]:
    """(surah, video id, its local files) for every uploaded surah that still has local files. Uses no network."""
    try:
        videos = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8")).get("videos", [])
    except (OSError, ValueError, AttributeError):
        return []
    surahs = read_surahs()
    upload_status = todo_file.read_statuses(TODO_FILE)
    video_status = todo_file.read_statuses(GENERATION_TODO_FILE)
    folders = {"exported_dir": EXPORTED_DIR, "live_dir": LIVE_EXPORTED_DIR, "sound_dir": SOUND_DIR, "thumbnail_dir": THUMBNAIL_DIR, "captions_dir": CAPTIONS_OUTPUT_DIR}
    candidates: list[tuple[int, str, list[Path]]] = []
    for video in videos:
        if not (mine(video) and video.get("video_id") and isinstance(video.get("surah"), int)):
            continue   # another reciter's video, or not a complete entry
        number = video["surah"]
        if upload_status.get(number) != "DONE" or video_status.get(number) != "DONE" or number not in surahs:
            continue   # still being made or uploaded (or being made again): its files are needed
        files = cleanup.surah_files(number, surahs[number], **folders, temp_dir=SURAH_TEMP_DIR)
        if files:
            candidates.append((number, str(video["video_id"]), files))
    return candidates


def cleanup_uploaded(youtube, dry_run: bool = False, candidates: list[tuple[int, str, list[Path]]] | None = None) -> dict[str, list[int]]:
    """Check every uploaded surah that still has local files on YouTube, and remove the files of those YouTube
    has fully processed with the right length. Never changes anything on YouTube. 1 quota unit per 50 videos.
    Returns {"removed": [...], "waiting": [...], "problem": [...]} (surah numbers)."""
    result: dict[str, list[int]] = {"removed": [], "waiting": [], "problem": []}
    candidates = cleanup_candidates() if candidates is None else candidates
    if not candidates:
        return result
    surahs = read_surahs()
    folders = {"exported_dir": EXPORTED_DIR, "live_dir": LIVE_EXPORTED_DIR, "sound_dir": SOUND_DIR, "thumbnail_dir": THUMBNAIL_DIR, "captions_dir": CAPTIONS_OUTPUT_DIR}
    on_youtube: dict[str, dict] = {}
    ids = [video_id for _number, video_id, _files in candidates]
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(part="status,contentDetails,processingDetails", id=",".join(ids[start:start + 50]), maxResults=50).execute(num_retries=API_RETRIES)
        on_youtube.update({item["id"]: item for item in response.get("items", [])})
    roots = [*folders.values(), SURAH_TEMP_DIR]
    for number, video_id, files in candidates:
        local_video = find_existing_video(EXPORTED_DIR, LIVE_EXPORTED_DIR, number, surahs[number])
        local_seconds = cleanup.local_duration(local_video) if local_video else None
        verdict, why = cleanup.youtube_verdict(on_youtube.get(video_id), local_seconds, CLEANUP_DURATION_TOLERANCE_SECONDS)
        if verdict == "wait":
            result["waiting"].append(number)
            log("UPLOAD_CHECK_WAITING", surah=number, video_id=video_id, message=why)
        elif verdict == "problem":
            result["problem"].append(number)
            log("UPLOAD_CHECK_PROBLEM", level="ERROR", surah=number, video_id=video_id, message=why, kept_files=len(files))
            notify(f"Check the uploaded video: surah {number}", f"{why}\nhttps://youtu.be/{video_id}\nIts local files are kept.", level="error", key=f"yt-check-{number}")
        elif dry_run:
            result["removed"].append(number)
            print(f"Surah {number}: would remove {len(files)} item(s), {sum(cleanup.size_of(f) for f in files) / 1024**2:.0f} MB ({why})")
            for path in files:
                print(f"    {path}")
        else:
            removed, freed = cleanup.remove_files(files, roots)
            result["removed"].append(number)
            log("LOCAL_FILES_REMOVED", surah=number, video_id=video_id, youtube=why, items=len(removed), freed_mb=round(freed / 1024**2, 1), files=removed)
    return result


def sync_deleted_videos(youtube, just_uploaded: str | None = None) -> list[int]:
    """Adapt to videos you deleted on YouTube (only videos recorded in YT_Schedule.json).

    For each one: remove it from YT_Schedule.json and move its upload in the queue from DONE to
    TODO (its video file is still here -> re-upload in order) or WAITING (no file yet ->
    it uploads once generated). Never deletes anything on YouTube. 1 quota unit per 50 videos.
    """
    if not SCHEDULE_FILE.exists() or SCHEDULE_FILE.stat().st_size == 0:
        return []
    data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return []
    # only this reciter's videos: the queue belongs to him, so another reciter's deleted video must not requeue anything
    entries = [video for video in videos if mine(video) and video.get("video_id") and isinstance(video.get("surah"), int)]
    if not entries:
        return []
    existing: set[str] = set()
    ids = [str(video["video_id"]) for video in entries]
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(part="id", id=",".join(ids[start:start + 50]), maxResults=50).execute(num_retries=API_RETRIES)
        existing |= {item["id"] for item in response.get("items", [])}
    gone = [video for video in entries if video["video_id"] not in existing and video["video_id"] != just_uploaded]
    # A freshly uploaded video can be missing from videos.list while YouTube processes it.
    # Only call it deleted if it's also gone from the channel's uploads list and is old enough.
    grace_cutoff = datetime.now() - timedelta(hours=DELETED_GRACE_HOURS)
    def recorded(video: dict) -> datetime:
        try:
            return datetime.fromisoformat(str(video.get("recorded_at")))
        except ValueError:
            return datetime.min
    gone = [video for video in gone if recorded(video) < grace_cutoff]
    if gone:
        on_channel = channel_upload_ids(youtube)
        gone = [video for video in gone if video["video_id"] not in on_channel]
    if not gone:
        return []
    data["videos"] = [video for video in videos if video not in gone]
    remaining = [str(video["publish_at"]) for video in data["videos"] if isinstance(video, dict) and video.get("publish_at")]
    data["latest_publish_at"] = max(remaining, key=parse_utc_datetime) if remaining else None
    save_schedule(data)
    surahs = read_surahs()
    requeued: list[str] = []
    for video in sorted(gone, key=lambda v: -v["surah"]):
        number = video["surah"]
        has_file = find_existing_video(EXPORTED_DIR, LIVE_EXPORTED_DIR, number, surahs.get(number, "")) is not None
        target = "TODO" if has_file else "WAITING"
        changed = todo_file.set_status(TODO_FILE, number, target, only_from={"DONE"})
        log("YOUTUBE_VIDEO_DELETED", level="WARNING", surah=number, video_id=video["video_id"],
            yt_todo=target if changed else "unchanged", message="Not on YouTube anymore: removed from YT_Schedule.json")
        requeued.append(f"{number} -> {target if changed else 'unchanged'}")
    notify("Videos deleted on YouTube", "Removed from the schedule and requeued:\n" + ", ".join(requeued), level="warning",
           key="yt-deleted-" + "-".join(str(v["surah"]) for v in gone))
    return [video["surah"] for video in gone]


def reorder_schedule(youtube, just_uploaded: str | None = None) -> int:
    """Publish in work order (1 first, then 114 -> 2) no matter what order videos were uploaded in.

    Takes every scheduled video not yet published, keeps the same set of publish slots,
    and gives the earliest slot to the highest surah number. Changed videos are updated
    on YouTube (videos.update, 50 quota units each). Returns how many were moved.
    """
    if not SCHEDULE_FILE.exists():
        return 0
    data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return 0
    cutoff = datetime.now(timezone.utc) + timedelta(hours=1)  # never touch a video that is about to go public
    future = [   # this reciter's videos only: another reciter's dates are never moved or handed out
        video for video in videos
        if mine(video) and isinstance(video.get("surah"), int) and video.get("video_id")
        and isinstance(video.get("publish_at"), str) and parse_utc_datetime(video["publish_at"]) > cutoff
    ]
    if not future:
        return 0
    # Videos you deleted on YouTube must not hold a slot (or break the date updates).
    if sync_deleted_videos(youtube, just_uploaded):
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
        videos = data["videos"]
        live_ids = {str(video.get("video_id")) for video in videos if isinstance(video, dict)}
        future = [video for video in future if str(video["video_id"]) in live_ids]
        future = [next(v for v in videos if isinstance(v, dict) and v.get("video_id") == video["video_id"]) for video in future]
    if not future:
        return 0
    # Consecutive slots from the earliest scheduled one: closes gaps left by deleted videos.
    slots = [min((video["publish_at"] for video in future), key=parse_utc_datetime)]
    while len(slots) < len(future):
        slots.append(next_spaced_publish_at(slots[-1]))
    ordered = sorted(future, key=lambda video: todo_file.order_key(int(video["surah"])))  # 1 first, then 114 -> 2
    moved = 0
    for video, slot in zip(ordered, slots):
        if video["publish_at"] == slot:
            continue
        old = video["publish_at"]
        youtube.videos().update(
            part="status",
            body={"id": video["video_id"], "status": {
                "privacyStatus": str(YOUTUBE.get("privacy_status", "private")),
                "publishAt": slot,
                "selfDeclaredMadeForKids": bool(YOUTUBE.get("made_for_kids", False)),
            }},
        ).execute(num_retries=API_RETRIES)
        video["publish_at"] = slot
        video["rescheduled_at"] = datetime.now().isoformat(timespec="seconds")
        save_schedule(data)
        log("PUBLISH_RESCHEDULED", surah=video["surah"], video_id=video["video_id"], old_publish_at=old, publish_at=slot)
        moved += 1
    if moved:
        every = [str(video["publish_at"]) for video in data["videos"] if isinstance(video, dict) and video.get("publish_at")]
        data["latest_publish_at"] = max(every, key=parse_utc_datetime)
        save_schedule(data)
    return moved


def schedule_by_surah() -> dict[int, str]:
    """{surah: publish date} of this reciter's uploaded videos."""
    if not SCHEDULE_FILE.exists() or SCHEDULE_FILE.stat().st_size == 0:
        return {}
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    videos = data.get("videos") if isinstance(data, dict) else None
    if not isinstance(videos, list):
        return {}
    schedule: dict[int, str] = {}
    for video in videos:
        if not mine(video):
            continue
        number = video.get("surah")
        publish_value = video.get("publish_at")
        if isinstance(number, int) and isinstance(publish_value, str) and publish_value:
            schedule[number] = publish_value
    return schedule


def format_metadata(number: int, arabic_name: str) -> tuple[str, str]:
    english_name = ENGLISH_NAMES.get(number, str(number))
    english_name_plain = ENGLISH_TITLE_NAMES.get(number, english_name)
    values = {
        "number": number,
        "surah_number": number,
        "arabic_name": arabic_name,
        "english_name": english_name,
        "english_name_plain": english_name_plain,
        "sheikh_arabic": YOUTUBE.get("sheikh_arabic", "محمود خليل الحصري"),
        "sheikh_english": YOUTUBE.get("sheikh_english", "Mahmoud Khalil Al Husary"),
        "sheikh_arabic_title": YOUTUBE.get("sheikh_arabic_title", "الحصري"),
        "sheikh_english_title": YOUTUBE.get("sheikh_english_title", "Sheikh Al-Husary"),
        "riwayah_arabic": YOUTUBE.get("riwayah_arabic", "حفص عن عاصم"),
        "riwayah_english": YOUTUBE.get("riwayah_english", "Hafs from Asim"),
        "riwayah_arabic_title": YOUTUBE.get("riwayah_arabic_title", "حفص"),
        "riwayah_english_title": YOUTUBE.get("riwayah_english_title", "Hafs"),
        "sheikh_hashtag": YOUTUBE.get("sheikh_hashtag", "#husary#"),
        "riwayah_hashtag": YOUTUBE.get("riwayah_hashtag", "#حفص"),
        "riwayah_full_hashtag": YOUTUBE.get("riwayah_full_hashtag", "#حفص_عن_عاصم"),
        "intro_text": formatted_intro_text(),
        "outro_text": formatted_intro_text(),
        "surahquran_url": (SETTINGS.get("surahquran") or {}).get("display_url", "https://surahquran.com") if isinstance(SETTINGS.get("surahquran"), dict) else "https://surahquran.com",
        "audio_source_url": (SETTINGS.get("audio") or {}).get("display_url", "https://qul.tarteel.ai") if isinstance(SETTINGS.get("audio"), dict) else "https://qul.tarteel.ai",
    }
    title = str(YOUTUBE.get("title_format", "Surah {english_name} | سورة {arabic_name}")).format(**values)
    description = str(YOUTUBE.get("description_format", "Surah {english_name} - سورة {arabic_name}")).format(**values)
    return title, description


def formatted_intro_text() -> str:
    lines_value = YOUTUBE.get("intro_lines", YOUTUBE.get("outro_lines"))
    if isinstance(lines_value, list):
        return "\n".join(str(line).strip() for line in lines_value).strip()

    arabic_text = str(YOUTUBE.get("intro_arabic_text", YOUTUBE.get("outro_arabic_text", YOUTUBE.get("outro_text", "")))).strip()
    english_text = str(YOUTUBE.get("intro_english_text", YOUTUBE.get("outro_english_text", ""))).strip()
    links_value = YOUTUBE.get("intro_links", YOUTUBE.get("outro_links", []))
    links = [str(link).strip() for link in links_value if str(link).strip()] if isinstance(links_value, list) else []

    if not arabic_text and isinstance(SETTINGS.get("video"), dict):
        intro = SETTINGS["video"].get("intro")
        if isinstance(intro, dict):
            arabic_text = str(intro.get("arabic_text", "")).strip()
    if not english_text and isinstance(SETTINGS.get("video"), dict):
        intro = SETTINGS["video"].get("intro")
        if isinstance(intro, dict):
            first_line = str(intro.get("english_text", "")).splitlines()[0:1]
            english_text = first_line[0].strip() if first_line else ""
    if not links:
        links = [
            str((SETTINGS.get("surahquran") or {}).get("display_url", "")).strip() if isinstance(SETTINGS.get("surahquran"), dict) else "",
            str((SETTINGS.get("audio") or {}).get("display_url", "")).strip() if isinstance(SETTINGS.get("audio"), dict) else "",
        ]
        links = [link for link in links if link]

    return "\n".join([line for line in [arabic_text, english_text, *links] if line])


def ensure_thumbnail(number: int, arabic_name: str) -> Path:
    thumbnail_file = THUMBNAIL_DIR / f"{number}.png"
    if thumbnail_file.exists() and thumbnail_file.stat().st_size > 0:
        return thumbnail_file
    log("THUMBNAIL_MISSING_GENERATE", surah=number, thumbnail=thumbnail_file)
    import surah_automation  # same renderer the pipeline uses, so it matches the other thumbnails

    english_name, _ayat, _translations = surah_automation.extract_surah_data(number, surah_automation.fetch_page(number))
    return surah_automation.generate_thumbnail(number, arabic_name, english_name)


def file_fingerprint(video_file: Path) -> list[int]:
    stat = video_file.stat()
    return [stat.st_size, stat.st_mtime_ns]


def load_upload_session(number: int, video_file: Path, now: datetime | None = None) -> dict | None:
    """The unfinished upload of this exact file left by an earlier run (stopped by a restart), if it can still be continued."""
    try:
        saved = json.loads(UPLOAD_SESSION_FILE.read_text(encoding="utf-8"))
        age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(saved["started"])
        usable = (saved["surah"] == number and saved["file"] == str(video_file) and saved["fingerprint"] == file_fingerprint(video_file)
                  and str(saved["uri"]).startswith("https://") and age.total_seconds() < UPLOAD_SESSION_MAX_AGE_HOURS * 3600
                  and str(saved["publish_at"]) > (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return saved if usable else None


def save_upload_session(number: int, video_file: Path, uri: str, publish_at_text: str) -> None:
    try:
        temp = UPLOAD_SESSION_FILE.with_name(UPLOAD_SESSION_FILE.name + ".tmp")
        temp.write_text(json.dumps({"surah": number, "file": str(video_file), "fingerprint": file_fingerprint(video_file), "uri": uri,
                                    "publish_at": publish_at_text, "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n", encoding="utf-8")
        os.chmod(temp, 0o600)   # the address lets anyone holding it finish the upload
        os.replace(temp, UPLOAD_SESSION_FILE)
    except OSError as exc:
        log("UPLOAD_SESSION_SAVE_ERROR", level="WARNING", surah=number, message=str(exc))


def clear_upload_session() -> None:
    try:
        UPLOAD_SESSION_FILE.unlink()
    except OSError:
        pass


def send_in_chunks(request, number: int, size: int, sleep=None, on_session=None) -> dict:
    """Resumable upload, one piece at a time. When the connection drops, wait, ask YouTube how much
    it already has and continue from there; give up (the surah is retried later) only when the same
    piece keeps failing for about 12 minutes."""
    import time as time_module
    sleep = sleep or time_module.sleep
    response, failures, last_logged = None, 0, -1
    while response is None:
        try:
            progress, response = request.next_chunk(num_retries=2)
        except Exception as exc:
            if not is_offline(exc) and not is_deferrable(exc, youtube_error_details(exc)):
                raise
            if failures >= len(UPLOAD_RETRY_WAITS):
                raise
            wait = UPLOAD_RETRY_WAITS[failures]
            failures += 1
            sent = int(getattr(request, "resumable_progress", 0) or 0)
            log("UPLOAD_CHUNK_RETRY", level="WARNING", surah=number, attempt=failures, wait_seconds=wait,
                sent_mb=round(sent / 1024**2, 1), size_mb=round(size / 1024**2, 1), message=short_error(exc))
            sleep(wait)
            if getattr(request, "resumable_uri", None):
                request._in_error_state = True   # next_chunk first asks YouTube which bytes it has, then resumes there
            continue
        failures = 0
        if on_session is not None and getattr(request, "resumable_uri", None):
            on_session(request.resumable_uri)   # once YouTube has opened the upload, note where to continue it
            on_session = None
        if progress is not None and response is None:
            tenth = int(progress.progress() * 10)
            if tenth > last_logged:
                last_logged = tenth
                log("UPLOAD_PROGRESS", surah=number, percent=round(progress.progress() * 100), sent_mb=round(progress.resumable_progress / 1024**2, 1))
    return response


def upload_one(youtube, MediaFileUpload, number: int, arabic_name: str) -> str:
    video_file = find_existing_video(EXPORTED_DIR, LIVE_EXPORTED_DIR, number, arabic_name)
    if video_file is None:
        raise RuntimeError(f"Missing video file: {EXPORTED_DIR / f'{number}.mp4'} (or another accepted name)")
    thumbnail_file = ensure_thumbnail(number, arabic_name)
    title, description = format_metadata(number, arabic_name)
    saved = load_upload_session(number, video_file)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": YOUTUBE.get("tags", []),
            "categoryId": str(YOUTUBE.get("category_id", "27")),
        },
        "status": {
            "privacyStatus": str(YOUTUBE.get("privacy_status", "private")),
            "publishAt": saved["publish_at"] if saved else publish_at(video_file),   # a continued upload keeps the date it was started with
            "selfDeclaredMadeForKids": bool(YOUTUBE.get("made_for_kids", False)),
        },
    }
    problems = preflight.upload_problems(title, description, list(body["snippet"]["tags"]), video_file, thumbnail_file,
                                         body["status"]["publishAt"], datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    playlist_id = str(YOUTUBE.get("playlist_id", "")).strip()
    if playlist_id and not problems:   # 1 quota unit: better than finding out after the upload
        found = youtube.playlists().list(part="id", id=playlist_id, maxResults=1).execute(num_retries=API_RETRIES)
        if not found.get("items"):
            problems.append(f"playlist {playlist_id} was not found on the channel")
    if problems:
        log("PREFLIGHT_FAILED", level="ERROR", surah=number, problems=problems)
        raise RuntimeError("Not uploaded, fix first: " + "; ".join(problems))
    log("PREFLIGHT_OK", surah=number, title_chars=len(title), description_bytes=len(description.encode("utf-8")), thumbnail_bytes=thumbnail_file.stat().st_size)
    log("UPLOAD_START", surah=number, video=video_file, thumbnail=thumbnail_file, title=title, publish_at=body["status"]["publishAt"])
    def new_request():
        return youtube.videos().insert(part="snippet,status", body=body,
                                       media_body=MediaFileUpload(str(video_file), chunksize=UPLOAD_CHUNK_BYTES, resumable=True))

    def remember(uri: str) -> None:
        save_upload_session(number, video_file, uri, str(body["status"]["publishAt"]))

    size = video_file.stat().st_size
    request = new_request()
    response = None
    if saved:   # a restart stopped this upload part-way: ask YouTube how much it has and send only the rest
        request.resumable_uri = saved["uri"]
        request._in_error_state = True
        log("UPLOAD_RESUME", surah=number, started=saved["started"])
        try:
            response = send_in_chunks(request, number, size)
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) not in (400, 404, 410):
                raise
            log("UPLOAD_RESUME_EXPIRED", level="WARNING", surah=number, message=short_error(exc))   # YouTube dropped it: start over
            clear_upload_session()
            request = new_request()
    if response is None:
        response = send_in_chunks(request, number, size, on_session=remember)
    clear_upload_session()
    video_id = response["id"]
    log("VIDEO_UPLOAD_OK", surah=number, video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_file))).execute(num_retries=API_RETRIES)
        log("THUMBNAIL_SET_OK", surah=number, video_id=video_id, thumbnail=thumbnail_file)
        add_to_playlist(youtube, video_id, number)
        if CAPTIONS_UPLOAD_ON_NEW_VIDEOS:
            upload_captions(youtube, MediaFileUpload, video_id, number)
    except Exception as exc:
        log("POST_UPLOAD_ERROR", level="ERROR", surah=number, video_id=video_id, **youtube_error_details(exc))
        exc.video_id = video_id  # type: ignore[attr-defined]  # video exists: never re-upload it automatically
        raise
    log("UPLOAD_OK", surah=number, video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")
    record_schedule(number, video_id, str(body["status"]["publishAt"]))
    try:
        reorder_schedule(youtube, just_uploaded=video_id)
    except Exception as exc:  # the upload itself succeeded; don't fail it over the ordering
        log("PUBLISH_RESCHEDULE_ERROR", level="WARNING", surah=number, **youtube_error_details(exc))
        notify("Publish order not fixed", f"Surah {number} uploaded, but reordering publish dates failed:\n{exc}\nRun: youtube_upload.py --reorder-schedule", level="warning", key="yt-reorder-error")
    return video_id


def add_to_playlist(youtube, video_id: str, number: int) -> None:
    playlist_id = str(YOUTUBE.get("playlist_id", "")).strip()
    if not playlist_id:
        return
    log("PLAYLIST_ADD_START", surah=number, video_id=video_id, playlist_id=playlist_id)
    try:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute(num_retries=API_RETRIES)
    except Exception as exc:
        log("PLAYLIST_ADD_ERROR", level="ERROR", surah=number, video_id=video_id, playlist_id=playlist_id, **youtube_error_details(exc))
        raise
    log("PLAYLIST_ADD_OK", surah=number, video_id=video_id, playlist_id=playlist_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", type=int)
    parser.add_argument("--auth-only", action="store_true", help="Log in through the browser and save a new token.json (resets the 7-day limit)")
    parser.add_argument("--playlist-video-id", help="Add an existing YouTube video ID to the configured playlist without uploading")
    parser.add_argument("--captions-video-id", help="Upload captions to an existing YouTube video ID without uploading a new video")
    parser.add_argument("--surah", type=int, help="Surah number for playlist-only logging")
    parser.add_argument("--reorder-schedule", action="store_true", help="Re-assign scheduled publish dates in work order (1, then 114 -> 2)")
    parser.add_argument("--cleanup-uploaded", action="store_true", help="Check uploaded videos on YouTube and remove the local files of those it has fully processed")
    parser.add_argument("--dry-run", action="store_true", help="With --cleanup-uploaded: only list what would be removed")
    parser.add_argument("--sync-deleted", action="store_true", help="Requeue surahs whose YouTube video was deleted (YT_Schedule.json + the queue)")
    args = parser.parse_args()
    processed = 0
    log("RUN_START", argv=sys.argv)
    ensure_schedule_file()
    if args.auth_only:
        youtube_client(allow_browser_auth=True, force_login=True)
        log("RUN_END", status="DONE", processed=0, message="OAuth token ready (new browser login)")
        return 0
    if args.sync_deleted:
        def sync() -> int:
            gone = sync_deleted_videos(youtube_client())
            log("RUN_END", status="DONE", processed=0, message=f"Deleted-video sync: {len(gone)} found", surahs=gone)
            print(f"Deleted on YouTube: {gone or 'none'}")
            return 0
        return run_side_task("sync-deleted", sync)
    if args.cleanup_uploaded:
        def clean() -> int:
            if not CLEANUP_ENABLED and not args.dry_run:
                log("RUN_END", status="DONE", processed=0, message="Local cleanup is switched off (cleanup.delete_local_after_upload)")
                print("Local cleanup is switched off.")
                return 0
            left = cleanup_candidates()
            if not left:   # nothing to check: no login, no request to YouTube
                log("RUN_END", status="DONE", processed=0, message="Local cleanup: nothing local left")
                print("Nothing local left.")
                return 0
            done = cleanup_uploaded(youtube_client(), dry_run=args.dry_run, candidates=left)
            log("RUN_END", status="DONE", processed=0, message=f"Local cleanup: {len(done['removed'])} removed, {len(done['waiting'])} waiting, {len(done['problem'])} to check", **done, dry_run=args.dry_run)
            print(f"Removed: {done['removed'] or 'none'}; still processing: {done['waiting'] or 'none'}; to check: {done['problem'] or 'none'}")
            return 0
        return run_side_task("cleanup-uploaded", clean)
    if args.reorder_schedule:
        def reorder() -> int:
            moved = reorder_schedule(youtube_client())
            log("RUN_END", status="DONE", processed=0, message=f"Publish schedule reordered ({moved} moved)")
            print(f"Moved {moved} video(s).")
            return 0
        return run_side_task("reorder-schedule", reorder)
    if args.playlist_video_id:
        youtube = youtube_client()
        add_to_playlist(youtube, args.playlist_video_id, args.surah or 0)
        log("RUN_END", status="DONE", processed=0, message="Existing video added to playlist", video_id=args.playlist_video_id)
        return 0
    if args.captions_video_id:
        if not args.surah:
            raise RuntimeError("--surah is required with --captions-video-id")
        _, _, _, _, MediaFileUpload = import_youtube_libs()
        youtube = youtube_client()
        summary = upload_captions(youtube, MediaFileUpload, args.captions_video_id, args.surah)
        log("RUN_END", status="DONE", processed=0, message="Caption upload completed", video_id=args.captions_video_id, **summary)
        return 0
    surahs = read_surahs()
    statuses = ensure_todo(surahs)
    if args.force is not None:
        todo_file.set_status(TODO_FILE, args.force, "STARTED", name=surahs.get(args.force))
        statuses = todo_file.read_statuses(TODO_FILE)
    number = args.force or next_todo(statuses)
    if number is None:
        log("RUN_END", status="DONE", processed=0, message=f"{todo_file.display_name(TODO_FILE)}: nothing to upload")
        return EXIT_OK
    if args.force is None:
        # Strict order (1, then 114 -> 2): never upload a surah while an earlier one is not uploaded (52 waits for 53).
        blocker = next((n for n in todo_file.in_order(statuses) if todo_file.comes_before(n, number) and statuses[n] not in {"DONE", "IGNORE"}), None)
        while blocker is not None and statuses[blocker] == "WAITING":
            ignored, answer = ignored_and_declined(blocker)
            if not ignored:
                break
            if answer == "no":
                # You said "No" to making it: it will never be uploaded, so it must not hold up the rest.
                todo_file.set_status(TODO_FILE, blocker, "IGNORE", only_from={"WAITING"}, name=surahs.get(blocker))
                statuses[blocker] = "IGNORE"
                log("UPLOAD_ORDER_SKIP_DECLINED", level="WARNING", surah=blocker, message=f"Declined ignored surah set to IGNORE in {todo_file.display_name(TODO_FILE)}")
                blocker = next((n for n in todo_file.in_order(statuses) if todo_file.comes_before(n, number) and statuses[n] not in {"DONE", "IGNORE"}), None)
                continue
            notify(f"Uploads wait for surah {blocker}", f"Surah {blocker} is ignored and waiting for your answer (make & upload it?).\nAnswer the question, or set it to IGNORE in {todo_file.display_name(TODO_FILE)}.", level="warning", key=f"yt-order-ignored-{blocker}")
            break
        if blocker is not None:
            log("UPLOAD_WAITING_FOR_ORDER", level="WARNING", surah=number, waiting_for=blocker, waiting_for_status=statuses[blocker])
            log("RUN_END", status="WATCHING", processed=0, message=f"Surah {number} waits until surah {blocker} is uploaded")
            if statuses[blocker] == "ERROR":
                notify(f"Uploads paused at surah {blocker}", f"Surah {blocker} is in ERROR, so {number} and below wait.\nFix it or set it to IGNORE in {todo_file.display_name(TODO_FILE)}.", level="warning", key=f"yt-order-error-{blocker}")
            return EXIT_WAITING_ORDER
    if statuses.get(number) == "STARTED" and args.force is None and number in schedule_by_surah():
        # A previous run uploaded it but died before marking DONE: don't upload a duplicate.
        todo_file.set_status(TODO_FILE, number, "DONE")
        log("ALREADY_UPLOADED", level="WARNING", surah=number, message="Found in YT_Schedule.json; marked DONE without re-uploading")
        log("RUN_END", status="DONE", processed=0)
        return EXIT_OK
    try:
        _, _, _, _, MediaFileUpload = import_youtube_libs()
        youtube = youtube_client()
    except AuthRequiredError as exc:
        # Not the surah's fault: leave it in TODO so it uploads once login is fixed.
        log("AUTH_REQUIRED", level="ERROR", surah=number, error_type=type(exc).__name__, message=str(exc))
        log("RUN_END", level="ERROR", status="ERROR", processed=0, error_type=type(exc).__name__, message=str(exc))
        notify("YouTube login required", f"Uploads are paused.\n{AUTH_FIX_HINT}", level="error", key="yt-auth")
        return EXIT_AUTH
    except Exception as exc:
        # e.g. no internet while renewing the login: keep it in TODO and retry later (not an ERROR).
        if not is_deferrable(exc, youtube_error_details(exc)):
            raise
        offline = is_offline(exc)
        log("UPLOAD_DEFERRED", level="WARNING", surah=number, offline=offline, stage="login", message=short_error(exc))
        log("RUN_END", level="WARNING", status="DEFERRED", processed=0, offline=offline, message=short_error(exc))
        if not offline:
            notify("YouTube upload postponed", f"Surah {number}: {short_error(exc)}\nWill retry automatically.", level="warning", key="yt-deferred-login")
        return EXIT_DEFERRED
    try:
        todo_file.set_status(TODO_FILE, number, "STARTED")
        video_id = upload_one(youtube, MediaFileUpload, number, surahs[number])
        todo_file.set_status(TODO_FILE, number, "DONE")
        processed = 1
        log("RUN_END", status="DONE", processed=processed)
        publish = schedule_by_surah().get(number, "")
        notify(f"Uploaded surah {number}", f"سورة {surahs[number]}\nhttps://youtu.be/{video_id}\nPublishes: {publish}", key=f"yt-ok-{number}")
        return EXIT_OK
    except Exception as exc:
        details = youtube_error_details(exc)
        uploaded_video_id = getattr(exc, "video_id", None)
        if uploaded_video_id is None and is_deferrable(exc, details):
            # Quota, rate limit, YouTube outage or network down: keep it queued and let the watcher retry later.
            todo_file.set_status(TODO_FILE, number, "TODO")
            log("UPLOAD_DEFERRED", level="WARNING", surah=number, offline=is_offline(exc), **details)
            log("RUN_END", level="ERROR", status="ERROR", processed=processed, deferred=True, **details)
            reason = str(details.get("api_reason") or details.get("http_status") or details["error_type"])
            notify("YouTube upload postponed", f"Surah {number}: {reason}\nWill retry automatically.", level="warning", key=f"yt-deferred-{reason}")
            return EXIT_DEFERRED
        todo_file.set_status(TODO_FILE, number, "ERROR")
        log("UPLOAD_ERROR", level="ERROR", surah=number, video_id=uploaded_video_id, **details)
        log("RUN_END", level="ERROR", status="ERROR", processed=processed, **details)
        extra = f"\nVideo was uploaded (https://youtu.be/{uploaded_video_id}); finish it in YouTube Studio." if uploaded_video_id else ""
        notify(f"YouTube upload failed: surah {number}", f"{details.get('api_message') or details['message']}{extra}", level="error", key=f"yt-error-{number}")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
