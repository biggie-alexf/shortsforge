"""Реальные провайдеры: Anthropic (LLM), ElevenLabs (TTS), yt-dlp (YouTube).

Ключи читаются из settings при создании (factory.get_providers, ADR-009).
Юнит-тесты покрывают только построение запросов — без сети.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
from pathlib import Path

import httpx

from ..media.ffutil import media_duration
from .base import FoundVideo, ScriptDraft, TTSResult

log = logging.getLogger("shortforge.providers.real")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-5"

SCRIPT_JSON_SPEC = """Return the script as JSON inside a single <json>...</json> tag:
{"title": str (<=55 chars), "description": str, "hook_pattern": str,
 "blocks": [{"ordinal": int, "role": "hook|setup|evidence|cta|twist|loop|punch",
             "text_en": str, "frame_desc": str, "search_keys": [str, ...],
             "fx": [{"t": "zoom", "target": str} | {"t": "sfx", "name": str}, ...]}]}"""


def _extract_json(text: str) -> dict:
    m = re.search(r"<json>\s*(.*?)\s*</json>", text, re.S)
    raw = m.group(1) if m else text
    return json.loads(raw)


def _draft_from_dict(d: dict) -> ScriptDraft:
    return ScriptDraft(
        title=str(d.get("title", ""))[:256],
        description=str(d.get("description", "")),
        hook_pattern=str(d.get("hook_pattern", ""))[:64],
        blocks=list(d.get("blocks", [])),
    )


def _draft_to_dict(d: ScriptDraft) -> dict:
    return {
        "title": d.title, "description": d.description,
        "hook_pattern": d.hook_pattern, "blocks": d.blocks,
    }


class RealLLM:
    is_mock = False

    def __init__(self, *, api_key: str, model: str = "") -> None:
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL

    # -------------------------------------------------- request building

    def build_request(
        self, *, system: str, messages: list[dict],
        tools: list[dict] | None = None, max_tokens: int = 4096,
    ) -> tuple[str, dict, dict]:
        """(url, headers, payload) — отдельно, чтобы тестировать без сети."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        return ANTHROPIC_URL, headers, payload

    async def _call(self, *, system: str, messages: list[dict],
                    tools: list[dict] | None = None) -> dict:
        url, headers, payload = self.build_request(
            system=system, messages=messages, tools=tools
        )
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def _call_json(self, *, system: str, prompt: str) -> dict:
        """Один ретрай на невалидный JSON: возвращаем ошибку модели и просим починить."""
        messages = [{"role": "user", "content": prompt}]
        data = await self._call(system=system, messages=messages)
        text = "".join(
            c.get("text", "") for c in data.get("content", []) if c.get("type") == "text"
        )
        try:
            return _extract_json(text)
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("bad JSON from model, retrying: %s", e)
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content":
                    f"Your JSON failed to parse: {e}. "
                    "Reply again with ONLY the corrected <json>...</json>."},
            ]
            data = await self._call(system=system, messages=messages)
            text = "".join(
                c.get("text", "") for c in data.get("content", [])
                if c.get("type") == "text"
            )
            return _extract_json(text)

    # -------------------------------------------------- interface

    async def write_script(
        self, *, game: str, idea: str, fmt: str, context: str
    ) -> ScriptDraft:
        system = (
            "You are the script writer for vertical Roblox shorts. Follow the "
            "production bible strictly (block structure, word budget, hooks). "
            + SCRIPT_JSON_SPEC
        )
        prompt = (
            f"Write a format-{fmt} script.\nGame: {game}\nIdea: {idea}\n"
            f"Context/bible extract:\n{context}"
        )
        return _draft_from_dict(await self._call_json(system=system, prompt=prompt))

    async def punch_up(self, draft: ScriptDraft, *, game: str, fmt: str) -> ScriptDraft:
        system = (
            "You punch up Roblox shorts scripts: sharper hook, tighter wording, "
            "stronger loop. Keep the structure and block count. " + SCRIPT_JSON_SPEC
        )
        prompt = (
            f"Punch up this format-{fmt} script for {game}:\n"
            f"<json>{json.dumps(_draft_to_dict(draft), ensure_ascii=False)}</json>"
        )
        return _draft_from_dict(await self._call_json(system=system, prompt=prompt))

    async def edit_script(
        self, current: ScriptDraft, *, instruction: str, block_ordinal: int | None
    ) -> ScriptDraft:
        system = (
            "You edit one Roblox shorts script per the user's instruction, "
            "changing as little as possible. " + SCRIPT_JSON_SPEC
        )
        target = (
            f"Apply to block ordinal {block_ordinal} only."
            if block_ordinal else "Apply to the whole script."
        )
        prompt = (
            f"Instruction: {instruction}\n{target}\nCurrent script:\n"
            f"<json>{json.dumps(_draft_to_dict(current), ensure_ascii=False)}</json>"
        )
        return _draft_from_dict(await self._call_json(system=system, prompt=prompt))

    async def agent_turn(
        self, *, system: str, messages: list[dict], tools: list[dict]
    ) -> dict:
        return await self._call(system=system, messages=messages, tools=tools)


# ------------------------------------------------------------------ TTS

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"


class RealTTS:
    is_mock = False

    def __init__(self, *, api_key: str) -> None:
        self.api_key = api_key

    def build_request(self, *, text: str, voice_id: str) -> tuple[str, dict, dict]:
        url = ELEVENLABS_URL.format(voice_id=voice_id or "default")
        headers = {"xi-api-key": self.api_key, "content-type": "application/json"}
        payload = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "output_format": "pcm_44100",
        }
        return url, headers, payload

    @staticmethod
    def _words_from_alignment(alignment: dict) -> list[dict]:
        """character-level alignment ElevenLabs -> пословные тайминги."""
        chars = alignment.get("characters", [])
        starts = alignment.get("character_start_times_seconds", [])
        ends = alignment.get("character_end_times_seconds", [])
        words: list[dict] = []
        cur, w_start, w_end = "", 0.0, 0.0
        for ch, s, e in zip(chars, starts, ends):
            if ch.isspace():
                if cur:
                    words.append({"w": cur, "s": round(w_start, 3), "e": round(w_end, 3)})
                    cur = ""
                continue
            if not cur:
                w_start = s
            cur += ch
            w_end = e
        if cur:
            words.append({"w": cur, "s": round(w_start, 3), "e": round(w_end, 3)})
        return words

    async def synth(self, *, text: str, voice_id: str, out_wav: str) -> TTSResult:
        url, headers, payload = self.build_request(text=text, voice_id=voice_id)
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        pcm = base64.b64decode(data.get("audio_base64", ""))
        out = Path(out_wav)
        out.parent.mkdir(parents=True, exist_ok=True)
        raw = out.with_suffix(".pcm")
        raw.write_bytes(pcm)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-y", "-f", "s16le", "-ar", "44100", "-ac", "1",
             "-i", str(raw), str(out)],
            check=True, capture_output=True,
        )
        raw.unlink(missing_ok=True)
        words = self._words_from_alignment(data.get("alignment") or {})
        if not words:
            words = self._align_with_whisper(out_wav, text)
        return TTSResult(
            wav_path=str(out), words=words,
            duration=round(media_duration(out), 3), is_mock=False,
        )

    @staticmethod
    def _align_with_whisper(wav_path: str, text: str) -> list[dict]:
        """Фолбэк-выравнивание. faster-whisper НЕ ставится в дев-контейнер (тяжёлый).

        TODO: сверять распознанные слова с текстом сценария (forced alignment).
        """
        try:
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as e:
            raise RuntimeError(
                "Word alignment fallback needs faster-whisper: run "
                "`pip install faster-whisper` on the server (не ставится в дев-окружении)."
            ) from e
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(wav_path, word_timestamps=True)
        words = []
        for seg in segments:
            for w in seg.words or []:
                words.append(
                    {"w": w.word.strip(), "s": round(w.start, 3), "e": round(w.end, 3)}
                )
        return words


# ------------------------------------------------------------------ YouTube


class RealYouTube:
    is_mock = False

    def __init__(self, *, proxy: str = "", cookies: str = "") -> None:
        self.proxy = proxy
        self.cookies = cookies  # текст cookies.txt

    def _base_opts(self, tmpdir: str | None = None) -> dict:
        opts: dict = {"quiet": True, "noprogress": True, "no_warnings": True}
        if self.proxy:
            opts["proxy"] = self.proxy
        if self.cookies and tmpdir:
            cf = Path(tmpdir) / "cookies.txt"
            cf.write_text(self.cookies, encoding="utf-8")
            opts["cookiefile"] = str(cf)
        return opts

    def build_search_opts(self, *, query: str, limit: int) -> tuple[str, dict]:
        """(поисковый URL, опции yt-dlp) — тестируется без сети."""
        opts = self._base_opts()
        opts["extract_flat"] = "in_playlist"
        return f"ytsearch{max(1, limit)}:{query}", opts

    def build_download_opts(
        self, *, yt_video_id: str, start: float, end: float, out_path: str
    ) -> tuple[str, dict]:
        import yt_dlp.utils  # noqa: PLC0415

        opts = self._base_opts()
        opts.update(
            {
                "format": "bestvideo[height<=1080][ext=mp4]/best[height<=1080]",
                "outtmpl": out_path,
                "download_ranges": yt_dlp.utils.download_range_func(
                    None, [(start, end)]
                ),
                "force_keyframes_at_cuts": True,
            }
        )
        return f"https://www.youtube.com/watch?v={yt_video_id}", opts

    async def search(self, *, query: str, limit: int) -> list[FoundVideo]:
        import asyncio  # noqa: PLC0415

        import yt_dlp  # noqa: PLC0415

        url, opts = self.build_search_opts(query=query, limit=limit)

        def _run() -> list[FoundVideo]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            out = []
            for e in (info or {}).get("entries", []) or []:
                out.append(
                    FoundVideo(
                        yt_video_id=str(e.get("id", ""))[:24],
                        title=str(e.get("title", ""))[:256],
                        channel=str(e.get("channel") or e.get("uploader") or "")[:128],
                        duration=float(e.get("duration") or 0),
                    )
                )
            return out

        return await asyncio.to_thread(_run)

    async def download_window(
        self, *, yt_video_id: str, start: float, end: float, out_path: str
    ) -> str:
        import asyncio  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        import yt_dlp  # noqa: PLC0415

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        url, opts = self.build_download_opts(
            yt_video_id=yt_video_id, start=start, end=end, out_path=out_path
        )

        def _run() -> None:
            with tempfile.TemporaryDirectory() as td:
                if self.cookies:
                    cf = Path(td) / "cookies.txt"
                    cf.write_text(self.cookies, encoding="utf-8")
                    opts["cookiefile"] = str(cf)
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])

        await asyncio.to_thread(_run)
        return out_path

    async def get_transcript(self, *, yt_video_id: str) -> list[dict]:
        """Автосабы через yt-dlp (json3), без скачивания видео."""
        import asyncio  # noqa: PLC0415

        import yt_dlp  # noqa: PLC0415

        opts = self._base_opts()
        opts.update({"writesubtitles": True, "writeautomaticsub": True, "skip_download": True})

        def _run() -> list[dict]:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={yt_video_id}", download=False
                )
            subs = (info or {}).get("automatic_captions") or {}
            tracks = subs.get("en") or []
            json3 = next((t for t in tracks if t.get("ext") == "json3"), None)
            if not json3:
                return []
            with httpx.Client(timeout=60) as client:
                data = client.get(json3["url"]).json()
            out = []
            for ev in data.get("events", []):
                text = "".join(s.get("utf8", "") for s in ev.get("segs", [])).strip()
                if text:
                    out.append({"t": round(ev.get("tStartMs", 0) / 1000.0, 2), "s": text})
            return out

        return await asyncio.to_thread(_run)
