"""transcribe.py — local Whisper wrapper for Discord voice messages.

Uses the `whisper` CLI (installed via `brew install openai-whisper` or
`pip install openai-whisper`) to transcribe audio attachments on-device.
No API key, no network call, no per-minute cost.

For faster speed on short clips, swap to Groq Whisper later by setting
GROQ_API_KEY — the function checks and prefers Groq if present.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("transcribe")

AUDIO_EXTS = {".ogg", ".mp3", ".m4a", ".wav", ".webm", ".flac", ".aac", ".opus"}

# Local whisper model — "base" is the sweet spot of speed vs accuracy on CPU.
# Options: tiny < base < small < medium < large (slower + more accurate).
LOCAL_MODEL = os.environ.get("WHISPER_MODEL", "base")
LOCAL_LANG = os.environ.get("WHISPER_LANG", "en")


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS


async def transcribe(path: Path) -> str | None:
    """Transcribe an audio file. Returns the transcript text or None on
    failure. Prefers Groq API if GROQ_API_KEY is set, falls back to local
    whisper CLI."""
    if not path.exists():
        log.warning("transcribe: file not found: %s", path)
        return None

    # Prefer Groq if wired
    if os.environ.get("GROQ_API_KEY"):
        try:
            return await _transcribe_groq(path)
        except Exception as e:
            log.warning("transcribe: Groq failed, falling back to local: %s", e)

    # Fall back to local whisper CLI
    return await _transcribe_local(path)


async def _transcribe_local(path: Path) -> str | None:
    """Run the local `whisper` CLI and read the resulting .txt."""
    if not shutil.which("whisper"):
        log.error("transcribe: `whisper` CLI not found. Install via "
                  "`brew install openai-whisper` or `pip install openai-whisper`.")
        return None

    out_dir = path.parent / "_transcripts"
    out_dir.mkdir(exist_ok=True)

    cmd = [
        "whisper", str(path),
        "--model", LOCAL_MODEL,
        "--language", LOCAL_LANG,
        "--output_format", "txt",
        "--output_dir", str(out_dir),
        "--fp16", "False",
        "--verbose", "False",
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
        if proc.returncode != 0:
            log.warning("transcribe: whisper rc=%d: %s",
                        proc.returncode, stderr.decode()[:500])
            return None
    except asyncio.TimeoutError:
        log.warning("transcribe: whisper timed out on %s", path)
        return None
    except Exception as e:
        log.warning("transcribe: whisper exec failed: %s", e)
        return None

    # whisper writes <stem>.txt into out_dir
    txt_path = out_dir / (path.stem + ".txt")
    if not txt_path.exists():
        log.warning("transcribe: expected transcript not found: %s", txt_path)
        return None

    text = txt_path.read_text().strip()
    if not text:
        return None
    log.info("transcribe: local whisper %s -> %d chars", path.name, len(text))
    return text


async def _transcribe_groq(path: Path) -> str | None:
    """Transcribe via Groq's Whisper API (fast, cheap, needs network)."""
    import httpx
    api_key = os.environ["GROQ_API_KEY"]
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    async with httpx.AsyncClient(timeout=60) as client:
        with path.open("rb") as f:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": "whisper-large-v3", "language": LOCAL_LANG},
                files={"file": (path.name, f, "audio/ogg")},
            )
    if resp.status_code != 200:
        log.warning("transcribe: Groq rc=%d: %s", resp.status_code, resp.text[:300])
        return None
    text = (resp.json().get("text") or "").strip()
    if not text:
        return None
    log.info("transcribe: Groq %s -> %d chars", path.name, len(text))
    return text
