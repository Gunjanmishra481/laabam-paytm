"""The evening summary (BUILD_PLAN §5.7). At the shop's closing time (default 21:00 IST) the worker builds the day's
figures in code (sales, profit, the item that earned most, what to watch from the open alerts), fills a template in
the shop's language (a model may phrase it; any digit means the template), speaks it on the Soundbox, and saves it.

Timer "evening_summary" per shop: it reschedules itself for the next closing time; changing the closing time
moves it. The owner can also ask for today's summary so far (POST /v1/summaries).
"""
from __future__ import annotations

from datetime import date, datetime, time
from fractions import Fraction

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from . import alerts, audit, tenancy, timers
from .ask import TEMPLATES as ASK_WORDS
from .ask import WEEKDAYS, Phraser, segments
from .clock import IST, ist_day, next_ist_time
from .ids import new_id
from .lang import DEFAULT_LANGUAGE
from .llm import fill_guarded
from .models import Alert, Item, Merchant, Summary, Timer, utcnow
from .money import rupees
from .profit import bp, profit_report, round_half_up

WORDS: dict[str, dict[str, str]] = {
    "en": {
        "profit": "Today {SALES} came in and profit was {ABOUT}{PROFIT}.",
        "no_profit": "Today {SALES} came in. Profit isn’t known yet: add distributor bills.",
        "nothing": "No paid bills today.",
        "top": "{TOP} earned the most.",
        "w_price_rise": "{ITEM} now costs {RISE} more {PER}.",
        "w_running_out": "{ITEM} will run out by {DAY}, so reorder tomorrow.",
        "w_slow": "{ITEM} hasn’t sold in {N} days.",
        "w_low_margin": "{ITEM} earns only {PCT}.",
    },
    "ta": {
        "profit": "இன்று {SALES} வந்தது, லாபம் {ABOUT}{PROFIT}.",
        "no_profit": "இன்று {SALES} வந்தது. லாபம் இன்னும் தெரியாது: விநியோகஸ்தர் பில்களைச் சேர்க்கவும்.",
        "nothing": "இன்று பணம் வந்த பில் இல்லை.",
        "top": "{TOP} அதிகம் ஈட்டியது.",
        "w_price_rise": "{ITEM} இப்போது {PER} {RISE} கூடுதல்.",
        "w_running_out": "{ITEM} {DAY} தீர்ந்துவிடும், அதனால் நாளை ஆர்டர் செய்யுங்கள்.",
        "w_slow": "{ITEM} {N} நாட்களாக விற்கவில்லை.",
        "w_low_margin": "{ITEM} லாபம் {PCT} மட்டுமே.",
    },
    "hi": {
        "profit": "आज {SALES} आए और मुनाफ़ा {ABOUT}{PROFIT} रहा।",
        "no_profit": "आज {SALES} आए। मुनाफ़ा अभी पता नहीं: डिस्ट्रीब्यूटर बिल जोड़ें।",
        "nothing": "आज कोई भुगतान वाला बिल नहीं।",
        "top": "सबसे ज़्यादा {TOP} ने कमाया।",
        "w_price_rise": "{ITEM} अब {PER} {RISE} महँगा है।",
        "w_running_out": "{ITEM} {DAY} तक खत्म हो जाएगा, इसलिए कल मँगाइए।",
        "w_slow": "{ITEM} {N} दिन से नहीं बिका।",
        "w_low_margin": "{ITEM} पर सिर्फ़ {PCT} मुनाफ़ा।",
    },
}
WATCH_MAX = 2  # sentences about open alerts


def word(lang: str, key: str) -> str:
    return WORDS.get(lang, {}).get(key) or WORDS[DEFAULT_LANGUAGE][key]


def pct(bp_: int) -> str:
    """370 -> '3.7%', 545 -> '5.5%' (half up, like the screens)."""
    tenths = round_half_up(Fraction(bp_, 10))
    return f"{tenths // 10}.{tenths % 10}%"


def _watch(a: Alert, items: dict[str, Item], lang: str, today: date) -> tuple[str, dict[str, str]]:
    f = a.figures
    name = items[a.item_id].name if a.item_id in items else ""
    per = ASK_WORDS.get(lang, ASK_WORDS["en"]).get(f"per_{f.get('unit_label', 'pack')}", "")
    if a.kind == "price_rise":
        return "w_price_rise", {"ITEM": name, "RISE": rupees(f["rise_paise"]), "PER": per}
    if a.kind == "running_out":
        out = date.fromisoformat(f["out_day"])
        gap = (out - today).days
        words = ASK_WORDS.get(lang, ASK_WORDS["en"])
        day = words["today"] if gap <= 0 else words["tomorrow"] if gap == 1 else \
            WEEKDAYS.get(lang, WEEKDAYS["en"])[out.weekday()]
        return "w_running_out", {"ITEM": name, "DAY": day}
    if a.kind == "slow":
        return "w_slow", {"ITEM": name, "N": str(f["days_quiet"])}
    return "w_low_margin", {"ITEM": name, "PCT": pct(f["margin_bp"])}


def build(s: Session, m: Merchant, today: date | None = None, phraser: Phraser | None = None) -> Summary:
    """Today's figures and words. Upserts the day's summary."""
    today = today or ist_day(utcnow())
    lang = m.language if m.language in WORDS else DEFAULT_LANGUAGE
    rep = profit_report(s, m.id, "today", today)
    top = rep["items"][0] if rep["items"] else None
    open_alerts = sorted(s.execute(select(Alert).where(Alert.merchant_id == m.id, Alert.status == "open")).scalars(),
                         key=lambda a: (alerts.ORDER.get(a.kind, 9), a.created_at))
    items = {it.id: it for it in s.execute(select(Item).where(Item.merchant_id == m.id)).scalars()}
    profit = round_half_up(Fraction(rep["profit_paise"], 100)) * 100  # spoken to the rupee
    parts: list[tuple[str, dict[str, str]]] = []
    if rep["sales_paise"] == 0:
        parts.append(("nothing", {}))
    elif rep["known_sales_paise"] > 0:
        about = ASK_WORDS.get(lang, ASK_WORDS["en"])["about"] if rep["estimated"] else ""
        parts.append(("profit", {"SALES": rupees(rep["sales_paise"]), "PROFIT": rupees(profit), "ABOUT": about}))
    else:
        parts.append(("no_profit", {"SALES": rupees(rep["sales_paise"])}))
    if top and top["profit_paise"] > 0:
        parts.append(("top", {"TOP": top["name"]}))
    watch = [a for a in open_alerts if a.item_id in items][:WATCH_MAX]
    parts += [_watch(a, items, lang, today) for a in watch]

    # one template for the whole summary, with each sentence's placeholders made unique
    template, values = [], {}
    for i, (key, vals) in enumerate(parts):
        t = word(lang, key)
        for k, v in vals.items():
            t = t.replace("{" + k + "}", "{" + f"{k}_{i}" + "}")
            values[f"{k}_{i}"] = v
        template.append(t)
    template_text = " ".join(template)
    model = phraser("summary", lang, template_text, set(values)) if phraser else None
    text, how = fill_guarded(model, template_text, values)

    figures = {
        "day": today.isoformat(), "sales_paise": rep["sales_paise"], "profit_paise": profit,
        "profit_known": rep["known_sales_paise"] > 0, "estimated": rep["estimated"],
        "top": None if not top else {"item_id": top["item_id"], "name": top["name"],
                                     "profit_paise": top["profit_paise"]},
        "watch": [{"alert_id": a.id, "kind": a.kind, "item_id": a.item_id, "item_name": items[a.item_id].name}
                  for a in watch],
        "coverage_bp": rep["coverage_bp"], "margin_bp": bp(rep["profit_paise"], rep["known_sales_paise"]),
        "segments": segments(model if how == "model" else template_text, values),
    }
    row = s.execute(select(Summary).where(Summary.merchant_id == m.id, Summary.day == today)).scalar_one_or_none()
    if row is None:
        row = Summary(id=new_id("sum"), merchant_id=m.id, day=today)
        s.add(row)
    row.language, row.figures, row.text, row.phrased_by, row.created_at = lang, figures, text, how, utcnow()
    s.flush()
    audit.record(s, m.id, "agent", "summary_built", row.id, day=today.isoformat(), phrased_by=how,
                 sales_paise=figures["sales_paise"], profit_paise=profit)
    return row


def summary_json(row: Summary) -> dict:
    return {"id": row.id, "day": row.day.isoformat(), "language": row.language, "text": row.text,
            "phrased_by": row.phrased_by, "figures": row.figures, "created_at": row.created_at.isoformat(),
            "played_at": row.played_at.isoformat() if row.played_at else None}


def latest(s: Session, merchant_id: str) -> Summary | None:
    return s.execute(select(Summary).where(Summary.merchant_id == merchant_id)
                     .order_by(Summary.day.desc()).limit(1)).scalar_one_or_none()


class Summaries:
    """Timer kind "evening_summary", subject = merchant id."""

    def __init__(self, announce, trace, phraser: Phraser | None = None) -> None:
        self.announce, self.trace, self.phraser = announce, trace, phraser

    @staticmethod
    def closing(m: Merchant) -> time:
        h, mm = (int(x) for x in (m.closing_time or "21:00").split(":"))
        return time(h, mm)

    def schedule(self, s: Session, m: Merchant, after: datetime | None = None) -> None:
        """The next summary at the shop's closing time (any pending one is moved)."""
        due = next_ist_time(after or utcnow(), self.closing(m))
        moved = s.execute(update(Timer).where(Timer.kind == "evening_summary", Timer.subject == m.id,
                                              Timer.status == "pending").values(due_at=due)).rowcount
        if not moved:
            timers.schedule(s, "evening_summary", due, m.id, subject=m.id)

    def ensure_all(self, s: Session) -> None:
        pending = set(s.execute(select(Timer.subject).where(Timer.kind == "evening_summary",
                                                            Timer.status.in_(("pending", "running")))).scalars())
        for mid in tenancy.shop_ids(s):
            if mid not in pending:
                tenancy.set_shop(s, mid)
                self.schedule(s, s.get(Merchant, mid))

    def on_timer(self, s: Session, t: Timer) -> None:
        m = s.get(Merchant, t.subject)
        if m is None:
            return
        row = self.play(s, m)
        self.schedule(s, m, after=utcnow().astimezone(IST))
        self.trace(m.id, "summary.played", {"day": row.day.isoformat(), "text": row.text, "phrased_by": row.phrased_by})

    def play(self, s: Session, m: Merchant) -> Summary:
        """Build today's summary and speak it on the Soundbox (after the transaction commits)."""
        row = build(s, m, phraser=self.phraser)
        row.played_at = utcnow()
        self.announce(s, m, row.text)
        return row
