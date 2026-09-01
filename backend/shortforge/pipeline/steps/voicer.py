"""Voicer: TTS полного текста, раскладка words по блокам -> t_start/t_end, VoiceTrack."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ...media.ffutil import job_dir, rel_path
from ...models import VideoJob, VoiceTrack
from ...providers.factory import get_providers
from .. import flow
from .common import active_script, script_blocks, words_count


async def run(session: AsyncSession, job: VideoJob, ctx: dict | None) -> str:
    providers = await get_providers(session)
    tts = providers.tts

    script = await active_script(session, job)
    if script is None:
        raise RuntimeError("voicer: нет сценария")
    blocks = await script_blocks(session, script)
    if not blocks:
        raise RuntimeError("voicer: сценарий без блоков")

    full_text = " ".join(b.text_en.strip() for b in blocks if b.text_en.strip())
    voice_dir = job_dir(job.batch_id, job.id, "voice")
    out_wav = voice_dir / f"voice_v{script.version}.wav"

    result = await tts.synth(
        text=full_text, voice_id=providers.voice_id, out_wav=str(out_wav)
    )

    # раскладка слов по блокам: последовательные срезы по числу слов блока
    words = result.words
    pos = 0
    for block in blocks:
        n = words_count(block.text_en)
        chunk = words[pos : pos + n]
        if chunk:
            block.t_start = float(chunk[0]["s"])
            block.t_end = float(chunk[-1]["e"])
        else:  # блок без слов — нулевая длительность на границе
            edge = words[pos - 1]["e"] if pos and words else 0.0
            block.t_start = block.t_end = float(edge)
        pos += n

    track = VoiceTrack(
        job_id=job.id,
        script_version=script.version,
        wav_path=rel_path(out_wav),
        words=words,
        duration=result.duration,
        voice_id=providers.voice_id,
        is_mock=result.is_mock,
    )
    session.add(track)
    await session.flush()
    await flow.after_voicer(session, job, ctx)
    return (
        f"voice v{script.version}: {result.duration:.1f}s, {len(words)} words, "
        f"mock={result.is_mock}"
    )
