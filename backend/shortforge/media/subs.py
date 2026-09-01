"""Генерация ASS-сабов из words[] по правилам bible-summary.

Пословные, КАПС, 1–3 слова на экран, центр на 62% высоты (PlayResY=1920),
активное слово жёлтое (#FFD400) через караоке \\k, чёрная обводка 4px,
«горячие» слова — красные + scale 110%. Применяется subs_dictionary из settings.
"""
from __future__ import annotations

import re
from pathlib import Path

PLAY_X, PLAY_Y = 1080, 1920
CENTER_X = PLAY_X // 2
CENTER_Y = int(PLAY_Y * 0.62)  # 1190

# ASS-цвета: &HAABBGGRR. FFD400 -> BGR 00D4FF; белый; чёрный; красный FF0000 -> 0000FF.
YELLOW = "&H0000D4FF"
WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
RED = "&H000000FF"

HOT_WORDS = {
    "secret", "never", "broken", "nobody", "banned", "hidden", "glitch",
    "overpowered", "delete", "reset", "twist", "dark", "trap",
}

HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_X}
PlayResY: {PLAY_Y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Word,Liberation Sans,96,{YELLOW},{WHITE},{BLACK},&H80000000,-1,0,0,0,100,100,1,0,1,4,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean_word(w: str, dictionary: dict[str, str]) -> str:
    for k, v in dictionary.items():
        if w.lower() == k.lower():
            w = v
            break
    return w


def _chunk_words(words: list[dict]) -> list[list[dict]]:
    """1–3 слова на строку: горячее слово даёт короткую строку, иначе по 3."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        bare = re.sub(r"\W+", "", w["w"]).lower()
        if bare in HOT_WORDS and cur:
            chunks.append(cur)
            cur = []
        cur.append(w)
        if bare in HOT_WORDS or len(cur) >= 3:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], *, dictionary: dict[str, str] | None = None) -> str:
    """words: [{"w":слово,"s":сек,"e":сек}] -> текст .ass."""
    dictionary = dictionary or {}
    lines = [HEADER]
    for chunk in _chunk_words(words):
        start, end = chunk[0]["s"], chunk[-1]["e"]
        parts = [f"{{\\pos({CENTER_X},{CENTER_Y})}}"]
        for w in chunk:
            dur_cs = max(1, int(round((w["e"] - w["s"]) * 100)))
            text = _clean_word(str(w["w"]), dictionary).upper()
            text = text.replace("{", "").replace("}", "").replace("\\", "")
            bare = re.sub(r"\W+", "", text).lower()
            if bare in HOT_WORDS:
                parts.append(
                    f"{{\\k{dur_cs}\\1c{RED}\\fscx110\\fscy110}}{text} "
                )
            else:
                parts.append(f"{{\\k{dur_cs}}}{text} ")
        text_field = "".join(parts).rstrip()
        lines.append(
            f"Dialogue: 0,{_ts(start)},{_ts(end)},Word,,0,0,0,,{text_field}\n"
        )
    return "".join(lines)


def write_ass(
    path: Path | str, words: list[dict], *, dictionary: dict[str, str] | None = None
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_ass(words, dictionary=dictionary), encoding="utf-8")
    return path
