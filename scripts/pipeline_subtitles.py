"""Timeline, ASS subtitles and the optional MLT project."""
from __future__ import annotations

from pipeline_config import *  # noqa: F401,F403  settings, constants, log, std imports


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
    tmp_dir = TEMP_DIR
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
        f"Style: Arabic,{FONT_ARABIC_AYAH},{setting_int('video.ayah_arabic.ass_fallback_size', 72)},{ass_color(PANGO_ARABIC_COLOR)},&H00000000,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,60,60,0,1",
        f"Style: English,{FONT_ENGLISH},{ENGLISH_SIZE},{ass_color(ENGLISH_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,2,90,90,170,1",
        f"Style: Credit,{FONT_STANDARD},{CREDIT_SIZE},{ass_color(CREDIT_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(INTRO_ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,5,80,80,0,1",
        f"Style: Outro,{FONT_STANDARD},{OUTRO_SIZE},{ass_color(OUTRO_COLOR)},&H00000000,&H00000000,&H00000000,{ass_bold(OUTRO_ENGLISH_BOLD)},0,0,0,100,100,0,0,1,0,0,5,80,80,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ass_dialogue(0.0, INTRO_CREDIT_SECONDS, "Credit", INTRO_CREDIT_TEXT, FADE_CREDIT_SECONDS),
    ]
    if not PANGO_ARABIC_OVERLAY:
        lines.extend([
            ass_dialogue(INTRO_CREDIT_SECONDS + 0.4, title_end, "Title", f"سورة {arabic_name}", FADE_TITLE_SECONDS),
            ass_dialogue(INTRO_CREDIT_SECONDS + 0.4, title_end, "Riwayah", "برواية:حفص عن عاصم", FADE_RIWAYAH_SECONDS),
        ])
    if basmalah_timing is not None:
        start, end = basmalah_timing
        if not PANGO_ARABIC_OVERLAY:
            lines.append(ass_dialogue(start, end, "Arabic", BASMALAH_ARABIC_TEXT, FADE_BASMALAH_SECONDS))
        if not PANGO_ARABIC_OVERLAY:
            lines.append(ass_dialogue(start, end, "English", BASMALAH_ENGLISH_TEXT, FADE_BASMALAH_SECONDS))
    for index, arabic in enumerate(ayat):
        start, end = ayah_timings[index]
        english = translations[index] if index < len(translations) else ""
        if not PANGO_ARABIC_OVERLAY:
            lines.append(ass_dialogue(start, end, "Arabic", wrap_arabic_ayah(arabic, ARABIC_AYAH_WRAP_WIDTH), FADE_AYAH_SECONDS))
        if english and not PANGO_ARABIC_OVERLAY:
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
