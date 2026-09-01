"""Юнит-тесты зоны B: сабы, вертикальный кроп, mock/real провайдеры (без сети)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from shortforge.media.ffutil import data_dir, media_duration, video_info
from shortforge.media.subs import CENTER_X, CENTER_Y, build_ass
from shortforge.media.vertical import crop_vertical, snippet_windows
from shortforge.providers.mock import MockLLM, MockTTS, MockYouTube
from shortforge.providers.real import RealLLM, RealTTS, RealYouTube

pytestmark = pytest.mark.asyncio

WORDS = [
    {"w": "Nobody", "s": 0.0, "e": 0.35},
    {"w": "noticed", "s": 0.35, "e": 0.7},
    {"w": "THIS", "s": 0.7, "e": 1.05},
    {"w": "secret", "s": 1.05, "e": 1.4},
    {"w": "door", "s": 1.4, "e": 1.75},
]


# ------------------------------------------------------------------ subs

async def test_subs_ass_generated():
    ass = build_ass(WORDS, dictionary={"door": "gate"})
    assert "[Script Info]" in ass and "PlayResY: 1920" in ass
    assert f"\\pos({CENTER_X},{CENTER_Y})" in ass  # центр 62% высоты
    assert "\\k" in ass  # караоке-подсветка активного слова
    assert "NOBODY" in ass and "NOTICED" in ass  # капс
    assert "GATE" in ass and "DOOR" not in ass  # subs_dictionary применён
    assert "&H000000FF" in ass  # горячее слово (secret) — красное
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert dialogues, "нет Dialogue-строк"
    for d in dialogues:  # 1-3 слова на строку
        n_words = d.count("\\k")
        assert 1 <= n_words <= 3


# ------------------------------------------------------------------ vertical

async def test_vertical_crop(fixture_videos):
    out = data_dir() / "_test_tmp" / "crop.mp4"
    crop_vertical(fixture_videos[0], out, start=1.0, duration=2.0)
    info = video_info(out)
    assert (info["width"], info["height"]) == (1080, 1920)
    assert 1.5 <= info["duration"] <= 2.5


async def test_snippet_windows(fixture_videos):
    windows = snippet_windows(fixture_videos[0], count=3)
    assert 1 <= len(windows) <= 3
    for start, dur, score in windows:
        assert start >= 0
        assert 3.0 <= dur <= 5.0
        assert score >= 0


# ------------------------------------------------------------------ mock providers

async def test_mock_llm_format_a():
    llm = MockLLM()
    draft = await llm.write_script(
        game="Steal a Brainrot", idea="secret ramadan brainrot", fmt="A", context=""
    )
    draft = await llm.punch_up(draft, game="Steal a Brainrot", fmt="A")
    roles = [b["role"] for b in draft.blocks]
    assert roles == ["hook", "setup", "evidence", "evidence", "cta", "twist", "loop"]
    assert len(draft.title) <= 55 and draft.title.endswith("..")
    assert "Steal a Brainrot" in draft.blocks[0]["text_en"]
    assert len(draft.blocks[0]["text_en"].split()) <= 14  # хук короткий
    assert all(b["search_keys"] for b in draft.blocks)
    total_words = sum(len(b["text_en"].split()) for b in draft.blocks)
    assert 55 <= total_words <= 120  # бюджет слов формата A


async def test_mock_llm_format_b_and_edit():
    llm = MockLLM()
    draft = await llm.write_script(game="Tower of Hell", idea="obby grind", fmt="B", context="")
    assert len(draft.blocks) == 5
    assert draft.title == draft.title.lower()

    edited = await llm.edit_script(draft, instruction="punch it up", block_ordinal=2)
    assert edited.blocks[1]["text_en"] != draft.blocks[1]["text_en"]
    assert edited.blocks[0]["text_en"] == draft.blocks[0]["text_en"]


async def test_mock_llm_agent_turn():
    llm = MockLLM()
    tools = [{"name": "replace_clip"}, {"name": "edit_block"}]
    out = await llm.agent_turn(
        system="", messages=[{"role": "user", "content": "replace clip in block 3"}],
        tools=tools,
    )
    assert out["stop_reason"] == "tool_use"
    assert out["content"][0]["name"] == "replace_clip"
    assert out["content"][0]["input"]["block_ordinal"] == 3


async def test_mock_tts(tmp_path):
    tts = MockTTS()
    text = "This secret door was never meant to be found by anyone"
    res = await tts.synth(text=text, voice_id="", out_wav=str(tmp_path / "v.wav"))
    assert res.is_mock and Path(res.wav_path).exists()
    assert res.duration > 1.0
    assert abs(media_duration(res.wav_path) - res.duration) < 0.5
    assert len(res.words) == len(text.split())
    for prev, cur in zip(res.words, res.words[1:]):  # равномерная монотонная раскладка
        assert cur["s"] >= prev["s"] and cur["e"] > cur["s"]
    assert abs(res.words[-1]["e"] - res.duration) < 0.1


async def test_mock_youtube(fixture_videos, tmp_path):
    yt = MockYouTube()
    found = await yt.search(query="Steal a Brainrot secret basement", limit=3)
    assert len(found) == 3
    assert found[0].duration == 60.0
    words = {e["s"] for e in found[0].transcript}
    assert "basement" in words  # слова запроса размазаны по таймлайну

    out = tmp_path / "win.mp4"
    await yt.download_window(
        yt_video_id=found[0].yt_video_id, start=10, end=20, out_path=str(out)
    )
    assert out.exists() and media_duration(out) > 5

    tr = await yt.get_transcript(yt_video_id=found[0].yt_video_id)
    assert tr and any("secret" == e["s"] for e in tr)


# ------------------------------------------------------------------ real providers (без сети)

async def test_real_llm_builds_request():
    llm = RealLLM(api_key="sk-test", model="")
    url, headers, payload = llm.build_request(
        system="sys", messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t"}],
    )
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"]
    assert payload["model"] == "claude-sonnet-4-5"  # дефолт из настроек
    assert payload["messages"][0]["content"] == "hi"
    assert payload["tools"] == [{"name": "t"}]


async def test_real_tts_builds_request():
    tts = RealTTS(api_key="el-test")
    url, headers, payload = tts.build_request(text="hello", voice_id="voice1")
    assert url.endswith("/v1/text-to-speech/voice1/with-timestamps")
    assert headers["xi-api-key"] == "el-test"
    assert payload["text"] == "hello"

    words = RealTTS._words_from_alignment(
        {
            "characters": list("hi yo"),
            "character_start_times_seconds": [0.0, 0.1, 0.2, 0.3, 0.4],
            "character_end_times_seconds": [0.1, 0.2, 0.3, 0.4, 0.5],
        }
    )
    assert [w["w"] for w in words] == ["hi", "yo"]
    assert words[0]["s"] == 0.0 and words[1]["e"] == 0.5


async def test_real_youtube_builds_opts():
    yt = RealYouTube(proxy="socks5://127.0.0.1:1080", cookies="")
    url, opts = yt.build_search_opts(query="roblox obby", limit=10)
    assert url == "ytsearch10:roblox obby"
    assert opts["proxy"] == "socks5://127.0.0.1:1080"

    url, opts = yt.build_download_opts(
        yt_video_id="dQw4w9WgXcQ", start=30.0, end=90.0, out_path="/tmp/x.mp4"
    )
    assert url.endswith("watch?v=dQw4w9WgXcQ")
    assert opts["outtmpl"] == "/tmp/x.mp4"
    assert "download_ranges" in opts and opts["force_keyframes_at_cuts"]


async def test_whisper_fallback_is_lazy_and_explains():
    if importlib.util.find_spec("faster_whisper"):  # pragma: no cover
        pytest.skip("faster-whisper установлен")
    with pytest.raises(RuntimeError, match="faster-whisper"):
        RealTTS._align_with_whisper("/tmp/none.wav", "text")
