"""Render the video in pieces, so a stopped render continues where it left off.

The video is cut into pieces of `video.render.piece_seconds`. Each piece is rendered on its own into
Exported_Vids/.pieces/<surah>-<key>/ and kept until the whole video is finished; a restart renders only the
missing pieces. The text helper paints straight onto the background, so ffmpeg only has to encode (no
overlay step), and a few pieces can render at the same time. At the end the pieces are joined without
re-encoding and the audio is added.
"""
from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports
from pipeline_fonts import ffmpeg_filter_escape, pango_environment

PIECES_DIR = EXPORTED_DIR / ".pieces"
VIDEO_ENCODE_ARGS = ["-c:v", "libx264", "-preset", RENDER_ENCODER_PRESET, "-tune", "stillimage", "-pix_fmt", "yuv420p"]
AUDIO_ENCODE_ARGS = ["-c:a", "aac", "-b:a", "192k"]
_ASS_TIME = re.compile(r"^Dialogue:\s*[^,]*,(\d+):(\d+):(\d+(?:\.\d+)?),(\d+):(\d+):(\d+(?:\.\d+)?),")


def piece_ranges(total_frames: int, piece_frames: int) -> list[tuple[int, int]]:
    """(first frame, frame count) of every piece, in order."""
    piece_frames = max(1, piece_frames)
    return [(start, min(piece_frames, total_frames - start)) for start in range(0, total_frames, piece_frames)]


def ass_spans(ass_file: Path) -> list[tuple[float, float]]:
    """When the subtitle file shows something (start, end in seconds). Only those pieces need the subtitle filter."""
    spans = []
    for line in ass_file.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _ASS_TIME.match(line)
        if match:
            h1, m1, s1, h2, m2, s2 = match.groups()
            spans.append((int(h1) * 3600 + int(m1) * 60 + float(s1), int(h2) * 3600 + int(m2) * 60 + float(s2)))
    return spans


def overlaps(start: float, end: float, spans: list[tuple[float, float]], margin: float = 1.0) -> bool:
    return any(start < span_end + margin and end > span_start - margin for span_start, span_end in spans)


def render_key(spec: Path, ass_file: Path, mp3: Path, helper_source: Path, total_frames: int, piece_frames: int) -> str:
    """Changes whenever anything that decides the picture or sound changes, so old pieces are never reused wrongly."""
    digest = hashlib.sha1()
    for path in (spec, ass_file, helper_source):
        digest.update(path.read_bytes())
    for path in (mp3, BG_IMAGE):
        stat = path.stat()
        digest.update(f"{path}|{stat.st_size}|{stat.st_mtime_ns}".encode())
    digest.update(json.dumps([total_frames, piece_frames, PANGO_OVERLAY_FPS, PANGO_ARABIC_LINE_SPACING, INTRO_CREDIT_SECONDS,
                              VIDEO_ENCODE_ARGS, AUDIO_ENCODE_ARGS]).encode())
    return digest.hexdigest()[:16]


def frame_count(video: Path) -> int:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames", "-of", "csv=p=0", str(video)],
                           capture_output=True, text=True)
    try:
        return int(probe.stdout.strip().split(",")[0])
    except ValueError:
        return -1


def cleanup_pieces(number: int, keep: Path | None = None) -> None:
    """Remove this surah's piece folders (all of them, or all but `keep`)."""
    if not PIECES_DIR.is_dir():
        return
    for folder in PIECES_DIR.glob(f"{number}-*"):
        if folder != keep:
            shutil.rmtree(folder, ignore_errors=True)
    try:
        PIECES_DIR.rmdir()   # only succeeds when empty
    except OSError:
        pass


def scaled_background(work: Path) -> Path:
    out = work / "background.png"
    if not out.exists():
        temp = work / "background.part.png"
        result = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(BG_IMAGE), "-vf", "scale=1920:1080,setsar=1", "-pix_fmt", "rgb24", "-frames:v", "1", str(temp)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr[-2000:])
        os.replace(temp, out)
    return out


def render_piece(helper: Path, spec: Path, background: Path, ass_file: Path, start: int, count: int, with_subtitles: bool, out: Path) -> None:
    temp = out.with_name(out.stem + ".part.mp4")
    helper_cmd = [str(helper), str(spec), "1920", "1080", str(count), str(PANGO_ARABIC_LINE_SPACING), str(start), str(background)]
    filters = []
    if with_subtitles:   # the subtitle file is timed from the start of the video, the piece starts later
        offset = start / PANGO_OVERLAY_FPS
        filters = ["-vf", f"setpts=PTS+{offset:.6f}/TB,subtitles='{ffmpeg_filter_escape(ass_file)}':fontsdir='{ffmpeg_filter_escape(FONTS_DIR)}',setpts=PTS-STARTPTS"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr0", "-s", "1920x1080", "-r", str(PANGO_OVERLAY_FPS), "-i", "pipe:0",
           *filters, *VIDEO_ENCODE_ARGS, "-an", "-threads", str(RENDER_PIECE_THREADS), str(temp)]
    overlay = subprocess.Popen(helper_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=pango_environment())
    assert overlay.stdout is not None
    result = subprocess.run(cmd, stdin=overlay.stdout, capture_output=True, text=False)
    overlay.stdout.close()
    _stdout, overlay_stderr = overlay.communicate()
    if overlay.returncode != 0:
        raise RuntimeError(overlay_stderr.decode("utf-8", errors="replace")[-4000:])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-4000:])
    frames = frame_count(temp)
    if frames != count:
        raise RuntimeError(f"Piece {out.name} has {frames} frames, expected {count}")
    os.replace(temp, out)


def encode_audio(mp3: Path, total_duration: float, out: Path) -> None:
    if out.exists():
        return
    temp = out.with_name("audio.part.m4a")
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-vn", "-af", f"adelay={int(round(INTRO_CREDIT_SECONDS * 1000))}:all=1,apad",
           *AUDIO_ENCODE_ARGS, "-t", f"{total_duration:.3f}", str(temp)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])
    os.replace(temp, out)


def render_in_pieces(number: int, mp3: Path, ass_file: Path, total_duration: float, pango_spec: Path, helper: Path, helper_source: Path,
                     out: Path, progress_file: Path | None = None) -> Path:
    """Render `out` (video + audio) piece by piece. Pieces already on disk from an earlier, stopped run are reused."""
    fps = PANGO_OVERLAY_FPS
    total_frames = max(1, int(round(total_duration * fps)))
    piece_frames = max(fps, int(RENDER_PIECE_SECONDS * fps))
    ranges = piece_ranges(total_frames, piece_frames)
    key = render_key(pango_spec, ass_file, mp3, helper_source, total_frames, piece_frames)
    work = PIECES_DIR / f"{number}-{key}"
    work.mkdir(parents=True, exist_ok=True)
    cleanup_pieces(number, keep=work)   # pieces of an older layout/audio are useless now
    for leftover in work.glob("*.part.*"):
        leftover.unlink(missing_ok=True)
    background = scaled_background(work)
    spans = ass_spans(ass_file)
    files = [work / f"piece_{index:05d}.mp4" for index in range(len(ranges))]
    todo = [index for index, (_start, count) in enumerate(ranges) if not (files[index].exists() and frame_count(files[index]) == count)]
    done_frames = sum(count for index, (_start, count) in enumerate(ranges) if index not in set(todo))
    log("PIECES_START", surah=number, pieces=len(ranges), already_done=len(ranges) - len(todo), piece_seconds=RENDER_PIECE_SECONDS,
        parallel=RENDER_PARALLEL_PIECES, folder=work)
    lock = threading.Lock()
    resumed_us = int(done_frames / fps * 1_000_000)   # already on disk when this run began: not counted in the speed

    def report() -> None:
        if progress_file is not None:
            try:
                progress_file.write_text(f"resumed_time_us={resumed_us}\nout_time_us={int(done_frames / fps * 1_000_000)}\nprogress=continue\n", encoding="utf-8")
            except OSError:
                pass

    report()

    def work_on(index: int) -> None:
        nonlocal done_frames
        start, count = ranges[index]
        render_piece(helper, pango_spec, background, ass_file, start, count,
                     overlaps(start / fps, (start + count) / fps, spans), files[index])
        with lock:
            done_frames += count
            report()

    with ThreadPoolExecutor(max_workers=max(1, RENDER_PARALLEL_PIECES)) as pool:
        for future in [pool.submit(work_on, index) for index in todo]:
            future.result()   # the first failed piece stops the render; finished pieces stay for the next try
    audio = work / "audio.m4a"
    encode_audio(mp3, total_duration, audio)
    listing = work / "pieces.txt"
    listing.write_text("".join(f"file '{path.name}'\n" for path in files), encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
           "-map", "0:v", "-map", "1:a", "-c", "copy", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=work)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:])
    joined = frame_count(out)
    if joined != total_frames:
        raise RuntimeError(f"Joined video has {joined} frames, expected {total_frames}")
    log("PIECES_JOINED", surah=number, pieces=len(ranges), rendered_now=len(todo), file=out, size=out.stat().st_size)
    return out
