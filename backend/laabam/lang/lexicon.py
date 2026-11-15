"""Word tables for reading a spoken order: number words, units, currency, corrections, fillers.

English (en), Tamil (ta, Tamil script, formal and common spoken forms) and Hindi (hi, Devanagari
and common romanised forms). Speech-to-text usually writes prices as digits; the number words
are for when it doesn't. Add words here, not in the parser.
"""
from __future__ import annotations

from decimal import Decimal as D

# ---------------- number words ----------------
# kind: "unit" adds to the number being read; "mult" multiplies (hundred, thousand);
# "mark" is a Hindi fraction marker that adjusts the next number (saadhe = +½, sawa = +¼, paune = −¼).

NumberWord = tuple[D, str]


def _units(pairs: dict[str, int | str]) -> dict[str, NumberWord]:
    return {w: (D(str(v)), "unit") for w, v in pairs.items()}


_EN = _units({
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "half": "0.5",
})
_EN.update({"hundred": (D(100), "mult"), "thousand": (D(1000), "mult"), "lakh": (D(100000), "mult")})

_HI_DEVANAGARI = [
    "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ", "दस",
    "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस",
    "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस", "तीस",
    "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस", "चालीस",
    "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास", "पचास",
    "इक्यावन", "बावन", "तिरपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ", "साठ",
    "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर", "सत्तर",
    "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी", "अस्सी",
    "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी", "नब्बे",
    "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]
_HI = {w: (D(i + 1), "unit") for i, w in enumerate(_HI_DEVANAGARI)}
_HI.update(_units({
    # common spelling variants
    "पांच": 5, "छः": 6, "छे": 6, "पन्द्रह": 15,
    # fractions said as numbers
    "डेढ़": "1.5", "डेढ": "1.5", "ढाई": "2.5", "आधा": "0.5", "आधे": "0.5", "आधी": "0.5",
    # romanised
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "chhah": 6,
    "saat": 7, "aath": 8, "nau": 9, "das": 10, "gyarah": 11, "barah": 12, "pandrah": 15, "bees": 20,
    "pachees": 25, "pachchis": 25, "tees": 30, "chalis": 40, "chaalis": 40, "pachas": 50, "pachaas": 50,
    "saath": 60, "sattar": 70, "assi": 80, "nabbe": 90, "dedh": "1.5", "dhai": "2.5", "dhaai": "2.5",
    "aadha": "0.5", "adha": "0.5",
}))
_HI.update({
    "सौ": (D(100), "mult"), "हज़ार": (D(1000), "mult"), "हजार": (D(1000), "mult"), "लाख": (D(100000), "mult"),
    "sau": (D(100), "mult"), "hazaar": (D(1000), "mult"), "hazar": (D(1000), "mult"), "lakh": (D(100000), "mult"),
    "साढ़े": (D("0.5"), "mark"), "साढे": (D("0.5"), "mark"), "saadhe": (D("0.5"), "mark"),
    "sadhe": (D("0.5"), "mark"), "saade": (D("0.5"), "mark"),
    "सवा": (D("0.25"), "mark"), "sawa": (D("0.25"), "mark"), "sava": (D("0.25"), "mark"),
    "पौने": (D("-0.25"), "mark"), "paune": (D("-0.25"), "mark"),
})

_TA = _units({
    "ஒன்று": 1, "ஒண்ணு": 1, "ஒரு": 1, "இரண்டு": 2, "ரெண்டு": 2, "மூன்று": 3, "மூணு": 3, "நான்கு": 4,
    "நாலு": 4, "ஐந்து": 5, "அஞ்சு": 5, "ஆறு": 6, "ஏழு": 7, "எட்டு": 8, "ஒன்பது": 9, "பத்து": 10,
    "பதினொன்று": 11, "பதினொண்ணு": 11, "பன்னிரண்டு": 12, "பன்னெண்டு": 12, "பதின்மூன்று": 13,
    "பதினான்கு": 14, "பதினைந்து": 15, "பதினஞ்சு": 15, "பதினாறு": 16, "பதினேழு": 17, "பதினெட்டு": 18,
    "பத்தொன்பது": 19,
    # tens, standing and joining forms ("இருபத்தி ஐந்து" = 25)
    "இருபது": 20, "இருபத்து": 20, "இருபத்தி": 20, "முப்பது": 30, "முப்பத்து": 30, "முப்பத்தி": 30,
    "நாற்பது": 40, "நாற்பத்து": 40, "நாப்பது": 40, "நாப்பத்தி": 40, "ஐம்பது": 50, "அம்பது": 50,
    "ஐம்பத்து": 50, "ஐம்பத்தி": 50, "அம்பத்தி": 50, "அறுபது": 60, "அறுபத்து": 60, "அறுபத்தி": 60,
    "எழுபது": 70, "எழுபத்து": 70, "எழுபத்தி": 70, "எண்பது": 80, "எண்பத்து": 80, "எண்பத்தி": 80,
    "தொண்ணூறு": 90, "தொண்ணூற்று": 90, "தொண்ணூத்தி": 90,
    # hundreds are single words in Tamil, so they add
    "இருநூறு": 200, "இருநூற்று": 200, "இருநூத்தி": 200, "ரெண்டுநூறு": 200,
    "முந்நூறு": 300, "முன்னூறு": 300, "முந்நூற்று": 300, "முன்னூத்தி": 300,
    "நானூறு": 400, "நானூற்று": 400, "நானூத்தி": 400,
    "ஐந்நூறு": 500, "ஐநூறு": 500, "ஐந்நூற்று": 500, "ஐநூத்தி": 500, "அஞ்சுநூறு": 500,
    "அறுநூறு": 600, "அறுநூற்று": 600, "அறுநூத்தி": 600, "எழுநூறு": 700, "எழுநூற்று": 700, "எழுநூத்தி": 700,
    "எண்ணூறு": 800, "எண்ணூற்று": 800, "எண்ணூத்தி": 800, "தொள்ளாயிரம்": 900, "தொள்ளாயிரத்து": 900,
    "அரை": "0.5",
})
_TA.update({
    "நூறு": (D(100), "mult"), "நூற்று": (D(100), "mult"), "நூற்றி": (D(100), "mult"), "நூத்தி": (D(100), "mult"),
    "ஆயிரம்": (D(1000), "mult"), "ஆயிரத்து": (D(1000), "mult"), "ஆயிரத்தி": (D(1000), "mult"),
    "லட்சம்": (D(100000), "mult"),
})

NUMBER_WORDS: dict[str, dict[str, NumberWord]] = {"en": _EN, "hi": _HI, "ta": _TA}

# Words allowed inside a number said in words ("two hundred and fifty", "one and a half").
NUMBER_CONNECTORS = {"en": {"and", "a"}, "hi": set(), "ta": set()}

# ---------------- units ----------------
# canonical unit and the factor from the spoken unit: 250 g -> 0.25 kg; a dozen -> 12 pc.
Unit = tuple[str, D]

_KG, _G = ("kg", D(1)), ("kg", D("0.001"))
_L, _ML = ("L", D(1)), ("L", D("0.001"))
_PC, _PACK, _DOZEN = ("pc", D(1)), ("pack", D(1)), ("pc", D(12))

UNIT_WORDS: dict[str, dict[str, Unit]] = {
    "en": {
        **dict.fromkeys(["kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"], _KG),
        **dict.fromkeys(["g", "gm", "gms", "gram", "grams", "gramme", "grammes"], _G),
        **dict.fromkeys(["l", "ltr", "ltrs", "litre", "litres", "liter", "liters"], _L),
        **dict.fromkeys(["ml", "mls", "millilitre", "millilitres", "milliliter", "milliliters"], _ML),
        **dict.fromkeys(["pc", "pcs", "piece", "pieces", "nos"], _PC),
        **dict.fromkeys(["pack", "packs", "packet", "packets", "pkt", "pkts"], _PACK),
        **dict.fromkeys(["dozen", "dozens"], _DOZEN),
    },
    "hi": {
        **dict.fromkeys(["किलो", "किग्रा"], _KG), **dict.fromkeys(["ग्राम"], _G),
        **dict.fromkeys(["लीटर", "लिटर"], _L), **dict.fromkeys(["पीस"], _PC),
        **dict.fromkeys(["पैकेट", "पैकिट"], _PACK), **dict.fromkeys(["दर्जन", "darjan"], _DOZEN),
    },
    "ta": {
        # Tamil adds case endings ("கிலோவுக்கு" = per kilo), so Tamil units match by prefix.
        **dict.fromkeys(["கிலோ"], _KG), **dict.fromkeys(["கிராம்"], _G),
        **dict.fromkeys(["லிட்டர்", "லீட்டர்"], _L), **dict.fromkeys(["பீஸ்"], _PC),
        **dict.fromkeys(["பாக்கெட்", "பாக்கட்"], _PACK), **dict.fromkeys(["டஜன்"], _DOZEN),
    },
}
PREFIX_MATCH_LANGS = {"ta"}

# ---------------- other word classes ----------------

CURRENCY_WORDS = {
    "en": {"rs", "rupee", "rupees", "inr", "rupaye", "rupay", "rupiya", "bucks"},
    "hi": {"रुपये", "रुपए", "रुपया", "रूपये", "रूपए", "रुपय", "rupaye", "rupaiye", "rupaya"},
    "ta": {"ரூபாய்", "ரூபா", "ரூ"},
}
PAISE_WORDS = {"en": {"paise", "paisa"}, "hi": {"पैसे", "पैसा", "paise"}, "ta": {"பைசா", "காசு"}}

# "at 60", "60 per kg", "60 each"
PER_WORDS = {"en": {"per", "at", "each", "every", "rate"}, "hi": {"प्रति", "prati"}, "ta": {"ஒவ்வொரு"}}

# "rice 200, no, 250": the next number replaces the last one.
CORRECTION_WORDS = {
    "en": {"no", "sorry", "wait", "actually", "correction", "make", "change"},
    "hi": {"नहीं", "नही", "nahi", "nahin", "nai", "matlab"},
    "ta": {"இல்லை", "இல்ல", "illa", "illai", "இல்லே"},
}
# "sugar 120 not 100": the number after "not" is the wrong one; keep the number before.
NOT_WORDS = {"en": {"not", "instead"}, "hi": set(), "ta": set()}

# Words between items that are not part of an item name.
FILLER_WORDS = {
    "en": {"and", "then", "also", "plus", "um", "umm", "uh", "erm", "ok", "okay", "please", "give", "it", "that",
           "the", "of", "worth", "for"},
    "hi": {"और", "फिर", "भी", "का", "की", "के", "वाला", "वाली", "aur", "phir", "bhi", "ka", "ki", "ke",
           "wala", "wali", "dena", "दे", "दीजिए"},
    "ta": {"மற்றும்", "அப்புறம்", "அப்பறம்", "அப்றம்", "அப்புறமா", "கொடுங்க", "குடுங்க", "um"},
}
# "दे दो" / "de do" means "give"; numbers.py skips "दो"/"do" right after "दे"/"de".
GIVE_PAIRS = {("दे", "दो"), ("de", "do")}
