"""US equity regular-session calendar shared by pricing and GUI previews."""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from dateutil.relativedelta import relativedelta

MARKET_TZ = ZoneInfo("America/New_York")
CALENDAR = "XNYS"


@lru_cache(maxsize=1)
def calendar():
    return xcals.get_calendar(CALENDAR, start="2020-01-01", end="2035-12-31")


def now_utc():
    return datetime.now(timezone.utc)


def next_session(day: date):
    return calendar().date_to_session(day, direction="next").date()


def start_date(as_of: datetime | date):
    if not isinstance(as_of, datetime):
        return next_session(as_of)
    if as_of.tzinfo is None:
        raise ValueError("Pricing time requires an explicit timezone.")
    local_day = as_of.astimezone(MARKET_TZ).date()
    candidate = calendar().date_to_session(local_day, direction="next")
    # Pre-open schedules today's upcoming session; regular trading can start today.
    # At/after the actual close (including early closes), move to the next session.
    if candidate.date() == local_day and as_of >= calendar().session_close(candidate):
        candidate = calendar().session_offset(candidate, 1)
    return candidate.date()


def maturity_date(start: date, term: str):
    start = next_session(start)
    if term.endswith("bd"):
        return calendar().session_offset(start, int(term[:-2])).date()
    target = (
        start
        + {
            "7d": timedelta(days=7),
            "1m": relativedelta(months=1),
            "3m": relativedelta(months=3),
            "6m": relativedelta(months=6),
            "1y": relativedelta(years=1),
        }[term]
    )
    return next_session(target)  # Following business-day convention.


def pricing_time(value: datetime | date):
    return (
        value
        if isinstance(value, datetime)
        else datetime.combine(value, time(9, 30), MARKET_TZ)
    )


def preview(terms, as_of=None):
    as_of = as_of or now_utc()
    start = start_date(as_of)
    session = calendar().date_to_session(start)
    return {
        "calendar": CALENDAR,
        "timezone": str(MARKET_TZ),
        "as_of": as_of.isoformat(),
        "start_date": start.isoformat(),
        "session_open": calendar().session_open(session).isoformat(),
        "session_close": calendar().session_close(session).isoformat(),
        "maturities": {t: maturity_date(start, t).isoformat() for t in terms},
    }
