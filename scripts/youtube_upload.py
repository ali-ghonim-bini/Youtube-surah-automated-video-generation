#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from shared_log import append_event


SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_DIR / "settings.json"
SECTION_ORDER = ("STARTED", "TODO", "WAITING", "DONE", "ERROR", "IGNORE")
SYMBOLS = {"STARTED": "~", "TODO": " ", "WAITING": "?", "DONE": "x", "ERROR": "!", "IGNORE": "-"}
SCOPES = ["https://www.googleapis.com/auth/youtube"]
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
    if not SETTINGS_FILE.exists():
        raise RuntimeError(f"Missing settings file: {SETTINGS_FILE}")
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))


def setting_value(path: str) -> object:
    current: object = SETTINGS
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise RuntimeError(f"Missing setting: {path}")
        current = current[part]
    return current


def setting_str(path: str) -> str:
    return str(setting_value(path))


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


def setting_path(path: str) -> Path:
    value = setting_value(path)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"Missing or invalid setting: {path}")
    return Path(value)


SETTINGS = load_settings()
YOUTUBE = setting_value("youtube")
if not isinstance(YOUTUBE, dict):
    raise RuntimeError("Setting must be an object: youtube")
SURAH_DIR = setting_path("surah_dir")
EXPORTED_DIR = setting_path("exported_dir")
THUMBNAIL_DIR = setting_path("thumbnail.output_dir")
SURAH_FILE = setting_path("surah_file")
LOG_FILE = setting_path("log_file")
TODO_FILE = setting_path("youtube.todo_file")
CLIENT_SECRET_FILE = setting_path("youtube.client_secret_file")
TOKEN_FILE = setting_path("youtube.token_file")
SCHEDULE_FILE = setting_path("youtube.schedule_file")
RUN_ID = datetime.now().strftime("yt-%Y%m%d-%H%M%S")


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


def read_surahs() -> dict[int, str]:
    surahs: dict[int, str] = {}
    for line in SURAH_FILE.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*(\d+)\s*-\s*(.+?)\s*$", line)
        if match:
            surahs[int(match.group(1))] = match.group(2)
    return surahs


def parse_todo() -> dict[int, str]:
    statuses: dict[int, str] = {}
    if not TODO_FILE.exists():
        return statuses
    symbol_statuses = {" ": "TODO", "?": "WAITING", "x": "DONE", "!": "ERROR", "~": "STARTED", "-": "IGNORE"}
    for line in TODO_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^\s*-\s*\[(?P<symbol>[ x?!~\-])\]\s*(?P<number>\d+)\s*-", line)
        if match:
            statuses[int(match.group("number"))] = symbol_statuses[match.group("symbol")]
    return statuses


def write_todo(surahs: dict[int, str], statuses: dict[int, str]) -> None:
    lines = ["# YouTube TODO", ""]
    for status in SECTION_ORDER:
        lines.append(f"## {status}")
        for number in sorted(surahs, reverse=True):
            if statuses.get(number) == status:
                lines.append(f"- [{SYMBOLS[status]}] {number} - {surahs[number]}")
        lines.append("")
    TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
    TODO_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def ensure_schedule_file() -> None:
    if SCHEDULE_FILE.exists() and SCHEDULE_FILE.stat().st_size > 0:
        return
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps({"latest_publish_at": None, "videos": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ensure_todo(surahs: dict[int, str]) -> dict[int, str]:
    statuses = parse_todo()
    if statuses:
        return statuses
    statuses = {number: "WAITING" for number in surahs}
    write_todo(surahs, statuses)
    return statuses


def next_todo(statuses: dict[int, str]) -> int | None:
    for number in sorted(statuses, reverse=True):
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


def youtube_client():
    Request, Credentials, InstalledAppFlow, build, _ = import_youtube_libs()
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CLIENT_SECRET_FILE.exists():
                raise RuntimeError(f"Missing client secret file: {CLIENT_SECRET_FILE}")
            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
            if os.environ.get("DISPLAY"):
                creds = flow.run_local_server(port=0)
            else:
                creds = flow.run_console()
        save_credentials(creds)
        log("TOKEN_OK", file=TOKEN_FILE)
    return build("youtube", "v3", credentials=creds)


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


def publish_at(video_file: Path) -> str:
    latest_publish = latest_scheduled_publish_at()
    if latest_publish is not None:
        return next_spaced_publish_at(latest_publish)
    return initial_publish_at(video_file)


def initial_publish_at(video_file: Path) -> str:
    publish_time = setting_str("youtube.publish_time")
    hour, minute = [int(part) for part in publish_time.split(":", 1)]
    gmt_offset_hours = setting_float("youtube.gmt_offset_hours")
    tz = timezone(timedelta(hours=gmt_offset_hours))
    days = setting_int("youtube.publish_days_after_video_done")
    base_date = datetime.fromtimestamp(video_file.stat().st_mtime, tz=tz).date() + timedelta(days=days)
    local_dt = datetime.combine(base_date, time(hour, minute), tzinfo=tz)
    now_local = datetime.now(tz)
    if local_dt <= now_local:
        local_dt = datetime.combine(now_local.date(), time(hour, minute), tzinfo=tz)
        if local_dt <= now_local:
            local_dt += timedelta(days=1)
    return local_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def next_spaced_publish_at(latest_publish_at: str) -> str:
    publish_time = setting_str("youtube.publish_time")
    hour, minute = [int(part) for part in publish_time.split(":", 1)]
    gmt_offset_hours = setting_float("youtube.gmt_offset_hours")
    tz = timezone(timedelta(hours=gmt_offset_hours))
    between_days = setting_int("youtube.publish_between_scheduled_days")
    latest_local = parse_utc_datetime(latest_publish_at).astimezone(tz)
    local_dt = datetime.combine(latest_local.date() + timedelta(days=between_days), time(hour, minute), tzinfo=tz)
    now_local = datetime.now(tz)
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
    candidates = [value for value in [latest_from_schedule_file(), latest_from_logs()] if value]
    if not candidates:
        return None
    return max(candidates, key=parse_utc_datetime)


def latest_from_schedule_file() -> str | None:
    if not SCHEDULE_FILE.exists() or SCHEDULE_FILE.stat().st_size == 0:
        return None
    try:
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    value = data.get("latest_publish_at") if isinstance(data, dict) else None
    return str(value) if value else None


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
    videos = [video for video in videos if not (isinstance(video, dict) and (video.get("surah") == number or video.get("video_id") == video_id))]
    record = {"surah": number, "video_id": video_id, "publish_at": publish_at_value, "recorded_at": datetime.now().isoformat(timespec="seconds")}
    videos.insert(0, record)
    data["videos"] = videos[:500]
    publish_values = [str(video.get("publish_at")) for video in videos if isinstance(video, dict) and video.get("publish_at")]
    data["latest_publish_at"] = max(publish_values, key=parse_utc_datetime) if publish_values else publish_at_value
    SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("SCHEDULE_RECORDED", surah=number, video_id=video_id, publish_at=publish_at_value, schedule_file=SCHEDULE_FILE)


def schedule_by_surah() -> dict[int, str]:
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
        if not isinstance(video, dict):
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
        "sheikh_arabic": setting_str("youtube.sheikh_arabic"),
        "sheikh_english": setting_str("youtube.sheikh_english"),
        "sheikh_arabic_title": setting_str("youtube.sheikh_arabic_title"),
        "sheikh_english_title": setting_str("youtube.sheikh_english_title"),
        "riwayah_arabic": setting_str("youtube.riwayah_arabic"),
        "riwayah_english": setting_str("youtube.riwayah_english"),
        "riwayah_arabic_title": setting_str("youtube.riwayah_arabic_title"),
        "riwayah_english_title": setting_str("youtube.riwayah_english_title"),
        "sheikh_hashtag": setting_str("youtube.sheikh_hashtag"),
        "riwayah_hashtag": setting_str("youtube.riwayah_hashtag"),
        "riwayah_full_hashtag": setting_str("youtube.riwayah_full_hashtag"),
        "intro_text": formatted_intro_text(),
        "outro_text": formatted_intro_text(),
        "surahquran_url": setting_str("surahquran.display_url"),
        "audio_source_url": setting_str("audio.display_url"),
    }
    title = setting_str("youtube.title_format").format(**values)
    description = setting_str("youtube.description_format").format(**values)
    return title, description


def formatted_intro_text() -> str:
    return "\n".join(str(line).strip() for line in setting_list("youtube.intro_lines") if str(line).strip())


def upload_one(youtube, MediaFileUpload, number: int, arabic_name: str) -> str:
    video_file = EXPORTED_DIR / f"{number}.mp4"
    thumbnail_file = THUMBNAIL_DIR / f"{number}.png"
    if not video_file.exists():
        raise RuntimeError(f"Missing video file: {video_file}")
    if not thumbnail_file.exists():
        raise RuntimeError(f"Missing thumbnail file: {thumbnail_file}")
    title, description = format_metadata(number, arabic_name)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [str(tag) for tag in setting_list("youtube.tags")],
            "categoryId": setting_str("youtube.category_id"),
        },
        "status": {
            "privacyStatus": setting_str("youtube.privacy_status"),
            "publishAt": publish_at(video_file),
            "selfDeclaredMadeForKids": setting_bool("youtube.made_for_kids"),
        },
    }
    log("UPLOAD_START", surah=number, video=video_file, thumbnail=thumbnail_file, title=title, publish_at=body["status"]["publishAt"])
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(video_file), chunksize=-1, resumable=True))
    response = None
    while response is None:
        _, response = request.next_chunk()
    video_id = response["id"]
    log("VIDEO_UPLOAD_OK", surah=number, video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_file))).execute()
        log("THUMBNAIL_SET_OK", surah=number, video_id=video_id, thumbnail=thumbnail_file)
        add_to_playlist(youtube, video_id, number)
    except Exception as exc:
        log("POST_UPLOAD_ERROR", level="ERROR", surah=number, video_id=video_id, **youtube_error_details(exc))
        raise
    log("UPLOAD_OK", surah=number, video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}")
    record_schedule(number, video_id, str(body["status"]["publishAt"]))
    return video_id


def add_to_playlist(youtube, video_id: str, number: int) -> None:
    playlist_id = setting_str("youtube.playlist_id").strip()
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
        ).execute()
    except Exception as exc:
        log("PLAYLIST_ADD_ERROR", level="ERROR", surah=number, video_id=video_id, playlist_id=playlist_id, **youtube_error_details(exc))
        raise
    log("PLAYLIST_ADD_OK", surah=number, video_id=video_id, playlist_id=playlist_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", type=int)
    parser.add_argument("--auth-only", action="store_true", help="Create or refresh token.json without uploading")
    parser.add_argument("--playlist-video-id", help="Add an existing YouTube video ID to the configured playlist without uploading")
    parser.add_argument("--surah", type=int, help="Surah number for playlist-only logging")
    args = parser.parse_args()
    processed = 0
    log("RUN_START", argv=sys.argv)
    ensure_schedule_file()
    if args.auth_only:
        youtube_client()
        log("RUN_END", status="DONE", processed=0, message="OAuth token ready")
        return 0
    if args.playlist_video_id:
        youtube = youtube_client()
        add_to_playlist(youtube, args.playlist_video_id, args.surah or 0)
        log("RUN_END", status="DONE", processed=0, message="Existing video added to playlist", video_id=args.playlist_video_id)
        return 0
    surahs = read_surahs()
    statuses = ensure_todo(surahs)
    if args.force is not None:
        statuses[args.force] = "STARTED"
        write_todo(surahs, statuses)
    number = args.force or next_todo(statuses)
    if number is None:
        log("RUN_END", status="DONE", processed=0, message="YT_Todo.md TODO section is empty")
        return 0
    try:
        _, _, _, _, MediaFileUpload = import_youtube_libs()
        youtube = youtube_client()
        statuses[number] = "STARTED"
        write_todo(surahs, statuses)
        upload_one(youtube, MediaFileUpload, number, surahs[number])
        statuses[number] = "DONE"
        processed = 1
        write_todo(surahs, statuses)
        log("RUN_END", status="DONE", processed=processed)
        return 0
    except Exception as exc:
        statuses[number] = "ERROR"
        write_todo(surahs, statuses)
        details = youtube_error_details(exc)
        log("UPLOAD_ERROR", level="ERROR", surah=number, **details)
        log("RUN_END", level="ERROR", status="ERROR", processed=processed, **details)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
