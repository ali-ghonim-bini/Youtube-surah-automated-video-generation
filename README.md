# YouTube Surah Automated Video Generation

Generates Quran recitation videos (one per surah) and uploads them to YouTube, fully automatically.

- **Recitation:** any reciter that [Qul](https://qul.tarteel.ai) offers ayah by ayah with timing data. The example settings use Sheikh Mahmoud Khalil Al‑Husary, Mujawwad, Hafs from Asim.
- **Text:** Arabic ayat + English translation from [surahquran.com](https://surahquran.com), synced to the audio.
- **Output:** 1080p video + thumbnail per surah, uploaded as a scheduled private video, added to the channel playlist, published one per day in surah order.

Everything runs on a Linux laptop as two `systemd --user` services.

---

## How it works

```
 Queue.md: TODO (make the video)            Queue.md: READY (upload it)     
 ┌──────────────────────────┐   hand-off    ┌──────────────────────────┐
 │ auto-queue picks next    │  ───────────▶ │ uploads strictly in      │
 │ surah (50, 49, 48 …)     │  video DONE → │ order (53 before 52 …)   │
 └────────────┬─────────────┘  upload TODO  └────────────┬─────────────┘
              ▼                                          ▼
   surah-todo-watcher.service                 surah-yt-watcher.service
   todo_watcher.py → surah_automation.py      yt_watcher.py → youtube_upload.py
              ▼                                          ▼
   سور/Exported_Vids/<n>.mp4                  YouTube (private, scheduled)
   Visual/Thumbnails/<n>.png                  + playlist, YT_Schedule.json
```

1. **Auto‑queue** (`auto_queue.py`) keeps the generation queue fed: when the pipeline is idle it moves the next surah to TODO in `Queue.md`. The **work order** is the same everywhere (generation, upload, publish dates): surahs in `first_surahs` (Al‑Fatiha, `[1]`) first, then 114, 113 … down to 2.
2. **Generation** (`surah_automation.py`) downloads the audio and text, builds the timing, renders the video with ffmpeg (Arabic drawn by a small Pango/Cairo helper, `pango_arabic_overlay.c`) and makes the thumbnail.
3. **Hand‑off:** a finished video moves its surah to READY in `Queue.md`.
4. **Upload** (`youtube_upload.py`) uploads, sets the thumbnail, adds it to the playlist and schedules publishing. After each upload the publish dates are re‑assigned so the channel publishes in surah order, one per day, with no gaps.

### Ignored surahs

Surahs in `skip_surah_numbers` (currently **18**) are never passed silently. When the queue reaches the surah just before one, a desktop notification asks *“make & upload it?”* (Yes / No). If a video for it already exists, a second question asks whether to **generate it again** or **upload the existing one**. Answers are stored in `سور/Decisions.json`:

```json
{"ignored_surahs": {"18": "yes"}, "regenerate": {"18": "no"}}
```

With no answer the queue pauses right before that surah and reminds you after 6 h. If you answer **No**, it is also set to IGNORE for the upload so the uploads below it are not held up; while a question is unanswered, the uploader tells you why uploads are waiting.

You can also skip the question: move it to TODO in `Queue.md` yourself (or run `surah_automation.py --force 18`). It is then generated fresh, even if an old video exists, and `skip_surah_numbers` no longer sends it back to IGNORE: that setting only sets the starting status.

### Text layout

Title, riwayah, Arabic ayah and English translation are stacked with a fixed gap between them and never overlap (`scripts/layout.py`).

- Every ayah is measured with the same Pango settings the renderer uses. If one doesn't fit, the whole surah's fonts are scaled down a little; the chosen scale is saved per surah in `سور/Layout_Overrides.json` (never in `settings.json`).
- **Very long ayat are split** instead of shrinking the surah below `split_below_scale` (85%): the ayah is shown as two screens (or more), cut at a pause mark (ۚ ۖ ۗ) near the middle, with the English cut at the matching sentence. Part 2 appears the moment the reciter starts its first word (QUL word timings). Only the last part shows the ayah number.
- After rendering, the service checks real frames (the tightest ayat and every split part): text must sit only in its planned area. If the check fails it shrinks the fonts and renders again (up to 3 times).

### Quality check before upload

After the layout check passes, the finished video is checked again **before** it gets its final name and is queued for upload (`quality_check` in `surah_automation.py`):

- **Every ayah is there:** each ayah 1…N has a screen (or its split parts), in order, with no overlaps, each on screen at least 1 s.
- **Every ayah is shown in full:** one frame per screen (all of them, not a sample): the text is in its planned area, nothing outside it, and the drawn text has the planned height, so a missing line, cut-off text or the wrong ayah is caught.
- **Audio sync:** the recitation's own pauses are found in the finished video's audio. Each ayah must appear when its recitation starts: it fails if the average is off by more than 0.3 s or more than 10% of ayat are off by more than 0.5 s. The audio must also be as long as the video.

If anything fails, the surah goes to ERROR with a notification naming the ayah and the problem, the video is kept as `<n>.quality-failed.mp4` for inspection, and it is **not** uploaded. Takes about 15–60 s per surah. Settings (optional) under `video.quality_check`: `enabled`, `sync_max_offset_seconds`, `sync_max_median_seconds`, `sync_max_bad_share`, `text_extent_tolerance_px`, `text_top_faint_px`.

---

## Queue files

There is one queue, `سور/Queue.md`, a Markdown checklist you can edit by hand. Each surah is one line and moves down these stages:

| Section | Mark | Meaning |
|---|---|---|
| `## WAITING` | `[?]` | not yet |
| `## TODO` | `[ ]` | its video will be made next |
| `## RENDERING` | `[~]` | the video is being made (continues by itself after a crash or restart) |
| `## READY` | `[>]` | the video is made and waits to be uploaded |
| `## UPLOADING` | `[^]` | being uploaded (continues by itself after a restart) |
| `## DONE` | `[x]` | uploaded |
| `## ERROR` | `[!]` | failed: fix it, then move it to TODO (video failed) or READY (upload failed) |
| `## IGNORE` | `[-]` | skipped |

- **To change what happens to a surah, move its line to another section and save.** A finished surah moved to TODO is made again; the new video is not uploaded a second time unless you then move it to READY.
- A line can end with a note such as `(video DONE, upload ERROR)`. That is how the file shows a surah whose video and upload are not at the usual point for its section, for example a failed upload. You never need to type or edit these notes: moving the line replaces them.
- A surah uploads only when **every surah before it in the work order (1, then 114 → 2) is DONE or IGNORE**.
- Inside the code the queue still has two sides, video and upload (`todo_file.py`). `"todo_file"` and `"youtube.todo_file"` in `settings.json` are now only the names of those two sides; no such files exist. Remove `"queue_file"` from `settings.json` to go back to the two separate files `Todo.md` and `YT_Todo.md`.
- The first run with `"queue_file"` builds `Queue.md` from `Todo.md` and `YT_Todo.md` and keeps the two old files as `Todo.md.migrated` and `YT_Todo.md.migrated`.

- `سور/YT_Schedule.json` — uploaded video IDs and publish dates.
- `سور/Logs.json` — summary log: the newest 200 events per section (`trigger`, `video_generation`, `youtube_upload`), long texts shortened.
- `سور/Logs/events-YYYY-MM.jsonl` — the complete history, one JSON event per line, one file per month (12 months kept).

---

## Reliability features

- **Stays awake while working:** each render/upload holds a `systemd-inhibit` idle‑sleep lock (`keep_awake.py`). Closing the lid still suspends.
- **Complete renders only:** videos render to `<n>.rendering.mp4` and are renamed to `<n>.mp4` only after their length matches the plan. Unreadable videos are renamed `*.corrupt-<time>.mp4` and regenerated.
- **Renders continue after a restart:** the video is rendered in 2‑minute pieces into `Exported_Vids/.pieces/` and joined at the end without re‑encoding. If the computer restarts or the service is stopped, the next run renders only the missing pieces. The pieces are removed once the finished video is in place. Settings under `video.render`: `piece_seconds` (120), `parallel_pieces` (2; each extra piece rendering at once needs about 1 GB of memory), `threads_per_piece` (4), `encoder_preset` (`"veryfast"`; `"medium"` is the encoder's own default: about half the speed, larger files, no visible difference), and `mode` (`"pieces"`, or `"single"` for the old one‑pass render).
- **Uploads continue after a restart:** the address of an unfinished upload is kept in `.upload_session.json`. The next run asks YouTube how much it already has and sends only the rest, with the same publish date. If YouTube has dropped the unfinished upload (after about a week), it starts over.
- **Check on YouTube, then free the disk** (`cleanup.py`): once an hour the upload service asks YouTube about every uploaded surah that still has local files. When YouTube reports the video as fully processed and its length matches the local video (within 2 s), that surah's local files are removed: the video and leftovers of failed renders, the ayah audio clips and the combined audio, the thumbnail, the caption text files and the temporary subtitle files. Shared files (basmalah audio, fonts, background, queue, logs, schedule, layout sizes) are never touched, and nothing is ever deleted on YouTube. A surah that is being made again keeps its files. If YouTube reports a failed or rejected upload, or a different length, you get a notification and the files stay. Switch it off with `"cleanup": {"delete_local_after_upload": false}`. To see what would be removed without removing anything: `python3 scripts/youtube_upload.py --cleanup-uploaded --dry-run`.
- **A finished series goes quiet:** once every surah is uploaded, the upload service stops its hourly questions to YouTube (deleted‑video check, login reminder). The local cleanup runs only until no local files are left. Moving a surah back to TODO or READY wakes it up again.
- **Retries:** API calls retry with backoff; quota, rate‑limit and outage errors keep the surah queued and retry every 30 min, a lost internet connection after 5 min (no error, no traceback). A video that was uploaded but failed a later step is never uploaded twice.
- **Disk space:** before each render the service checks there is room for the video (at least `min_free_gb`, 5 GB). If not, the surah stays in TODO, you get a notification, and it retries every 10 min.
- **Safe file edits:** every queue change, by every script, is made one surah at a time under a lock (`todo_file.py`), so the services and your own edits don't overwrite each other.
- **Checks before an upload** (`preflight.py`): title ≤ 100 characters, description ≤ 5000 bytes, no `<` `>`, tags ≤ 500 characters, video present, thumbnail present and ≤ 2 MB, publish time in the future, playlist exists. A failure goes to ERROR with the reason before any upload quota is spent.
- **Laptop slept:** if the laptop was suspended while a render or upload was in progress, you get a notice with the times; the work continues by itself.
- **Daily summary:** once a day (`notifications.daily_summary_hour`, default 21; `null` = off): what was made and uploaded today, what is left, and anything that needs you.
- **Notifications** (`notify.py`): desktop notifications for video ready / failed, uploaded, upload postponed / failed, login required. Optional phone alerts via [ntfy](https://ntfy.sh).
- **YouTube login:** if the token is missing or expired, uploads pause with a “login required” notification instead of hanging.
- **Login reminder:** while the OAuth app is in Google's *Testing* mode, a login lasts only 7 days. The upload service warns you a day before it runs out (once a day). `--auth-only` always opens a fresh browser login, which restarts the 7 days; the login time is kept in `auth_certification/token_meta.json`. Set `"oauth_testing_mode": false` under `youtube` in `settings.json` once the app is *In production*.

---

## Setup

### Requirements

```bash
sudo apt install ffmpeg python3-cairo libpango1.0-dev libcairo2-dev pkg-config build-essential libnotify-bin fonts-noto-core fonts-liberation
python3 -m pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2
```

Fonts used: **Noto Sans Arabic** and **Liberation Sans** (checked at start‑up).

### Configuration

All paths and options are in [`scripts/settings.json`](scripts/settings.json): folders, fonts, video layout, thumbnail style, YouTube metadata/playlist/publish time, `auto_queue`, `notifications`, `captions`.

The file in this repository is a working example with placeholder paths. Before the first run:

1. Replace every `/path/to/Channel` with your own folder. The queue, logs, audio, videos and thumbnails are created there by the services.
2. Copy `Surhs.txt` and `Caption_Languages.json` from the repository root to the places `surah_file` and `captions.languages_file` point at.
3. Put your background picture where `video.background_image` points (any picture; it is scaled to 1920x1080).
4. Fill in `youtube.playlist_id` (and `playlist_url`) with your own playlist, or leave them empty to skip the playlist step.
5. Adjust the reciter (`audio.*`), the title and description templates and the tags under `youtube`.

Two settings are deliberately cautious in this example. `cleanup.delete_local_after_upload` is `false`: set it to `true` only if you want a surah's local video, audio and thumbnail removed once YouTube has confirmed the upload. `notifications.ntfy_url` is empty: add your own [ntfy](https://ntfy.sh) topic for phone alerts.

To check that the settings are read correctly, without rendering or uploading anything:

```bash
python3 scripts/status.py
```

### The next series

The two services work on **one series at a time**. A series is one set of videos: a reciter, a look, a playlist, and its own queue, audio, videos and log, all described by one `settings.json`. When a series is finished you create the next one and switch to it; the finished one is never worked on again, also after the computer restarts.

```bash
python3 scripts/series.py new warsh      # creates series/warsh/ ; starts nothing
python3 scripts/series.py use warsh      # the services now work on warsh only
python3 scripts/series.py which          # shows the active series
python3 scripts/series.py use main       # back to scripts/settings.json
```

- `new` creates `series/warsh/` with its own `settings.json` (every working path inside that folder, the playlist left empty) and copies of `Surhs.txt` and `Caption_Languages.json`. It lists the settings to edit first: the recitation (`audio.*`), the playlist, the title and description templates, the tags and the background. `surahquran.page_url_template` chooses the text and translation page. The YouTube login is shared.
- `use` records the choice in `scripts/active_series.json` and restarts the two services. They read that file at every start, so the choice survives a restart of the computer. While the file does not exist, the active series is `scripts/settings.json`. `use` refuses while the current series is in the middle of a render or an upload (`--force` stops it and switches anyway).
- The new series has its own empty queue, so it starts from the first surah.

Two settings help when one series follows another:

- `"series_name"` is put in front of every notification title, for example `[Alafasy] Uploaded surah 5`.
- `"youtube.publish_after_schedule_file"` names the previous series' `YT_Schedule.json`. The first video of the new series is then scheduled for the day after the previous series' last video, and the rest follow one per day. The file is read at every upload, so a changed date is followed; if it is set but cannot be read, the upload stops with an error rather than publishing early.

To look at a series without making it active, point a single command at it:

```bash
SURAH_SETTINGS=series/warsh/settings.json python3 scripts/status.py
```

### YouTube credentials (not in the repo)

1. In Google Cloud Console create an OAuth client (Desktop app) with the YouTube Data API v3 enabled, and save it as `auth_certification/client_secret.json`.
2. Set the OAuth consent screen to **In production** — in “Testing” mode the login expires every 7 days.
3. Log in (opens the browser and writes `auth_certification/token.json`). In *Testing* mode, repeat this when the reminder asks, about once a week:

   ```bash
   python3 scripts/youtube_upload.py --auth-only
   ```

`auth_certification/` is git‑ignored. Keep it private (`chmod 700` the folder, `600` the files).

### Services

Create `~/.config/systemd/user/surah-todo-watcher.service`:

```ini
[Unit]
Description=Channel todo watcher (surah video generation)
After=default.target

[Service]
Type=simple
WorkingDirectory=/path/to/Channel/scripts
ExecStart=/usr/bin/python3 /path/to/Channel/scripts/todo_watcher.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

and `surah-yt-watcher.service` the same way with `yt_watcher.py` and the description *Channel YouTube todo watcher (video upload)*. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now surah-todo-watcher.service surah-yt-watcher.service
loginctl enable-linger "$USER"   # start at boot, even without logging in
```

---

## Everyday use

| Task | How |
|---|---|
| Watch live | `journalctl --user -u surah-todo-watcher -u surah-yt-watcher -f` |
| Status (one screen) | `python3 scripts/status.py`: services, both queues, the render in progress with % done and time left, next publish dates, login expiry, disk space, last error |
| Service status | `systemctl --user status surah-todo-watcher surah-yt-watcher` |
| Stop / start | `systemctl --user stop …` / `systemctl --user start …` |
| Re‑generate a surah | move it to TODO in `Queue.md` (or `surah_automation.py --force <n>`) |
| Re‑upload a surah | delete the video on YouTube, then move it to READY in `Queue.md` |
| Fix publish order | `python3 scripts/youtube_upload.py --reorder-schedule` |
| Captions for a video | `python3 scripts/youtube_upload.py --captions-video-id <id> --surah <n>` |
| Turn off auto‑queue | `"auto_queue": {"enabled": false}` in `settings.json` |

### YouTube quota

The default quota is 10,000 units/day. Without captions an upload costs about **100 units** (thumbnail 50 + playlist 50; the video itself uses its own 100/day bucket), plus 50 per publish‑date change. Captions cost 400 units **per language**, so they're off for new uploads (`captions.upload_on_new_videos`).

---

## Repository layout

```
scripts/
  surah_automation.py     video generation: entry point and main loop
  pipeline_config.py        settings, constants, log
  pipeline_queue.py         which surahs to make; queue statuses
  pipeline_sources.py       downloads with retries; text and translation
  pipeline_audio.py         ayah-by-ayah audio and timing
  pipeline_subtitles.py     timeline, ASS subtitles, optional MLT project
  pipeline_render.py        thumbnail, overlay spec, ffmpeg render
  pipeline_pieces.py        render in pieces, continue a stopped render, join
  pipeline_checks.py        layout fit, frame checks, quality check, disk check
  layout.py               text measuring, stacking and ayah splitting
  pango_arabic_overlay.c  Arabic text renderer (built automatically)
  youtube_upload.py       YouTube upload, thumbnail, playlist, scheduling
  preflight.py            checks before an upload
  cleanup.py              check an uploaded video on YouTube; remove a surah's local files
  series.py               create the next series; choose which one the services work on
  todo_watcher.py         service: runs generation when Queue.md has work in TODO
  yt_watcher.py           service: runs uploads; login reminder, daily summary
  auto_queue.py           keeps the generation queue fed; ignored-surah questions
  todo_file.py            the queue files: locked, atomic, one surah at a time; work order
  status.py               one-screen status; daily summary
  keep_awake.py           sleep inhibitor around jobs
  notify.py               desktop / ntfy notifications
  shared_log.py           Logs.json summary + monthly history
  settings.json           configuration
Surhs.txt                 surah names (copy to your data folder)
Caption_Languages.json    caption languages (optional; copy to your data folder)
YT_Schedule.json          empty upload schedule (created automatically if missing)
```

The queue (`Queue.md`), logs, audio, videos, thumbnails and credentials live in your own data folder, outside this repository, and are never committed.

## Credits

Audio and ayah timings: [Qul](https://qul.tarteel.ai) · Text and translation: [surahquran.com](https://surahquran.com)
