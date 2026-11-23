"""Per-surah text layout: measure every ayah and place blocks so nothing overlaps.

Screen, top to bottom (1920x1080):

    surah title          (fixed, centred on title_y)
    riwayah line         (fixed, centred on riwayah_y)
    ---- gap ----
    Arabic ayah          (centred on arabic_y when there is room)
    ---- gap ----
    English translation  (starts at english_top_y, or lower if the Arabic needs it)
    ---- bottom margin ----

Text is measured with Pango using exactly the settings the overlay helper
(pango_arabic_overlay.c) draws with, so the measured boxes are the drawn boxes.
Boxes include ink (diacritics can reach outside the line box).

If any ayah of a surah cannot fit, *both* fonts of that surah are scaled down
step by step (the whole surah keeps one consistent size). The chosen scale is
stored per surah in Layout_Overrides.json; settings.json is never changed.

An ayah that still does not fit at `split_below_scale` is shown in parts instead
(two screens, or more if needed), split at a pause mark (waqf) near the middle when
there is one. Only the last part carries the ayah number.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Callable

WIDTH, HEIGHT = 1920, 1080


class LayoutError(RuntimeError):
    pass


@dataclass(frozen=True)
class LayoutConfig:
    arabic_font: str            # e.g. "Noto Sans Arabic 54"
    english_font: str           # e.g. "Liberation Sans Bold 27"
    arabic_box_width: int
    english_box_width: int
    line_spacing: float
    title_text: str
    title_font: str
    title_y: int
    riwayah_text: str
    riwayah_font: str
    riwayah_y: int
    arabic_y: int               # preferred centre of the Arabic block
    english_top_y: int          # preferred top of the English block
    gap: int                    # minimum empty space between blocks
    bottom_margin: int
    fit_step: float             # scale multiplier per fitting step, e.g. 0.94
    min_scale: float            # never go below this, e.g. 0.70
    fontconfig_file: str | None = None
    split_below_scale: float = 0.85   # split an ayah rather than shrink the surah below this
    max_split_parts: int = 4


@dataclass(frozen=True)
class Box:
    """Measured text block. Offsets are relative to the logical top the helper uses."""
    logical_h: int
    ink_top: int      # <= 0 when marks rise above the line box
    ink_bottom: int   # >= logical_h when marks hang below it

    @property
    def height(self) -> int:
        return self.ink_bottom - self.ink_top


@dataclass
class Placement:
    key: str                    # "basmalah" or the ayah number as text
    arabic_text: str            # Arabic exactly as measured (wrapped for this scale)
    arabic_center_y: int        # what the helper gets (it centres the logical box on this)
    english_center_y: int | None
    arabic_top: int             # ink-inclusive bounds on screen
    arabic_bottom: int
    english_top: int | None
    english_bottom: int | None
    slack: int                  # smallest spare space around this ayah (px); lower = tighter


@dataclass
class SurahLayout:
    scale: float
    arabic_font: str
    english_font: str
    zone_top: int               # first row the Arabic may use (riwayah bottom + gap)
    zone_bottom: int            # last row the English may use
    title_bounds: tuple[int, int]
    riwayah_bounds: tuple[int, int]
    placements: dict[str, Placement] = field(default_factory=dict)

    def tightest(self, count: int) -> list[Placement]:
        return sorted(self.placements.values(), key=lambda p: p.slack)[:count]


def scale_font(font: str, scale: float) -> str:
    """'Noto Sans Arabic 54' x 0.9 -> 'Noto Sans Arabic 48.6'."""
    match = re.search(r"(\d+(?:\.\d+)?)(px)?\s*$", font)
    if not match:
        raise LayoutError(f"Font has no size: {font!r}")
    size = round(float(match.group(1)) * scale, 1)
    return f"{font[:match.start()]}{size:g}{match.group(2) or ''}"


@lru_cache(maxsize=None)
def _pango():
    import gi
    gi.require_foreign("cairo")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    import cairo
    from gi.repository import Pango, PangoCairo
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 8, 8)
    return Pango, PangoCairo, cairo.Context(surface)


@lru_cache(maxsize=None)
def measure(text: str, font: str, box_width: int, line_spacing: float, ltr: bool) -> Box:
    """Measure like the helper: centred, word-char wrap, fixed base direction, same line spacing."""
    Pango, PangoCairo, cr = _pango()
    layout = PangoCairo.create_layout(cr)
    layout.get_context().set_base_dir(Pango.Direction.LTR if ltr else Pango.Direction.RTL)
    layout.context_changed()
    layout.set_auto_dir(False)
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_line_spacing(line_spacing)
    layout.set_font_description(Pango.FontDescription.from_string(font))
    layout.set_width(box_width * Pango.SCALE)
    layout.set_text(text, -1)
    ink, logical = layout.get_pixel_extents()
    return Box(logical_h=logical.height, ink_top=min(0, ink.y), ink_bottom=max(logical.height, ink.y + ink.height))


def _bounds(box: Box, center_y: int) -> tuple[int, int]:
    top = center_y - box.logical_h // 2   # same rounding as the helper
    return top + box.ink_top, top + box.ink_bottom


def _center_for_top(box: Box, ink_top_on_screen: int) -> int:
    logical_top = ink_top_on_screen - box.ink_top
    return logical_top + box.logical_h // 2


def compute(config: LayoutConfig, entries: list[tuple[str, str, str]], scale: float,
            wrap_arabic: Callable[[str, float], str] | None = None) -> tuple[SurahLayout, list[str]]:
    """Lay out all entries at one scale. entries = (key, arabic_text, english_text).

    wrap_arabic(text, scale) re-wraps the Arabic for this scale (smaller font -> more words per line).

    Returns the layout and a list of problems (empty = everything fits).
    """
    if config.fontconfig_file:
        os.environ["FONTCONFIG_FILE"] = config.fontconfig_file
    arabic_font = scale_font(config.arabic_font, scale)
    english_font = scale_font(config.english_font, scale)
    title = _bounds(measure(config.title_text, config.title_font, config.arabic_box_width, config.line_spacing, False), config.title_y)
    riwayah = _bounds(measure(config.riwayah_text, config.riwayah_font, config.arabic_box_width, config.line_spacing, False), config.riwayah_y)
    zone_top = riwayah[1] + config.gap
    zone_bottom = HEIGHT - config.bottom_margin
    result = SurahLayout(scale, arabic_font, english_font, zone_top, zone_bottom, title, riwayah)
    problems: list[str] = []
    for key, arabic, english in entries:
        if wrap_arabic is not None:
            arabic = wrap_arabic(arabic, scale)
        a = measure(arabic, arabic_font, config.arabic_box_width, config.line_spacing, False)
        e = measure(english, english_font, config.english_box_width, config.line_spacing, True) if english.strip() else None
        a_top, a_bottom = _bounds(a, config.arabic_y)
        if a_top < zone_top:                     # never closer than `gap` to the riwayah line
            a_top, a_bottom = zone_top, zone_top + a.height
        if e is None:
            if a_bottom > zone_bottom:
                shift = a_bottom - zone_bottom
                a_top, a_bottom = a_top - shift, a_bottom - shift
            e_top = e_bottom = None
        else:
            e_top = max(config.english_top_y, a_bottom + config.gap)
            overflow = e_top + e.height - zone_bottom
            if overflow > 0:                     # first pull the English up toward the Arabic
                e_top = max(a_bottom + config.gap, e_top - overflow)
                overflow = e_top + e.height - zone_bottom
            if overflow > 0:                     # then move both up
                a_top, a_bottom, e_top = a_top - overflow, a_bottom - overflow, e_top - overflow
            e_bottom = e_top + e.height
        if a_top < zone_top:
            problems.append(f"{key}: needs {zone_top - a_top}px more height")
        slack_parts = [a_top - zone_top]
        if e_top is not None:
            slack_parts += [e_top - a_bottom - config.gap, zone_bottom - e_bottom]
        else:
            slack_parts.append(zone_bottom - a_bottom)
        result.placements[key] = Placement(
            key=key, arabic_text=arabic,
            arabic_center_y=_center_for_top(a, a_top),
            english_center_y=_center_for_top(e, e_top) if e is not None else None,
            arabic_top=a_top, arabic_bottom=a_bottom,
            english_top=e_top, english_bottom=e_bottom,
            slack=min(slack_parts),
        )
    return result, problems


def fit(config: LayoutConfig, entries: list[tuple[str, str, str]], start_scale: float = 1.0,
        wrap_arabic: Callable[[str, float], str] | None = None) -> SurahLayout:
    """Largest scale (<= start_scale) at which every entry fits, or LayoutError."""
    scale = start_scale
    while True:
        layout, problems = compute(config, entries, scale, wrap_arabic)
        if not problems:
            return layout
        next_scale = round(scale * config.fit_step, 4)
        if next_scale < config.min_scale:
            raise LayoutError(f"Text does not fit even at {scale:.0%} size: " + "; ".join(problems[:3]))
        scale = next_scale


# ----- splitting very long ayat ------------------------------------------------------

WAQF_MARKS = set("\u06d6\u06d7\u06d8\u06d9\u06da\u06db")
_AYAH_NUMBER = re.compile(r"[\u200e\u202a\u202c\u2066\u2069]*\(\d+\)[\u200e\u202a\u202c\u2066\u2069]*")


@dataclass(frozen=True)
class SplitPart:
    key: str              # e.g. "29.1"
    parent: str           # e.g. "29"
    index: int            # 1-based
    count: int
    arabic: str
    english: str
    word_start: int       # index of this part's first real word in the whole ayah (0-based)
    word_total: int       # real words in the whole ayah (no pause marks, no number)
    char_start: float     # where this part starts, as a fraction of the Arabic text


def _is_word(token: str) -> bool:
    return not all(ch in WAQF_MARKS for ch in token) and not _AYAH_NUMBER.fullmatch(token)


def _choose_cuts(lengths: list[int], candidates: list[tuple[int, float]], parts: int) -> list[int]:
    """Pick parts-1 cut positions (token indexes) closest to equal-length targets.

    candidates = (token index, penalty as a fraction of the text length); lower penalty = better place.
    """
    total = lengths[-1] or 1
    cuts: list[int] = []
    for j in range(1, parts):
        target = total * j / parts
        options = [(abs(lengths[b] - target) / total + penalty, b) for b, penalty in candidates if not cuts or b > cuts[-1]]
        if not options:
            break
        cuts.append(min(options)[1])
    return cuts


def split_entry(key: str, arabic: str, english: str, parts: int) -> list[SplitPart]:
    """Split one ayah (and its translation) into `parts` screens of about equal length."""
    tokens = arabic.split()
    # prefix character length before token b (what the text up to that cut measures)
    lengths = [len(" ".join(tokens[:b])) for b in range(len(tokens) + 1)]
    candidates: list[tuple[int, float]] = []
    for b in range(1, len(tokens)):
        if not _is_word(tokens[b]):          # pause marks stay with the words before them; number with the last part
            continue
        candidates.append((b, 0.0 if all(ch in WAQF_MARKS for ch in tokens[b - 1]) else 0.12))
    cuts = _choose_cuts(lengths, candidates, parts)
    if len(cuts) != parts - 1:
        return []
    bounds = [0] + cuts + [len(tokens)]
    word_index = [sum(1 for t in tokens[:b] if _is_word(t)) for b in range(len(tokens) + 1)]
    word_total = word_index[-1]
    fractions = [lengths[b] / (lengths[-1] or 1) for b in bounds]

    # English: cut at the punctuation nearest the same fractions (sentence end best, then ; then ,)
    words_en = english.split()
    en_lengths = [len(" ".join(words_en[:b])) for b in range(len(words_en) + 1)]
    en_candidates: list[tuple[int, float]] = []
    for b in range(1, len(words_en)):
        prev = words_en[b - 1]
        penalty = 0.0 if prev.endswith((".", "!", "?")) else 0.04 if prev.endswith((";", ":")) else 0.08 if prev.endswith((",", "-")) or words_en[b] in {"-", "\u2013", "\u2014"} else 0.15
        en_candidates.append((b, penalty))
    en_cuts: list[int] = []
    en_total = en_lengths[-1] or 1
    for j in range(1, parts):
        target = en_total * fractions[j]
        options = [(abs(en_lengths[b] - target) / en_total + penalty, b) for b, penalty in en_candidates if not en_cuts or b > en_cuts[-1]]
        en_cuts.append(min(options)[1] if options else len(words_en))
    en_bounds = [0] + en_cuts + [len(words_en)]

    result = []
    for i in range(parts):
        result.append(SplitPart(
            key=f"{key}.{i + 1}", parent=key, index=i + 1, count=parts,
            arabic=" ".join(tokens[bounds[i]:bounds[i + 1]]),
            english=" ".join(words_en[en_bounds[i]:en_bounds[i + 1]]),
            word_start=word_index[bounds[i]], word_total=word_total, char_start=fractions[i],
        ))
    return result


def fit_with_splits(config: LayoutConfig, entries: list[tuple[str, str, str]], start_scale: float = 1.0,
                    wrap_arabic: Callable[[str, float], str] | None = None) -> tuple[SurahLayout, dict[str, list[SplitPart]]]:
    """Like fit(), but an ayah that does not fit at split_below_scale is split into parts first.

    Returns the layout (placements keyed "29.1", "29.2" … for split ayat) and the splits by ayah key.
    """
    floor = min(start_scale, max(config.split_below_scale, config.min_scale))
    scale = start_scale
    while True:                                   # 1) shrinking a little is fine
        layout, problems = compute(config, entries, scale, wrap_arabic)
        if not problems:
            return layout, {}
        next_scale = round(scale * config.fit_step, 4)
        if next_scale < floor:
            break
        scale = next_scale
    # 2) split whatever still does not fit at the floor, using as few parts as possible
    _layout, problems = compute(config, entries, floor, wrap_arabic)
    failing = {problem.split(":", 1)[0] for problem in problems}
    splits: dict[str, list[SplitPart]] = {}
    for key, arabic, english in entries:
        if key not in failing:
            continue
        for parts in range(2, config.max_split_parts + 1):
            candidate = split_entry(key, arabic, english, parts)
            if not candidate:
                break
            _l, part_problems = compute(config, [(p.key, p.arabic, p.english) for p in candidate], floor, wrap_arabic)
            if not part_problems:
                splits[key] = candidate
                break
        else:
            candidate = split_entry(key, arabic, english, config.max_split_parts)
            if candidate:
                splits[key] = candidate        # best effort; the full fit below may still shrink it
    expanded: list[tuple[str, str, str]] = []
    for key, arabic, english in entries:
        if key in splits:
            expanded += [(p.key, p.arabic, p.english) for p in splits[key]]
        else:
            expanded.append((key, arabic, english))
    # 3) normal fit over the new list (may still shrink, never below min_scale)
    return fit(config, expanded, start_scale, wrap_arabic), splits


# ----- per-surah overrides (Layout_Overrides.json) ------------------------------------

def read_override_scale(overrides_file: Path, number: int) -> float:
    try:
        data = json.loads(overrides_file.read_text(encoding="utf-8"))
        return float(data[str(number)]["scale"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 1.0


def save_override(overrides_file: Path, number: int, scale: float, arabic_font: str, english_font: str, reason: str) -> None:
    try:
        data = json.loads(overrides_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    if scale >= 1.0:
        data.pop(str(number), None)
    else:
        data[str(number)] = {
            "scale": scale,
            "arabic_font": arabic_font,
            "english_font": english_font,
            "reason": reason,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    overrides_file.write_text(json.dumps(dict(sorted(data.items(), key=lambda kv: -int(kv[0]))), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
