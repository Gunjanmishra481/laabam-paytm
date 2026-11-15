"""Questions to Laabam (BUILD_PLAN §5.7): "why is profit down?", "what should I reorder?", "best item this month?",
and "write to the distributor about it".

A question is matched to one tool by simple rules (en / ta / hi). The tool computes every figure in code; the
answer is a template from the strings table with {PLACEHOLDERS}, filled by code. A model may phrase the template
(`phraser`), but llm.fill_guarded drops any model text with a digit or an unknown placeholder. Every answer lists
its sources (bill lines, voice bills). Drafted messages are for the owner to send; Laabam never sends them.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy.orm import Session

from . import alerts
from .clock import ist_day
from .lang import DEFAULT_LANGUAGE
from .llm import PLACEHOLDER, fill_guarded
from .models import Item, Merchant, utcnow
from .money import rupees
from .profit import CostFact, Window, cheaper_elsewhere, ledger, profit_report, round_half_up, unit_label

INTENTS = ("draft", "reorder", "best", "why_down")
KEYWORDS: dict[str, tuple[str, ...]] = {
    "draft": ("write", "message", "draft", "tell them", "எழுது", "செய்தி", "लिख", "संदेश", "मैसेज"),
    "reorder": ("reorder", "re-order", "restock", "order", "running out", "run out", "buy", "ஆர்டர்", "வாங்க",
                "தீர", "मँगा", "मंगा", "ऑर्डर", "खरीद", "खत्म"),
    "best": ("best", "most", "top", "சிறந்த", "அதிக", "सबसे", "बढ़िया"),
    "why_down": ("why", "down", "less", "fell", "drop", "ஏன்", "குறை", "क्यों", "कम", "घट"),
}

# Answers. Every number is a {PLACEHOLDER} filled by code. Tamil and Hindi: native-speaker review welcome.
TEMPLATES: dict[str, dict[str, str]] = {
    "en": {
        "why_down_rise": "Mostly {ITEM}. It now costs {NEW} {PER}, up from {OLD}, so you made {ABOUT}{LOST} less on it "
                         "this week.",
        "why_down_item": "Mostly {ITEM}: it made {DROP} less than last week.",
        "not_down": "Profit isn’t down this week: about {NOW}, against {BEFORE} the week before.",
        "no_data": "There aren’t enough paid bills yet to compare weeks.",
        "reorder": "Reorder {ITEMS} soon. {FIRST} will run out by {DAY}.",
        "reorder_none": "Nothing is running low right now.",
        "best": "{ITEM} earned the most this month: {ABOUT}{PROFIT} on {SALES} of sales.",
        "best_none": "No item has a known profit yet. Add a distributor bill first.",
        "draft_cheaper": "Namaste. {ITEM} is {NEW} on bill {BILL}, up from {OLD} on {DAY}. Another distributor "
                         "billed {CHEAPER}. Can you do {CHEAPER}?",
        "draft": "Namaste. {ITEM} is {NEW} on bill {BILL}, up from {OLD} on {DAY}. Can you keep it at {OLD}?",
        "draft_none": "There’s no recent price rise to write about.",
        "draft_ready": "Here’s a message to {WHO}. You send it.",
        "unknown": "I can tell you why profit is down, what to reorder, and your best item this month.",
        "per_litre": "a litre", "per_kg": "a kg", "per_pack": "a pack",
        "and": "and", "today": "today", "tomorrow": "tomorrow", "about": "about ",
    },
    "ta": {
        "why_down_rise": "பெரும்பாலும் {ITEM}. இப்போது {PER} {NEW}, முன்பு {OLD}. அதனால் இந்த வாரம் அதில் {ABOUT}{LOST} "
                         "குறைவாகக் கிடைத்தது.",
        "why_down_item": "பெரும்பாலும் {ITEM}: போன வாரத்தை விட {DROP} குறைவு.",
        "not_down": "இந்த வாரம் லாபம் குறையவில்லை: சுமார் {NOW}, போன வாரம் {BEFORE}.",
        "no_data": "வாரங்களை ஒப்பிட இன்னும் போதுமான பில்கள் இல்லை.",
        "reorder": "{ITEMS} விரைவில் மறுபடி ஆர்டர் செய்யுங்கள். {FIRST} {DAY} தீர்ந்துவிடும்.",
        "reorder_none": "இப்போது எதுவும் குறைவாக இல்லை.",
        "best": "இந்த மாதம் {ITEM} அதிகம் ஈட்டியது: {SALES} விற்பனையில் {ABOUT}{PROFIT} லாபம்.",
        "best_none": "எந்தப் பொருளுக்கும் இன்னும் லாபம் தெரியாது. முதலில் விநியோகஸ்தர் பில்லைச் சேர்க்கவும்.",
        "draft_cheaper": "வணக்கம். பில் {BILL} இல் {ITEM} {NEW}, {DAY} அன்று {OLD} ஆக இருந்தது. வேறொரு "
                         "விநியோகஸ்தர் {CHEAPER} என பில் போட்டார். {CHEAPER} க்குத் தர முடியுமா?",
        "draft": "வணக்கம். பில் {BILL} இல் {ITEM} {NEW}, {DAY} அன்று {OLD} ஆக இருந்தது. {OLD} ஆகவே வைக்க முடியுமா?",
        "draft_none": "எழுதுவதற்கு சமீபத்திய விலை ஏற்றம் இல்லை.",
        "draft_ready": "{WHO} க்கு ஒரு செய்தி. நீங்கள் அனுப்புங்கள்.",
        "unknown": "லாபம் ஏன் குறைந்தது, எதை மறு ஆர்டர் செய்வது, இந்த மாதச் சிறந்த பொருள் எது என்று சொல்ல முடியும்.",
        "per_litre": "லிட்டருக்கு", "per_kg": "கிலோவுக்கு", "per_pack": "பாக்கெட்டுக்கு",
        "and": "மற்றும்", "today": "இன்று", "tomorrow": "நாளை", "about": "சுமார் ",
    },
    "hi": {
        "why_down_rise": "ज़्यादातर {ITEM} की वजह से। अब यह {NEW} {PER} है, पहले {OLD} था, इसलिए इस हफ़्ते इस पर "
                         "{ABOUT}{LOST} कम बना।",
        "why_down_item": "ज़्यादातर {ITEM}: पिछले हफ़्ते से {DROP} कम बना।",
        "not_down": "इस हफ़्ते मुनाफ़ा कम नहीं हुआ: लगभग {NOW}, पिछले हफ़्ते {BEFORE}।",
        "no_data": "हफ़्तों की तुलना के लिए अभी काफ़ी बिल नहीं हैं।",
        "reorder": "{ITEMS} जल्दी मँगाइए। {FIRST} {DAY} तक खत्म हो जाएगा।",
        "reorder_none": "अभी कुछ भी कम नहीं है।",
        "best": "इस महीने {ITEM} ने सबसे ज़्यादा कमाया: {SALES} की बिक्री पर {ABOUT}{PROFIT} मुनाफ़ा।",
        "best_none": "अभी किसी सामान का मुनाफ़ा पता नहीं। पहले डिस्ट्रीब्यूटर बिल जोड़ें।",
        "draft_cheaper": "नमस्ते। बिल {BILL} पर {ITEM} {NEW} है, {DAY} को {OLD} था। एक और डिस्ट्रीब्यूटर ने {CHEAPER} "
                         "का बिल दिया। क्या आप {CHEAPER} कर सकते हैं?",
        "draft": "नमस्ते। बिल {BILL} पर {ITEM} {NEW} है, {DAY} को {OLD} था। क्या आप {OLD} पर रख सकते हैं?",
        "draft_none": "लिखने के लिए हाल में कोई दाम नहीं बढ़ा।",
        "draft_ready": "{WHO} के लिए संदेश। भेजना आपके हाथ में है।",
        "unknown": "मैं बता सकता हूँ कि मुनाफ़ा क्यों कम है, क्या मँगाना है, और इस महीने सबसे अच्छा सामान कौन सा है।",
        "per_litre": "प्रति लीटर", "per_kg": "प्रति किलो", "per_pack": "प्रति पैकेट",
        "and": "और", "today": "आज", "tomorrow": "कल", "about": "लगभग ",
    },
}
MONTHS = {
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "ta": ("ஜன", "பிப்", "மார்", "ஏப்", "மே", "ஜூன்", "ஜூலை", "ஆக", "செப்", "அக்", "நவ", "டிச"),
    "hi": ("जन", "फ़र", "मार्च", "अप्रै", "मई", "जून", "जुला", "अग", "सित", "अक्टू", "नव", "दिस"),
}
WEEKDAYS = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "ta": ("திங்கள்", "செவ்வாய்", "புதன்", "வியாழன்", "வெள்ளி", "சனி", "ஞாயிறு"),
    "hi": ("सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"),
}

# A model may phrase an answer from (intent, language, template, placeholder names); None means "use the template".
Phraser = Callable[[str, str, str, set[str]], str | None]


def words(lang: str, key: str) -> str:
    return TEMPLATES.get(lang, {}).get(key) or TEMPLATES[DEFAULT_LANGUAGE][key]


def day_text(d: date, lang: str) -> str:
    return f"{d.day} {MONTHS.get(lang, MONTHS['en'])[d.month - 1]}"


def classify(text: str) -> str | None:
    low = text.lower()
    for intent in INTENTS:
        if any(k in low for k in KEYWORDS[intent]):
            return intent
    return None


@dataclass
class Answer:
    intent: str
    key: str
    values: dict[str, str]
    sources: list[dict] = field(default_factory=list)
    item_id: str | None = None
    draft_key: str | None = None  # a message for the owner to send
    draft_to: str | None = None


BOLD = {"NEW", "LOST", "DROP", "PROFIT", "NOW", "DAY"}  # the figures the answer turns on, shown in bold


def segments(text_template: str, values: dict[str, str]) -> list[dict]:
    """The filled answer as [{"t": text, "b": bold}], so the key figures are bold in any language."""
    out, pos = [], 0
    for m in PLACEHOLDER.finditer(text_template):
        if m.start() > pos:
            out.append({"t": text_template[pos:m.start()], "b": False})
        out.append({"t": values[m.group(1)], "b": m.group(1) in BOLD})
        pos = m.end()
    if pos < len(text_template):
        out.append({"t": text_template[pos:], "b": False})
    return out


def _line_source(f: CostFact) -> dict:
    return {"kind": "bill_line", "purchase_id": f.purchase_id, "line_id": f.line_id, "position": f.position,
            "distributor": f.distributor, "day": f.day.isoformat(), "bill_no": f.bill_no}


class Asker:
    def __init__(self, th: alerts.Thresholds, phraser: Phraser | None = None) -> None:
        self.th, self.phraser = th, phraser

    # ---------------- tools (code computes every figure) ----------------

    def why_down(self, s: Session, m: Merchant, today: date) -> Answer:
        lang = m.language
        this = ledger(s, m.id, Window(today - timedelta(days=6), today))
        last = ledger(s, m.id, Window(today - timedelta(days=13), today - timedelta(days=7)), this.costs)
        # 1. a cost rise that took profit this week, at the newer bill's cost
        worst, worst_lost = None, 0
        for item_id in {lp.item_id for lp in this.lines if lp.item_id}:
            new = this.costs.latest(item_id)
            old = this.costs.before(new) if new else None
            if not (new and old and new.usual_cost and old.usual_cost and new.usual_cost > old.usual_cost):
                continue
            at_new = [lp for lp in this.lines if lp.item_id == item_id and lp.cost is new and lp.qty_usual is not None]
            lost = round_half_up(sum((lp.qty_usual for lp in at_new), Fraction(0)) * (new.usual_cost - old.usual_cost))
            if lost > worst_lost:
                worst, worst_lost = (item_id, new, old, any(lp.estimated for lp in at_new)), lost
        if worst:
            item_id, new, old, estimated = worst
            n_bills = len({lp.bill_id for lp in this.lines if lp.item_id == item_id})
            return Answer("why_down", "why_down_rise", {
                "ITEM": this.items[item_id].name.lower() if lang == "en" else this.items[item_id].name,
                "NEW": rupees(new.usual_cost), "PER": words(lang, f"per_{unit_label(new)}"),
                "OLD": rupees(old.usual_cost), "LOST": rupees(worst_lost),
                "ABOUT": words(lang, "about") if estimated else "",
            }, [_line_source(new), {"kind": "voice_bills", "item_id": item_id, "count": n_bills}], item_id=item_id)
        # 2. otherwise the item whose profit fell most since last week
        def by_item(led) -> dict[str, int]:
            out: dict[str, int] = {}
            for lp in led.lines:
                if lp.item_id and lp.profit_paise is not None:
                    out[lp.item_id] = out.get(lp.item_id, 0) + lp.profit_paise
            return out
        now_p, before_p = by_item(this), by_item(last)
        total_now, total_before = sum(now_p.values()), sum(before_p.values())
        if not now_p and not before_p:
            return Answer("why_down", "no_data", {})
        if total_now >= total_before:
            return Answer("why_down", "not_down", {"NOW": rupees(total_now), "BEFORE": rupees(total_before)})
        drops = sorted(((before_p.get(i, 0) - now_p.get(i, 0), i) for i in set(now_p) | set(before_p)), reverse=True)
        drop, item_id = drops[0]
        n_bills = len({lp.bill_id for lp in this.lines if lp.item_id == item_id})
        return Answer("why_down", "why_down_item", {"ITEM": this.items[item_id].name, "DROP": rupees(drop)},
                      [{"kind": "voice_bills", "item_id": item_id, "count": n_bills}], item_id=item_id)

    def reorder(self, s: Session, m: Merchant, today: date) -> Answer:
        lang = m.language
        out = [(f, item_id) for kind, item_id, f in alerts.findings(s, m.id, today, self.th) if kind == "running_out"]
        if not out:
            return Answer("reorder", "reorder_none", {})
        out.sort(key=lambda x: x[0]["out_day"])
        names = {it.id: it.name for it in s.query(Item).filter(Item.merchant_id == m.id)}
        listed = [names[i] for _, i in out]
        items = listed[0] if len(listed) == 1 else f"{', '.join(listed[:-1])} {words(lang, 'and')} {listed[-1]}"
        first_day = date.fromisoformat(out[0][0]["out_day"])
        gap = (first_day - today).days
        day = words(lang, "today") if gap <= 0 else words(lang, "tomorrow") if gap == 1 else \
            WEEKDAYS.get(lang, WEEKDAYS["en"])[first_day.weekday()]
        return Answer("reorder", "reorder", {"ITEMS": items, "FIRST": listed[0], "DAY": day},
                      [{"kind": "item", "item_id": i, "name": names[i]} for _, i in out], item_id=out[0][1])

    def best(self, s: Session, m: Merchant, today: date) -> Answer:
        rep = profit_report(s, m.id, "month", today)
        if not rep["items"]:
            return Answer("best", "best_none", {})
        top = rep["items"][0]
        n_bills = len({lp.bill_id for lp in ledger(s, m.id, Window(date.fromisoformat(rep["from"]), today)).lines
                       if lp.item_id == top["item_id"]})
        return Answer("best", "best", {"ITEM": top["name"], "PROFIT": rupees(round_half_up(Fraction(top["profit_paise"],
                      100)) * 100), "SALES": rupees(top["sales_paise"]),
                      "ABOUT": words(m.language, "about") if top["estimated"] else ""},
                      [{"kind": "voice_bills", "item_id": top["item_id"], "count": n_bills, "period": "month"}],
                      item_id=top["item_id"])

    def draft(self, s: Session, m: Merchant, today: date, item_id: str | None) -> Answer:
        """A message to the distributor who raised the price: the item's latest rise, or the worst one this week."""
        lang = m.language
        costs = ledger(s, m.id, Window(today, today)).costs
        candidates = [item_id] if item_id else list(costs.by_item)
        best = None
        for iid in candidates:
            new = costs.latest(iid) if iid in costs.by_item else None
            old = costs.before(new) if new else None
            if new and old and new.usual_cost and old.usual_cost and new.usual_cost > old.usual_cost and \
                    (best is None or new.usual_cost - old.usual_cost > best[1].usual_cost - best[2].usual_cost):
                best = (iid, new, old)
        if best is None:
            return Answer("draft", "draft_none", {})
        iid, new, old = best
        item = s.get(Item, iid)
        cheaper = cheaper_elsewhere(costs, new, today)
        size = " 1 L" if unit_label(new) == "litre" and new.by_count else ""
        values = {"ITEM": f"{item.name}{size}", "NEW": rupees(new.usual_cost), "BILL": new.bill_no or "—",
                  "OLD": rupees(old.usual_cost), "DAY": day_text(old.day, lang), "WHO": new.distributor}
        if cheaper and cheaper.usual_cost:
            values["CHEAPER"] = rupees(cheaper.usual_cost)
        sources = [_line_source(new), _line_source(old)] + ([_line_source(cheaper)] if cheaper else [])
        return Answer("draft", "draft_ready", values, sources, item_id=iid,
                      draft_key="draft_cheaper" if cheaper else "draft", draft_to=new.distributor)

    # ---------------- the question ----------------

    def ask(self, s: Session, m: Merchant, text: str, intent: str | None = None, item_id: str | None = None,
            today: date | None = None) -> dict:
        today = today or ist_day(utcnow())
        lang = m.language if m.language in TEMPLATES else DEFAULT_LANGUAGE
        intent = intent if intent in INTENTS else classify(text or "")
        if intent == "why_down":
            a = self.why_down(s, m, today)
        elif intent == "reorder":
            a = self.reorder(s, m, today)
        elif intent == "best":
            a = self.best(s, m, today)
        elif intent == "draft":
            a = self.draft(s, m, today, item_id)
        else:
            a = Answer("unknown", "unknown", {})
        template = words(lang, a.key)
        model = self.phraser(a.intent, lang, template, set(a.values)) if self.phraser else None
        text_out, how = fill_guarded(model, template, a.values)
        used = model if how == "model" else template
        out = {"question": text, "intent": a.intent, "language": lang, "text": text_out,
               "segments": segments(used, a.values), "phrased_by": how, "sources": a.sources, "item_id": a.item_id,
               "draft": None}
        if a.draft_key:
            out["draft"] = {"to": a.draft_to, "item_id": a.item_id,
                            "text": fill_guarded(None, words(lang, a.draft_key), a.values)[0]}
        return out


