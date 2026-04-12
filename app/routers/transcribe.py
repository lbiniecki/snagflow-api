"""
Transcribe router — send audio to OpenAI Whisper, get text back
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.models.schemas import TranscribeResponse
from app.services.auth_dep import get_current_user
from app.config import settings
import httpx
import tempfile
import os

router = APIRouter()

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
MAX_AUDIO_MB = 25  # Whisper limit


@router.post("/", response_model=TranscribeResponse)
async def transcribe_audio(
    audio: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """
    Transcribe audio to text using OpenAI Whisper.
    Accepts: mp3, mp4, mpeg, mpga, m4a, wav, webm
    """
    content = await audio.read()

    if len(content) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Audio too large (max {MAX_AUDIO_MB}MB)")

    # Write to temp file (Whisper needs a file)
    suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            with open(tmp_path, "rb") as f:
                response = await client.post(
                    WHISPER_URL,
                    headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                    files={"file": (audio.filename or f"audio{suffix}", f, audio.content_type or "audio/webm")},
                    data={
                        "model": "whisper-1",
                        "language": "en",
                        "response_format": "verbose_json",
                    },
                )

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Whisper API error: {response.text}",
            )

        result = response.json()
        return TranscribeResponse(
            text=result.get("text", ""),
            duration=result.get("duration"),
        )
    finally:
        os.unlink(tmp_path)
