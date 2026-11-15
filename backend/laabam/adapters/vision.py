"""Vision providers: read a distributor bill photo into header + lines.

Sarvam Document AI (Sarvam Vision, schema-based Extract), Gemini, and an offline twin; LAABAM_VISION picks
one. The model copies what is printed; it computes nothing. Code checks the arithmetic afterwards
(purchases/reading.py), and no figure counts until the owner confirms the bill.
"""
from __future__ import annotations

import base64
import dataclasses
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import yaml

from ..purchases.reading import BillReading, ReadLine
from .base import AdapterError

SAMPLES_FILE = Path(__file__).resolve().parents[1] / "bill_samples.yaml"
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")

PROMPT = """You are reading a photo of a distributor's bill (an Indian wholesale invoice, printed or
handwritten) for a small shop. Copy what is written. Do not calculate, correct, or guess anything.

- distributor: the seller's name at the top. bill_no and bill_date exactly as written.
- lines: one per item row, top to bottom. raw_text = the item description as written (keep pack sizes
  like "1LTR" or "500G"). qty, unit, rate (price per unit) and amount (the row total) exactly as written,
  digits only plus any decimal point or commas. Use null for anything you cannot read clearly.
- Skip rows that are not items: sub-totals, tax summaries, round-off, previous balance.
- total: the bill's grand total as written, or null.
If this is not a bill, return an empty lines list."""

_NULLABLE = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "distributor": _NULLABLE,
        "bill_no": _NULLABLE,
        "bill_date": _NULLABLE,
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"raw_text": {"type": "string"}, "qty": _NULLABLE, "unit": _NULLABLE,
                               "rate": _NULLABLE, "amount": _NULLABLE},
                "required": ["raw_text", "qty", "unit", "rate", "amount"],
            },
        },
        "total": _NULLABLE,
    },
    "required": ["distributor", "bill_no", "bill_date", "lines", "total"],
}

MAX_LINES = 60


class VisionNotSetUp(RuntimeError):
    """No vision service is configured and the photo is not a known sample."""


class VisionRejected(AdapterError):
    """The provider refused this photo or this account (bad file, no credits, job failed). Retrying the same
    request won't help; another provider might."""


def _s(v) -> str | None:
    """Numbers can come back as JSON numbers despite the schema; keep them as printed text."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    return str(v).strip() or None


def reading_from_json(data: dict, provider: str, sample: str | None = None,
                      request_id: str | None = None) -> BillReading:
    if not isinstance(data, dict) or not isinstance(data.get("lines", []), list):
        raise AdapterError(f"{provider}: unexpected bill shape")
    lines = []
    for row in data.get("lines", [])[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        raw = (_s(row.get("raw_text")) or "")[:200]
        if not raw:
            continue
        lines.append(ReadLine(raw, _s(row.get("qty")), _s(row.get("unit")), _s(row.get("rate")),
                              _s(row.get("amount"))))
    return BillReading(lines=lines, distributor=(_s(data.get("distributor")) or "")[:120],
                       bill_no=(_s(data.get("bill_no")) or "")[:64], bill_date=_s(data.get("bill_date")) or "",
                       total=_s(data.get("total")), provider=provider, sample=sample, request_id=request_id)


class GeminiVision:
    """Gemini API generateContent with a JSON schema. https://ai.google.dev/gemini-api/docs"""

    name = "gemini"
    accepts = IMAGE_TYPES

    def __init__(self, api_key: str, model: str, base_url: str = "https://generativelanguage.googleapis.com",
                 timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        self.api_key, self.model = api_key, model
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def read_bill(self, image: bytes, content_type: str, sample: str | None = None) -> BillReading:
        if not self.api_key:
            raise VisionNotSetUp("LAABAM_GEMINI_API_KEY is not set")
        mime = content_type.split(";")[0].strip().lower()
        if mime not in IMAGE_TYPES:
            raise VisionRejected(f"gemini: unsupported image type {mime}")
        body = {
            "contents": [{"role": "user", "parts": [
                {"text": PROMPT},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(image).decode()}},
            ]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                                 "responseJsonSchema": SCHEMA},
        }
        url = f"{self.base_url}/v1beta/models/{self.model}:generateContent"
        try:
            r = self.client.post(url, headers={"x-goog-api-key": self.api_key}, json=body)
        except httpx.HTTPError as e:
            raise AdapterError(f"gemini: {type(e).__name__}") from e
        if r.status_code != 200:
            status = ""
            try:
                status = r.json().get("error", {}).get("status", "")
            except ValueError:
                pass
            err = VisionRejected if r.status_code in (400, 401, 403, 413) else AdapterError
            raise err(f"gemini: HTTP {r.status_code} {status}".strip())
        try:
            out = r.json()
            cand = out["candidates"][0]
            text = "".join(p.get("text", "") for p in cand["content"]["parts"] if not p.get("thought"))
            data = json.loads(text)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise AdapterError("gemini: no readable answer") from e
        return reading_from_json(data, self.name, request_id=out.get("responseId"))


# ---------------- Sarvam Document AI ----------------

# Extract schema rules (docs.sarvam.ai, Document AI > Extract): root object with properties, every field a type
# and a description, depth <= 4, no null types. Every value is a string so numbers come back as printed; an
# empty string means "couldn't read it", and code then flags the line for the owner.
SARVAM_SCHEMA = {
    "type": "object",
    "properties": {
        "distributor": {"type": "string", "description": "The seller's (distributor's or wholesaler's) business "
                        "name at the top of the bill, exactly as written. Not the buyer's shop name."},
        "bill_no": {"type": "string", "description": "Bill or invoice number exactly as written, e.g. SLA/2611."},
        "bill_date": {"type": "string", "description": "Bill date exactly as written, e.g. 08/11/2026 or 15-11-26. "
                      "Do not reformat it."},
        "total": {"type": "string", "description": "The bill's grand total (net amount payable) exactly as "
                  "printed or written, digits with any commas and decimal point. Empty string if not visible."},
        "lines": {
            "type": "array",
            "description": "One entry per item row, top to bottom. Skip rows that are not items: sub-totals, tax "
                           "summaries, discounts, round-off, previous balance.",
            "items": {
                "type": "object",
                "description": "One item row of the bill.",
                "properties": {
                    "raw_text": {"type": "string", "description": "The item description exactly as written, "
                                 "in its own script, keeping pack sizes such as 1LTR, 500G, 25KG."},
                    "qty": {"type": "string", "description": "Quantity exactly as written, digits only. Empty "
                            "string if unclear."},
                    "unit": {"type": "string", "description": "Unit exactly as written (PCS, KG, PKT, BOX, BAG, "
                             "L). Empty string if none."},
                    "rate": {"type": "string", "description": "Price per unit exactly as written, digits with any "
                             "decimal point. Empty string if the bill has no rate column or it is unclear."},
                    "amount": {"type": "string", "description": "The row's amount exactly as written, digits with "
                               "any commas and decimal point. Copy it; never calculate it. Empty string if unclear."},
                },
            },
        },
    },
}

SARVAM_DONE = ("completed", "partially_completed")
SARVAM_FAILED = ("failed", "rejected")
_SARVAM_RETRY = (429, 500, 502, 503, 504)
_SARVAM_EXT = {"image/jpeg": "jpg", "image/png": "png"}


class SarvamVision:
    """Sarvam Document AI Extract (Sarvam Vision): POST /doc-ai/v1/job/extract (multipart: file + schema),
    poll GET /doc-ai/v1/job/{id}/status, then GET /doc-ai/v1/job/{id}/results -> {"result": {...}}.

    Accepts JPEG and PNG (and PDF, ZIP); the service converts other photo types before submitting. The whole
    read is bounded by `timeout` seconds; 429 and 5xx answers are retried with backoff inside it, but 402 (no
    credits) and a seat-budget 429 are not, since waiting won't clear them.
    """

    name = "sarvam"
    accepts = ("image/jpeg", "image/png")

    def __init__(self, api_key: str, base_url: str = "https://api.sarvam.ai", language: str = "",
                 timeout: float = 90.0, poll_every: float = 2.0, attempts: int = 3,
                 client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.api_key, self.language = api_key, language
        self.base_url = base_url.rstrip("/")
        self.timeout, self.poll_every, self.attempts = timeout, poll_every, max(1, attempts)
        self.client = client or httpx.Client(timeout=30.0)
        self.sleep, self.clock = sleep, clock

    def _call(self, method: str, path: str, deadline: float, label: str, **kw) -> dict:
        last = f"sarvam {label}: no answer"
        for attempt in range(self.attempts):
            wait = float(2**attempt)
            try:
                r = self.client.request(method, f"{self.base_url}{path}",
                                        headers={"api-subscription-key": self.api_key}, **kw)
            except httpx.HTTPError as e:
                last = f"sarvam {label}: {type(e).__name__}"
            else:
                if r.status_code in (200, 201, 202):
                    try:
                        out = r.json()
                    except ValueError as e:
                        raise AdapterError(f"sarvam {label}: not JSON") from e
                    if not isinstance(out, dict):
                        raise AdapterError(f"sarvam {label}: unexpected answer")
                    return out
                code = ""
                try:
                    err = r.json().get("error", {})
                    code = err.get("code", "") if isinstance(err, dict) else ""
                except (ValueError, AttributeError):
                    pass
                last = f"sarvam {label}: HTTP {r.status_code} {code}".strip()
                if r.status_code not in _SARVAM_RETRY or code == "seat_budget_exhausted_error":
                    raise VisionRejected(last)
                try:
                    wait = min(10.0, max(wait, float(r.headers.get("retry-after", 0))))
                except ValueError:
                    pass
            if attempt + 1 < self.attempts:
                if self.clock() + wait >= deadline:
                    break
                self.sleep(wait)
        raise AdapterError(last)

    def read_bill(self, image: bytes, content_type: str, sample: str | None = None) -> BillReading:
        if not self.api_key:
            raise VisionNotSetUp("LAABAM_SARVAM_API_KEY is not set")
        mime = content_type.split(";")[0].strip().lower()
        if mime not in self.accepts:
            raise VisionRejected(f"sarvam: send JPEG or PNG, not {mime}")
        deadline = self.clock() + self.timeout
        form = {"schema": json.dumps(SARVAM_SCHEMA), "output_format": "json"}
        if self.language:
            form["language"] = self.language
        job = self._call("POST", "/doc-ai/v1/job/extract", deadline, "extract", data=form,
                         files={"file": (f"bill.{_SARVAM_EXT[mime]}", image, mime)})
        job_id = job.get("job_id")
        if not isinstance(job_id, str) or not job_id or "/" in job_id:
            raise AdapterError("sarvam extract: no job id")
        status = str(job.get("status", "")).lower()
        while status not in SARVAM_DONE:
            if status in SARVAM_FAILED:
                raise VisionRejected(f"sarvam: job {status}")
            if self.clock() + self.poll_every >= deadline:
                raise AdapterError(f"sarvam: still {status or 'pending'} after {self.timeout:.0f}s")
            self.sleep(self.poll_every)
            status = str(self._call("GET", f"/doc-ai/v1/job/{job_id}/status", deadline, "status")
                         .get("status", "")).lower()
        out = self._call("GET", f"/doc-ai/v1/job/{job_id}/results", deadline, "results")
        data = out.get("result")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError as e:
                raise AdapterError("sarvam results: not JSON") from e
        reading = reading_from_json(data, self.name, request_id=job_id)
        if status == "partially_completed":
            reading = dataclasses.replace(reading, notes=["partially_completed"])
        return reading


class VisionChain:
    """The chosen provider, then a fallback (LAABAM_VISION_FALLBACK) if it fails or reads no lines.
    The reading names the provider that read it, and its notes say why the first one didn't."""

    def __init__(self, primary, fallback) -> None:
        self.primary, self.fallback = primary, fallback
        self.name = primary.name
        self.accepts = tuple(t for t in primary.accepts if t in fallback.accepts) or primary.accepts

    def read_bill(self, image: bytes, content_type: str, sample: str | None = None) -> BillReading:
        try:
            first = self.primary.read_bill(image, content_type, sample)
            if first.lines:
                return first
            why = f"{self.primary.name}: no lines"
        except AdapterError as e:
            why = str(e)
        second = self.fallback.read_bill(image, content_type, sample)
        return dataclasses.replace(second, notes=[*second.notes, f"fallback after {why}"])


def sample_date(text: str | None, today=None) -> str | None:
    """'@today-6' -> six days ago (IST) as printed on Indian bills, '08/11/2026'. Other dates pass through."""
    if not text or not str(text).startswith("@today"):
        return text
    from datetime import timedelta

    from ..clock import ist_day
    from ..models import utcnow

    day = (today or ist_day(utcnow())) + timedelta(days=int(str(text)[6:] or 0))
    return day.strftime("%d/%m/%Y")


class OfflineVision:
    """Sample bills (bill_samples.yaml), chosen by name."""

    name = "offline"
    accepts = IMAGE_TYPES

    def __init__(self, path: Path = SAMPLES_FILE) -> None:
        self.samples: dict[str, dict] = yaml.safe_load(path.read_text(encoding="utf-8"))

    def read_bill(self, image: bytes, content_type: str, sample: str | None = None) -> BillReading:
        if sample and sample in self.samples:
            data = dict(self.samples[sample])
            data["bill_date"] = sample_date(data.get("bill_date"))
            return reading_from_json(data, self.name, sample=sample)
        raise VisionNotSetUp("offline vision knows only the sample bills")
