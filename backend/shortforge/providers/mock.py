"""Mock-провайдеры (ADR-011): работают без сети и без ключей.

MockLLM     — детерминированные сценарии по форматам A/B (bible-summary).
MockTTS     — espeak-ng (если есть в системе) или тон 200 Гц; words — равномерно.
MockYouTube — «поиск» по синтетическим летсплеям из {DATA_DIR}/_fixtures.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from pathlib import Path

from ..media.ffutil import media_duration, run_ffmpeg
from ..media.fixtures import FIXTURE_COUNT, FIXTURE_DURATION, ensure_fixtures, letsplay_path
from .base import FoundVideo, ScriptDraft, TTSResult

# ------------------------------------------------------------------ LLM


def _blocks_format_a(game: str, idea: str) -> list[dict]:
    g = game.strip()
    idea = idea.strip().rstrip(".")
    return [
        {
            "ordinal": 1, "role": "hook",
            "text_en": f"Nobody noticed THIS secret hiding inside {g}..",
            "frame_desc": "Тёмный угол карты, курсор замирает на двери",
            "search_keys": [f"{g} secret basement", f"{g} gameplay"],
            "fx": [{"t": "sfx", "name": "impact"}, {"t": "zoom", "target": "door"}],
        },
        {
            "ordinal": 2, "role": "setup",
            "text_en": (
                f"Everyone thinks {g} is just another grind, but the {idea} "
                "changes everything you know about this game."
            ),
            "frame_desc": "Обычный геймплей, игрок бежит по спавну",
            "search_keys": [f"{g} spawn gameplay", f"{g} walkthrough"],
            "fx": [],
        },
        {
            "ordinal": 3, "role": "evidence",
            "text_en": (
                "Look at the basement door, the second the timer hits zero "
                "it glows for exactly one frame."
            ),
            "frame_desc": "Крупный план двери, таймер в нуле",
            "search_keys": [f"{g} basement door timer", f"{g} base tour"],
            "fx": [{"t": "sfx", "name": "sting"}],
        },
        {
            "ordinal": 4, "role": "evidence",
            "text_en": (
                "Watch the trader, he only spawns after you flip the golden "
                "brainrot twice in one server."
            ),
            "frame_desc": "NPC-трейдер появляется у стойки",
            "search_keys": [f"{g} trader spawn", f"{g} golden trade"],
            "fx": [{"t": "sfx", "name": "sting"}, {"t": "zoom", "target": "trader"}],
        },
        {
            "ordinal": 5, "role": "cta",
            "text_en": (
                "You have five seconds to like this, or the secret resets "
                "for your whole server."
            ),
            "frame_desc": "Таймер 5..4..3 поверх геймплея",
            "search_keys": [f"{g} funny moments", f"{g} server event"],
            "fx": [{"t": "sfx", "name": "tick"}],
        },
        {
            "ordinal": 6, "role": "twist",
            "text_en": (
                "Here is the twist, the secret was never hidden, the game "
                "shows it every single round."
            ),
            "frame_desc": "Замедленный повтор момента из хука",
            "search_keys": [f"{g} replay moment", f"{g} best plays"],
            "fx": [{"t": "sfx", "name": "glitch"}, {"t": "zoom", "target": "badge"}],
        },
        {
            "ordinal": 7, "role": "loop",
            "text_en": "So watch the first second again, because you will see the",
            "frame_desc": "Кадр, почти идентичный первому (луп-стык)",
            "search_keys": [f"{g} secret basement", f"{g} intro gameplay"],
            "fx": [],
        },
    ]


def _blocks_format_b(game: str, idea: str) -> list[dict]:
    g = game.strip()
    idea = idea.strip().rstrip(".")
    return [
        {
            "ordinal": 1, "role": "hook",
            "text_en": f"roblox {g} players will grind {idea} but refuse to touch grass..",
            "frame_desc": "Нейтральный обби-паркур, ровный бег",
            "search_keys": [f"{g} obby parkour", "roblox obby gameplay"],
            "fx": [{"t": "sfx", "name": "impact"}],
        },
        {
            "ordinal": 2, "role": "setup",
            "text_en": (
                "there is an unspoken rule, the longer the obby, the more "
                "your homework simply stops existing."
            ),
            "frame_desc": "Тот же паркур, без склеек",
            "search_keys": [f"{g} long obby", "roblox parkour run"],
            "fx": [],
        },
        {
            "ordinal": 3, "role": "evidence",
            "text_en": (
                "which means a two hour checkpoint run is technically a "
                "productivity hack, thank you for coming to my talk."
            ),
            "frame_desc": "Паркур продолжается, одна склейка",
            "search_keys": [f"{g} checkpoint run", "roblox obby fail"],
            "fx": [{"t": "sfx", "name": "sting"}],
        },
        {
            "ordinal": 4, "role": "punch",
            "text_en": (
                "so next time an adult asks what you are doing, say time "
                "management practice, in a video game."
            ),
            "frame_desc": "Финальный прыжок к чекпоинту",
            "search_keys": [f"{g} final jump", "roblox obby win"],
            "fx": [{"t": "sfx", "name": "glitch"}],
        },
        {
            "ordinal": 5, "role": "loop",
            "text_en": "and if they keep asking, just tell them about the",
            "frame_desc": "Бег в кадре обрывается на полушаге",
            "search_keys": [f"{g} obby parkour", "roblox running loop"],
            "fx": [],
        },
    ]


def _title_a(game: str) -> str:
    t = f"Never Enter THIS Basement In {game.strip()}.."
    return t[:55]


def _title_b(game: str) -> str:
    return f"{game.strip().lower()} obbies have a dark secret"[:55]


class MockLLM:
    is_mock = True

    async def write_script(
        self, *, game: str, idea: str, fmt: str, context: str
    ) -> ScriptDraft:
        if fmt == "B":
            return ScriptDraft(
                title=_title_b(game),
                description=(
                    f"{idea.strip()} in {game.strip()} explained. "
                    "#roblox #obby #shorts"
                ),
                hook_pattern="broken_mechanic",
                blocks=_blocks_format_b(game, idea),
            )
        game_tag = re.sub(r"\W+", "", game.lower())
        return ScriptDraft(
            title=_title_a(game),
            description=(
                f"The {idea.strip()} in {game.strip()} is real. "
                f"#roblox #{game_tag} #shorts"
            ),
            hook_pattern="you_noticed",
            blocks=_blocks_format_a(game, idea),
        )

    async def punch_up(self, draft: ScriptDraft, *, game: str, fmt: str) -> ScriptDraft:
        """Второй проход: капс на горячем слове заголовка, чистка филлеров, '..'."""
        title = draft.title
        if fmt != "B" and not title.endswith(".."):
            title = title.rstrip(".") + ".."
        blocks = []
        for b in draft.blocks:
            nb = dict(b)
            nb["text_en"] = re.sub(r"\b[Vv]ery ", "", nb["text_en"]).strip()
            blocks.append(nb)
        desc = draft.description
        if "#shorts" not in desc:
            desc += " #shorts"
        return ScriptDraft(
            title=title, description=desc,
            hook_pattern=draft.hook_pattern, blocks=blocks,
        )

    async def edit_script(
        self, current: ScriptDraft, *, instruction: str, block_ordinal: int | None
    ) -> ScriptDraft:
        """Простая правка: перегенерация текста блока с детерминированной вариацией."""
        suffixes = [" for real", " no joke", " and that is the point"]
        pick = suffixes[
            int(hashlib.sha256(instruction.encode()).hexdigest(), 16) % len(suffixes)
        ]
        blocks = []
        for b in current.blocks:
            nb = dict(b)
            if block_ordinal is None or nb.get("ordinal") == block_ordinal:
                nb["text_en"] = nb["text_en"].rstrip(". ") + pick + "."
            blocks.append(nb)
        return ScriptDraft(
            title=current.title, description=current.description,
            hook_pattern=current.hook_pattern, blocks=blocks,
        )

    async def agent_turn(
        self, *, system: str, messages: list[dict], tools: list[dict]
    ) -> dict:
        """Правило-ориентированный разбор последней инструкции пользователя."""
        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                c = m.get("content")
                last = c if isinstance(c, str) else str(c)
                break
        low = last.lower()
        m_ord = re.search(r"(?:block|блок\w*)\s*#?\s*(\d+)", low)
        ordinal = int(m_ord.group(1)) if m_ord else 1
        tool_names = {t.get("name") for t in tools}

        def tool_use(name: str, args: dict) -> dict:
            return {
                "stop_reason": "tool_use",
                "content": [{
                    "type": "tool_use", "id": f"toolu_mock_{name}",
                    "name": name, "input": args,
                }],
            }

        if ("replace" in low or "замен" in low or "клип" in low or "clip" in low) \
                and "replace_clip" in tool_names:
            return tool_use("replace_clip", {"block_ordinal": ordinal})
        if ("rewrite" in low or "text" in low or "текст" in low or "переп" in low) \
                and "edit_block" in tool_names:
            return tool_use("edit_block", {"block_ordinal": ordinal, "instruction": last})
        return {
            "stop_reason": "end_turn",
            "content": [{
                "type": "text",
                "text": "Mock agent: could not map the request to a tool. "
                        "Try 'replace clip in block 3' or 'rewrite text of block 2'.",
            }],
        }


# ------------------------------------------------------------------ TTS

WORDS_PER_SEC = 2.8


class MockTTS:
    is_mock = True

    async def synth(self, *, text: str, voice_id: str, out_wav: str) -> TTSResult:
        words = re.findall(r"\S+", text)
        n = max(1, len(words))
        target = n / WORDS_PER_SEC
        out = Path(out_wav)
        out.parent.mkdir(parents=True, exist_ok=True)

        duration = 0.0
        espeak = shutil.which("espeak-ng")
        if espeak:
            try:
                with tempfile.TemporaryDirectory() as td:
                    raw = Path(td) / "raw.wav"
                    import subprocess  # noqa: PLC0415

                    subprocess.run(
                        [espeak, "-v", "en-us+m3", "-s", "168", "-w", str(raw), text],
                        check=True, capture_output=True,
                    )
                    run_ffmpeg(
                        ["-i", str(raw), "-ar", "44100", "-ac", "1",
                         "-c:a", "pcm_s16le", str(out)]
                    )
                duration = media_duration(out)
            except Exception:  # noqa: BLE001
                duration = 0.0
        if duration <= 0.0:
            run_ffmpeg(
                ["-f", "lavfi", "-i", f"sine=frequency=200:duration={target:.3f}",
                 "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(out)]
            )
            duration = target

        slot = duration / n
        laid = [
            {"w": w, "s": round(i * slot, 3),
             "e": round(min(duration, (i + 1) * slot), 3)}
            for i, w in enumerate(words)
        ]
        return TTSResult(
            wav_path=str(out), words=laid, duration=round(duration, 3), is_mock=True
        )


# ------------------------------------------------------------------ YouTube

# Запросы, «увиденные» каждым mock-видео — из них строится синтетический транскрипт,
# чтобы поиск моментов в hunter находил совпадения.
_SEEN_QUERIES: dict[str, list[str]] = {}


def _mock_transcript(words: list[str], duration: float = FIXTURE_DURATION) -> list[dict]:
    """Слова размазаны по таймлайну с шагом ~2 с."""
    if not words:
        words = ["roblox", "gameplay", "secret"]
    out = []
    t = 1.0
    i = 0
    while t < duration - 1:
        out.append({"t": round(t, 2), "s": words[i % len(words)]})
        t += 2.0
        i += 1
    return out


class MockYouTube:
    is_mock = True

    async def search(self, *, query: str, limit: int) -> list[FoundVideo]:
        ensure_fixtures()
        q_words = [w.lower() for w in re.findall(r"\w+", query)]
        found = []
        for n in range(1, min(FIXTURE_COUNT, max(1, limit)) + 1):
            vid = f"mockvid{n}"
            seen = _SEEN_QUERIES.setdefault(vid, [])
            for w in q_words:
                if w not in seen:
                    seen.append(w)
            found.append(
                FoundVideo(
                    yt_video_id=vid,
                    title=f"{query} — epic letsplay #{n}",
                    channel=f"MockPlays{n}",
                    duration=FIXTURE_DURATION,
                    transcript=_mock_transcript(q_words),
                )
            )
        return found

    async def download_window(
        self, *, yt_video_id: str, start: float, end: float, out_path: str
    ) -> str:
        ensure_fixtures()
        m = re.search(r"(\d+)", yt_video_id)
        n = (int(m.group(1)) - 1) % FIXTURE_COUNT + 1 if m else 1
        src = letsplay_path(n)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        start = max(0.0, min(start, FIXTURE_DURATION - 1))
        dur = max(1.0, min(end, FIXTURE_DURATION) - start)
        run_ffmpeg(
            ["-ss", f"{start:.3f}", "-i", str(src), "-t", f"{dur:.3f}",
             "-c", "copy", "-an", str(out)]
        )
        return str(out)

    async def get_transcript(self, *, yt_video_id: str) -> list[dict]:
        return _mock_transcript(_SEEN_QUERIES.get(yt_video_id, []))
