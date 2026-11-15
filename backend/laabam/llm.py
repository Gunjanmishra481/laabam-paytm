"""The guard between model text and the owner.

Rule: no figure is invented by a model. A model may write text with placeholders such as
{PROFIT_TODAY}; code fills every value. Any digit in raw model output (in any script: Python's
\\d matches Devanagari, Tamil and other digits too), or any placeholder code did not provide,
means the model text is dropped and the offline template is used instead.
"""
from __future__ import annotations

import re
from collections.abc import Mapping

PLACEHOLDER = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
_DIGIT = re.compile(r"\d")


class MissingValue(KeyError):
    pass


def placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER.findall(template))


def fill(template: str, values: Mapping[str, str]) -> str:
    """Replace {NAME} with values[NAME]. Values are strings code already formatted (e.g. rupees())."""
    missing = placeholders(template) - set(values)
    if missing:
        raise MissingValue(", ".join(sorted(missing)))
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def safe_model_text(model_text: str | None, allowed: set[str]) -> bool:
    """True if model text may be used as a template: no digits, only allowed placeholders."""
    if not model_text or not model_text.strip():
        return False
    if _DIGIT.search(PLACEHOLDER.sub("", model_text)):
        return False
    return placeholders(model_text) <= allowed


def fill_guarded(model_text: str | None, fallback_template: str, values: Mapping[str, str]) -> tuple[str, str]:
    """Fill the model's draft if it passes the guard, else the fallback template.

    Returns (text, source) where source is "model" or "template", so callers can log which was used.
    """
    if safe_model_text(model_text, set(values)):
        return fill(model_text, values), "model"
    return fill(fallback_template, values), "template"
