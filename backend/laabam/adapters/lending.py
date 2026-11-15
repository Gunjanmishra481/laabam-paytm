"""Lending adapter (BUILD_PLAN §5.6). Laabam only sizes the loan from the owner's plan and relays it; the lending
partner makes the credit decision and sets the terms. The sandbox twin returns a deterministic offer for the amount
asked, with no terms (the screen shows the partner's placeholders), and records applications.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from .base import Shop


@dataclass(frozen=True)
class LoanQuote:
    ref: str
    amount_paise: int
    partner: str | None = None  # the lender's name, when the partner gives it
    rate_text: str | None = None  # e.g. "1.5% a month", as the partner states it
    days: int | None = None
    daily_paise: int | None = None  # taken from daily UPI collections


class SandboxLending:
    name = "sandbox"

    def __init__(self) -> None:
        self._n = itertools.count(1)
        self.applications: list[dict] = []

    def offer(self, shop: Shop, amount_paise: int, purpose: str) -> LoanQuote:
        return LoanQuote(ref=f"SBXLOAN-{next(self._n):05d}", amount_paise=amount_paise)

    def apply(self, shop: Shop, ref: str) -> str:
        """The owner approved: send the application. Returns the partner's status ("applied")."""
        self.applications.append({"shop": shop.id, "ref": ref})
        return "applied"
