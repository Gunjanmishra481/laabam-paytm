"""Distributor bills: photo -> read (vision: copies what's printed) -> checks (code) -> owner fixes -> confirm.

A live reader (Sarvam Document AI, Gemini) takes up to a minute, so the photo is saved as a bill in status
"reading" and the worker reads it (timer "purchase_read"): the call to the model runs outside any transaction,
then the lines are written and checked exactly as for an instant read. A read that fails twice leaves the bill
"failed"; the owner can try again or discard it. Offline sample bills are read at once.

Unit costs count only after the owner confirms. Item names on distributor bills differ from what the
owner says ("SUNFLWR OIL 1LTR POUCH" is "oil"): a guess is asked once ("Is this Oil?"), and the answer
is learned as an alias when the bill is confirmed.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .. import audit, timers
from ..adapters.base import AdapterError
from ..adapters.vision import IMAGE_TYPES, VisionNotSetUp, VisionRejected
from ..billing.items import display_name, find_item, item_key, merchant_items, resolve_item
from ..billing.service import NotAllowed, NotFound
from ..ids import new_id
from ..models import Distributor, Item, Merchant, PricePoint, PurchaseBill, PurchaseLine, utcnow
from ..photos import (
    MAX_UPLOAD_BYTES,
    MIN_UPLOAD_BYTES,
    PhotoNotReadable,
    clean_key,
    photo_key,
    sanitize,
    upload_key,
)
from ..timers import TimerInfo
from .reading import (
    TOLERANCE_PAISE,
    BillReading,
    base_quantity,
    check_line,
    contains_name,
    date_of,
    match_key,
    milli_of,
    paise_of,
    unit_of,
)

MAX_PHOTO_BYTES = 8_000_000
READ_ATTEMPTS = 2  # a live read that fails for a passing reason (timeout, 429, 5xx) is tried once more
OPEN_STATES = ("uploading", "reading", "failed", "read")  # not yet confirmed or discarded
ITEM_FLAGS = {"item_ask", "item_new"}  # the owner must say which item this is
Trace = Callable[[str, str, dict[str, Any]], None]


class VisionUnavailable(RuntimeError):
    pass


class UnreadablePhoto(NotAllowed):
    pass


@dataclass(frozen=True)
class ReadOutcome:
    """What the worker's read produced: a reading, or why there is none."""

    reading: BillReading | None = None
    failure: str | None = None  # read_failed | no_lines | photo_missing
    error: str = ""
    read_ms: float = 0.0


@dataclass
class PurchaseLineEdit:
    item_id: str | None = None  # this line is that item
    accept_suggestion: bool = False  # "Is this Oil?" -> yes
    new_item_name: str | None = None  # a new item, named by the owner
    raw_text: str | None = None
    qty_milli: int | None = None
    unit: str | None = None
    rate_paise: int | None = None
    clear_rate: bool = False
    line_total_paise: int | None = None


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def line_json(ln: PurchaseLine) -> dict:
    return {
        "id": ln.id, "position": ln.position, "raw_text": ln.raw_text,
        "item_id": ln.item_id, "item_name": ln.item.name if ln.item else None, "match": ln.match,
        "suggested_item_id": ln.suggested_item_id,
        "suggested_item_name": ln.suggested_item.name if ln.suggested_item else None,
        "name_hint": display_name(match_key(ln.raw_text)),  # "SUNFLWR OIL 1LTR POUCH" -> "Sunflwr oil"
        "qty_milli": ln.qty_milli, "unit": ln.unit, "rate_paise": ln.rate_paise,
        "line_total_paise": ln.line_total_paise, "base_unit": ln.base_unit, "base_qty_milli": ln.base_qty_milli,
        "unit_cost_paise": ln.unit_cost_paise, "check_ok": ln.check_ok, "flags": list(ln.flags or []),
        # qty x rate, for the "10 x ₹96 = ₹960, but the bill says ₹484" message
        "expected_paise": None if ln.qty_milli is None or ln.rate_paise is None
        else (ln.qty_milli * ln.rate_paise + 500) // 1000,
    }


def purchase_json(b: PurchaseBill) -> dict:
    lines = [line_json(ln) for ln in b.lines]
    return {
        "id": b.id, "status": b.status, "distributor_name": b.distributor_name, "bill_no": b.bill_no,
        "bill_date": _iso(b.bill_date), "printed_total_paise": b.printed_total_paise, "total_paise": b.total_paise,
        "read_issues": list(b.read_issues or []), "reader": b.reader, "sample": b.sample,
        "has_photo": b.photo_key is not None, "lines": lines,
        "can_confirm": b.status == "read" and bool(lines) and not any(ln["flags"] for ln in lines),
        "can_retry": b.status == "failed" and b.photo_key is not None,
        "created_at": b.created_at.isoformat(), "confirmed_at": _iso(b.confirmed_at),
    }


def _match(items: list[Item], raw_text: str) -> tuple[Item | None, Item | None, str | None]:
    """(item, suggestion, how). Exact alias or a close spelling -> the item; a known name inside the
    printed text -> a suggestion the owner confirms; otherwise nothing."""
    key = match_key(raw_text)
    if not key:
        return None, None, None
    found, how = find_item(items, key)
    if found is not None:
        return found, None, "alias" if how == "exact" else "fuzzy"
    best, best_len = None, 0
    for it in items:
        for alias in {it.key, *(it.aliases or [])}:
            if contains_name(key, alias) and len(alias) > best_len:
                best, best_len = it, len(alias)
    return None, best, None


class PurchaseService:
    def __init__(self, sessions: sessionmaker[Session], vision, photos, demo_samples: bool,
                 trace: Trace) -> None:
        self.sessions, self.vision, self.photos = sessions, vision, photos
        self.demo_samples = demo_samples
        self.trace = trace
        self.on_confirmed: list[Callable[[Session, Merchant, PurchaseBill], None]] = []  # Phase 4: alert scans

    @property
    def mode(self) -> str:
        if self.vision.name != "offline":
            return "live"
        return "samples" if self.demo_samples else "off"

    def samples(self) -> list[str]:
        return sorted(getattr(self.vision, "samples", {})) if self.mode == "samples" else []

    # ---------------- reading ----------------

    def _bill(self, s: Session, merchant_id: str, bill_id: str, lock: bool = False) -> PurchaseBill:
        q = select(PurchaseBill).where(PurchaseBill.id == bill_id, PurchaseBill.merchant_id == merchant_id)
        if lock:
            q = q.with_for_update()
        b = s.execute(q).scalar_one_or_none()
        if b is None:
            raise NotFound("purchase")
        return b

    def _open(self, s: Session, merchant_id: str, bill_id: str) -> PurchaseBill:
        b = self._bill(s, merchant_id, bill_id, lock=True)
        if b.status != "read":
            raise NotAllowed("This bill is already confirmed or discarded.")
        return b

    def get(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions() as s:
            return purchase_json(self._bill(s, merchant_id, bill_id))

    def list(self, merchant_id: str, limit: int = 20) -> list[dict]:
        with self.sessions() as s:
            rows = s.execute(select(PurchaseBill).where(PurchaseBill.merchant_id == merchant_id,
                                                        PurchaseBill.status != "discarded")
                             .order_by(PurchaseBill.created_at.desc()).limit(limit)).scalars()
            return [purchase_json(b) for b in rows]

    def photo(self, merchant_id: str, bill_id: str) -> tuple[bytes, str] | None:
        with self.sessions() as s:
            b = self._bill(s, merchant_id, bill_id)
            key, ctype = b.photo_key, b.photo_type
        if not key:
            return None
        data = self.photos.get(key)
        return (data, ctype or "image/jpeg") if data else None

    # ---------------- photo -> draft ----------------

    def create_from_photo(self, merchant_id: str, image: bytes, content_type: str,
                          sample: str | None = None) -> dict:
        ctype = content_type.split(";")[0].strip().lower()
        if not sample:  # real photos go straight to storage (start_upload); only named samples come this way
            raise UnreadablePhoto("Upload the photo with POST /v1/purchases/uploads.")
        t0 = time.perf_counter()
        try:
            reading = self.vision.read_bill(image, ctype, sample)
        except VisionNotSetUp as e:
            raise VisionUnavailable(str(e)) from e
        except AdapterError:
            self.trace(merchant_id, "vision.failed", {"provider": self.vision.name, "photo_bytes": len(image)})
            raise
        read_ms = round((time.perf_counter() - t0) * 1000, 1)
        if not reading.lines:
            raise UnreadablePhoto("No items could be read. Take the photo again, flat and in good light.")
        bill_id = new_id("pur")
        key = None
        if image and not sample:
            key = photo_key(merchant_id, bill_id, ctype)
            self.photos.put(key, image)
        with self.sessions.begin() as s:
            m = s.get(Merchant, merchant_id)
            if m is None:
                raise NotFound("merchant")
            b = PurchaseBill(id=bill_id, merchant_id=m.id, photo_key=key, photo_type=ctype if key else None,
                             sample=reading.sample, reader=reading.provider)
            s.add(b)
            self._fill(s, b, reading)
            out = purchase_json(b)
        self._trace_read(merchant_id, out, reading, read_ms, len(image))
        out["timings"] = {"read_ms": read_ms}
        return out

    def start_upload(self, merchant_id: str, content_type: str, size: int) -> dict:
        """A bill waiting for its photo, and a presigned PUT for it (5 minutes, this type, this size, this shop's
        key). The photo goes straight to storage."""
        ctype = content_type.split(";")[0].strip().lower()
        if ctype not in IMAGE_TYPES:
            raise UnreadablePhoto("Please send a photo (JPEG, PNG, WebP or HEIC).")
        if size > MAX_UPLOAD_BYTES:
            raise UnreadablePhoto("That photo is too large. Please try again.")
        if size < MIN_UPLOAD_BYTES:
            raise UnreadablePhoto("That photo is empty. Please take it again.")
        if self.vision.name == "offline":
            raise VisionUnavailable("offline vision reads only the sample bills")
        bill_id = new_id("pur")
        key = upload_key(merchant_id, bill_id, ctype)
        upload = self.photos.presign_put(key, ctype, size)
        with self.sessions.begin() as s:
            m = s.get(Merchant, merchant_id)
            if m is None:
                raise NotFound("merchant")
            b = PurchaseBill(id=bill_id, merchant_id=m.id, photo_key=key, photo_type=ctype, status="uploading",
                             reader=self.vision.name, read_issues=[])
            s.add(b)
            s.flush()
            audit.record(s, m.id, "owner", "purchase_upload_started", b.id, photo_type=ctype, photo_bytes=size)
            out = purchase_json(b)
        return {"purchase": out, "upload": upload}

    def finish_upload(self, merchant_id: str, bill_id: str) -> dict:
        """The phone says the photo is up: check it is there and not too big, then the worker reads it."""
        with self.sessions.begin() as s:
            b = self._bill(s, merchant_id, bill_id, lock=True)
            if b.status != "uploading":
                raise NotAllowed("This photo was already sent.")
            size = self.photos.size(b.photo_key) if b.photo_key else None
            if size is None:
                raise NotAllowed("The photo hasn't arrived. Please try again.")
            if size > MAX_UPLOAD_BYTES:
                self.photos.delete(b.photo_key)
                raise UnreadablePhoto("That photo is too large. Please try again.")
            b.status = "reading"
            timers.schedule(s, "purchase_read", utcnow(), merchant_id, subject=bill_id)
            audit.record(s, merchant_id, "owner", "purchase_photo", b.id, photo_bytes=size, reader=self.vision.name)
            out = purchase_json(b)
        self.trace(merchant_id, "purchase.queued", {"provider": self.vision.name, "photo_bytes": size})
        return out

    def retry(self, merchant_id: str, bill_id: str) -> dict:
        """Read a bill again after a failed read."""
        with self.sessions.begin() as s:
            b = self._bill(s, merchant_id, bill_id, lock=True)
            if b.status != "failed" or not b.photo_key:
                raise NotAllowed("This bill can't be read again.")
            b.status, b.read_issues = "reading", []
            timers.schedule(s, "purchase_read", utcnow(), merchant_id, subject=bill_id)
            audit.record(s, merchant_id, "owner", "purchase_read_retry", b.id)
            return purchase_json(b)

    # ---------------- the worker's read (timer "purchase_read") ----------------

    def read_queued(self, t: TimerInfo) -> ReadOutcome | None:
        """Outside any transaction: the photo -> the reader (submit, poll, fetch). A passing failure raises,
        so the timer tries again with backoff; the last attempt, or a refusal, returns the failure instead."""
        with self.sessions() as s:
            b = s.get(PurchaseBill, t.subject)
            if b is None or b.status != "reading":
                return None
            key, ctype = b.photo_key, b.photo_type or "image/jpeg"
        image = self.photos.get(key) if key else None
        if not image:
            return ReadOutcome(failure="photo_missing")
        if key.split("/")[-1].split(".")[0].endswith("_orig"):  # straight from the phone: never sent as is
            try:
                image = sanitize(image)
            except PhotoNotReadable as e:
                self.photos.delete(key)
                return ReadOutcome(failure="not_a_photo", error=str(e))
            clean = clean_key(t.merchant_id, t.subject)
            self.photos.put(clean, image)
            with self.sessions.begin() as s:
                b = s.get(PurchaseBill, t.subject)
                b.photo_key, b.photo_type = clean, "image/jpeg"
            self.photos.delete(key)
            ctype = "image/jpeg"
        t0 = time.perf_counter()
        try:
            reading = self.vision.read_bill(image, ctype)
        except (VisionNotSetUp, VisionRejected) as e:
            return ReadOutcome(failure="read_failed", error=str(e))
        except AdapterError as e:
            if t.attempts + 1 < READ_ATTEMPTS:
                raise
            return ReadOutcome(failure="read_failed", error=str(e))
        read_ms = round((time.perf_counter() - t0) * 1000, 1)
        if not reading.lines:
            return ReadOutcome(failure="no_lines", read_ms=read_ms)
        return ReadOutcome(reading=reading, read_ms=read_ms)

    def on_read(self, s: Session, timer, outcome: ReadOutcome | None) -> None:
        if outcome is None:
            return
        b = s.execute(select(PurchaseBill).where(PurchaseBill.id == timer.subject).with_for_update()).scalar_one()
        if b.status != "reading":  # discarded while it was being read
            return
        if outcome.reading is None:
            b.status, b.read_issues = "failed", [outcome.failure]
            audit.record(s, b.merchant_id, "agent", "purchase_read_failed", b.id, reader=self.vision.name,
                         reason=outcome.failure, error=outcome.error[:300])
            self.trace(b.merchant_id, "vision.failed", {"provider": self.vision.name, "bill": b.id,
                                                        "reason": outcome.failure, "error": outcome.error[:300]})
            return
        b.status, b.reader = "read", outcome.reading.provider
        self._fill(s, b, outcome.reading)
        out = purchase_json(b)
        self._trace_read(b.merchant_id, out, outcome.reading, outcome.read_ms, None)

    # ---------------- reading -> lines and checks ----------------

    def _fill(self, s: Session, b: PurchaseBill, reading: BillReading) -> None:
        """Header and lines as read; every figure is then parsed and checked in code."""
        # C5: the owner is told when the backup reader (Gemini, outside India; laabam-demo only) read the photo
        b.read_issues = ["read_by_fallback"] if any(n.startswith("fallback after") for n in reading.notes) else []
        self._fill_header(s, b, reading)
        items = merchant_items(s, b.merchant_id)
        for pos, rl in enumerate(reading.lines):
            ln = PurchaseLine(id=new_id("pln"), position=pos, raw_text=rl.raw_text, flags=[])
            ln.qty_milli, ln.unit = milli_of(rl.qty), unit_of(rl.unit) or "pc"
            ln.rate_paise, ln.line_total_paise = paise_of(rl.rate), paise_of(rl.amount)
            item, suggestion, how = _match(items, rl.raw_text)
            ln.item_id, ln.match = (item.id, how) if item else (None, None)
            ln.suggested_item_id = suggestion.id if suggestion else None
            self._check(ln)
            b.lines.append(ln)
        self._totals(s, b)
        s.flush()
        audit.record(s, b.merchant_id, "agent", "purchase_read", b.id, reader=reading.provider, sample=reading.sample,
                     request_id=reading.request_id, notes=list(reading.notes),
                     distributor=b.distributor_name, bill_no=b.bill_no, lines=[
                         {**{k: v for k, v in line_json(ln).items() if k in
                             ("qty_milli", "unit", "rate_paise", "line_total_paise", "flags")},
                          "raw_text": audit.digest(s, b.merchant_id, ln.raw_text)}
                         for ln in b.lines],
                     printed_total_paise=b.printed_total_paise, read_issues=b.read_issues)

    def _trace_read(self, merchant_id: str, out: dict, reading: BillReading, read_ms: float,
                    photo_bytes: int | None) -> None:
        self.trace(merchant_id, "purchase.read", {
            "provider": reading.provider, "sample": reading.sample, "read_ms": read_ms, "photo_bytes": photo_bytes,
            "notes": list(reading.notes), "distributor": out["distributor_name"], "lines": [
                {"raw_text": ln["raw_text"], "item": ln["item_name"], "suggested": ln["suggested_item_name"],
                 "qty_milli": ln["qty_milli"], "unit": ln["unit"], "rate_paise": ln["rate_paise"],
                 "line_total_paise": ln["line_total_paise"], "check_ok": ln["check_ok"], "flags": ln["flags"],
                 "unit_cost_paise": ln["unit_cost_paise"], "base_unit": ln["base_unit"]} for ln in out["lines"]],
            "printed_total_paise": out["printed_total_paise"], "total_paise": out["total_paise"],
            "read_issues": out["read_issues"],
        })

    def _fill_header(self, s: Session, b: PurchaseBill, reading: BillReading) -> None:
        b.distributor_name = reading.distributor.strip()
        b.bill_no = reading.bill_no.strip()
        b.bill_date = date_of(reading.bill_date)
        b.printed_total_paise = paise_of(reading.total)

    # ---------------- checks (code) ----------------

    def _check(self, ln: PurchaseLine) -> None:
        ln.base_unit, ln.base_qty_milli = base_quantity(ln.raw_text, ln.qty_milli, ln.unit)
        c = check_line(ln.qty_milli, ln.rate_paise, ln.line_total_paise, ln.base_qty_milli)
        flags = c.flags
        if ln.item_id is None:
            flags.append("item_ask" if ln.suggested_item_id else "item_new")
        ln.check_ok, ln.unit_cost_paise, ln.flags = c.check_ok, c.unit_cost_paise, flags

    def _totals(self, s: Session, b: PurchaseBill) -> None:
        b.total_paise = sum(ln.line_total_paise or 0 for ln in b.lines)
        issues = [i for i in (b.read_issues or []) if i == "read_by_fallback"]  # stays through edits
        if b.printed_total_paise is not None and abs(b.printed_total_paise - b.total_paise) > TOLERANCE_PAISE:
            issues.append("total_mismatch")
        if b.bill_no and b.distributor_name:
            dup = s.execute(select(PurchaseBill.id).where(
                PurchaseBill.merchant_id == b.merchant_id, PurchaseBill.id != b.id,
                PurchaseBill.status == "confirmed", PurchaseBill.bill_no == b.bill_no,
                PurchaseBill.distributor_name == b.distributor_name)).first()
            if dup:
                issues.append("maybe_duplicate")
        b.read_issues = issues

    # ---------------- owner fixes ----------------

    def patch(self, merchant_id: str, bill_id: str, distributor_name: str | None = None, bill_no: str | None = None,
              bill_date: date | None = None, clear_date: bool = False) -> dict:
        with self.sessions.begin() as s:
            b = self._open(s, merchant_id, bill_id)
            if distributor_name is not None:
                b.distributor_name = " ".join(distributor_name.split())[:120]
            if bill_no is not None:
                b.bill_no = bill_no.strip()[:64]
            if bill_date is not None or clear_date:
                b.bill_date = bill_date
            self._totals(s, b)
            audit.record(s, merchant_id, "owner", "purchase_edited", b.id, distributor=b.distributor_name,
                         bill_no=b.bill_no, bill_date=_iso(b.bill_date))
            return purchase_json(b)

    def _item_for_owner(self, s: Session, merchant_id: str, item_id: str) -> Item:
        it = s.get(Item, item_id)
        if it is None or it.merchant_id != merchant_id:
            raise NotFound("item")
        return it

    def edit_line(self, merchant_id: str, bill_id: str, line_id: str, e: PurchaseLineEdit) -> dict:
        with self.sessions.begin() as s:
            b = self._open(s, merchant_id, bill_id)
            ln = next((x for x in b.lines if x.id == line_id), None)
            if ln is None:
                raise NotFound("line")
            if e.raw_text is not None and e.raw_text.strip():
                ln.raw_text = e.raw_text.strip()[:200]
            if e.qty_milli is not None:
                ln.qty_milli = e.qty_milli
            if e.unit is not None:
                ln.unit = e.unit if e.unit in ("kg", "L", "pc", "pack") else unit_of(e.unit)
            if e.clear_rate:
                ln.rate_paise = None
            elif e.rate_paise is not None:
                ln.rate_paise = e.rate_paise
            if e.line_total_paise is not None:
                ln.line_total_paise = e.line_total_paise
            if e.accept_suggestion:
                if ln.suggested_item_id is None:
                    raise NotAllowed("There is no suggestion for this line.")
                ln.item_id, ln.match = ln.suggested_item_id, "owner"
            elif e.item_id:
                ln.item_id, ln.match = self._item_for_owner(s, merchant_id, e.item_id).id, "owner"
            elif e.new_item_name is not None:
                name = " ".join(e.new_item_name.split())[:120]
                if not item_key(name):
                    raise NotAllowed("Please give the item a name.")
                existing, _ = find_item(merchant_items(s, merchant_id), name)
                item = existing or resolve_item(s, merchant_id, name)
                ln.item_id, ln.match = item.id, "owner" if existing else "new"
            s.flush()
            s.refresh(ln, ["item"])
            self._check(ln)
            self._totals(s, b)
            audit.record(s, merchant_id, "owner", "purchase_line_edited", b.id, line=ln.id, **{
                k: v for k, v in line_json(ln).items() if k in ("item_id", "qty_milli", "unit", "rate_paise",
                                                                   "line_total_paise", "flags")})
            return purchase_json(b)

    def delete_line(self, merchant_id: str, bill_id: str, line_id: str) -> dict:
        with self.sessions.begin() as s:
            b = self._open(s, merchant_id, bill_id)
            ln = next((x for x in b.lines if x.id == line_id), None)
            if ln is None:
                raise NotFound("line")
            b.lines.remove(ln)
            self._totals(s, b)
            audit.record(s, merchant_id, "owner", "purchase_line_deleted", b.id, line=line_id,
                         raw_text=audit.digest(s, merchant_id, ln.raw_text))
            return purchase_json(b)

    def add_line(self, merchant_id: str, bill_id: str, e: PurchaseLineEdit) -> dict:
        """A row the reader missed: its text, then the same fixes as any line."""
        text = (e.raw_text or e.new_item_name or "").strip()
        if not text:
            raise NotAllowed("Please give the item a name.")
        with self.sessions.begin() as s:
            b = self._open(s, merchant_id, bill_id)
            if len(b.lines) >= 60:
                raise NotAllowed("This bill has too many lines.")
            ln = PurchaseLine(id=new_id("pln"), raw_text=text[:200], flags=[], unit="pc",
                              position=max((x.position for x in b.lines), default=-1) + 1)
            item, suggestion, how = _match(merchant_items(s, merchant_id), text)
            ln.item_id, ln.match = (item.id, how) if item else (None, None)
            ln.suggested_item_id = suggestion.id if suggestion else None
            b.lines.append(ln)
            s.flush()
            line_id = ln.id
        return self.edit_line(merchant_id, bill_id, line_id, PurchaseLineEdit(**{**e.__dict__, "raw_text": None}))

    # ---------------- confirm / discard ----------------

    def confirm(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions.begin() as s:
            b = self._open(s, merchant_id, bill_id)
            m = s.get(Merchant, merchant_id)
            if not b.lines:
                raise NotAllowed("This bill has no lines.")
            if any(ln.flags for ln in b.lines):
                raise NotAllowed("Some lines still need a look.")
            b.distributor_id = self._distributor(s, merchant_id, b.distributor_name)
            learned = []
            for ln in b.lines:
                item = ln.item
                key = match_key(ln.raw_text)
                if key and key not in (item.aliases or []) and key != item.key:
                    item.aliases = [*(item.aliases or []), key]  # new list: JSON columns track assignment
                    learned.append({"item": item.name, "alias": key})
                if item.unit is None and ln.base_unit in ("kg", "L", "pc"):
                    item.unit = ln.base_unit
            b.status, b.confirmed_at = "confirmed", utcnow()
            audit.record(s, merchant_id, "owner", "purchase_confirmed", b.id, distributor=b.distributor_name,
                         bill_no=b.bill_no, bill_date=_iso(b.bill_date), total_paise=b.total_paise,
                         aliases_learned=learned, lines=[
                             {"item_id": ln.item_id, "unit_cost_paise": ln.unit_cost_paise, "base_unit": ln.base_unit,
                              "base_qty_milli": ln.base_qty_milli, "line_total_paise": ln.line_total_paise}
                             for ln in b.lines])
            for hook in self.on_confirmed:
                hook(s, m, b)
            out = purchase_json(b)
        self.trace(merchant_id, "purchase.confirmed", {"distributor": out["distributor_name"],
                                                       "total_paise": out["total_paise"], "aliases_learned": learned})
        return out

    def _distributor(self, s: Session, merchant_id: str, name: str) -> str | None:
        key = item_key(name)
        if not key:
            return None
        d = s.execute(select(Distributor).where(Distributor.merchant_id == merchant_id,
                                                Distributor.key == key)).scalar_one_or_none()
        if d is None:
            d = Distributor(id=new_id("dist"), merchant_id=merchant_id, name=display_name(name), key=key)
            try:
                with s.begin_nested():
                    s.add(d)
            except IntegrityError:
                d = s.execute(select(Distributor).where(Distributor.merchant_id == merchant_id,
                                                        Distributor.key == key)).scalar_one()
        return d.id

    def discard(self, merchant_id: str, bill_id: str) -> dict:
        with self.sessions.begin() as s:
            b = self._bill(s, merchant_id, bill_id, lock=True)
            if b.status not in OPEN_STATES:
                raise NotAllowed("This bill is already confirmed or discarded.")
            b.status = "discarded"
            audit.record(s, merchant_id, "owner", "purchase_discarded", b.id)
            return purchase_json(b)

    # ---------------- items ----------------

    def items(self, merchant_id: str) -> list[dict]:
        """Every item with its latest selling price (spoken) and latest confirmed cost (bills)."""
        with self.sessions() as s:
            items = merchant_items(s, merchant_id)
            out = []
            for it in items:
                price = s.execute(select(PricePoint).where(PricePoint.item_id == it.id)
                                  .order_by(PricePoint.seen_at.desc()).limit(1)).scalar_one_or_none()
                cost = s.execute(select(PurchaseLine, PurchaseBill).join(PurchaseBill).where(
                    PurchaseLine.item_id == it.id, PurchaseBill.status == "confirmed",
                    PurchaseLine.unit_cost_paise.is_not(None))
                    .order_by(PurchaseBill.confirmed_at.desc()).limit(1)).first()
                out.append({
                    "id": it.id, "name": it.name, "unit": it.unit, "aliases": list(it.aliases or []),
                    "price": None if price is None else {
                        "price_paise": price.price_paise, "per_qty_milli": price.per_qty_milli,
                        "per_unit": price.per_unit, "seen_at": price.seen_at.isoformat()},
                    "cost": None if cost is None else {
                        "unit_cost_paise": cost[0].unit_cost_paise, "per_unit": cost[0].base_unit,
                        "purchase_id": cost[1].id, "line_id": cost[0].id, "distributor": cost[1].distributor_name,
                        "bill_date": _iso(cost[1].bill_date)},
                })
            return out
