"""Caption generation — word timings to burned-in ASS subtitles.

Word grouping decides readability; ASS styling decides whether it looks like a
real short or like a subtitle track. Four presets cover the range from
attention-grabbing to professional.

Per-word animation (Bold Pop, Boxed) is done by emitting one Dialogue event per
word, each showing the whole caption group with the active word overridden.
That's how libass does word highlighting — there's no per-glyph timeline — and
it's why event counts look high relative to word counts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pysubs2

from .transcript import Word

log = logging.getLogger(__name__)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

#: Words are grouped into caption events; more than this is hard to read at a
#: glance on a phone.
DEFAULT_MAX_WORDS = 4
#: A pause longer than this ends a caption group regardless of word count.
DEFAULT_MAX_GAP_S = 0.4

#: Reference canvas. Styles are authored against this and scaled to the real
#: output size, so a preset looks identical at 9:16, 1:1, and 16:9.
REFERENCE_HEIGHT = 1920


def hex_to_ass(colour: str, alpha: int = 0) -> pysubs2.Color:
    """Convert ``#RRGGBB`` to a pysubs2 colour.

    ASS stores colours as ``&HAABBGGRR`` — alpha first, then reversed RGB.
    pysubs2 handles the byte order; we only translate from hex.
    """
    r, g, b = _rgb(colour)
    return pysubs2.Color(r, g, b, alpha)


def _rgb(colour: str) -> tuple[int, int, int]:
    value = colour.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected #RRGGBB, got {colour!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def ass_colour_override(colour: str) -> str:
    r"""Format ``#RRGGBB`` as an inline ASS colour value, e.g. ``&H00E5FF&``.

    Inline ``\c`` overrides take the raw ``&HBBGGRR&`` form — reversed byte
    order and no alpha component, unlike the style-level colour fields.
    """
    r, g, b = _rgb(colour)
    return f"&H{b:02X}{g:02X}{r:02X}&"


@dataclass
class CaptionStyle:
    """A caption preset."""

    key: str
    label: str
    description: str

    font: str = "Anton"
    font_file: str = "Anton-Regular.ttf"
    #: Font size as a fraction of frame height, so presets scale across ratios.
    size_ratio: float = 0.058
    primary: str = "#FFFFFF"
    #: Colour of the currently-spoken word. None disables per-word highlighting.
    accent: str | None = "#FFE500"
    outline: str = "#000000"
    outline_width: float = 4.0
    shadow: float = 0.0
    bold: bool = True
    all_caps: bool = True
    #: Distance from the bottom of the frame, as a fraction of height.
    margin_v_ratio: float = 0.24
    max_words: int = DEFAULT_MAX_WORDS
    #: "none" | "scale" | "karaoke"
    animation: str = "scale"
    #: Percentage the active word grows to. Only used when animation is "scale".
    scale_percent: int = 118
    #: Opaque background box behind the text (ASS BorderStyle 3).
    boxed: bool = False
    box_colour: str = "#000000"
    box_alpha: int = 40

    @property
    def font_path(self) -> Path:
        return FONT_DIR / self.font_file


PRESETS: dict[str, CaptionStyle] = {
    "bold": CaptionStyle(
        key="bold",
        label="Bold Pop",
        description="Chunky white with a heavy outline; the spoken word grows and turns yellow.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.062,
        primary="#FFFFFF",
        accent="#FFE500",
        outline_width=5.0,
        all_caps=True,
        animation="scale",
        scale_percent=118,
        max_words=4,
    ),
    "karaoke": CaptionStyle(
        key="karaoke",
        label="Karaoke Fill",
        description="Words fill with colour exactly as they're spoken.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.058,
        primary="#FFFFFF",
        accent="#31E981",
        outline_width=4.0,
        all_caps=True,
        animation="karaoke",
        max_words=5,
    ),
    "clean": CaptionStyle(
        key="clean",
        label="Clean Lower",
        description="Minimal lower third, no animation. For talks and professional cuts.",
        font="Inter",
        font_file="Inter-Variable.ttf",
        size_ratio=0.034,
        primary="#FFFFFF",
        accent=None,
        outline_width=2.0,
        shadow=1.0,
        bold=False,
        all_caps=False,
        margin_v_ratio=0.10,
        animation="none",
        max_words=8,
    ),
    "hormozi": CaptionStyle(
        key="hormozi",
        label="Hormozi",
        description="Bold uppercase captions with an emphatic red active word.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.061,
        primary="#FFFFFF",
        accent="#FF3B30",
        outline_width=5.0,
        all_caps=True,
        animation="scale",
        scale_percent=116,
        max_words=4,
    ),
    "mrbeast": CaptionStyle(
        key="mrbeast",
        label="MrBeast",
        description="High-contrast boxed captions with a bright active word.",
        font="Anton",
        font_file="Anton-Regular.ttf",
        size_ratio=0.052,
        primary="#FFFFFF",
        accent="#FF4D4D",
        outline_width=0.0,
        all_caps=True,
        animation="scale",
        scale_percent=112,
        max_words=4,
        boxed=True,
        box_colour="#000000",
        box_alpha=30,
    ),
}

DEFAULT_STYLE = "bold"

_LEGACY_STYLE_ALIASES = {
    "bold_pop": "bold",
    "karaoke_fill": "karaoke",
    "clean_lower": "clean",
    "boxed": "mrbeast",
}


def get_style(key: str) -> CaptionStyle:
    style = PRESETS.get(_LEGACY_STYLE_ALIASES.get(key, key))
    if style is None:
        raise ValueError(f"Unknown caption style {key!r}. Available: {', '.join(PRESETS)}")
    return style


# --------------------------------------------------------------------------
# Grouping
# --------------------------------------------------------------------------


@dataclass
class CaptionGroup:
    words: list[Word] = field(default_factory=list)

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words)


def group_words(
    words: list[Word],
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_gap_s: float = DEFAULT_MAX_GAP_S,
) -> list[CaptionGroup]:
    """Split words into caption-sized groups.

    Breaks on three signals, in priority order: sentence-ending punctuation, a
    pause longer than ``max_gap_s``, and the word-count ceiling. Punctuation
    first means a caption rarely spans two sentences, which reads badly even
    when the timing is correct.
    """
    groups: list[CaptionGroup] = []
    current: list[Word] = []

    for index, word in enumerate(words):
        if current:
            gap = word.start - current[-1].end
            if gap > max_gap_s or len(current) >= max_words:
                groups.append(CaptionGroup(words=current))
                current = []

        current.append(word)

        is_last = index == len(words) - 1
        if word.ends_sentence and not is_last:
            groups.append(CaptionGroup(words=current))
            current = []

    if current:
        groups.append(CaptionGroup(words=current))

    return [g for g in groups if g.words]


# --------------------------------------------------------------------------
# ASS generation
# --------------------------------------------------------------------------

STYLE_NAME = "Clippy"


def build_ass(
    words: list[Word],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    time_offset_s: float = 0.0,
) -> pysubs2.SSAFile:
    """Build an ASS subtitle file for a clip.

    ``time_offset_s`` is subtracted from every timestamp, so absolute transcript
    times become clip-relative ones.

    Preconditions:
        words are in chronological order and carry start/end timings.
    """
    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(width)
    subs.info["PlayResY"] = str(height)
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["WrapStyle"] = "0"

    scale = height / REFERENCE_HEIGHT
    subs.styles[STYLE_NAME] = _build_ass_style(style, height=height, scale=scale)

    groups = group_words(words, max_words=style.max_words)

    for group in groups:
        if style.animation == "karaoke":
            subs.events.append(_karaoke_event(group, style, time_offset_s))
        elif style.animation == "scale" and style.accent:
            subs.events.extend(_per_word_events(group, style, time_offset_s))
        else:
            subs.events.append(_static_event(group, style, time_offset_s))

    return subs


def _build_ass_style(style: CaptionStyle, *, height: int, scale: float) -> pysubs2.SSAStyle:
    ass_style = pysubs2.SSAStyle()
    ass_style.fontname = style.font
    ass_style.fontsize = round(height * style.size_ratio)
    ass_style.primarycolor = hex_to_ass(style.primary)
    ass_style.secondarycolor = hex_to_ass(style.accent or style.primary)
    ass_style.outlinecolor = hex_to_ass(style.outline)
    ass_style.backcolor = hex_to_ass(style.box_colour, alpha=style.box_alpha)
    ass_style.bold = style.bold
    ass_style.outline = style.outline_width * scale
    ass_style.shadow = style.shadow * scale
    # BorderStyle 3 draws an opaque box using backcolor instead of an outline.
    ass_style.borderstyle = 3 if style.boxed else 1
    ass_style.alignment = pysubs2.Alignment.BOTTOM_CENTER
    ass_style.marginv = round(height * style.margin_v_ratio)
    ass_style.marginl = ass_style.marginr = round(height * 0.03)
    return ass_style


def _text_of(word: Word, style: CaptionStyle) -> str:
    text = word.text.strip()
    return text.upper() if style.all_caps else text


def _event(start_s: float, end_s: float, text: str, offset: float) -> pysubs2.SSAEvent:
    return pysubs2.SSAEvent(
        start=pysubs2.make_time(s=max(0.0, start_s - offset)),
        end=pysubs2.make_time(s=max(0.0, end_s - offset)),
        text=text,
        style=STYLE_NAME,
    )


def _static_event(group: CaptionGroup, style: CaptionStyle, offset: float) -> pysubs2.SSAEvent:
    text = " ".join(_text_of(w, style) for w in group.words)
    return _event(group.start, group.end, text, offset)


def _karaoke_event(group: CaptionGroup, style: CaptionStyle, offset: float) -> pysubs2.SSAEvent:
    r"""Build a ``\kf`` karaoke line.

    ``\kf`` sweeps the fill across each word over its own duration; durations
    are in centiseconds, which is the unit ASS karaoke tags use.
    """
    parts: list[str] = []
    for index, word in enumerate(group.words):
        duration_cs = max(1, round(word.duration * 100))
        # Roll the inter-word gap into the preceding word so the sweep stays
        # locked to the audio instead of drifting late across the line.
        if index + 1 < len(group.words):
            gap = group.words[index + 1].start - word.end
            if 0 < gap < 0.5:
                duration_cs += round(gap * 100)
        parts.append(f"{{\\kf{duration_cs}}}{_text_of(word, style)}")

    return _event(group.start, group.end, " ".join(parts), offset)


def _per_word_events(
    group: CaptionGroup, style: CaptionStyle, offset: float
) -> list[pysubs2.SSAEvent]:
    """Emit one event per word, highlighting the active one.

    Every event renders the full group so the line stays stable on screen; only
    the emphasised word changes between them.
    """
    accent_tag = f"\\c{ass_colour_override(style.accent or style.primary)}"
    scale = style.scale_percent
    events: list[pysubs2.SSAEvent] = []

    for active, word in enumerate(group.words):
        rendered: list[str] = []
        for index, other in enumerate(group.words):
            text = _text_of(other, style)
            if index == active:
                rendered.append(f"{{{accent_tag}\\fscx{scale}\\fscy{scale}}}{text}{{\\r}}")
            else:
                rendered.append(text)

        # Hold the last word until the group ends so the caption doesn't blink
        # out early when a word's measured end precedes the next word's start.
        end = word.end if active + 1 < len(group.words) else group.end
        next_start = group.words[active + 1].start if active + 1 < len(group.words) else end
        events.append(_event(word.start, max(end, next_start), " ".join(rendered), offset))

    return events


def write_ass(
    path: Path,
    words: list[Word],
    style: CaptionStyle,
    *,
    width: int,
    height: int,
    time_offset_s: float = 0.0,
) -> Path:
    """Render captions to an .ass file and return its path."""
    subs = build_ass(words, style, width=width, height=height, time_offset_s=time_offset_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8")
    return path


def write_srt(path: Path, words: list[Word], *, time_offset_s: float = 0.0) -> Path:
    """Write a plain .srt sidecar — for platforms that want an upload-time file."""
    subs = pysubs2.SSAFile()
    for group in group_words(words, max_words=8):
        subs.events.append(
            pysubs2.SSAEvent(
                start=pysubs2.make_time(s=max(0.0, group.start - time_offset_s)),
                end=pysubs2.make_time(s=max(0.0, group.end - time_offset_s)),
                text=group.text,
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8", format_="srt")
    return path
