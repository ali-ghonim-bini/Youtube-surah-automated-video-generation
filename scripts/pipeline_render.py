"""Thumbnail, Pango overlay spec and the ffmpeg render."""
from __future__ import annotations

import hashlib

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports
from pipeline_fonts import ffmpeg_filter_escape, pango_environment
from pipeline_subtitles import timeline, wrap_arabic_ayah


def pango_helper_binary() -> Path:
    source = SCRIPT_DIR / "pango_arabic_overlay.c"
    # the name carries the source's fingerprint, so two checkouts of the code never run each other's helper
    binary = Path(tempfile.gettempdir()) / "surah_automation" / f"pango_arabic_overlay-{hashlib.sha1(source.read_bytes()).hexdigest()[:12]}"
    binary.parent.mkdir(parents=True, exist_ok=True)
    if binary.exists():
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


def write_pango_overlay_spec(number: int, arabic_name: str, ayat: list[str], basmalah_timing: tuple[float, float] | None, ayah_timings: list[tuple[float, float]],
                             translations: list[str] | None = None, surah_layout: "text_layout.SurahLayout | None" = None,
                             splits: dict[str, list["text_layout.SplitPart"]] | None = None,
                             times: dict[str, tuple[float, float]] | None = None) -> Path | None:
    if not PANGO_ARABIC_OVERLAY:
        return None
    tmp_dir = TEMP_DIR
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
        ltr: bool = False,
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
            + ("\tltr" if ltr else "")
        )

    add_cue(0.0, INTRO_CREDIT_SECONDS, FADE_CREDIT_SECONDS, INTRO_CREDIT_ARABIC_TEXT, INTRO_ARABIC_Y, INTRO_ARABIC_FONT, INTRO_ARABIC_BOX_WIDTH, INTRO_ARABIC_COLOR, setting_float("video.intro.fade_out_seconds", 0.4))
    title_end = ayah_timings[-1][1] if ayah_timings else timeline(0, len(ayat))[0]
    add_cue(INTRO_CREDIT_SECONDS + 0.4, title_end, FADE_TITLE_SECONDS, f"سورة {arabic_name}", LAYOUT_TITLE_Y, PANGO_TITLE_FONT, PANGO_ARABIC_BOX_WIDTH, TITLE_COLOR)
    add_cue(INTRO_CREDIT_SECONDS + 0.4, title_end, FADE_RIWAYAH_SECONDS, RIWAYAH_TEXT, LAYOUT_RIWAYAH_Y, PANGO_RIWAYAH_FONT, PANGO_ARABIC_BOX_WIDTH, RIWAYAH_COLOR)

    def add_pair(key: str, start: float, end: float, fade: float, arabic: str, english: str) -> None:
        if surah_layout is None:  # legacy: Arabic centred on a fixed line, English comes from the ASS file
            add_cue(start, end, fade, arabic)
            return
        placed = surah_layout.placements[key]
        add_cue(start, end, fade, placed.arabic_text, placed.arabic_center_y, surah_layout.arabic_font)
        if english.strip() and placed.english_center_y is not None:
            add_cue(start, end, fade, english, placed.english_center_y, surah_layout.english_font, ENGLISH_BOX_WIDTH, ENGLISH_COLOR, ltr=True)

    translations = translations or []
    if basmalah_timing is not None:
        start, end = basmalah_timing
        add_pair("basmalah", start, end, FADE_BASMALAH_SECONDS, BASMALAH_ARABIC_TEXT, BASMALAH_ENGLISH_TEXT)
    for index, arabic in enumerate(ayat):
        start, end = ayah_timings[index]
        english = translations[index] if index < len(translations) else ""
        key = str(index + 1)
        if surah_layout is not None and splits and key in splits and times:
            for part in splits[key]:   # one ayah shown as several screens; only the last part has the number
                part_start, part_end = times[part.key]
                add_pair(part.key, part_start, part_end, FADE_AYAH_SECONDS, part.arabic, part.english)
            continue
        add_pair(key, start, end, FADE_AYAH_SECONDS, pango_text(arabic), english)
    _title_end, _intro_end, _usable_start, _usable_end, _per_ayah, outro_start, total_duration = timeline(0, len(ayat))
    if ayah_timings:
        outro_start = ayah_timings[-1][1]
    add_cue(outro_start, outro_start + OUTRO_CREDIT_SECONDS, setting_float("video.outro.fade_in_seconds", FADE_CREDIT_SECONDS), OUTRO_CREDIT_ARABIC_TEXT, OUTRO_ARABIC_Y, OUTRO_ARABIC_FONT, OUTRO_ARABIC_BOX_WIDTH, OUTRO_ARABIC_COLOR, OUTRO_FADE_OUT_SECONDS)
    spec.write_text("\n".join(rows) + "\n", encoding="utf-8")
    log("PANGO_SPEC_OK", surah=number, file=spec, cues=len(rows))
    return spec


def finalize_render(number: int, rendering: Path, final: Path, total_duration: float) -> Path:
    """Publish a finished render under its real name only if it is complete.

    Rendering goes to <n>.rendering.mp4 first, so a stopped/killed render never leaves a
    file named <n>.mp4 (ffmpeg closes a stopped file cleanly, so it would look valid).
    """
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(rendering)],
                           capture_output=True, text=True)
    try:
        duration = float(probe.stdout.strip())
    except ValueError:
        duration = 0.0
    if duration < total_duration - 1.0:
        raise RuntimeError(f"Render incomplete: {rendering} is {duration:.1f}s, expected {total_duration:.1f}s")
    os.replace(rendering, final)
    log("EXPORT_FINALIZED", surah=number, file=final, duration=duration, expected=total_duration)
    return final


RENDER_PROGRESS_FILE = SURAH_DIR / ".render_progress.json"


def start_progress(number: int, total_duration: float, out: Path) -> Path | None:
    """Note where the render's progress is written, so `status.py` can show % done and time left."""
    progress = TEMP_DIR / f"{number}.progress"
    try:
        progress.parent.mkdir(parents=True, exist_ok=True)
        progress.write_text("", encoding="utf-8")
        RENDER_PROGRESS_FILE.write_text(json.dumps({"surah": number, "total_seconds": total_duration, "progress_file": str(progress),
                                                    "output": str(out), "started": now(), "pid": os.getpid()}) + "\n", encoding="utf-8")
    except OSError:
        return None
    return progress


def progress_args(number: int, total_duration: float, out: Path) -> list[str]:
    """Make ffmpeg report its position every 5 s (one-pass render)."""
    progress = start_progress(number, total_duration, out)
    return ["-progress", str(progress), "-stats_period", "5"] if progress else []


def render_mp4(number: int, mp3: Path, ass_file: Path, total_duration: float, pango_spec: Path | None = None, finalize: bool = True) -> Path:
    final = EXPORTED_DIR / f"{number}.mp4"
    out = EXPORTED_DIR / f"{number}.rendering.mp4"  # renamed to `final` only once complete
    if not BG_IMAGE.exists():
        raise RuntimeError(f"Missing background image: {BG_IMAGE}")
    total_frames = max(1, int(round(total_duration * PANGO_OVERLAY_FPS)))
    if pango_spec is not None and RENDER_MODE == "pieces":
        import pipeline_pieces
        log("EXPORT_START", surah=number, file=out, audio_delay_seconds=INTRO_CREDIT_SECONDS, total_duration=total_duration, pango_overlay=True, mode="pieces")
        pipeline_pieces.render_in_pieces(number, mp3, ass_file, total_duration, pango_spec, pango_helper_binary(),
                                         SCRIPT_DIR / "pango_arabic_overlay.c", out, start_progress(number, total_duration, out))
        log("EXPORT_OK", surah=number, file=out, size=out.stat().st_size)
        return finalize_render(number, out, final, total_duration) if finalize else out
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
            *progress_args(number, total_duration, out),
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
        return finalize_render(number, out, final, total_duration) if finalize else out

    cmd = [
        "ffmpeg",
        "-y",
        *progress_args(number, total_duration, out),
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
    return finalize_render(number, out, final, total_duration) if finalize else out
