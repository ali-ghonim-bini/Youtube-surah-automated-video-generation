# YouTube Surah Automated Video Generation

Automated Quran surah video generation and YouTube upload workflow.

This project generates surah videos, creates thumbnails, manages Markdown-based processing queues, uploads videos to YouTube, applies thumbnails, adds videos to playlists, schedules publishing, and writes structured logs for each process.

## What It Does

The automation is split into two main workflows:

```txt
Todo.md -> generate MP4 video -> generate thumbnail -> mark video DONE
YT_Todo.md -> upload to YouTube -> set thumbnail -> add to playlist -> schedule publish -> mark upload DONE
```

The system is designed so you can control work by editing Markdown queue files. Move a surah from `WAITING` to `TODO`, and the watcher or script processes it.

## Main Features

- Generates MP4 videos directly with `ffmpeg`.
- Downloads and combines QUL ayah-by-ayah audio.
- Uses QUL ayah timing data for synchronization.
- Refines timing with voice detection when enabled.
- Renders Arabic ayah text with Pango/Cairo for correct Arabic shaping.
- Generates thumbnails with Python Cairo/Pango.
- Uses Markdown queue files for video and YouTube workflows.
- Uploads videos to YouTube with OAuth.
- Sets YouTube thumbnails automatically.
- Adds uploaded videos to a configured YouTube playlist.
- Schedules YouTube publish time using local GMT offset settings.
- Spaces scheduled uploads by a configurable number of days.
- Creates missing TODO/schedule files automatically.
- Writes structured JSON logs for trigger, video, image, and YouTube processes.

## Repository Files

```txt
scripts/
  pango_arabic_overlay.c      Arabic Pango/Cairo video overlay helper
  shared_log.py               Shared structured JSON logging
  surah_automation.py         Main video/audio/thumbnail generator
  todo_watcher.py             Watches Todo.md for video generation tasks
  youtube_upload.py           YouTube OAuth, upload, thumbnail, playlist, schedule
  yt_watcher.py               Watches YT_Todo.md for YouTube upload tasks
  settings.json               Main configuration template

Surhs.txt                     Surah name reference file
Todo.md                       Video generation queue template
YT_Todo.md                    YouTube upload queue template
YT_Schedule.json              YouTube publish schedule tracking template
README.md                     Project documentation
```

## Requirements

Required command-line tools:

- `ffmpeg`
- `ffprobe`
- C compiler for the Pango overlay helper, such as `gcc`

Required Python/system packages depend on your OS. On Ubuntu, typical packages include:

```bash
sudo apt-get install -y ffmpeg gcc pkg-config libpango1.0-dev libcairo2-dev python3-gi python3-cairo python3-gi-cairo
sudo apt-get install -y python3-googleapi python3-google-auth python3-google-auth-oauthlib python3-google-auth-httplib2
```

You also need:

- A background image for video generation.
- Fonts available on your system, usually Arial or another configured font.
- The included `Surhs.txt` file, or your own equivalent file containing all 114 surah names.
- A Google OAuth client secret JSON file for YouTube upload.

Example `Surhs.txt` format:

```txt
1- الفاتحة
2- البقرة
3- آل عمران
```

## Configuration

Edit:

```txt
scripts/settings.json
```

The included `settings.json` is a required placeholder template. Replace every placeholder with real values before running the scripts. Some fields must be numbers, booleans, arrays, or objects at runtime, even though the template describes them with text.

The scripts do not provide runtime defaults for configuration values. Missing settings fail fast so incorrect paths, metadata, or scheduling values are fixed explicitly.

Important path settings:

```json
{
  "surah_dir": "Your Surah Folder",
  "sound_dir": "Your Sound Folder",
  "exported_dir": "Your Exported Videos Folder",
  "surah_file": "Your Surhs.txt Path",
  "todo_file": "Your Todo.md Path",
  "log_file": "Your Logs.json Path",
  "fonts_dir": "Your Fonts Folder",
  "strict_font_validation": true,
  "voice_start_threshold_db": -25
}
```

Important video settings:

```json
{
  "video": {
    "background_image": "Your Background Image Path",
    "font_standard": "Arial"
  },
  "thumbnail": {
    "output_dir": "Your Thumbnail Output Folder"
  }
}
```

Important YouTube settings:

```json
{
  "youtube": {
    "todo_file": "Your YT_Todo.md Path",
    "client_secret_file": "Your OAuth Client Secret JSON Path",
    "token_file": "Your Youtube Channel Token",
    "schedule_file": "Your YT_Schedule.json Path",
    "playlist_url": "Playlist Url",
    "playlist_id": "Playlist Id",
    "publish_time": "09:00",
    "gmt_offset_hours": 2,
    "publish_between_scheduled_days": 1
  }
}
```

## Queue Files

The project uses Markdown queues.

`Todo.md` controls video generation.

`YT_Todo.md` controls YouTube uploads.

Both template files start with all 114 surahs in `WAITING`.

Queue statuses:

```txt
[~] STARTED
[ ] TODO
[?] WAITING
[x] DONE
[!] ERROR
[-] IGNORE
```

To start processing a surah, move it from `WAITING` into `TODO` and change the checkbox to empty:

```md
## TODO
- [ ] 94 - الشرح

## WAITING
- [?] 93 - الضحى
```

The scripts move items between sections as they run.

## Video Generation

Generate one surah manually:

```bash
python3 scripts/surah_automation.py --force 94
```

Process whatever is in `Todo.md`:

```bash
python3 scripts/surah_automation.py
```

Run the video watcher:

```bash
python3 scripts/todo_watcher.py
```

The watcher checks `Todo.md` and starts generation when the `TODO` section contains items.

Video generation includes:

- validating configured fonts
- validating QUL audio source configuration
- fetching surah text and translation data
- downloading ayah audio when needed
- combining audio into one track
- calculating timings
- writing subtitle/overlay data
- rendering MP4 with `ffmpeg`
- verifying the MP4 with `ffprobe`
- generating the thumbnail
- marking the surah `DONE` only when video and thumbnail are both valid

## YouTube Setup

Create a Google OAuth client for YouTube upload access, then place its client secret JSON file at the path configured in:

```json
"client_secret_file": "Your OAuth Client Secret JSON Path"
```

Generate the YouTube token:

```bash
python3 scripts/youtube_upload.py --auth-only
```

The script will open or print a Google authorization URL. After approval, it writes the token to the configured `token_file` path.

## YouTube Upload

Upload the next item from `YT_Todo.md`:

```bash
python3 scripts/youtube_upload.py
```

Run the YouTube watcher:

```bash
python3 scripts/yt_watcher.py
```

The YouTube uploader does this sequence:

- reads the next `TODO` item from `YT_Todo.md`
- verifies the MP4 and thumbnail exist
- formats title and description from `settings.json`
- calculates the publish schedule
- uploads the video to YouTube
- sets the YouTube thumbnail
- adds the video to the configured playlist
- records the schedule in `YT_Schedule.json`
- marks the item `DONE`

Add an already-uploaded video to the configured playlist:

```bash
python3 scripts/youtube_upload.py --playlist-video-id VIDEO_ID --surah 94
```

## Scheduling

Scheduling uses these settings:

```json
{
  "publish_time": "09:00",
  "gmt_offset_hours": 2,
  "publish_days_after_video_done": 0,
  "publish_between_scheduled_days": 1
}
```

Meaning:

- `publish_time` is the local publish time.
- `gmt_offset_hours` tells the script what local timezone offset to use.
- `publish_days_after_video_done` controls the first schedule date relative to generation/upload timing.
- `publish_between_scheduled_days` controls spacing between scheduled uploads.

Example:

```txt
09:00 GMT+2 -> 07:00Z sent to YouTube
```

`YT_Schedule.json` stores schedule history:

```json
{
  "latest_publish_at": null,
  "videos": []
}
```

When a video is uploaded successfully, the script records:

```json
{
  "surah": 94,
  "video_id": "VIDEO_ID",
  "publish_at": "2026-05-29T07:00:00Z",
  "recorded_at": "2026-05-27T20:06:43"
}
```

Future uploads use this schedule history so the next video is placed after the configured day gap.

## Logging

Logs are written to the configured `Logs.json` file.

Root metadata:

```json
{
  "timestamp": "...",
  "status": "...",
  "done": false,
  "has_error": false
}
```

Process sections:

```txt
trigger
video_generation
image_generation
youtube_upload
```

Each process section includes:

```json
{
  "status": "RUNNING",
  "done": false,
  "has_error": false,
  "current_step": "UPLOAD_START",
  "last_error": null,
  "events": []
}
```

Root process sections are written newest-first, so the most recently active process appears near the top.

## Optional Systemd Watchers

You can run the watchers manually, or configure user systemd services.

Example video watcher service:

```ini
[Unit]
Description=Surah Todo.md automation watcher

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/repo/scripts/todo_watcher.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Example YouTube watcher service:

```ini
[Unit]
Description=YouTube upload automation watcher

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/repo/scripts/yt_watcher.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

Enable and start a user service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now SERVICE_NAME.service
```

## Troubleshooting

OAuth app blocked:

- Add your Google account as a test user in Google Cloud OAuth consent settings.
- Re-run `python3 scripts/youtube_upload.py --auth-only`.

Missing YouTube Python packages:

- Install the Google API packages listed in the requirements section.

Insufficient playlist scope:

- Recreate the token after changing scopes:
  ```bash
  python3 scripts/youtube_upload.py --auth-only
  ```

YouTube title rejected:

- YouTube titles must be under 100 characters.
- Shorten `youtube.title_format` in `settings.json`.

Arabic shaping looks wrong:

- Verify Pango/Cairo dependencies are installed.
- Verify the configured Arabic font exists.

Watcher does nothing:

- Check that the wanted item is under the `TODO` section, not `WAITING`.
- Check `Logs.json` for `trigger` and process errors.

Same-day upload schedules:

- Check `YT_Schedule.json` and verify it contains the latest scheduled upload.
- The next upload is scheduled based on the latest known `publish_at` value.
