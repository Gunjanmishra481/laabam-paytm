"""Voice billing: audio -> transcript (speech provider) -> the same pipeline as typed orders.

Audio is held in memory for the call and dropped; only the transcript is kept, with the bill.
Each step's time is logged (logger "laabam.latency") and shown in the demo's trace.
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from .adapters.base import AdapterError, Transcript
from .adapters.speech import SpeechNotSetUp
from .billing.service import BillService, NotAllowed
from .models import Item, Merchant

latency_log = logging.getLogger("laabam.latency")

MAX_AUDIO_BYTES = 2_000_000  # about 30 s of compressed speech, well over a counter order
MIN_AUDIO_BYTES = 200
MAX_AUDIO_SECONDS = 35  # the app stops recording at 30 s


class BadAudio(ValueError):
    """Not a recording in a codec we accept."""


def check_audio(audio: bytes, content_type: str) -> None:
    """The body must be what its Content-Type says, in an accepted codec (magic bytes), so nothing else (an image, a
    script) reaches the speech partner. WAV carries its length: over MAX_AUDIO_SECONDS is refused. Compressed codecs
    (WebM/Opus, Ogg, MP4/AAC, MP3) don't reliably say; their length is bounded by MAX_AUDIO_BYTES, the app's 30 s
    stop, and the speech partner's own limit."""
    base = content_type.split(";")[0].strip().lower()
    head = audio[:16]
    ok = {
        "audio/webm": head.startswith(bytes.fromhex("1a45dfa3")),  # EBML
        "audio/ogg": head.startswith(b"OggS"),
        "audio/mp4": head[4:8] == b"ftyp",
        "audio/aac": head[:2] in (bytes.fromhex("fff1"), bytes.fromhex("fff9")) or head[4:8] == b"ftyp",
        "audio/mpeg": head.startswith(b"ID3") or head[:2] in (bytes.fromhex("fffb"), bytes.fromhex("fff3"),
                                                              bytes.fromhex("fff2")),
        "audio/wav": head.startswith(b"RIFF") and audio[8:12] == b"WAVE",
        "audio/x-wav": head.startswith(b"RIFF") and audio[8:12] == b"WAVE",
    }.get(base)
    if not ok:
        raise BadAudio(base)
    if base in ("audio/wav", "audio/x-wav") and len(audio) >= 44:
        byte_rate = int.from_bytes(audio[28:32], "little")
        if byte_rate <= 0 or (len(audio) - 44) / byte_rate > MAX_AUDIO_SECONDS:
            raise BadAudio("too long")


class VoiceUnavailable(RuntimeError):
    pass


class HeardNothing(NotAllowed):
    pass


class VoiceService:
    def __init__(self, sessions, bills: BillService, speech, demo_samples: bool,
                 trace: Callable[[str, str, dict[str, Any]], None]) -> None:
        self.sessions, self.bills, self.speech = sessions, bills, speech
        self.demo_samples = demo_samples  # sandbox + offline speech: the demo may ask for sample orders
        self.trace = trace

    @property
    def mode(self) -> str:
        """live: a speech service; samples: the demo's sample clips; off: type orders."""
        if self.speech.name != "offline":
            return "live"
        return "samples" if self.demo_samples else "off"

    def _context(self, merchant_id: str) -> tuple[str, tuple[str, ...]]:
        with self.sessions() as s:
            m = s.get(Merchant, merchant_id)
            if m is None:
                raise NotAllowed("No such shop.")
            names = s.execute(select(Item.name).where(Item.merchant_id == merchant_id)
                              .order_by(Item.created_at.desc()).limit(50)).scalars().all()
            return m.language, tuple(names)

    def _transcribe(self, merchant_id: str, audio: bytes, content_type: str, clip_name: str | None,
                    purpose: str, sample_order: bool = False) -> tuple[Transcript, dict]:
        if len(audio) > MAX_AUDIO_BYTES:
            raise NotAllowed("That recording is too long. Please say a shorter order.")
        language, keyterms = self._context(merchant_id)
        # A sample order plays only when the demo asks for one by name. A recording is never swapped for a
        # sample: with no speech service it is refused (503), so the owner is told rather than misled.
        if sample_order:
            if self.mode != "samples" or purpose != "order":
                raise NotAllowed("Sample orders play only in the demo, when no speech service is set up.")
            clip_name = self.speech.next_order_sample(language, merchant_id)
            if clip_name is None:
                raise VoiceUnavailable(f"no sample orders in {language}")
        elif not clip_name and len(audio) < MIN_AUDIO_BYTES:
            raise HeardNothing("Nothing was heard. Hold the button and speak.")
        t0 = time.perf_counter()
        try:
            heard = self.speech.transcribe(audio, content_type, language, keyterms, clip_name)
        except SpeechNotSetUp as e:
            raise VoiceUnavailable(str(e)) from e
        except AdapterError:
            self.trace(merchant_id, "speech.failed", {"provider": self.speech.name, "audio_bytes": len(audio)})
            raise
        ms = round((time.perf_counter() - t0) * 1000, 1)
        info = {"provider": heard.provider, "sample": heard.sample, "request_id": heard.request_id,
                "audio_bytes": len(audio), "transcribe_ms": ms, "keyterms": len(keyterms)}
        self.trace(merchant_id, "speech.transcribed", {**info, "transcript": heard.text, "purpose": purpose})
        if not heard.text.strip():
            raise HeardNothing("Nothing was heard. Please say the order again.")
        return heard, info

    def _log(self, step: str, merchant_id: str, timings: dict, started: float) -> dict:
        timings = {**timings, "server_total_ms": round((time.perf_counter() - started) * 1000, 1)}
        latency_log.info(json.dumps({"step": step, "merchant": merchant_id, **timings}))
        return timings

    def question(self, merchant_id: str, audio: bytes, content_type: str, clip_name: str | None = None) -> str:
        """A spoken question for Ask: the words only. The audio is not stored."""
        heard, _ = self._transcribe(merchant_id, audio, content_type, clip_name, "question")
        return heard.text

    def bill(self, merchant_id: str, audio: bytes, content_type: str, clip_name: str | None = None,
             sample_order: bool = False) -> dict:
        started = time.perf_counter()
        heard, info = self._transcribe(merchant_id, audio, content_type, clip_name, "order", sample_order)
        out = self.bills.create_from_text(merchant_id, heard.text)
        timings = self._log("voice_bill", merchant_id, {"audio_bytes": info["audio_bytes"],
                                                         "transcribe_ms": info["transcribe_ms"],
                                                         **out.get("timings", {})}, started)
        out["timings"] = timings
        out["speech"] = {"provider": heard.provider, "sample": heard.sample, "heard": heard.text}
        return out

    def say(self, merchant_id: str, bill_id: str, audio: bytes, content_type: str,
            clip_name: str | None = None) -> dict:
        started = time.perf_counter()
        if self.bills.get(merchant_id, bill_id)["status"] != "draft":  # check before paying for speech
            raise NotAllowed("This bill is already confirmed or closed.")
        heard, info = self._transcribe(merchant_id, audio, content_type, clip_name, "fix")
        out = self.bills.say(merchant_id, bill_id, heard.text)
        out["timings"] = self._log("voice_fix", merchant_id, {"audio_bytes": info["audio_bytes"],
                                                               "transcribe_ms": info["transcribe_ms"]}, started)
        out["speech"] = {"provider": heard.provider, "sample": heard.sample, "heard": heard.text}
        return out
