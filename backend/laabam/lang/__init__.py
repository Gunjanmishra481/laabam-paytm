"""Language support: supported languages, word tables and number reading."""
from __future__ import annotations

LANGUAGES = ("en", "ta", "hi")
DEFAULT_LANGUAGE = "en"


def active_languages(language: str) -> tuple[str, ...]:
    """English is always understood alongside the owner's language (code-mixing is normal at the counter)."""
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language {language!r}")
    return ("en",) if language == "en" else ("en", language)
