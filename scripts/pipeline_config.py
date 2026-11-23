"""Settings, constants and the log function shared by the whole video pipeline."""
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
import http.client
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from auto_queue import approved_ignored, corrupt_videos, find_existing_video
from notify import notify
from shared_log import append_event, json_safe
import layout as text_layout
import todo_file
import settings_path


SCRIPT_DIR = Path(__file__).resolve().parent
TEMP_DIR = settings_path.temp_dir()   # temporary per-surah files
SETTINGS_FILE = settings_path.settings_file()  # SURAH_SETTINGS: tests / smoke runs


DEFAULT_SETTINGS: dict[str, object] = {
    "surah_dir": "/path/to/Channel/سور",
    "sound_dir": "/path/to/Channel/سور/Sound",
    "exported_dir": "/path/to/Channel/سور/Exported_Vids",
    "live_exported_dir": "/path/to/Channel/سور/Exported_Vids/Live",
    "surah_file": "/path/to/Channel/سور/Surhs.txt",
    "todo_file": "/path/to/Channel/سور/Todo.md",
    "log_file": "/path/to/Channel/سور/Logs.json",
    "fonts_dir": "/mnt/c/Windows/Fonts",
    "strict_font_validation": True,
    "skip_surah_numbers": [18],
    "process_one_per_run": False,
    "run_until_todo_empty": True,
    "generate_mlt": False,
    "voice_timing_enabled": True,
    "voice_start_threshold_db": -25,
    "voice_timing_search_padding_seconds": 1.0,
    "audio": {
        "source": "qul_ayah_by_ayah",
        "recitation_id": 21,
        "recitation_page_url": "https://qul.tarteel.ai/resources/recitation/111?ayah=1%3A5",
        "segments_url_template": "https://qul.tarteel.ai/api/v1/audio/ayah_segments/{recitation_id}?surah={surah_number}&from=1",
        "expected_sheikh_name": "Mahmoud Khalil Al Husary",
        "expected_source_terms": ["Ayah by Ayah", "Mujawwad", "With segments"],
        "display_name": "قل | Qul",
        "display_url": "https://qul.tarteel.ai",
    },
    "surahquran": {"display_url": "https://surahquran.com"},
    "video": {
        "background_image": "/path/to/Channel/Visual/BG.png",
        "font_standard": "Arial",
        "pango_overlay_fps": 25,
        "pango_sync_offset_seconds": 0.0,
        "layout": {"center_x": 960, "credit_y": 540},
        "title": {"center_y": 70, "size": 72, "color": "000000", "bold": True, "fade_in_seconds": 0.16},
        "riwayah": {"center_y": 190, "size": 50, "color": "aa0000", "bold": True, "fade_in_seconds": 0.16},
        "basmalah": {
            "arabic_text": "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ",
            "english_text": "In the name of Allah, the Entirely Merciful, the Especially Merciful.",
            "fade_in_seconds": 0.96,
        },
        "ayah": {"fade_in_seconds": 0.4, "fade_out_seconds": 0.4},
        "ayah_arabic": {
            "use_pango": True,
            "family": "Arial",
            "font": "Arial 64",
            "ass_fallback_size": 72,
            "color": "08553b",
            "center_y": 520,
            "box_width": 1840,
            "line_spacing": 1.0,
            "line_height": 86,
            "number_gap": 18,
            "wrap_width": 72,
            "number_bidi": "none",
        },
        "ayah_english": {"family": "Arial", "size": 45, "color": "000000", "center_y": 715, "bold": True, "wrap_width": 72},
        "intro": {
            "duration_seconds": 3.56,
            "arabic_text": "شكر خاص لأصحاب المواقع",
            "english_text": "Special thanks to the websites owners:\nhttps://surahquran.com\n\nقل | Qul\nhttps://qul.tarteel.ai",
            "arabic_font": "Arial 56",
            "arabic_color": "08553b",
            "arabic_y": 375,
            "arabic_box_width": 1500,
            "english_size": 56,
            "english_color": "08553b",
            "english_bold": True,
            "fade_in_seconds": 0.32,
            "fade_out_seconds": 0.4,
        },
        "outro": {
            "duration_seconds": 4.6,
            "arabic_text": ".لا تنسوني و والديّ من صالح دعائكم",
            "english_text": "Please don't forget me and my parents in your prayers.",
            "arabic_font": "Arial 56",
            "arabic_color": "000000",
            "arabic_y": 450,
            "arabic_box_width": 1500,
            "english_size": 56,
            "english_color": "000000",
            "english_bold": True,
            "fade_in_seconds": 0.32,
            "fade_out_seconds": 0.0,
        },
    },
    "thumbnail": {
        "output_dir": "/path/to/Channel/Visual/Thumbnails",
        "use_gradient": True,
        "gradient_type": "radial",
        "gradient_shape": "ellipse",
        "gradient_position": "bottom",
        "gradient_stops": [
            {"offset": 0.0, "color": "8EAEA0"},
            {"offset": 0.6, "color": "5B8C76"},
            {"offset": 1.0, "color": "356B55"},
        ],
        "background_color": "356B55",
        "arabic_font": "Arial Bold 162.54425049px",
        "english_font": "Arial Bold 114.27213287px",
        "font_color": "ffffff",
        "font_alpha": 0.96,
        "arabic_y": 285,
        "english_y": 455,
        "text_box_width": 1100,
    },
    "youtube": {
        "todo_file": "/path/to/Channel/سور/YT_Todo.md",
        "client_secret_file": "/path/to/Channel/auth_certification/client_secret.json",
        "token_file": "/path/to/Channel/auth_certification/token.json",
        "schedule_file": "/path/to/Channel/سور/YT_Schedule.json",
        "privacy_status": "private",
        "publish_time": "09:00",
        "gmt_offset_hours": 2,
        "publish_days_after_video_done": 0,
        "publish_between_scheduled_days": 1,
        "playlist_url": "",
        "playlist_id": "",
        "title_format": "سورة {arabic_name} للشيخ {sheikh_arabic_title} برواية {riwayah_arabic_title} | Surah {english_name_plain} by {sheikh_english_title}, narrated by {riwayah_english_title}",
        "description_format": "سورة {arabic_name} بصوت الشيخ {sheikh_arabic} - برواية {riwayah_arabic}\n\nاستمع إلى تلاوة خاشعة لسورة {arabic_name} من القرآن الكريم، بأداء مميز من القارئ الشيخ {sheikh_arabic}، أحد أعلام التلاوة في العالم الإسلامي، برواية {riwayah_arabic}.\n\n📌 About This Recitation (English):\nA beautiful and serene recitation of Surah {english_name} by the renowned Qari Sheikh {sheikh_english}, in the {riwayah_english} narration. Known for his clear and precise style, Sheikh Al-Husary’s recitations are widely used for memorization and reflection.\n\n\n{intro_text}\n\n\n#{arabic_name} #القرآن #الحصري #تلاوة {english_name} {sheikh_hashtag} {riwayah_full_hashtag} {riwayah_hashtag} #quranrecitation #سورة_{arabic_name} #quran",
        "intro_lines": [
            "شكر خاص لأصحاب المواقع",
            "Special thanks to the websites owners:",
            "https://surahquran.com",
            "",
            "قل | Qul",
            "https://qul.tarteel.ai",
        ],
        "sheikh_arabic": "محمود خليل الحصري",
        "sheikh_english": "Mahmoud Khalil Al Husary",
        "sheikh_arabic_title": "الحصري",
        "sheikh_english_title": "Sheikh Al-Husary",
        "sheikh_hashtag": "#husary#",
        "riwayah_arabic": "حفص عن عاصم",
        "riwayah_english": "Hafs from Asim",
        "riwayah_arabic_title": "حفص",
        "riwayah_english_title": "Hafs",
        "riwayah_hashtag": "#حفص",
        "riwayah_full_hashtag": "#حفص_عن_عاصم",
        "category_id": "27",
        "made_for_kids": False,
        "tags": ["Quran", "Surah", "Mahmoud Khalil Al Husary", "Mujawwad"],
    },
}


def load_settings() -> dict[str, object]:
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def path_from_setting(settings: dict[str, object], key: str, base: Path) -> Path:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing or invalid setting: {key}")
    path = Path(value)
    return path if path.is_absolute() else base / path


def setting_value(path: str, default: object = None) -> object:
    current: object = SETTINGS
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def setting_str(path: str, default: str) -> str:
    value = setting_value(path, default)
    return str(value)


def setting_int(path: str, default: int) -> int:
    return int(setting_value(path, default))


def setting_float(path: str, default: float) -> float:
    return float(setting_value(path, default))


def setting_bool(path: str, default: bool) -> bool:
    return bool(setting_value(path, default))


def setting_list(path: str, default: list[object]) -> list[object]:
    value = setting_value(path, default)
    return value if isinstance(value, list) else default


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
YT_TODO_FILE = Path(str(setting_value("youtube.todo_file") or SURAH_DIR / "YT_Todo.md"))
AUTO_QUEUE_UPLOAD = setting_bool("youtube.auto_queue_after_video", True)
LOG_FILE = path_from_setting(SETTINGS, "log_file", SURAH_DIR)
BG_IMAGE = setting_path("video.background_image", CHANNEL_DIR)
THUMBNAIL_DIR = setting_path("thumbnail.output_dir", CHANNEL_DIR)

SKIP_SURAH_NUMBERS = {int(number) for number in SETTINGS.get("skip_surah_numbers", [])}
# Ignored surahs you approved via the auto-queue notification (Decisions.json) are no longer skipped.
SKIP_SURAH_NUMBERS -= approved_ignored(SURAH_DIR / "Decisions.json")
PROCESS_ONE_PER_RUN = bool(SETTINGS.get("process_one_per_run", True))
RUN_UNTIL_TODO_EMPTY = bool(SETTINGS.get("run_until_todo_empty", not PROCESS_ONE_PER_RUN))
FADE_TITLE_SECONDS = setting_float("video.title.fade_in_seconds", 0.16)
FADE_RIWAYAH_SECONDS = setting_float("video.riwayah.fade_in_seconds", 0.16)
FADE_BASMALAH_SECONDS = setting_float("video.basmalah.fade_in_seconds", 0.96)
FADE_AYAH_SECONDS = setting_float("video.ayah.fade_in_seconds", 0.4)
FADE_CREDIT_SECONDS = setting_float("video.intro.fade_in_seconds", 0.32)
FADE_OUT_SECONDS = setting_float("video.ayah.fade_out_seconds", 0.4)
INTRO_CREDIT_SECONDS = setting_float("video.intro.duration_seconds", 3.56)
OUTRO_CREDIT_SECONDS = setting_float("video.outro.duration_seconds", 4.6)
PANGO_SYNC_OFFSET_SECONDS = setting_float("video.pango_sync_offset_seconds", 0.0)
GENERATE_MLT = bool(SETTINGS.get("generate_mlt", False))
VOICE_TIMING_ENABLED = bool(SETTINGS.get("voice_timing_enabled", True))
VOICE_START_THRESHOLD_DB = float(SETTINGS.get("voice_start_threshold_db", -25))
VOICE_TIMING_SEARCH_PADDING_SECONDS = float(SETTINGS.get("voice_timing_search_padding_seconds", 1.0))
LAYOUT_CENTER_X = setting_int("video.layout.center_x", 960)
LAYOUT_TITLE_Y = setting_int("video.title.center_y", 70)
LAYOUT_RIWAYAH_Y = setting_int("video.riwayah.center_y", 190)
LAYOUT_ARABIC_Y = setting_int("video.ayah_arabic.center_y", 520)
LAYOUT_ENGLISH_Y = setting_int("video.ayah_english.center_y", 715)
LAYOUT_CREDIT_Y = setting_int("video.layout.credit_y", 540)
FONT_STANDARD = setting_str("video.font_standard", "Arial")
FONT_ARABIC_AYAH = setting_str("video.ayah_arabic.family", "Arial")
FONT_ENGLISH = setting_str("video.ayah_english.family", "Arial")
FONTS_DIR = path_from_setting(SETTINGS, "fonts_dir", Path("/mnt/c/Windows/Fonts"))
STRICT_FONT_VALIDATION = bool(SETTINGS.get("strict_font_validation", False))
ARABIC_AYAH_NUMBER_BIDI = setting_str("video.ayah_arabic.number_bidi", "none")
ARABIC_AYAH_LINE_HEIGHT = setting_int("video.ayah_arabic.line_height", 86)
ARABIC_AYAH_NUMBER_GAP = setting_int("video.ayah_arabic.number_gap", 18)
ARABIC_AYAH_WRAP_WIDTH = setting_int("video.ayah_arabic.wrap_width", 72)
PANGO_ARABIC_OVERLAY = setting_bool("video.ayah_arabic.use_pango", True)
PANGO_OVERLAY_FPS = setting_int("video.pango_overlay_fps", 25)
RENDER_MODE = setting_str("video.render.mode", "pieces")   # "pieces" (resumable, fast) or "single" (the old one-pass render)
RENDER_PIECE_SECONDS = setting_int("video.render.piece_seconds", 120)
RENDER_PARALLEL_PIECES = setting_int("video.render.parallel_pieces", 2)
RENDER_PIECE_THREADS = setting_int("video.render.threads_per_piece", 4)
RENDER_ENCODER_PRESET = setting_str("video.render.encoder_preset", "medium")   # x264 preset; "veryfast" measured 2x faster, smaller, same look
PANGO_ARABIC_FONT = setting_str("video.ayah_arabic.font", f"{FONT_ARABIC_AYAH} 64")
PANGO_ARABIC_BOX_WIDTH = setting_int("video.ayah_arabic.box_width", 1840)
PANGO_ARABIC_COLOR = setting_str("video.ayah_arabic.color", "08553b")
PANGO_ARABIC_LINE_SPACING = setting_float("video.ayah_arabic.line_spacing", 1.0)
TITLE_SIZE = setting_int("video.title.size", 72)
TITLE_COLOR = setting_str("video.title.color", "000000")
TITLE_BOLD = setting_bool("video.title.bold", True)
RIWAYAH_SIZE = setting_int("video.riwayah.size", 50)
RIWAYAH_COLOR = setting_str("video.riwayah.color", "aa0000")
RIWAYAH_BOLD = setting_bool("video.riwayah.bold", True)
PANGO_TITLE_FONT = setting_str("video.title.pango_font", f"{FONT_STANDARD} {'Bold ' if TITLE_BOLD else ''}{TITLE_SIZE}")
PANGO_RIWAYAH_FONT = setting_str("video.riwayah.pango_font", f"{FONT_STANDARD} {'Bold ' if RIWAYAH_BOLD else ''}{RIWAYAH_SIZE}")
ENGLISH_SIZE = setting_int("video.ayah_english.size", 45)
ENGLISH_COLOR = setting_str("video.ayah_english.color", "000000")
ENGLISH_BOLD = setting_bool("video.ayah_english.bold", True)
ENGLISH_WRAP_WIDTH = setting_int("video.ayah_english.wrap_width", 72)
# Stacked layout (see layout.py): English is drawn by the Pango helper so it can be measured exactly.
ENGLISH_PANGO_FONT = setting_str("video.ayah_english.pango_font", f"{FONT_ENGLISH} {'Bold ' if ENGLISH_BOLD else ''}27")
ENGLISH_BOX_WIDTH = setting_int("video.ayah_english.box_width", 1640)
ENGLISH_TOP_Y = setting_int("video.ayah_english.top_y", 780)
LAYOUT_GAP = setting_int("video.layout.content_gap", 36)
LAYOUT_BOTTOM_MARGIN = setting_int("video.layout.bottom_margin", 40)
LAYOUT_FIT_STEP = setting_float("video.layout.fit_step", 0.94)
LAYOUT_MIN_SCALE = setting_float("video.layout.min_scale", 0.70)
LAYOUT_CHECK_FRAMES = setting_int("video.layout.check_frames", 4)
LAYOUT_MAX_RENDERS = setting_int("video.layout.max_render_attempts", 3)
# Quality check of the finished render, before it is handed to the uploader.
QUALITY_CHECK_ENABLED = setting_bool("video.quality_check.enabled", True)
QUALITY_EXTENT_TOLERANCE_PX = setting_int("video.quality_check.text_extent_tolerance_px", 14)
# The thinnest marks above the letters are too faint to count at check resolution, so the drawn top can read
# up to this much lower than planned (seen: 0-27 px). A missing/extra line is ~90 px, so it is still caught.
QUALITY_TOP_FAINT_PX = setting_int("video.quality_check.text_top_faint_px", 40)
QUALITY_SYNC_MAX_OFFSET = setting_float("video.quality_check.sync_max_offset_seconds", 0.5)
QUALITY_SYNC_MAX_MEDIAN = setting_float("video.quality_check.sync_max_median_seconds", 0.3)
QUALITY_SYNC_MAX_BAD_SHARE = setting_float("video.quality_check.sync_max_bad_share", 0.1)
QUALITY_MIN_SCREEN_SECONDS = 1.0
LAYOUT_OVERRIDES_FILE = SURAH_DIR / "Layout_Overrides.json"
LAYOUT_SPLIT_BELOW_SCALE = setting_float("video.layout.split_below_scale", 0.85)
LAYOUT_MAX_SPLIT_PARTS = setting_int("video.layout.max_split_parts", 4)
SPLIT_MIN_PART_SECONDS = 1.5
# QUL word timings per ayah (surah, ayah) -> [[word_from, word_to, start_ms, end_ms], ...], used to time split ayat
WORD_SEGMENTS: dict[tuple[int, int], list[list[float]]] = {}
RIWAYAH_TEXT = "برواية:حفص عن عاصم"
CREDIT_SIZE = setting_int("video.intro.english_size", 56)
CREDIT_COLOR = setting_str("video.intro.english_color", "08553b")
INTRO_ENGLISH_BOLD = setting_bool("video.intro.english_bold", True)
OUTRO_SIZE = setting_int("video.outro.english_size", 56)
OUTRO_COLOR = setting_str("video.outro.english_color", "000000")
OUTRO_ENGLISH_BOLD = setting_bool("video.outro.english_bold", True)
BASMALAH_ARABIC_TEXT = setting_str("video.basmalah.arabic_text", "بِسْمِ اللَّهِ الرَّحْمَنِ الرَّحِيمِ")
BASMALAH_ENGLISH_TEXT = setting_str("video.basmalah.english_text", "In the name of Allah, the Entirely Merciful, the Especially Merciful.")
INTRO_ARABIC_FONT = setting_str("video.intro.arabic_font", "Arial 56")
INTRO_ARABIC_COLOR = setting_str("video.intro.arabic_color", "08553b")
INTRO_ARABIC_Y = setting_int("video.intro.arabic_y", LAYOUT_CREDIT_Y - 165)
INTRO_ARABIC_BOX_WIDTH = setting_int("video.intro.arabic_box_width", 1500)
OUTRO_ARABIC_FONT = setting_str("video.outro.arabic_font", "Arial 56")
OUTRO_ARABIC_COLOR = setting_str("video.outro.arabic_color", "000000")
OUTRO_ARABIC_Y = setting_int("video.outro.arabic_y", LAYOUT_CREDIT_Y - 90)
OUTRO_ARABIC_BOX_WIDTH = setting_int("video.outro.arabic_box_width", 1500)
OUTRO_FADE_OUT_SECONDS = setting_float("video.outro.fade_out_seconds", 0.0)
THUMBNAIL_USE_GRADIENT = setting_bool("thumbnail.use_gradient", True)
THUMBNAIL_GRADIENT_TYPE = setting_str("thumbnail.gradient_type", "radial")
THUMBNAIL_GRADIENT_STOPS = setting_list("thumbnail.gradient_stops", [
    {"offset": 0.0, "color": "8EAEA0"},
    {"offset": 0.6, "color": "5B8C76"},
    {"offset": 1.0, "color": "356B55"},
])
THUMBNAIL_BACKGROUND_COLOR = setting_str("thumbnail.background_color", "356B55")
THUMBNAIL_ARABIC_FONT = setting_str("thumbnail.arabic_font", "Arial Bold 162.54425049px")
THUMBNAIL_ENGLISH_FONT = setting_str("thumbnail.english_font", "Arial Bold 114.27213287px")
THUMBNAIL_FONT_COLOR = setting_str("thumbnail.font_color", "ffffff")
THUMBNAIL_FONT_ALPHA = setting_float("thumbnail.font_alpha", 0.96)
THUMBNAIL_ARABIC_Y = setting_float("thumbnail.arabic_y", 285)
THUMBNAIL_ENGLISH_Y = setting_float("thumbnail.english_y", 455)
THUMBNAIL_TEXT_BOX_WIDTH = setting_float("thumbnail.text_box_width", 1100)
AUDIO_SOURCE = setting_str("audio.source", "qul_ayah_by_ayah")
AUDIO_RECITATION_ID = setting_int("audio.recitation_id", 21)
AUDIO_RECITATION_PAGE_URL = setting_str("audio.recitation_page_url", "https://qul.tarteel.ai/resources/recitation/111?ayah=1%3A5")
EXPECTED_SHEIKH_NAME = setting_str("audio.expected_sheikh_name", "Mahmoud Khalil Al Husary")
EXPECTED_AUDIO_SOURCE_TERMS = [str(term) for term in setting_value("audio.expected_source_terms", ["Ayah by Ayah", "Mujawwad", "With segments"])]
AUDIO_SEGMENTS_URL_TEMPLATE = setting_str("audio.segments_url_template", "https://qul.tarteel.ai/api/v1/audio/ayah_segments/{recitation_id}?surah={surah_number}&from=1")
AUDIO_SOURCE_DISPLAY_NAME = setting_str("audio.display_name", "قل | Qul")
AUDIO_SOURCE_DISPLAY_URL = setting_str("audio.display_url", "https://qul.tarteel.ai")
SURAHQURAN_DISPLAY_URL = setting_str("surahquran.display_url", "https://surahquran.com")
TEXT_PAGE_URL_TEMPLATE = setting_str("surahquran.page_url_template", "https://surahquran.com/Surah-translation/Arabic-language-2-surah-{number}.html")   # Arabic text + translation page
INTRO_CREDIT_ARABIC_TEXT = setting_str("video.intro.arabic_text", "شكر خاص لأصحاب المواقع")
INTRO_CREDIT_TEXT = setting_str("video.intro.english_text", f"Special thanks to the websites owners:\n{SURAHQURAN_DISPLAY_URL}\n\n{AUDIO_SOURCE_DISPLAY_NAME}\n{AUDIO_SOURCE_DISPLAY_URL}")
OUTRO_CREDIT_ARABIC_TEXT = setting_str("video.outro.arabic_text", ".لا تنسوني و والديّ من صالح دعائكم")
OUTRO_CREDIT_TEXT = setting_str("video.outro.english_text", "Please don't forget me and my parents in your prayers.")
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
