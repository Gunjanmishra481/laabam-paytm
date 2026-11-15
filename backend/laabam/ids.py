"""Identifiers."""
from __future__ import annotations

import secrets

# No 0/O/1/I: easy to read out at the counter or on a call.
READABLE = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_id(prefix: str, nbytes: int = 8) -> str:
    """'bill_3f9a0c1d2e4b5a69'. Fits String(32) for prefixes up to 15 characters."""
    return f"{prefix}_{secrets.token_hex(nbytes)}"


def new_readable_id(prefix: str, length: int = 7) -> str:
    """'LB-7KQ3M9X': for things the owner might read aloud, like a bill number."""
    return f"{prefix}-" + "".join(secrets.choice(READABLE) for _ in range(length))
