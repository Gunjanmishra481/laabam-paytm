"""Words Laabam says to people outside the app (Soundbox, SMS), by language.

Templates have {PLACEHOLDERS}; code fills every value (llm.fill). The app's own screens use the
frontend's strings table. Tamil and Hindi: native-speaker review welcome.
"""
from __future__ import annotations

from .lang import DEFAULT_LANGUAGE
from .llm import fill

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "qr_screen": "Scan to pay {AMOUNT}",
        "paid_announce": "{AMOUNT} received.",
        "new_device": "A new phone signed in to Laabam: {DEVICE}. If it wasn't you, sign it out in Settings.",
    },
    "ta": {
        "qr_screen": "{AMOUNT} செலுத்த ஸ்கேன் செய்யுங்கள்",
        "paid_announce": "{AMOUNT} பெறப்பட்டது.",
        "new_device": "புதிய போன் Laabam இல் உள்நுழைந்தது: {DEVICE}. நீங்கள் இல்லையென்றால் அமைப்புகளில் வெளியேற்றுங்கள்.",
    },
    "hi": {
        "qr_screen": "{AMOUNT} चुकाने के लिए स्कैन करें",
        "paid_announce": "{AMOUNT} प्राप्त हुए।",
        "new_device": "एक नए फ़ोन ने Laabam में साइन इन किया: {DEVICE}। अगर यह आप नहीं थे, तो सेटिंग्स में साइन आउट करें।",
    },
}


def say(language: str, key: str, **values: str) -> str:
    template = STRINGS.get(language, {}).get(key) or STRINGS[DEFAULT_LANGUAGE][key]
    return fill(template, values)
