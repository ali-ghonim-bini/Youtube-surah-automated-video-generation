#!/usr/bin/env python3
from __future__ import annotations

import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
import urllib.request
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from shared_log import append_event, json_safe


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_DIR / "settings.json"


def load_settings() -> dict[str, object]:
    if not SETTINGS_FILE.exists():
        raise RuntimeError(f"Missing settings file: {SETTINGS_FILE}")
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def path_from_setting(settings: dict[str, object], key: str, base: Path) -> Path:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing or invalid setting: {key}")
    path = Path(value)
    return path if path.is_absolute() else base / path


def setting_value(path: str) -> object:
    current: object = SETTINGS
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"Missing setting: {path}")
        current = current[part]
    return current


def setting_str(path: str) -> str:
    value = setting_value(path)
    return str(value)


def setting_int(path: str) -> int:
    return int(setting_value(path))


def setting_float(path: str) -> float:
    return float(setting_value(path))


def setting_bool(path: str) -> bool:
    return bool(setting_value(path))


def setting_list(path: str) -> list[object]:
    value = setting_value(path)
    if not isinstance(value, list):
        raise RuntimeError(f"Setting must be a list: {path}")
    return value


def setting_path(path: str, base: Path) -> Path:
    value = setting_value(path)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing or invalid setting: {path}")
    expanded = Path(value)
    return expanded if expanded.is_absolute() else base / expanded


def color_rgb(value: str) -> tuple[float, float, float]:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise RuntimeError(f"Invalid RGB color: {value}")
    return int(cleaned[0:2], 16) / 255.0, int(cleaned[2:4], 16) / 255.0, int(cleaned[4:6], 16) / 255.0


def ass_color(value: str) -> str:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise RuntimeError(f"Invalid RGB color: {value}")
    return f"&H00{cleaned[4:6]}{cleaned[2:4]}{cleaned[0:2]}"


def ass_bold(value: bool) -> int:
    return 1 if value else 0


SETTINGS = load_settings()
SURAH_DIR = path_from_setting(SETTINGS, "surah_dir", SCRIPT_DIR)
CHANNEL_DIR = SURAH_DIR.parent
SOUND_DIR = path_from_setting(SETTINGS, "sound_dir", SURAH_DIR)
EXPORTED_DIR = path_from_setting(SETTINGS, "exported_dir", SURAH_DIR)
LIVE_EXPORTED_DIR = path_from_setting(SETTINGS, "live_exported_dir", EXPORTED_DIR)
SURAH_FILE = path_from_setting(SETTINGS, "surah_file", SURAH_DIR)
TODO_FILE = path_from_setting(SETTINGS, "todo_file", SURAH_DIR)
LOG_FILE = path_from_setting(SETTINGS, "log_file", SURAH_DIR)
BG_IMAGE = setting_path("video.background_image", CHANNEL_DIR)
THUMBNAIL_DIR = setting_path("thumbnail.output_dir", CHANNEL_DIR)

SKIP_SURAH_NUMBERS = {int(number) for number in setting_list("skip_surah_numbers")}
PROCESS_ONE_PER_RUN = setting_bool("process_one_per_run")
RUN_UNTIL_TODO_EMPTY = setting_bool("run_until_todo_empty")
FADE_TITLE_SECONDS = setting_float("video.title.fade_in_seconds")
FADE_RIWAYAH_SECONDS = setting_float("video.riwayah.fade_in_seconds")
FADE_BASMALAH_SECONDS = setting_float("video.basmalah.fade_in_seconds")
FADE_AYAH_SECONDS = setting_float("video.ayah.fade_in_seconds")
FADE_CREDIT_SECONDS = setting_float("video.intro.fade_in_seconds")
FADE_OUT_SECONDS = setting_float("video.ayah.fade_out_seconds")
INTRO_CREDIT_SECONDS = setting_float("video.intro.duration_seconds")
OUTRO_CREDIT_SECONDS = setting_float("video.outro.duration_seconds")
PANGO_SYNC_OFFSET_SECONDS = setting_float("video.pango_sync_offset_seconds")
GENERATE_MLT = setting_bool("generate_mlt")
VOICE_TIMING_ENABLED = setting_bool("voice_timing_enabled")
VOICE_START_THRESHOLD_DB = setting_float("voice_start_threshold_db")
VOICE_TIMING_SEARCH_PADDING_SECONDS = setting_float("voice_timing_search_padding_seconds")
LAYOUT_CENTER_X = setting_int("video.layout.center_x")
LAYOUT_TITLE_Y = setting_int("video.title.center_y")
LAYOUT_RIWAYAH_Y = setting_int("video.riwayah.center_y")
LAYOUT_ARABIC_Y = setting_int("video.ayah_arabic.center_y")
LAYOUT_ENGLISH_Y = setting_int("video.ayah_english.center_y")
LAYOUT_CREDIT_Y = setting_int("video.layout.credit_y")
FONT_STANDARD = setting_str("video.font_standard")
FONT_ARABIC_AYAH = setting_str("video.ayah_arabic.family")
FONT_ENGLISH = setting_str("video.ayah_english.family")
FONTS_DIR = path_from_setting(SETTINGS, "fonts_dir", SCRIPT_DIR)
STRICT_FONT_VALIDATION = setting_bool("strict_font_validation")
ARABIC_AYAH_NUMBER_BIDI = setting_str("video.ayah_arabic.number_bidi")
ARABIC_AYAH_LINE_HEIGHT = setting_int("video.ayah_arabic.line_height")
ARABIC_AYAH_NUMBER_GAP = setting_int("video.ayah_arabic.number_gap")
ARABIC_AYAH_WRAP_WIDTH = setting_int("video.ayah_arabic.wrap_width")
PANGO_ARABIC_OVERLAY = setting_bool("video.ayah_arabic.use_pango")
PANGO_OVERLAY_FPS = setting_int("video.pango_overlay_fps")
PANGO_ARABIC_FONT = setting_str("video.ayah_arabic.font")
PANGO_ARABIC_BOX_WIDTH = setting_int("video.ayah_arabic.box_width")
PANGO_ARABIC_COLOR = setting_str("video.ayah_arabic.color")
PANGO_ARABIC_LINE_SPACING = setting_float("video.ayah_arabic.line_spacing")
TITLE_SIZE = setting_int("video.title.size")
TITLE_COLOR = setting_str("video.title.color")
TITLE_BOLD = setting_bool("video.title.bold")
RIWAYAH_SIZE = setting_int("video.riwayah.size")
RIWAYAH_COLOR = setting_str("video.riwayah.color")
RIWAYAH_BOLD = setting_bool("video.riwayah.bold")
ENGLISH_SIZE = setting_int("video.ayah_english.size")
ENGLISH_COLOR = setting_str("video.ayah_english.color")
ENGLISH_BOLD = setting_bool("video.ayah_english.bold")
ENGLISH_WRAP_WIDTH = setting_int("video.ayah_english.wrap_width")
CREDIT_SIZE = setting_int("video.intro.english_size")
CREDIT_COLOR = setting_str("video.intro.english_color")
INTRO_ENGLISH_BOLD = setting_bool("video.intro.english_bold")
OUTRO_SIZE = setting_int("video.outro.english_size")
OUTRO_COLOR = setting_str("video.outro.english_color")
OUTRO_ENGLISH_BOLD = setting_bool("video.outro.english_bold")
BASMALAH_ARABIC_TEXT = setting_str("video.basmalah.arabic_text")
BASMALAH_ENGLISH_TEXT = setting_str("video.basmalah.english_text")
INTRO_ARABIC_FONT = setting_str("video.intro.arabic_font")
INTRO_ARABIC_COLOR = setting_str("video.intro.arabic_color")
INTRO_ARABIC_Y = setting_int("video.intro.arabic_y")
INTRO_ARABIC_BOX_WIDTH = setting_int("video.intro.arabic_box_width")
OUTRO_ARABIC_FONT = setting_str("video.outro.arabic_font")
OUTRO_ARABIC_COLOR = setting_str("video.outro.arabic_color")
OUTRO_ARABIC_Y = setting_int("video.outro.arabic_y")
OUTRO_ARABIC_BOX_WIDTH = setting_int("video.outro.arabic_box_width")
OUTRO_FADE_OUT_SECONDS = setting_float("video.outro.fade_out_seconds")
THUMBNAIL_USE_GRADIENT = setting_bool("thumbnail.use_gradient")
THUMBNAIL_GRADIENT_TYPE = setting_str("thumbnail.gradient_type")
THUMBNAIL_GRADIENT_STOPS = setting_list("thumbnail.gradient_stops")
THUMBNAIL_BACKGROUND_COLOR = setting_str("thumbnail.background_color")
THUMBNAIL_ARABIC_FONT = setting_str("thumbnail.arabic_font")
THUMBNAIL_ENGLISH_FONT = setting_str("thumbnail.english_font")
THUMBNAIL_FONT_COLOR = setting_str("thumbnail.font_color")
THUMBNAIL_FONT_ALPHA = setting_float("thumbnail.font_alpha")
THUMBNAIL_ARABIC_Y = setting_float("thumbnail.arabic_y")
THUMBNAIL_ENGLISH_Y = setting_float("thumbnail.english_y")
THUMBNAIL_TEXT_BOX_WIDTH = setting_float("thumbnail.text_box_width")
AUDIO_SOURCE = setting_str("audio.source")
AUDIO_RECITATION_ID = setting_int("audio.recitation_id")
AUDIO_RECITATION_PAGE_URL = setting_str("audio.recitation_page_url")
EXPECTED_SHEIKH_NAME = setting_str("audio.expected_sheikh_name")
EXPECTED_AUDIO_SOURCE_TERMS = [str(term) for term in setting_list("audio.expected_source_terms")]
AUDIO_SEGMENTS_URL_TEMPLATE = setting_str("audio.segments_url_template")
AUDIO_SOURCE_DISPLAY_NAME = setting_str("audio.display_name")
AUDIO_SOURCE_DISPLAY_URL = setting_str("audio.display_url")
SURAHQURAN_DISPLAY_URL = setting_str("surahquran.display_url")
INTRO_CREDIT_ARABIC_TEXT = setting_str("video.intro.arabic_text")
INTRO_CREDIT_TEXT = setting_str("video.intro.english_text")
OUTRO_CREDIT_ARABIC_TEXT = setting_str("video.outro.arabic_text")
OUTRO_CREDIT_TEXT = setting_str("video.outro.english_text")
RUN_ID = datetime.now().strftime("%Y%m%d-%H%M%S")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log(event: str, level: str = "INFO", **fields: object) -> None:
    event_fields = dict(fields)
    section = "image_generation" if event.startswith("THUMBNAIL_") else "video_generation"
    section_status = None
    if event == "RUN_START":
        section_status = "RUNNING"
    elif event == "THUMBNAIL_START":
        section_status = "RUNNING"
    elif event == "THUMBNAIL_OK":
        section_status = "DONE"
    elif event == "RUN_END":
        section_status = str(event_fields.pop("status", "DONE"))
    elif level == "ERROR" or event in {"ERROR", "FATAL"}:
        section_status = "ERROR"
    if "status" in event_fields:
        event_fields["item_status"] = event_fields.pop("status")
    append_event(LOG_FILE, section, event, level=level, run_id=RUN_ID, status=section_status, **event_fields)


def normalize_search_text(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\w\s\u0600-\u06FF]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def ffmpeg_filter_escape(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def pango_fontconfig_file() -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "surah_automation"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config = tmp_dir / "fonts.conf"
    config.write_text(
        f'''<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">
<fontconfig>
  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>
  <dir>{html.escape(str(FONTS_DIR))}</dir>
</fontconfig>
''',
        encoding="utf-8",
    )
    return config


def pango_environment() -> dict[str, str]:
    env = dict(os.environ)
    env["FONTCONFIG_FILE"] = str(pango_fontconfig_file())
    return env


def font_family_in_dir(font_name: str) -> str | None:
    if not FONTS_DIR.exists():
        return None
    requested = normalize_search_text(font_name)
    for font_file in sorted(FONTS_DIR.glob("*.[Tt][Tt][Ff]"), key=lambda path: path.name.lower()):
        result = subprocess.run(["fc-scan", "--format=%{family}\n", str(font_file)], capture_output=True, text=True)
        if result.returncode != 0:
            continue
        families = [normalize_search_text(family) for line in result.stdout.splitlines() for family in line.split(",")]
        if requested in families:
            return str(font_file)
    return None


def validate_font(font_name: str) -> None:
    font_file = font_family_in_dir(font_name)
    if font_file:
        log("FONT_VALIDATE_OK", requested=font_name, resolved=font_file, fonts_dir=FONTS_DIR)
        return
    result = subprocess.run(["fc-match", font_name], capture_output=True, text=True)
    if result.returncode != 0:
        message = result.stderr.strip()
        if STRICT_FONT_VALIDATION:
            raise RuntimeError(f"Font validation failed for {font_name}: {message}")
        log("FONT_VALIDATE_ERROR", level="WARNING", requested=font_name, message=message, fonts_dir=FONTS_DIR)
        return
    resolved = result.stdout.strip()
    if normalize_search_text(font_name) not in normalize_search_text(resolved):
        if STRICT_FONT_VALIDATION:
            raise RuntimeError(f"Font {font_name} was not found in {FONTS_DIR}; fc-match resolved {resolved}")
        log("FONT_FALLBACK_WARNING", level="WARNING", requested=font_name, resolved=resolved, fonts_dir=FONTS_DIR)
    else:
        log("FONT_VALIDATE_OK", requested=font_name, resolved=resolved, fonts_dir=FONTS_DIR)


def validate_fonts() -> None:
    for font_name in sorted({FONT_STANDARD, FONT_ARABIC_AYAH, FONT_ENGLISH}):
        validate_font(font_name)


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
    statuses: dict[int, str] = {}
    if not TODO_FILE.exists():
        return statuses
    for line in TODO_FILE.read_text(encoding="utf-8").splitlines():
        markdown_match = re.match(r"^\s*-\s*\[(?P<symbol>[ x?!~\-])\]\s*(?P<number>\d+)\s*-", line)
        if markdown_match:
            symbol_statuses = {" ": "TODO", "?": "WAITING", "x": "DONE", "!": "ERROR", "~": "STARTED", "-": "IGNORE"}
            statuses[int(markdown_match.group("number"))] = symbol_statuses[markdown_match.group("symbol")]
            continue
        match = re.match(r"^\s*(\d+)\s*-.*?\[(\w+)\]\s*$", line)
        if match:
            legacy = match.group(2)
            statuses[int(match.group(1))] = {"SKIP": "IGNORE", "DOING": "STARTED", "START_NOW": "STARTED"}.get(legacy, legacy)
    return statuses


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


def existing_video_for(number: int, arabic_name: str) -> Path | None:
    candidates = [
        EXPORTED_DIR / f"{number}.mp4",
        EXPORTED_DIR / f"{number:03d}.mp4",
        EXPORTED_DIR / f"{arabic_name}.mp4",
        EXPORTED_DIR / f"{arabic_name} .mp4",
        LIVE_EXPORTED_DIR / f"{number}.mp4",
        LIVE_EXPORTED_DIR / f"{number:03d}.mp4",
        LIVE_EXPORTED_DIR / f"{arabic_name}.mp4",
        LIVE_EXPORTED_DIR / f"{arabic_name} .mp4",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            return path
    return None


def build_statuses(surahs: dict[int, str], mp3s: dict[int, Path], old_statuses: dict[int, str]) -> dict[int, str]:
    statuses: dict[int, str] = {}
    use_existing_queue = bool(old_statuses)
    for number in sorted(surahs, reverse=True):
        arabic_name = surahs[number]
        previous = old_statuses.get(number)
        if number in SKIP_SURAH_NUMBERS:
            statuses[number] = "IGNORE"
        elif existing_video_for(number, arabic_name):
            statuses[number] = "DONE"
        elif previous in {"ERROR", "IGNORE"}:
            statuses[number] = previous
        elif use_existing_queue and previous in {"TODO", "WAITING", "STARTED"}:
            statuses[number] = "TODO" if previous == "STARTED" else previous
        elif number not in mp3s:
            statuses[number] = "NO_MP3"
        else:
            statuses[number] = "WAITING"

    # Enforce descending sequence across available MP3s: lower numbers wait until higher available numbers are done.
    if use_existing_queue:
        return statuses
    blocked = False
    for number in sorted(mp3s, reverse=True):
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


def write_todo(surahs: dict[int, str], statuses: dict[int, str]) -> None:
    sections = [
        ("STARTED", "~"),
        ("TODO", " "),
        ("WAITING", "?"),
        ("DONE", "x"),
        ("ERROR", "!"),
        ("IGNORE", "-"),
    ]
    lines = ["# TODO", ""]
    for status, symbol in sections:
        lines.append(f"## {status}")
        entries = [number for number in sorted(surahs, reverse=True) if statuses.get(number) == status]
        if entries:
            lines.extend(f"- [{symbol}] {number} - {surahs[number]}" for number in entries)
        lines.append("")
    TODO_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def todo_count(statuses: dict[int, str]) -> int:
    return sum(1 for status in statuses.values() if status == "TODO")


def next_todo(statuses: dict[int, str], mp3s: dict[int, Path]) -> int | None:
    for number in sorted(mp3s, reverse=True):
        if statuses.get(number) in {"STARTED", "TODO"}:
            return number
    return None


def fetch_page(number: int) -> str:
    url = f"https://surahquran.com/Surah-translation/Arabic-language-2-surah-{number}.html"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read().decode("utf-8", errors="replace")
            log("FETCH_OK", surah=number, url=url, bytes=len(body), attempt=attempt)
            return body
        except Exception as exc:
            last_error = exc
            log("FETCH_RETRY", level="WARNING", surah=number, url=url, attempt=attempt, message=str(exc))
    raise RuntimeError(f"Failed to fetch surah page after retries: {last_error}")


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value))).strip()


def extract_surah_data(number: int, page: str) -> tuple[str, list[str], list[str]]:
    text = html.unescape(page)
    name_match = re.search(r"\|\s*سورة\s+[^|<]+\|\s*([^\-<|]+?)\s*-\s*عدد آياتها", text)
    if not name_match:
        name_match = re.search(r"معنى السورة بالإنجليزية:\s*([^.<]+)", text)
    english_name = strip_tags(name_match.group(1)) if name_match else f"Surah {number}"

    arabic_ayat: list[str] = []
    translations: list[str] = []
    pattern = re.compile(
        r"(?P<ayah>[\u0600-\u06FF\u0670\u06D6-\u06ED\sًٌٍَُِّْٰـ]+)\s*\((?P<num>\d+)\)\s*<[^>]*>\s*<a[^>]+english-aya-\d+-sora-"
        + str(number)
        + r"\.html[^>]*>(?P<translation>.*?)</a>",
        re.DOTALL,
    )
    for match in pattern.finditer(text):
        ayah_number = int(match.group("num"))
        ayah = strip_tags(match.group("ayah"))
        translation = strip_tags(match.group("translation"))
        if ayah and translation and ayah_number == len(arabic_ayat) + 1:
            arabic_ayat.append(f"{ayah} ({ayah_number})")
            translations.append(translation)

    if not arabic_ayat:
        raise RuntimeError(f"Could not extract ayat for surah {number}")
    log("EXTRACT_OK", surah=number, english_name=english_name, ayat=len(arabic_ayat), translations=len(translations))
    return english_name, arabic_ayat, translations


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


def fetch_json(url: str) -> dict[str, object]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def validate_audio_source_page() -> None:
    page = fetch_text(AUDIO_RECITATION_PAGE_URL)
    normalized_page = normalize_search_text(page)
    normalized_sheikh = normalize_search_text(EXPECTED_SHEIKH_NAME)
    missing_terms = [term for term in EXPECTED_AUDIO_SOURCE_TERMS if normalize_search_text(term) not in normalized_page]
    if normalized_sheikh not in normalized_page:
        log(
            "AUDIO_SOURCE_VALIDATE_ERROR",
            level="ERROR",
            expected_sheikh=EXPECTED_SHEIKH_NAME,
            normalized_expected_sheikh=normalized_sheikh,
            recitation_page_url=AUDIO_RECITATION_PAGE_URL,
            message="Expected Sheikh name was not found in audio source page after normalized matching",
        )
        raise RuntimeError(f"Audio source Sheikh validation failed for expected Sheikh: {EXPECTED_SHEIKH_NAME}")
    if missing_terms:
        log(
            "AUDIO_SOURCE_VALIDATE_ERROR",
            level="ERROR",
            expected_terms=EXPECTED_AUDIO_SOURCE_TERMS,
            missing_terms=missing_terms,
            recitation_page_url=AUDIO_RECITATION_PAGE_URL,
            message="Audio source page is missing expected recitation terms",
        )
        raise RuntimeError(f"Audio source validation failed, missing terms: {', '.join(missing_terms)}")
    log(
        "AUDIO_SOURCE_VALIDATE_OK",
        expected_sheikh=EXPECTED_SHEIKH_NAME,
        normalized_expected_sheikh=normalized_sheikh,
        expected_terms=EXPECTED_AUDIO_SOURCE_TERMS,
        recitation_id=AUDIO_RECITATION_ID,
        recitation_page_url=AUDIO_RECITATION_PAGE_URL,
    )


def download_file(url: str, out: Path) -> None:
    if out.exists() and out.stat().st_size > 0:
        log("DOWNLOAD_SKIP", file=out, reason="already exists")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as response, tmp.open("wb") as f:
        shutil.copyfileobj(response, f)
    tmp.replace(out)
    log("DOWNLOAD_OK", file=out, bytes=out.stat().st_size, url=url)


def audio_segments_url(number: int) -> str:
    return AUDIO_SEGMENTS_URL_TEMPLATE.format(recitation_id=AUDIO_RECITATION_ID, surah_number=number)


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


def fetch_audio_segments(number: int) -> dict[str, object]:
    url = audio_segments_url(number)
    data = fetch_json(url)
    segments = data.get("segments")
    if not isinstance(segments, dict):
        raise RuntimeError(f"Audio source response missing segments for surah {number}")
    log("AUDIO_SEGMENTS_FETCH_OK", surah=number, url=url, ayat=len(segments), recitation_id=AUDIO_RECITATION_ID)
    return segments


def prepare_qul_audio(number: int, ayah_count: int) -> tuple[Path, tuple[float, float] | None, list[tuple[float, float]]]:
    segments = fetch_audio_segments(number)

    ayah_dir = SOUND_DIR / str(number)
    combined_dir = SOUND_DIR / "Qul"
    combined_dir.mkdir(parents=True, exist_ok=True)
    combined = combined_dir / f"{number:03d}.mp3"
    ayah_files: list[Path] = []
    clip_windows: list[tuple[float, float]] = []
    basmalah_timing: tuple[float, float] | None = None

    if number not in {1, 9}:
        basmalah_segments = fetch_audio_segments(1)
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

    list_file = Path(tempfile.gettempdir()) / "surah_automation" / f"{number}_concat.txt"
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


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis -= 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def ass_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def normalize_arabic(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def ass_text(style: str, value: str) -> str:
    if style == "Arabic":
        return "\u202b" + normalize_arabic(value) + "\u202c"
    return value


def ass_position(style: str) -> str:
    positions = {
        "Title": (LAYOUT_CENTER_X, LAYOUT_TITLE_Y),
        "Riwayah": (LAYOUT_CENTER_X, LAYOUT_RIWAYAH_Y),
        "Arabic": (LAYOUT_CENTER_X, LAYOUT_ARABIC_Y),
        "English": (LAYOUT_CENTER_X, LAYOUT_ENGLISH_Y),
        "Credit": (LAYOUT_CENTER_X, LAYOUT_CREDIT_Y),
        "Outro": (LAYOUT_CENTER_X, LAYOUT_CREDIT_Y),
    }
    x, y = positions.get(style, (LAYOUT_CENTER_X, LAYOUT_CREDIT_Y))
    return f"\\an5\\pos({x},{y})"


def wrap_arabic_ayah(value: str, width: int) -> str:
    value = normalize_arabic(value)
    match = re.search(r"\s*\((\d+)\)\s*$", value)
    if not match:
        return wrap_text(value, width)
    number = f"({match.group(1)})"
    if ARABIC_AYAH_NUMBER_BIDI == "ltr_embedding":
        number = "\u202a" + number + "\u202c"
    elif ARABIC_AYAH_NUMBER_BIDI == "ltr_isolate":
        number = "\u2066" + number + "\u2069"
    body = value[: match.start()].strip()
    words = body.split()
    if not words:
        return number
    final_group = f"{words[-1]}\u00A0{number}"
    tokens = words[:-1] + [final_group]
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip()
        if current and len(candidate) > width:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines)


def split_arabic_ayah_number(value: str, width: int) -> tuple[str, str | None]:
    match = re.search(r"\s*\((\d+)\)\s*$", value)
    if not match:
        return wrap_text(value, width), None
    body = value[: match.start()].strip()
    return wrap_text(body, width), f"({match.group(1)})"


def estimated_arabic_line_width(value: str) -> int:
    text = re.sub(r"[\u064B-\u065F\u0670]", "", value)
    width = 0
    for char in text:
        if char.isspace():
            width += 18
        elif re.match(r"[\u0600-\u06FF]", char):
            width += 38
        else:
            width += 28
    return width


def wrap_text(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if re.search(r"[A-Za-z]", value):
        return "\n".join(textwrap.wrap(value, width=width, break_long_words=False))
    parts = []
    current = ""
    for word in value.split():
        if len(current) + len(word) + 1 > width and current:
            parts.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        parts.append(current)
    if len(parts) >= 2 and re.fullmatch(r"[\u200e\u2066\u2069]*\(\d+\)[\u200e\u2066\u2069]*", parts[-1].strip()):
        parts[-2] = f"{parts[-2]} {parts[-1]}"
        parts.pop()
    return "\n".join(parts)


def ass_dialogue(start: float, end: float, style: str, text_value: str, fade_in: float, fade_out: float = FADE_OUT_SECONDS) -> str:
    fade = f"{{{ass_position(style)}\\fad({int(fade_in * 1000)},{int(fade_out * 1000)})}}"
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{fade}{ass_escape(ass_text(style, text_value))}"


def ass_ayah_number_dialogue(start: float, end: float, wrapped_arabic: str, number: str, fade_in: float) -> str:
    arabic_lines = wrapped_arabic.split("\n")
    final_line = arabic_lines[-1] if arabic_lines else ""
    final_line_y = LAYOUT_ARABIC_Y + int(round((len(arabic_lines) - 1) * ARABIC_AYAH_LINE_HEIGHT / 2))
    final_line_left = LAYOUT_CENTER_X - int(round(estimated_arabic_line_width(final_line) / 2))
    x = final_line_left - ARABIC_AYAH_NUMBER_GAP
    fade = f"{{\\an6\\pos({x},{final_line_y})\\fad({int(fade_in * 1000)},{int(FADE_OUT_SECONDS * 1000)})}}"
    return f"Dialogue: 1,{ass_time(start)},{ass_time(end)},ArabicNumber,,0,0,0,,{fade}{ass_escape(number)}"


def timeline(recitation_duration: float, ayah_count: int) -> tuple[float, float, float, float, float, float, float]:
    recitation_start = INTRO_CREDIT_SECONDS
    title_end = recitation_start + max(8.0, recitation_duration - 2.0)
    intro_end = recitation_start + min(7.2, max(4.0, recitation_duration * 0.12))
    usable_start = intro_end
    usable_end = recitation_start + max(usable_start - recitation_start + 1.0, recitation_duration - 4.5)
    per_ayah = max(3.0, (usable_end - usable_start) / max(1, ayah_count))
    outro_start = recitation_start + recitation_duration
    total_duration = outro_start + OUTRO_CREDIT_SECONDS
    return title_end, intro_end, usable_start, usable_end, per_ayah, outro_start, total_duration


def fallback_ayah_timings(recitation_duration: float, ayah_count: int) -> list[tuple[float, float]]:
    _title_end, intro_end, usable_start, usable_end, per_ayah, _outro_start, _total = timeline(recitation_duration, ayah_count)
    timings = []
    for index in range(ayah_count):
        start = usable_start + index * per_ayah
        end = usable_start + (index + 1) * per_ayah
        if index == ayah_count - 1:
            end = usable_end
        timings.append((start, end))
    return timings


def write_ass(number: int, arabic_name: str, english_name: str, ayat: list[str], translations: list[str], recitation_duration: float, basmalah_timing: tuple[float, float] | None, ayah_timings: list[tuple[float, float]]) -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / "surah_automation"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    ass_file = tmp_dir / f"{number}.ass"
    title_end, intro_end, _usable_start, _usable_end, _per_ayah, outro_start, total_duration = timeline(recitation_duration, len(ayat))
    if ayah_timings:
        title_end = ayah_timings[-1][1]

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Title,{FONT_STANDARD},{TITLE_SIZE},{ass_color(TITLE_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(TITLE_BOLD)},0,0,0,100,100,0,0,1,0,0,8,40,40,24,1",
        f"Style: Riwayah,{FONT_STANDARD},{RIWAYAH_SIZE},{ass_color(RIWAYAH_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(RIWAYAH_BOLD)},0,0,0,100,100,0,0,1,0,0,8,40,40,145,1",
        f"Style: Arabic,{FONT_ARABIC_AYAH},{setting_int('video.ayah_arabic.ass_fallback_size')},{ass_color(PANGO_ARABIC_COLOR)},&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1",
        f"Style: English,{FONT_ENGLISH},{ENGLISH_SIZE},{ass_color(ENGLISH_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,2,90,90,170,1",
        f"Style: Credit,{FONT_STANDARD},{CREDIT_SIZE},{ass_color(CREDIT_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(INTRO_ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,5,80,80,0,1",
        f"Style: Outro,{FONT_STANDARD},{OUTRO_SIZE},{ass_color(OUTRO_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(OUTRO_ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,5,80,80,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ass_dialogue(0.0, INTRO_CREDIT_SECONDS, "Credit", INTRO_CREDIT_TEXT, FADE_CREDIT_SECONDS),
        ass_dialogue(INTRO_CREDIT_SECONDS + 0.4, title_end, "Title", f"سورة {arabic_name}", FADE_TITLE_SECONDS),
        ass_dialogue(INTRO_CREDIT_SECONDS + 0.4, title_end, "Riwayah", "برواية:حفص عن عاصم", FADE_RIWAYAH_SECONDS),
    ]
    if basmalah_timing is not None:
        start, end = basmalah_timing
        if not PANGO_ARABIC_OVERLAY:
            lines.append(ass_dialogue(start, end, "Arabic", BASMALAH_ARABIC_TEXT, FADE_BASMALAH_SECONDS))
        lines.append(ass_dialogue(start, end, "English", BASMALAH_ENGLISH_TEXT, FADE_BASMALAH_SECONDS))
    for index, arabic in enumerate(ayat):
        start, end = ayah_timings[index]
        english = translations[index] if index < len(translations) else ""
        if not PANGO_ARABIC_OVERLAY:
            lines.append(ass_dialogue(start, end, "Arabic", wrap_arabic_ayah(arabic, ARABIC_AYAH_WRAP_WIDTH), FADE_AYAH_SECONDS))
        if english:
            lines.append(ass_dialogue(start, end, "English", wrap_text(english, ENGLISH_WRAP_WIDTH), FADE_AYAH_SECONDS))

    lines.append(ass_dialogue(outro_start, total_duration, "Outro", OUTRO_CREDIT_TEXT, FADE_CREDIT_SECONDS, 0.0))
    ass_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("ASS_OK", surah=number, file=ass_file)
    return ass_file


def mlt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        secs += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def qrich_html(text_value: str, font_size: int, color: str) -> str:
    paragraphs = []
    for line in text_value.split("\n"):
        safe = html.escape(line)
        direction = "rtl" if re.search(r"[\u0600-\u06FF]", line) else "ltr"
        paragraphs.append(
            f"&lt;p dir=&quot;{direction}&quot; align=&quot;center&quot; style=&quot; margin-top:0px; margin-bottom:0px; margin-left:0px; margin-right:0px; -qt-block-indent:0; text-indent:0px;&quot;&gt;"
            f"&lt;span style=&quot; font-family:'Verdana'; font-size:{font_size}pt; color:{color};&quot;&gt;{safe}&lt;/span&gt;&lt;/p&gt;"
        )
    return (
        "&lt;!DOCTYPE HTML PUBLIC &quot;-//W3C//DTD HTML 4.0//EN&quot; &quot;http://www.w3.org/TR/REC-html40/strict.dtd&quot;&gt;\n"
        "&lt;html&gt;&lt;head&gt;&lt;meta name=&quot;qrichtext&quot; content=&quot;1&quot; /&gt;&lt;meta charset=&quot;utf-8&quot; /&gt;&lt;style type=&quot;text/css&quot;&gt;\n"
        "p, li { white-space: pre-wrap; }\n"
        "hr { height: 1px; border-width: 0; }\n"
        "&lt;/style&gt;&lt;/head&gt;&lt;body style=&quot; font-family:'Segoe UI'; font-size:9pt; font-weight:400; font-style:normal;&quot;&gt;\n"
        + "\n".join(paragraphs)
        + "&lt;/body&gt;&lt;/html&gt;"
    )


def text_producer_xml(
    producer_id: str,
    filter_id: int,
    caption: str,
    text_value: str,
    geometry: str,
    font_size: int,
    color: str,
    clip_length: float,
    fade_in_seconds: float,
    fade_out_seconds: float = FADE_OUT_SECONDS,
) -> str:
    clip = mlt_time(clip_length)
    fade_in = min(fade_in_seconds, max(0.0, clip_length * 0.75))
    fade_out = min(fade_out_seconds, max(0.0, clip_length - fade_in))
    fade_out_start = max(fade_in, clip_length - fade_out)
    fade_out_end = clip_length
    html_doc = qrich_html(text_value, font_size, color)
    return f'''  <producer id="{producer_id}" in="00:00:00.000" out="{clip}">
    <property name="length">{clip}</property>
    <property name="eof">pause</property>
    <property name="resource">#00000000</property>
    <property name="aspect_ratio">1</property>
    <property name="mlt_service">color</property>
    <property name="mlt_image_format">rgba</property>
    <property name="shotcut:caption">{xml_escape(caption)}</property>
    <property name="shotcut:detail">transparent</property>
    <property name="ignore_points">0</property>
    <property name="meta.shotcut.vui">1</property>
    <property name="xml">was here</property>
    <property name="seekable">1</property>
    <filter id="filter{filter_id}" out="{clip}">
      <property name="argument">text</property>
      <property name="geometry">{geometry}</property>
      <property name="family">Verdana</property>
      <property name="size">80</property>
      <property name="weight">7000</property>
      <property name="style">normal</property>
      <property name="fgcolour">#ffffffff</property>
      <property name="bgcolour">#00000000</property>
      <property name="olcolour">#00000000</property>
      <property name="pad">0</property>
      <property name="halign">center</property>
      <property name="valign">top</property>
      <property name="outline">0</property>
      <property name="pixel_ratio">1</property>
      <property name="opacity">1</property>
      <property name="mlt_service">qtext</property>
      <property name="shotcut:filter">richText</property>
      <property name="html">{html_doc}</property>
      <property name="shotcut:usePointSize">1</property>
      <property name="shotcut:pointSize">60</property>
      <property name="disable">0</property>
    </filter>
    <filter id="filter{filter_id + 1}" out="{clip}">
      <property name="start">1</property>
      <property name="level">1</property>
      <property name="mlt_service">brightness</property>
      <property name="disable">0</property>
    </filter>
    <filter id="filter{filter_id + 2}" out="{clip}">
      <property name="start">1</property>
      <property name="level">1</property>
      <property name="mlt_service">brightness</property>
      <property name="shotcut:filter">brightnessOpacity</property>
      <property name="alpha">00:00:00.000=0;{mlt_time(fade_in)}=1;{mlt_time(fade_out_start)}=1;{mlt_time(fade_out_end)}=0</property>
      <property name="opacity">00:00:00.000=0;{mlt_time(fade_in)}=1;{mlt_time(fade_out_start)}=1;{mlt_time(fade_out_end)}=0</property>
      <property name="shotcut:animIn">0</property>
      <property name="shotcut:animOut">0</property>
    </filter>
  </producer>'''


def playlist_xml(playlist_id: str, name: str, entries: list[tuple[str, float, float]], audio: bool = False) -> str:
    lines = [f'  <playlist id="{playlist_id}">']
    lines.append(f'    <property name="shotcut:{"audio" if audio else "video"}">1</property>')
    lines.append(f'    <property name="shotcut:name">{name}</property>')
    cursor = 0.0
    for producer_id, start, end in sorted(entries, key=lambda item: item[1]):
        if start > cursor:
            lines.append(f'    <blank length="{mlt_time(start - cursor)}"/>')
        lines.append(f'    <entry producer="{producer_id}" in="00:00:00.000" out="{mlt_time(end - start)}"/>')
        cursor = end
    lines.append("  </playlist>")
    return "\n".join(lines)


def generate_mlt(number: int, arabic_name: str, english_name: str, mp3: Path, recitation_duration: float, basmalah_timing: tuple[float, float] | None, ayat: list[str], translations: list[str], ayah_timings: list[tuple[float, float]]) -> Path:
    out = CHANNEL_DIR / f"{number}.mlt"
    title_end, intro_end, _usable_start, _usable_end, _per_ayah, outro_start, total_duration = timeline(recitation_duration, len(ayat))
    total = mlt_time(total_duration)
    audio_total = mlt_time(recitation_duration)
    rel_mp3 = xml_escape(str(mp3.relative_to(CHANNEL_DIR)))
    rel_bg = xml_escape(str(BG_IMAGE.relative_to(CHANNEL_DIR)))

    producers = []
    credit_entries = [("text_intro_credit", 0.0, INTRO_CREDIT_SECONDS), ("text_outro_credit", outro_start, total_duration)]
    title_entries = [("text_title", INTRO_CREDIT_SECONDS + 0.4, title_end)]
    riwayah_entries = [("text_riwayah", INTRO_CREDIT_SECONDS + 0.4, title_end)]
    arabic_entries: list[tuple[str, float, float]] = []
    english_entries: list[tuple[str, float, float]] = []

    producers.append(text_producer_xml("text_intro_credit", 990, "Source credits", INTRO_CREDIT_TEXT, "3 260 1920 560 1", 56, "#08553b", INTRO_CREDIT_SECONDS, FADE_CREDIT_SECONDS))
    producers.append(text_producer_xml("text_title", 1000, f"سورة {arabic_name}", f"سورة {arabic_name}", "11 5 1920 221 1", 72, "#000000", title_end - (INTRO_CREDIT_SECONDS + 0.4), FADE_TITLE_SECONDS))
    producers.append(text_producer_xml("text_riwayah", 1010, "حفص", "برواية:حفص عن عاصم", "3 146 1920 148 1", 50, "#aa0000", title_end - (INTRO_CREDIT_SECONDS + 0.4), FADE_RIWAYAH_SECONDS))
    if basmalah_timing is not None:
        basmalah_start, basmalah_end = basmalah_timing
        arabic_entries.append(("text_basm_ar", basmalah_start, basmalah_end))
        english_entries.append(("text_basm_en", basmalah_start, basmalah_end))
        producers.append(text_producer_xml("text_basm_ar", 1020, "البسملة", "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ", "11 450 1920 169 1", 72, "#08553b", basmalah_end - basmalah_start, FADE_BASMALAH_SECONDS))
        producers.append(text_producer_xml("text_basm_en", 1030, "Bismillah English", "In the name of Allah, the Entirely Merciful, the Especially Merciful.", "4 645 1920 148 1", 45, "#000000", basmalah_end - basmalah_start, FADE_BASMALAH_SECONDS))

    filter_id = 1040
    for index, arabic in enumerate(ayat):
        start, end = ayah_timings[index]
        arabic_id = f"text_ayah_ar_{index + 1}"
        english_id = f"text_ayah_en_{index + 1}"
        arabic_entries.append((arabic_id, start, end))
        english_entries.append((english_id, start, end))
        producers.append(text_producer_xml(arabic_id, filter_id, f"الاية {index + 1}", wrap_text(arabic, 48), "0 453 1920 169 1", 72, "#08553b", end - start, FADE_AYAH_SECONDS))
        filter_id += 10
        english = translations[index] if index < len(translations) else ""
        producers.append(text_producer_xml(english_id, filter_id, f"English ayah {index + 1}", wrap_text(english, 72), "4 645 1920 148 1", 45, "#000000", end - start, FADE_AYAH_SECONDS))
        filter_id += 10
    producers.append(text_producer_xml("text_outro_credit", filter_id, "Prayer", OUTRO_CREDIT_TEXT, "3 414 1920 251 1", 72, "#000000", OUTRO_CREDIT_SECONDS, FADE_CREDIT_SECONDS, 0.0))

    content = f'''<?xml version="1.0" standalone="no"?>
<mlt LC_NUMERIC="C" version="7.33.0" title="Surah automation" producer="main_bin">
  <profile description="HD 1080p 25 fps" width="1920" height="1080" progressive="1" sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="16" display_aspect_den="9" frame_rate_num="25" frame_rate_den="1" colorspace="709"/>
  <producer id="background" in="00:00:00.000" out="{total}">
    <property name="length">{total}</property>
    <property name="eof">pause</property>
    <property name="resource">{rel_bg}</property>
    <property name="ttl">1</property>
    <property name="aspect_ratio">1</property>
    <property name="meta.media.progressive">1</property>
    <property name="seekable">1</property>
    <property name="mlt_service">qimage</property>
    <property name="shotcut_resource">{rel_bg}</property>
    <property name="autolength">0</property>
    <property name="shotcut_sequence">0</property>
    <property name="xml">was here</property>
    <property name="shotcut:caption">BG.png</property>
  </producer>
  <chain id="audio" out="{audio_total}">
    <property name="length">{audio_total}</property>
    <property name="eof">pause</property>
    <property name="resource">{rel_mp3}</property>
    <property name="mlt_service">avformat-novalidate</property>
    <property name="shotcut:skipConvert">1</property>
    <property name="xml">was here</property>
    <property name="shotcut:caption">{xml_escape(mp3.name)}</property>
  </chain>
{chr(10).join(producers)}
  <playlist id="main_bin">
    <entry producer="background" in="00:00:00.000" out="{total}"/>
    <entry producer="audio" in="00:00:00.000" out="{audio_total}"/>
  </playlist>
  <playlist id="playlist0">
    <property name="shotcut:video">1</property>
    <property name="shotcut:name">V1</property>
    <entry producer="background" in="00:00:00.000" out="{total}"/>
  </playlist>
  <playlist id="playlist1">
    <property name="shotcut:audio">1</property>
    <property name="shotcut:name">A1</property>
    <blank length="{mlt_time(INTRO_CREDIT_SECONDS)}"/>
    <entry producer="audio" in="00:00:00.000" out="{audio_total}"/>
  </playlist>
{playlist_xml("playlist2", "Title", title_entries)}
{playlist_xml("playlist3", "Riwayah", riwayah_entries)}
{playlist_xml("playlist4", "Arabic Ayat", arabic_entries)}
{playlist_xml("playlist5", "English Translation", english_entries)}
{playlist_xml("playlist6", "Credits", credit_entries)}
  <tractor id="tractor0" title="Surah automation" in="00:00:00.000" out="{total}">
    <property name="shotcut">1</property>
    <property name="shotcut:projectAudioChannels">2</property>
    <track producer="playlist0"/>
    <track producer="playlist1" hide="video"/>
    <track producer="playlist2"/>
    <track producer="playlist3"/>
    <track producer="playlist4"/>
    <track producer="playlist5"/>
    <track producer="playlist6"/>
  </tractor>
</mlt>
'''
    out.write_text(content, encoding="utf-8")
    log("MLT_OK", surah=number, file=out)
    return out


def pango_helper_binary() -> Path:
    source = SCRIPT_DIR / "pango_arabic_overlay.c"
    binary = Path(tempfile.gettempdir()) / "surah_automation" / "pango_arabic_overlay"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    pkg = subprocess.run(["pkg-config", "--cflags", "--libs", "pangocairo", "cairo", "pango"], capture_output=True, text=True, check=True)
    cmd = ["cc", "-O2", "-Wall", "-Wextra", str(source), "-o", str(binary), *pkg.stdout.split(), "-lm"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])
    log("PANGO_HELPER_OK", binary=binary)
    return binary


def draw_pango_centered(cr: object, text: str, font: str, y: float, width: float) -> None:
    import gi

    gi.require_foreign("cairo")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(font))
    layout.set_width(int(width * Pango.SCALE))
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_auto_dir(True)
    layout.set_text(text, -1)
    _text_width, text_height = layout.get_pixel_size()
    cr.move_to((1280 - width) / 2, y - text_height / 2)
    PangoCairo.show_layout(cr, layout)


def draw_thumbnail_background(cr: object) -> None:
    import cairo

    if not THUMBNAIL_USE_GRADIENT:
        cr.set_source_rgb(*color_rgb(THUMBNAIL_BACKGROUND_COLOR))
        cr.rectangle(0, 0, 1280, 720)
        cr.fill()
        return

    if THUMBNAIL_GRADIENT_TYPE == "radial":
        cr.save()
        cr.translate(640, 720)
        cr.scale(1.8, 1.0)
        gradient = cairo.RadialGradient(0, 0, 0, 0, 0, 720)
        cr.rectangle(-640 / 1.8, -720, 1280 / 1.8, 720)
    elif THUMBNAIL_GRADIENT_TYPE == "linear":
        gradient = cairo.LinearGradient(0, 0, 0, 720)
        cr.rectangle(0, 0, 1280, 720)
    else:
        raise RuntimeError(f"Unsupported thumbnail gradient type: {THUMBNAIL_GRADIENT_TYPE}")
    for stop in THUMBNAIL_GRADIENT_STOPS:
        if not isinstance(stop, dict):
            continue
        offset = float(stop.get("offset", 0.0))
        color = str(stop.get("color", THUMBNAIL_BACKGROUND_COLOR))
        gradient.add_color_stop_rgb(offset, *color_rgb(color))
    if THUMBNAIL_GRADIENT_TYPE == "linear":
        cr.rectangle(0, 0, 1280, 720)
    cr.rectangle(0, 0, 1280, 720)
    cr.set_source(gradient)
    cr.fill()
    if THUMBNAIL_GRADIENT_TYPE == "radial":
        cr.restore()


def generate_thumbnail(number: int, arabic_name: str, english_name: str) -> Path:
    import cairo

    THUMBNAIL_DIR.mkdir(parents=True, exist_ok=True)
    out = THUMBNAIL_DIR / f"{number}.png"
    arabic_title = f"سورة {arabic_name}"
    log("THUMBNAIL_START", surah=number, file=out, arabic=arabic_title, english=english_name)
    os.environ.update(pango_environment())
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1280, 720)
    cr = cairo.Context(surface)
    draw_thumbnail_background(cr)
    cr.set_source_rgba(*color_rgb(THUMBNAIL_FONT_COLOR), THUMBNAIL_FONT_ALPHA)
    draw_pango_centered(cr, arabic_title, THUMBNAIL_ARABIC_FONT, THUMBNAIL_ARABIC_Y, THUMBNAIL_TEXT_BOX_WIDTH)
    draw_pango_centered(cr, english_name, THUMBNAIL_ENGLISH_FONT, THUMBNAIL_ENGLISH_Y, THUMBNAIL_TEXT_BOX_WIDTH)
    surface.write_to_png(str(out))
    if not out.exists() or out.stat().st_size <= 0:
        raise RuntimeError(f"Thumbnail missing or empty: {out}")
    log("THUMBNAIL_OK", surah=number, file=out, size=out.stat().st_size)
    return out


def pango_text(value: str) -> str:
    return wrap_arabic_ayah(value, ARABIC_AYAH_WRAP_WIDTH)


def write_pango_overlay_spec(number: int, ayat: list[str], basmalah_timing: tuple[float, float] | None, ayah_timings: list[tuple[float, float]]) -> Path | None:
    if not PANGO_ARABIC_OVERLAY:
        return None
    tmp_dir = Path(tempfile.gettempdir()) / "surah_automation"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    spec = tmp_dir / f"{number}_pango_arabic.tsv"
    rows: list[str] = []

    def add_cue(
        start: float,
        end: float,
        fade_in: float,
        text: str,
        center_y: int = LAYOUT_ARABIC_Y,
        font: str = PANGO_ARABIC_FONT,
        box_width: int = PANGO_ARABIC_BOX_WIDTH,
        color: str = PANGO_ARABIC_COLOR,
        fade_out: float = FADE_OUT_SECONDS,
    ) -> None:
        adjusted_start = max(0.0, start + PANGO_SYNC_OFFSET_SECONDS)
        adjusted_end = max(adjusted_start + (1 / PANGO_OVERLAY_FPS), end + PANGO_SYNC_OFFSET_SECONDS)
        start_frame = max(0, int(round(adjusted_start * PANGO_OVERLAY_FPS)))
        end_frame = max(start_frame + 1, int(round(adjusted_end * PANGO_OVERLAY_FPS)))
        fade_in_frames = max(0, int(round(fade_in * PANGO_OVERLAY_FPS)))
        fade_out_frames = max(0, int(round(fade_out * PANGO_OVERLAY_FPS)))
        encoded_font = base64.b64encode(font.encode("utf-8")).decode("ascii")
        encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
        rows.append(
            f"{start_frame}\t{end_frame}\t{fade_in_frames}\t{fade_out_frames}\t"
            f"{LAYOUT_CENTER_X}\t{center_y}\t{box_width}\t{color.lstrip('#')}\t{encoded_font}\t{encoded}"
        )

    add_cue(0.0, INTRO_CREDIT_SECONDS, FADE_CREDIT_SECONDS, INTRO_CREDIT_ARABIC_TEXT, INTRO_ARABIC_Y, INTRO_ARABIC_FONT, INTRO_ARABIC_BOX_WIDTH, INTRO_ARABIC_COLOR, setting_float("video.intro.fade_out_seconds"))
    if basmalah_timing is not None:
        start, end = basmalah_timing
        add_cue(start, end, FADE_BASMALAH_SECONDS, BASMALAH_ARABIC_TEXT)
    for index, arabic in enumerate(ayat):
        start, end = ayah_timings[index]
        add_cue(start, end, FADE_AYAH_SECONDS, pango_text(arabic))
    _title_end, _intro_end, _usable_start, _usable_end, _per_ayah, outro_start, total_duration = timeline(0, len(ayat))
    if ayah_timings:
        outro_start = ayah_timings[-1][1]
    add_cue(outro_start, outro_start + OUTRO_CREDIT_SECONDS, setting_float("video.outro.fade_in_seconds"), OUTRO_CREDIT_ARABIC_TEXT, OUTRO_ARABIC_Y, OUTRO_ARABIC_FONT, OUTRO_ARABIC_BOX_WIDTH, OUTRO_ARABIC_COLOR, OUTRO_FADE_OUT_SECONDS)
    spec.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log("PANGO_SPEC_OK", surah=number, file=spec, cues=len(rows))
    return spec


def render_mp4(number: int, mp3: Path, ass_file: Path, total_duration: float, pango_spec: Path | None = None) -> Path:
    out = EXPORTED_DIR / f"{number}.mp4"
    if not BG_IMAGE.exists():
        raise RuntimeError(f"Missing background image: {BG_IMAGE}")
    total_frames = max(1, int(round(total_duration * PANGO_OVERLAY_FPS)))
    if pango_spec is not None:
        helper = pango_helper_binary()
        overlay_cmd = [
            str(helper),
            str(pango_spec),
            "1920",
            "1080",
            str(total_frames),
            str(PANGO_ARABIC_LINE_SPACING),
        ]
        filter_graph = (
            f"[0:v]scale=1920:1080,setsar=1,subtitles='{ffmpeg_filter_escape(ass_file)}':fontsdir='{ffmpeg_filter_escape(FONTS_DIR)}'[sub];"
            "[sub][2:v]overlay=0:0:alpha=premultiplied:format=auto[v]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(BG_IMAGE),
            "-i",
            str(mp3),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgra",
            "-s",
            "1920x1080",
            "-r",
            str(PANGO_OVERLAY_FPS),
            "-i",
            "pipe:0",
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-map",
            "1:a",
            "-af",
            f"adelay={int(round(INTRO_CREDIT_SECONDS * 1000))}:all=1,apad",
            "-c:v",
            "libx264",
            "-tune",
            "stillimage",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            f"{total_duration:.3f}",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
        log("EXPORT_START", surah=number, file=out, audio_delay_seconds=INTRO_CREDIT_SECONDS, total_duration=total_duration, pango_overlay=True)
        overlay = subprocess.Popen(overlay_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=pango_environment())
        assert overlay.stdout is not None
        result = subprocess.run(cmd, stdin=overlay.stdout, capture_output=True, text=False)
        overlay.stdout.close()
        _overlay_stdout, overlay_stderr = overlay.communicate()
        if overlay.returncode != 0:
            raise RuntimeError(overlay_stderr.decode("utf-8", errors="replace")[-4000:])
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-4000:])
        log("EXPORT_OK", surah=number, file=out, size=out.stat().st_size)
        return out

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(BG_IMAGE),
        "-i",
        str(mp3),
        "-vf",
        f"scale=1920:1080,setsar=1,subtitles='{ffmpeg_filter_escape(ass_file)}':fontsdir='{ffmpeg_filter_escape(FONTS_DIR)}'",
        "-af",
        f"adelay={int(round(INTRO_CREDIT_SECONDS * 1000))}:all=1,apad",
        "-c:v",
        "libx264",
        "-tune",
        "stillimage",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-t",
        f"{total_duration:.3f}",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    log("EXPORT_START", surah=number, file=out, audio_delay_seconds=INTRO_CREDIT_SECONDS, total_duration=total_duration, pango_overlay=False)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])
    log("EXPORT_OK", surah=number, file=out, size=out.stat().st_size)
    return out


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


def mark_status(surahs: dict[int, str], statuses: dict[int, str], number: int, status: str) -> None:
    statuses[number] = status
    write_todo(surahs, statuses)
    log("STATUS", surah=number, status=status)


def parse_force_arg() -> int | None:
    if len(sys.argv) == 3 and sys.argv[1] == "--force":
        return int(sys.argv[2])
    if len(sys.argv) != 1:
        raise RuntimeError("Usage: surah_automation.py [--force SURAH_NUMBER]")
    return None


def main() -> int:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
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
        statuses = build_statuses(surahs, mp3s, read_todo_statuses())
        if force_number is not None:
            if force_number not in surahs:
                raise RuntimeError(f"Unknown surah number: {force_number}")
            if force_number not in mp3s:
                raise RuntimeError(f"Cannot force surah {force_number}: MP3 is missing")
            if force_number in SKIP_SURAH_NUMBERS:
                raise RuntimeError(f"Cannot force surah {force_number}: it is configured as SKIP")
            statuses[force_number] = "STARTED"
            log("FORCE", surah=force_number)
        write_todo(surahs, statuses)
        current_todo_count = todo_count(statuses)
        log("SCAN_OK", mp3_count=len(mp3s), todo=current_todo_count, done=sum(1 for v in statuses.values() if v == "DONE"), ignored=sum(1 for v in statuses.values() if v == "IGNORE"), run_until_todo_empty=RUN_UNTIL_TODO_EMPTY)
        if force_number is None and current_todo_count == 0:
            log("SERVICE_IDLE", message="Todo.md TODO section is empty")
            log("RUN_END", status="DONE", processed=processed, message="Todo.md TODO section is empty")
            return 0
        log("SERVICE_START", todo=current_todo_count, force=force_number)

        while True:
            number = force_number if force_number is not None and processed == 0 else next_todo(statuses, mp3s)
            if number is None:
                log("SERVICE_STOP", message="Todo.md TODO section is empty")
                log("RUN_END", status="DONE", processed=processed, message="Todo.md TODO section is empty")
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
                pango_spec = write_pango_overlay_spec(number, ayat, basmalah_timing, ayah_timings)
                mp4 = render_mp4(number, mp3, ass_file, total_duration, pango_spec)
                verify_mp4(number, mp4)
                thumbnail = generate_thumbnail(number, arabic_name, english_name)
                mark_status(surahs, statuses, number, "DONE")
                statuses = build_statuses(surahs, mp3s, statuses)
                write_todo(surahs, statuses)
                log("STATUS_REFRESH", next_todo=next_todo(statuses, mp3s))
                processed += 1
                log("PROCESS_DONE", surah=number, file=mp4, thumbnail=thumbnail)
            except Exception as exc:
                mark_status(surahs, statuses, number, "ERROR")
                log("ERROR", level="ERROR", surah=number, message=str(exc))
                log("RUN_END", level="ERROR", status="ERROR", processed=processed, error=str(exc))
                return 1
            if PROCESS_ONE_PER_RUN or processed >= 1:
                if force_number is None and RUN_UNTIL_TODO_EMPTY and todo_count(statuses) > 0:
                    continue
                if force_number is None:
                    log("SERVICE_STOP", message="Todo.md TODO section is empty")
                log("RUN_END", status="DONE", processed=processed)
                return 0
    except Exception as exc:
        log("FATAL", level="ERROR", message=str(exc))
        log("RUN_END", level="ERROR", status="ERROR", processed=processed, error=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
