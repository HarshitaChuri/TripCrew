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


# Whisper models (including Groq's) are known to "hallucinate" these exact
# generic phrases when given silent or near-silent audio — an artifact of
# being trained partly on YouTube captions. If we get back one of these,
# it almost certainly means no real speech was captured, not that the
# user genuinely said this.
SILENCE_HALLUCINATIONS = {
    "thank you.",
    "thank you",
    "thanks for watching.",
    "thanks for watching!",
    "thank you for watching.",
    "please subscribe.",
    "subscribe.",
    "bye.",
    "bye-bye.",
    "you",
    "the",
    "okay.",
    "ok.",
}


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

    if not text or len(text) < 3:
        raise ValueError(
            "Could not detect clear speech in that recording. "
            "Please try again, speaking clearly right after the mic starts listening."
        )

    if text.lower() in SILENCE_HALLUCINATIONS:
        raise ValueError(
            "Didn't catch that clearly (the recording may have been too short or silent). "
            "Please try again, speaking right after clicking the mic."
        )

    return text