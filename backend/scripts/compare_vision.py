"""Read the sample bill photos with each live reader and score them against what the photos actually say.

    cd backend && LAABAM_SARVAM_API_KEY=... python -m scripts.compare_vision
    python -m scripts.compare_vision --providers sarvam --bills handwritten tamil --json out.json

Calls live services (costs a few pages of credit); tests never run it. Each reading goes through the same code
the app uses (purchases/reading.py): numbers parsed as printed, qty x rate vs amount, lines vs total. A line is
"right" when the text, qty, rate and amount all match the photo; otherwise the report says which field was wrong
and whether the code checks flagged it for the owner (so it couldn't have been saved unnoticed).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from laabam.adapters.vision import GeminiVision, SarvamVision
from laabam.config import Settings
from laabam.purchases.reading import (
    TOLERANCE_PAISE,
    base_quantity,
    check_line,
    date_of,
    milli_of,
    paise_of,
    unit_of,
)

BILLS = Path(__file__).resolve().parents[1] / "tests" / "data" / "bills"


def readers(cfg: Settings, names: list[str]):
    out = {}
    for name in names:
        if name == "sarvam" and cfg.sarvam_api_key:
            out[name] = SarvamVision(cfg.sarvam_api_key, cfg.sarvam_base_url, cfg.sarvam_vision_language,
                                     timeout=cfg.vision_timeout_seconds, poll_every=cfg.vision_poll_seconds)
        elif name == "gemini" and cfg.gemini_api_key:
            out[name] = GeminiVision(cfg.gemini_api_key, cfg.gemini_model, cfg.gemini_base_url, timeout=60)
        else:
            print(f"skip {name}: no API key (LAABAM_{name.upper()}_API_KEY)", file=sys.stderr)
    return out


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text or "").lower().split())


def score_line(truth: dict, ln) -> dict:
    qty, rate, amount = milli_of(ln.qty), paise_of(ln.rate), paise_of(ln.amount)
    base_unit, base_qty = base_quantity(ln.raw_text, qty, unit_of(ln.unit) or "pc")
    check = check_line(qty, rate, amount, base_qty)
    wrong = []
    # "Milk 500ml" with unit "pkt" is a right reading of "Milk 500ml pkt": the unit moved to its column
    text_ok = max(SequenceMatcher(None, norm(truth["text"]), norm(t)).ratio()
                  for t in (ln.raw_text, f"{ln.raw_text} {ln.unit or ''}"))
    if text_ok < 0.85:
        wrong.append("text")
    if qty != truth["qty"] * 1000:
        wrong.append("qty")
    if rate != truth["rate"] * 100:
        wrong.append("rate")
    if amount != truth["amount"] * 100:
        wrong.append("amount")
    return {"truth": truth["text"], "read": {"text": ln.raw_text, "qty": ln.qty, "unit": ln.unit, "rate": ln.rate,
                                             "amount": ln.amount},
            "wrong": wrong, "flags": check.flags, "caught": bool(check.flags) or not wrong}


def score(truth: dict, reading) -> dict:
    lines, unused = [], list(reading.lines)
    for t in truth["lines"]:  # pair each true line with the closest read line
        best = max(unused, key=lambda ln: SequenceMatcher(None, norm(t["text"]), norm(ln.raw_text)).ratio(),
                   default=None)
        if best is None or SequenceMatcher(None, norm(t["text"]), norm(best.raw_text)).ratio() < 0.5:
            lines.append({"truth": t["text"], "read": None, "wrong": ["missing"], "flags": [], "caught": False})
            continue
        unused.remove(best)
        lines.append(score_line(t, best))
    total = paise_of(reading.total)
    line_sum = sum(paise_of(ln.amount) or 0 for ln in reading.lines)
    header = {
        "distributor": norm(reading.distributor) == norm(truth["distributor"]),
        "bill_no": norm(reading.bill_no) == norm(str(truth["bill_no"])),
        "bill_date": date_of(reading.bill_date) == truth["bill_date"],
        "total": total == truth["total"] * 100,
    }
    return {"header": header, "read_header": {"distributor": reading.distributor, "bill_no": reading.bill_no,
                                              "bill_date": reading.bill_date, "total": reading.total},
            "lines": lines, "extra_lines": [ln.raw_text for ln in unused],
            "total_mismatch_flagged": total is not None and abs(total - line_sum) > TOLERANCE_PAISE,
            "notes": list(reading.notes)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", default=["sarvam"])  # gemini is off (see config); pass it to compare
    ap.add_argument("--bills", nargs="+", default=None)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    truth = yaml.safe_load((BILLS / "truth.yaml").read_text(encoding="utf-8"))
    bills = args.bills or list(truth)
    results: dict = {}
    for name, reader in readers(Settings(), args.providers).items():
        for bill in bills:
            image = (BILLS / f"{bill}.jpg").read_bytes()
            t0 = time.perf_counter()
            try:
                reading = reader.read_bill(image, "image/jpeg")
            except Exception as e:  # noqa: BLE001 - report it and go on
                results.setdefault(name, {})[bill] = {"error": f"{type(e).__name__}: {e}"}
                print(f"{name:7} {bill:12} ERROR {e}")
                continue
            r = score(truth[bill], reading)
            r["seconds"] = round(time.perf_counter() - t0, 1)
            results.setdefault(name, {})[bill] = r
            right = sum(not ln["wrong"] for ln in r["lines"])
            print(f"{name:7} {bill:12} {right}/{len(r['lines'])} lines right, header "
                  f"{sum(r['header'].values())}/4, {r['seconds']}s")
            for ln in r["lines"]:
                if ln["wrong"]:
                    how = "flagged by code checks" if ln["caught"] else "NOT flagged: owner must spot it"
                    print(f"          - {ln['truth']}: wrong {', '.join(ln['wrong'])}; read {ln['read']} ({how})")
            for k, ok in r["header"].items():
                if not ok:
                    print(f"          - header {k}: read {r['read_header'][k]!r}")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
