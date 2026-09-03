import os
import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Groq's audio transcription endpoint (OpenAI-compatible Whisper API).
# Reuses the same GROQ_API_KEY already used for the LLM in backend.py.
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# whisper-large-v3-turbo is fast and cheap and is plenty accurate for
# short travel-request voice clips. Swap to "whisper-large-v3" if you
# need higher accuracy on noisy audio.
TRANSCRIBE_MODEL = "whisper-large-v3-turbo"


def transcribe_audio(file_bytes: bytes, filename: str = "audio.webm") -> str:
    """Sends recorded audio bytes to Groq Whisper and returns the transcript text."""
    if not GROQ_API_KEY:
        raise ValueError(
            "GROQ_API_KEY is missing. Please add it to your .env file to use voice input."
        )

    if not file_bytes:
        raise ValueError("No audio data received.")

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}"
    }

    files = {
        "file": (filename, file_bytes),
    }

    data = {
        "model": TRANSCRIBE_MODEL,
        "response_format": "json",
    }

    response = requests.post(
        GROQ_TRANSCRIBE_URL,
        headers=headers,
        files=files,
        data=data,
        timeout=60
    )

    if response.status_code != 200:
        raise ValueError(f"Groq transcription failed ({response.status_code}): {response.text}")

    result = response.json()
    text = result.get("text", "").strip()

    if not text:
        raise ValueError("Could not transcribe any speech from the recording.")

    return text
