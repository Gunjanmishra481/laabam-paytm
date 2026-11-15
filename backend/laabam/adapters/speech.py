"""Speech providers: Sarvam (speech-to-text saaras, text-to-speech bulbul) and an offline twin.

Audio passes through and is dropped; nothing here stores it.
"""
from __future__ import annotations

import base64
import json
import threading
from collections import defaultdict
from pathlib import Path

import httpx
import yaml

from .base import AdapterError, Transcript

SARVAM_LANGUAGE = {"en": "en-IN", "ta": "ta-IN", "hi": "hi-IN"}
MAX_KEYTERMS = 50  # saaras:v4 limit
SAMPLES_FILE = Path(__file__).resolve().parents[1] / "speech_samples.yaml"


class SpeechNotSetUp(RuntimeError):
    """No speech service is configured and the clip is not a known sample."""


def _ext(content_type: str) -> str:
    base = content_type.split(";")[0].strip().lower()
    return {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
            "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac"}.get(base, "webm")


class SarvamSpeech:
    """https://docs.sarvam.ai — POST /speech-to-text (multipart) and /text-to-speech (JSON)."""

    name = "sarvam"

    def __init__(self, api_key: str, base_url: str = "https://api.sarvam.ai", stt_model: str = "saaras:v4",
                 tts_model: str = "bulbul:v3", speaker: str = "", timeout: float = 15.0,
                 client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.stt_model, self.tts_model, self.speaker = stt_model, tts_model, speaker
        self.client = client or httpx.Client(timeout=timeout)

    def _post(self, path: str, **kw) -> dict:
        if not self.api_key:
            raise SpeechNotSetUp("LAABAM_SARVAM_API_KEY is not set")
        try:
            r = self.client.post(f"{self.base_url}{path}", headers={"api-subscription-key": self.api_key}, **kw)
        except httpx.HTTPError as e:
            raise AdapterError(f"sarvam {path}: {type(e).__name__}") from e
        if r.status_code != 200:
            code = ""
            try:
                code = r.json().get("error", {}).get("code", "")
            except ValueError:
                pass
            raise AdapterError(f"sarvam {path}: HTTP {r.status_code} {code}".strip())
        try:
            return r.json()
        except ValueError as e:
            raise AdapterError(f"sarvam {path}: not JSON") from e

    def transcribe(self, audio: bytes, content_type: str, language: str, keyterms: tuple[str, ...] = (),
                   clip_name: str | None = None) -> Transcript:
        data = {"model": self.stt_model, "language_code": SARVAM_LANGUAGE.get(language, "unknown")}
        if self.stt_model.endswith("v4") and keyterms:
            data["keyterms"] = json.dumps([k[:64] for k in keyterms[:MAX_KEYTERMS]], ensure_ascii=False)
        ctype = content_type.split(";")[0].strip() or "audio/webm"
        body = self._post("/speech-to-text", data=data,
                          files={"file": (f"order.{_ext(content_type)}", audio, ctype)})
        text = body.get("transcript")
        if not isinstance(text, str):
            raise AdapterError("sarvam /speech-to-text: no transcript")
        return Transcript(text=text.strip(), language=language, provider=self.name,
                          request_id=body.get("request_id"))

    def speak(self, text: str, language: str) -> bytes | None:
        if language not in SARVAM_LANGUAGE:
            return None
        payload = {"text": text[:2500], "language_code": SARVAM_LANGUAGE[language], "model": self.tts_model,
                   "output_audio_codec": "mp3"}
        if self.speaker:
            payload["speaker"] = self.speaker
        body = self._post("/text-to-speech", json=payload)
        audios = body.get("audios") or []
        if not audios or not isinstance(audios[0], str):
            raise AdapterError("sarvam /text-to-speech: no audio")
        try:
            return base64.b64decode(audios[0], validate=True)
        except ValueError as e:
            raise AdapterError("sarvam /text-to-speech: bad audio") from e


class OfflineSpeech:
    """Fixtures: a clip name maps to a transcript (speech_samples.yaml). Speaks nothing (the browser does)."""

    name = "offline"

    def __init__(self, path: Path = SAMPLES_FILE) -> None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.samples: dict[str, Transcript] = {}
        self.orders: dict[str, list[str]] = defaultdict(list)
        for lang, rows in raw.items():
            for row in rows:
                language = row.get("language", lang)
                self.samples[row["name"]] = Transcript(row["text"], language, self.name, sample=row["name"])
                if lang != "fixes":
                    self.orders[language].append(row["name"])
        self._next: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = threading.Lock()

    def transcribe(self, audio: bytes, content_type: str, language: str, keyterms: tuple[str, ...] = (),
                   clip_name: str | None = None) -> Transcript:
        if clip_name and clip_name in self.samples:
            return self.samples[clip_name]
        raise SpeechNotSetUp("offline speech knows only the sample clips")

    def next_order_sample(self, language: str, shop: str = "") -> str | None:
        """The demo's stand-in for a live order: this shop's next sample clip in its language."""
        names = self.orders.get(language) or []
        if not names:
            return None
        with self._lock:
            i = self._next[(shop, language)] % len(names)
            self._next[(shop, language)] += 1
        return names[i]

    def speak(self, text: str, language: str) -> bytes | None:
        return None
