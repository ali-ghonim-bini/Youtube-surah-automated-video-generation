"""Font checks and Pango/fontconfig setup."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports


def normalize_search_text(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\w\s\u0600-\u06FF]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def ffmpeg_filter_escape(value: Path) -> str:
    return str(value).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def pango_fontconfig_file() -> Path:
    tmp_dir = TEMP_DIR
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
