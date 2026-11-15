"""IST display. India has no daylight saving, so a fixed +05:30 offset works on every OS
(Windows has no time-zone database for zoneinfo). Store UTC; convert only for display."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("naive datetime; use timezone-aware UTC")
    return dt.astimezone(IST)


def ist_time(dt: datetime) -> str:
    # "%-I" is not supported on Windows, so strip the leading zero by hand.
    return to_ist(dt).strftime("%I:%M %p").lstrip("0")


def ist_date(dt: datetime) -> str:
    d = to_ist(dt)
    return f"{d.day} {d.strftime('%b')}"


def ist_day(dt: datetime) -> date:
    """The shop's calendar day for a timestamp."""
    return to_ist(dt).date()


def ist_day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, as aware datetimes (compare against UTC columns)."""
    start = datetime.combine(day, time(0, 0), IST)
    return start, start + timedelta(days=1)


def next_ist_time(now: datetime, at: time) -> datetime:
    """The next moment the IST wall clock reads `at` (e.g. the 21:00 closing summary)."""
    local = to_ist(now)
    candidate = datetime.combine(local.date(), at, IST)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate
