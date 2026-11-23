"""Layout fitting, frame checks, the quality check before upload, and the disk-space check."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports
from pipeline_fonts import pango_fontconfig_file
from pipeline_subtitles import wrap_arabic_ayah
from pipeline_render import finalize_render, render_mp4, write_pango_overlay_spec
from pipeline_pieces import cleanup_pieces


# Free space needed before a render: the planned video (~3.6 MB/min, x3 for temp files), at least min_free_gb.
MIN_FREE_GB = float(SETTINGS.get("min_free_gb", 5))
RENDER_MB_PER_MINUTE = 3.6


class LowDiskError(RuntimeError):
    pass


def check_disk_space(number: int, total_duration: float) -> None:
    need = max(MIN_FREE_GB * 1024**3, total_duration / 60 * RENDER_MB_PER_MINUTE * 1024**2 * 3)
    free = shutil.disk_usage(EXPORTED_DIR).free
    if free < need:
        raise LowDiskError(f"Low disk space: {free / 1024**3:.1f} GB free, surah {number} needs {need / 1024**3:.1f} GB. Free some space; it retries automatically.")
    log("DISK_OK", surah=number, free_gb=round(free / 1024**3, 1), need_gb=round(need / 1024**3, 1))


CHECK_W, CHECK_H = 960, 540   # frames are checked at half resolution
INK_THRESHOLD = 40             # grey-level difference from the bare background that counts as text
_BACKGROUND_GRAY: dict[tuple[str, int], bytes] = {}


def layout_config(arabic_name: str) -> "text_layout.LayoutConfig":
    return text_layout.LayoutConfig(
        arabic_font=PANGO_ARABIC_FONT, english_font=ENGLISH_PANGO_FONT,
        arabic_box_width=PANGO_ARABIC_BOX_WIDTH, english_box_width=ENGLISH_BOX_WIDTH,
        line_spacing=PANGO_ARABIC_LINE_SPACING,
        title_text=f"سورة {arabic_name}", title_font=PANGO_TITLE_FONT, title_y=LAYOUT_TITLE_Y,
        riwayah_text=RIWAYAH_TEXT, riwayah_font=PANGO_RIWAYAH_FONT, riwayah_y=LAYOUT_RIWAYAH_Y,
        arabic_y=LAYOUT_ARABIC_Y, english_top_y=ENGLISH_TOP_Y, gap=LAYOUT_GAP, bottom_margin=LAYOUT_BOTTOM_MARGIN,
        fit_step=LAYOUT_FIT_STEP, min_scale=LAYOUT_MIN_SCALE, fontconfig_file=str(pango_fontconfig_file()),
        split_below_scale=LAYOUT_SPLIT_BELOW_SCALE, max_split_parts=LAYOUT_MAX_SPLIT_PARTS,
    )


def wrap_for_scale(arabic: str, scale: float) -> str:
    """Smaller font -> proportionally more characters per line, so the block really gets shorter."""
    return wrap_arabic_ayah(arabic, max(1, round(ARABIC_AYAH_WRAP_WIDTH / scale)))


def layout_entries(ayat: list[str], translations: list[str], basmalah_timing: tuple[float, float] | None) -> list[tuple[str, str, str]]:
    entries = [("basmalah", BASMALAH_ARABIC_TEXT, BASMALAH_ENGLISH_TEXT)] if basmalah_timing is not None else []
    entries += [(str(i + 1), arabic, translations[i] if i < len(translations) else "") for i, arabic in enumerate(ayat)]
    return entries


def split_part_starts(number: int, ayah: int, start: float, end: float, parts: list["text_layout.SplitPart"]) -> list[float]:
    """When each part of a split ayah appears: the moment the reciter starts its first word (QUL word timings).

    Falls back to the part's share of the text when word timings are missing or look wrong.
    """
    rows = WORD_SEGMENTS.get((number, ayah)) or []
    starts = [start]
    if rows:
        window_start = min(float(row[2]) for row in rows) / 1000.0   # the ayah timing starts where the voice starts
        words_in_audio = max(int(row[1]) for row in rows)
    for part in parts[1:]:
        at = start + part.char_start * (end - start)
        if rows and part.word_total:
            first_word = round(part.word_start * words_in_audio / part.word_total)   # same index when the counts match
            hits = [float(row[2]) / 1000.0 for row in rows if int(row[0]) >= first_word]
            if hits:
                at = start + hits[0] - window_start
        low = starts[-1] + SPLIT_MIN_PART_SECONDS
        high = end - SPLIT_MIN_PART_SECONDS * (len(parts) - len(starts))
        if not low <= at <= high:
            at = min(max(start + part.char_start * (end - start), low), high)
        starts.append(at)
    return starts


def cue_times(basmalah_timing: tuple[float, float] | None, ayah_timings: list[tuple[float, float]],
              number: int = 0, splits: dict[str, list["text_layout.SplitPart"]] | None = None) -> dict[str, tuple[float, float]]:
    times = {str(i + 1): timing for i, timing in enumerate(ayah_timings)}
    if basmalah_timing is not None:
        times["basmalah"] = basmalah_timing
    for key, parts in (splits or {}).items():
        start, end = times[key]
        starts = split_part_starts(number, int(key), start, end, parts) + [end]
        for i, part in enumerate(parts):
            times[part.key] = (starts[i], starts[i + 1])
    return times


def gray_frame(source: Path, seconds: float | None, prescale: str = "") -> bytes:
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{seconds:.3f}"] if seconds is not None else [])
    cmd += ["-i", str(source), "-frames:v", "1", "-vf", f"{prescale}scale={CHECK_W}:{CHECK_H},format=gray", "-f", "rawvideo", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    if len(data) != CHECK_W * CHECK_H:
        raise RuntimeError(f"Unexpected frame size from {source} at {seconds}s")
    return data


def background_gray() -> bytes:
    key = (str(BG_IMAGE), BG_IMAGE.stat().st_mtime_ns)
    if key not in _BACKGROUND_GRAY:
        _BACKGROUND_GRAY[key] = gray_frame(BG_IMAGE, None, prescale="scale=1920:1080,setsar=1,")  # same chain as the render
    return _BACKGROUND_GRAY[key]


def check_time(start: float, end: float) -> float:
    """A moment well inside an ayah's screen time (after its fade-in)."""
    return start + min(max(FADE_AYAH_SECONDS + 0.15, (end - start) / 2), max(0.0, end - start - 0.1))


def inspect_frame(video: Path, at: float, placed: "text_layout.Placement", surah_layout: "text_layout.SurahLayout",
                  background: bytes) -> dict[str, object]:
    """Where is text drawn in this frame, compared with where this ayah was planned?"""
    frame = gray_frame(video, at)
    ink = [sum(1 for x in range(row * CHECK_W, (row + 1) * CHECK_W) if abs(frame[x] - background[x]) > INK_THRESHOLD) > 2
           for row in range(CHECK_H)]

    def rows(top: int, bottom: int) -> list[int]:  # full-res bounds -> ink rows (half-res, inclusive)
        return [r for r in range(max(0, top // 2), min(CHECK_H, (bottom + 1) // 2 + 1)) if ink[r]]

    bands = [(placed.arabic_top, placed.arabic_bottom)]
    if placed.english_top is not None:
        bands.append((placed.english_top, placed.english_bottom))
    found = all(rows(top, bottom) for top, bottom in bands)
    # empty space required: riwayah -> Arabic, Arabic -> English, below the last block (2-row tolerance at edges)
    gaps = [(surah_layout.riwayah_bounds[1] + 4, placed.arabic_top - 4)]
    if placed.english_top is not None:
        gaps.append((placed.arabic_bottom + 4, placed.english_top - 4))
    gaps.append((bands[-1][1] + 4, 1080 - 1))
    intrusions = {f"{top}-{bottom}": len(rows(top, bottom)) * 2 for top, bottom in gaps if bottom > top and rows(top, bottom)}
    zone = rows(surah_layout.riwayah_bounds[1] + 4, 1080 - 1)
    return {
        "text_found": found,
        "intrusions_px": intrusions,
        "any_text": any(ink[r] for r in range(max(0, surah_layout.zone_top // 2 - 4), CHECK_H)),
        # drawn text extent (full-res px) vs. the planned ink extent of this ayah
        "ink_top": zone[0] * 2 if zone else None,
        "ink_bottom": zone[-1] * 2 + 1 if zone else None,
        "planned_top": bands[0][0],
        "planned_bottom": bands[-1][1],
    }


def validate_render(number: int, video: Path, surah_layout: "text_layout.SurahLayout", times: dict[str, tuple[float, float]]) -> tuple[bool, bool, dict[str, object]]:
    """Check real frames at the tightest ayat: text must sit only inside its planned bands.

    Returns (ok, conclusive, report). conclusive=False means the text could not be found at all
    (timing/render problem, not a layout problem).
    """
    background = background_gray()
    checked: list[dict[str, object]] = []
    ok = conclusive = True
    to_check = surah_layout.tightest(LAYOUT_CHECK_FRAMES)
    to_check += [p for key, p in surah_layout.placements.items() if "." in key and p not in to_check]  # every split part
    for placed in to_check:
        start, end = times[placed.key]
        at = check_time(start, end)
        seen = inspect_frame(video, at, placed, surah_layout, background)
        found, intrusions, any_text = seen["text_found"], seen["intrusions_px"], seen["any_text"]
        checked.append({"ayah": placed.key, "at": round(at, 2), "text_found": found, "intrusions_px": intrusions})
        if not any_text:          # nothing drawn at all: a timing/render problem, not a layout one
            conclusive = ok = False
        elif not found or intrusions:  # text exists but not (only) where planned -> layout failure
            ok = False
    report = {"scale": surah_layout.scale, "frames": checked}
    log("LAYOUT_CHECK_OK" if ok else "LAYOUT_CHECK_FAILED", level="INFO" if ok else "WARNING", surah=number, **report)
    return ok, conclusive, report


class QualityCheckError(RuntimeError):
    pass


def voice_onsets(video: Path) -> list[float]:
    """Moments the recitation starts again after a pause, measured in the finished video's own audio."""
    result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn",
                             "-af", f"silencedetect=noise={VOICE_START_THRESHOLD_DB}dB:d=0.15", "-f", "null", "-"],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise QualityCheckError(f"Could not read the video's audio: {result.stderr[-300:]}")
    return [float(v) for v in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]


def stream_durations(video: Path) -> dict[str, float]:
    data = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(video)],
                                     capture_output=True, text=True, check=True).stdout)
    return {s.get("codec_type"): float(s.get("duration") or 0) for s in data.get("streams", [])}


def quality_check(number: int, video: Path, surah_layout: "text_layout.SurahLayout", times: dict[str, tuple[float, float]],
                  ayah_count: int, total_duration: float) -> dict[str, object]:
    """Before upload: every ayah is on screen, complete, at the right time, and in sync with the recitation.
    Raises QualityCheckError listing what is wrong."""
    problems: list[str] = []

    # 1. Coverage: each ayah 1..N has a screen (or its split parts), in order, no overlaps, long enough.
    screens = [key for key in surah_layout.placements if key != "basmalah"]
    for i in range(1, ayah_count + 1):
        if str(i) not in surah_layout.placements and f"{i}.1" not in surah_layout.placements:
            problems.append(f"ayah {i}: no screen")
    extra = sorted({key.split(".")[0] for key in screens} - {str(i) for i in range(1, ayah_count + 1)}, key=lambda k: int(k))
    if extra:
        problems.append(f"screens for ayat that do not exist: {', '.join(extra)}")
    ordered = sorted(screens, key=lambda key: times[key][0])
    previous_end, previous_key = 0.0, None
    for key in ordered:
        start, end = times[key]
        if end - start < QUALITY_MIN_SCREEN_SECONDS:
            problems.append(f"ayah {key}: on screen only {end - start:.2f}s")
        if previous_key is not None and start < previous_end - 0.05:
            problems.append(f"ayah {key} starts {previous_end - start:.2f}s before ayah {previous_key} ends")
        if end > total_duration + 0.05:
            problems.append(f"ayah {key} ends after the video ({end:.1f}s > {total_duration:.1f}s)")
        previous_end, previous_key = end, key
    numbers = [int(key.split(".")[0]) for key in ordered]
    if numbers != sorted(numbers):
        problems.append("ayat are not shown in order")

    # 2. Every screen, as rendered: text present, nothing outside its area, and the drawn text has the
    #    planned height/position (a missing line, cut-off text or the wrong ayah would not match).
    background = background_gray()
    frames: list[dict[str, object]] = []
    for key in ordered:
        placed = surah_layout.placements[key]
        start, end = times[key]
        at = check_time(start, end)
        seen = inspect_frame(video, at, placed, surah_layout, background)
        top_off = None if seen["ink_top"] is None else int(seen["ink_top"]) - int(seen["planned_top"])
        bottom_off = None if seen["ink_bottom"] is None else int(seen["ink_bottom"]) - int(seen["planned_bottom"])
        frames.append({"ayah": key, "at": round(at, 2), "found": seen["text_found"], "top_off": top_off, "bottom_off": bottom_off,
                       **({"intrusions_px": seen["intrusions_px"]} if seen["intrusions_px"] else {})})
        if not seen["any_text"]:
            problems.append(f"ayah {key}: no text on screen at {at:.1f}s")
        elif not seen["text_found"]:
            problems.append(f"ayah {key}: text missing from its planned area at {at:.1f}s")
        elif seen["intrusions_px"]:
            problems.append(f"ayah {key}: text outside its area at {at:.1f}s ({seen['intrusions_px']})")
        elif not (-QUALITY_EXTENT_TOLERANCE_PX <= top_off <= QUALITY_TOP_FAINT_PX) or abs(bottom_off) > QUALITY_EXTENT_TOLERANCE_PX:
            problems.append(f"ayah {key}: drawn text does not match the plan at {at:.1f}s (top {top_off:+d}px, bottom {bottom_off:+d}px)")

    # 3. Audio: present, as long as the video, and each ayah appears when its recitation starts.
    durations = stream_durations(video)
    if "audio" not in durations:
        problems.append("the video has no audio")
    elif abs(durations["audio"] - durations.get("video", durations["audio"])) > 0.5:
        problems.append(f"audio is {durations['audio']:.1f}s but video is {durations.get('video', 0):.1f}s")
    onsets = voice_onsets(video)
    offsets: dict[str, float] = {}
    unverified: list[str] = []
    for key in ["basmalah"] * ("basmalah" in times) + [str(i) for i in range(1, ayah_count + 1) if str(i) in times]:
        start = times[key][0]
        near = [onset - start for onset in onsets if abs(onset - start) <= 1.5]
        if near:
            offsets[key] = round(min(near, key=abs), 3)
        else:
            unverified.append(key)   # no pause right before it (continuous recitation): can't measure
    sync = {"measured": len(offsets), "unverified": len(unverified)}
    if offsets:
        values = sorted(abs(v) for v in offsets.values())
        median = values[len(values) // 2]
        bad = {k: v for k, v in offsets.items() if abs(v) > QUALITY_SYNC_MAX_OFFSET}
        sync.update(median_s=median, max_s=values[-1], late_or_early=bad)
        if median > QUALITY_SYNC_MAX_MEDIAN:
            problems.append(f"audio sync: ayat appear {median:.2f}s off on average")
        if len(bad) > max(1, QUALITY_SYNC_MAX_BAD_SHARE * len(offsets)):
            worst = sorted(bad.items(), key=lambda kv: -abs(kv[1]))[:5]
            problems.append(f"audio sync: {len(bad)} ayat off by more than {QUALITY_SYNC_MAX_OFFSET}s, e.g. "
                            + ", ".join(f"ayah {k} {v:+.2f}s" for k, v in worst))
    if len(offsets) < 0.3 * (len(offsets) + len(unverified)):
        problems.append(f"audio sync: could only measure {len(offsets)} of {len(offsets) + len(unverified)} ayat")

    report = {"ayat": ayah_count, "screens": len(ordered), "sync": sync, "problems": problems[:20],
              "frames_checked": len(frames), "frames": [f for f in frames if not f["found"] or "intrusions_px" in f
                                                         or not (-QUALITY_EXTENT_TOLERANCE_PX <= (f["top_off"] or 0) <= QUALITY_TOP_FAINT_PX)
                                                         or abs(f["bottom_off"] or 0) > QUALITY_EXTENT_TOLERANCE_PX][:20]}
    if problems:
        log("QUALITY_CHECK_FAILED", level="ERROR", surah=number, **report)
        more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
        raise QualityCheckError("Quality check failed: " + "; ".join(problems[:3]) + more)
    log("QUALITY_CHECK_OK", surah=number, **report)
    return report


def render_validated(number: int, arabic_name: str, ayat: list[str], translations: list[str], mp3: Path, ass_file: Path,
                     total_duration: float, basmalah_timing: tuple[float, float] | None, ayah_timings: list[tuple[float, float]]) -> Path:
    """Fit the layout, render, check real frames; on a failed check shrink this surah's fonts and render again."""
    if not PANGO_ARABIC_OVERLAY:
        return render_mp4(number, mp3, ass_file, total_duration, None)
    config = layout_config(arabic_name)
    entries = layout_entries(ayat, translations, basmalah_timing)
    scale = text_layout.read_override_scale(LAYOUT_OVERRIDES_FILE, number)
    reason = "saved override" if scale < 1.0 else "fit"
    rendering: Path | None = None
    for attempt in range(1, LAYOUT_MAX_RENDERS + 1):
        try:
            surah_layout, splits = text_layout.fit_with_splits(config, entries, scale, wrap_for_scale)
        except text_layout.LayoutError as exc:
            raise RuntimeError(f"Layout: {exc}") from exc
        times = cue_times(basmalah_timing, ayah_timings, number, splits)
        for key, parts in splits.items():
            log("LAYOUT_SPLIT", surah=number, ayah=key, parts=len(parts),
                starts=[round(times[part.key][0], 2) for part in parts], ayah_end=round(times[key][1], 2))
        tight = surah_layout.tightest(1)[0]
        log("LAYOUT_FIT", surah=number, attempt=attempt, scale=surah_layout.scale, arabic_font=surah_layout.arabic_font,
            english_font=surah_layout.english_font, tightest_ayah=tight.key, tightest_slack_px=tight.slack)
        if surah_layout.scale < scale:
            reason = "fit"
        spec = write_pango_overlay_spec(number, arabic_name, ayat, basmalah_timing, ayah_timings, translations, surah_layout, splits, times)
        rendering = render_mp4(number, mp3, ass_file, total_duration, spec, finalize=False)
        ok, conclusive, report = validate_render(number, rendering, surah_layout, times)
        if ok:
            text_layout.save_override(LAYOUT_OVERRIDES_FILE, number, surah_layout.scale, surah_layout.arabic_font, surah_layout.english_font, reason)
            if surah_layout.scale < 1.0:
                log("LAYOUT_SMALLER_FONTS", surah=number, scale=surah_layout.scale, reason=reason)
            if QUALITY_CHECK_ENABLED:
                try:
                    quality_check(number, rendering, surah_layout, times, len(ayah_timings), total_duration)
                except QualityCheckError:
                    failed = EXPORTED_DIR / f"{number}.quality-failed.mp4"   # kept for inspection, never uploaded
                    os.replace(rendering, failed)
                    cleanup_pieces(number)
                    raise
            final = finalize_render(number, rendering, EXPORTED_DIR / f"{number}.mp4", total_duration)
            cleanup_pieces(number)   # the pieces were only needed until the finished video is in place
            return final
        if not conclusive:
            raise RuntimeError(f"Layout check could not find the text in the rendered frames: {report['frames']}")
        scale = round(surah_layout.scale * LAYOUT_FIT_STEP, 4)
        reason = "frame check"
        if scale < LAYOUT_MIN_SCALE:
            break
        text_layout.save_override(LAYOUT_OVERRIDES_FILE, number, scale, text_layout.scale_font(PANGO_ARABIC_FONT, scale),
                                  text_layout.scale_font(ENGLISH_PANGO_FONT, scale), reason)
        log("LAYOUT_RERENDER", surah=number, attempt=attempt, next_scale=scale)
    # keep the last failed render and a frame of it for inspection
    failed = EXPORTED_DIR / f"{number}.layout-failed.mp4"
    if rendering is not None and rendering.exists():
        os.replace(rendering, failed)
    cleanup_pieces(number)
    raise RuntimeError(f"Layout check still failing after {LAYOUT_MAX_RENDERS} renders; kept {failed.name}")
