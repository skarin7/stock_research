"""Structural extraction of time/date filters from natural language queries.

Pure regex — zero LLM calls, zero latency. Handles common Indian equity
research time expressions. Returns a QueryFilters dataclass; missing fields
are None (caller treats them as "no filter applied").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4,
    "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30,
              "quarter": 90, "quarters": 90, "year": 365, "years": 365}

# last/past N <unit>  — e.g. "last 7 days", "past 3 weeks"
_RE_LAST_N = re.compile(
    r'\b(?:last|past|over\s+the\s+last|in\s+the\s+(?:last|past))\s+(\d+)\s+(days?|weeks?|months?|quarters?|years?)\b',
    re.IGNORECASE,
)

# last/past <word>  — e.g. "last week", "past month"
_RE_LAST_WORD = re.compile(
    r'\b(?:last|past)\s+(week|month|quarter|year)\b',
    re.IGNORECASE,
)

# yesterday
_RE_YESTERDAY = re.compile(r'\byesterday\b', re.IGNORECASE)

# ISO date: 2026-06-20
_RE_ISO = re.compile(r'\b(\d{4})-(\d{1,2})-(\d{1,2})\b')

# DD/MM/YYYY or DD-MM-YYYY
_RE_DMY = re.compile(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b')

# Jun 20th / June 20 / 20 Jun / 20 June  (with optional year)
_RE_MDY = re.compile(
    r'\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?\b',
    re.IGNORECASE,
)
_RE_DM = re.compile(
    r'\b(\d{1,2})(?:st|nd|rd|th)?\s+'
    r'(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|'
    r'jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
    r'(?:\s+(\d{4}))?\b',
    re.IGNORECASE,
)


@dataclass
class QueryFilters:
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    lookback_days: Optional[int] = None
    period_label: str = ""

    def has_filter(self) -> bool:
        return any([self.date_from, self.date_to, self.lookback_days])

    def as_hint(self) -> str:
        """Format as a one-line context hint for the agent."""
        if not self.has_filter():
            return ""
        parts = []
        if self.period_label:
            parts.append(f"period={self.period_label!r}")
        if self.lookback_days:
            parts.append(f"lookback_days={self.lookback_days}")
        if self.date_from:
            parts.append(f"date_from={self.date_from.isoformat()}")
        if self.date_to:
            parts.append(f"date_to={self.date_to.isoformat()}")
        return "(time_context: " + ", ".join(parts) + ")"


def _resolve_year(year_str: str | None, month: int, day: int) -> int:
    """If year is missing, use current year; roll back one year if the date is in the future."""
    if year_str:
        return int(year_str)
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate > today:
        return today.year - 1
    return today.year


def extract_filters(text: str) -> QueryFilters:
    """Extract time/date filters from free-form query text.

    Returns empty QueryFilters when no time expression is found.
    Never raises — any parse error returns empty filters.
    """
    if not text:
        return QueryFilters()

    try:
        today = date.today()

        # last/past N <unit>
        m = _RE_LAST_N.search(text)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            days = n * _UNIT_DAYS.get(unit.rstrip("s") + ("s" if not unit.endswith("s") else ""),
                                      _UNIT_DAYS.get(unit, 1))
            return QueryFilters(
                lookback_days=days,
                date_from=today - timedelta(days=days),
                date_to=today,
                period_label=m.group(0),
            )

        # last/past <word>
        m = _RE_LAST_WORD.search(text)
        if m:
            unit = m.group(1).lower()
            days = _UNIT_DAYS.get(unit, 7)
            return QueryFilters(
                lookback_days=days,
                date_from=today - timedelta(days=days),
                date_to=today,
                period_label=m.group(0),
            )

        # yesterday
        if _RE_YESTERDAY.search(text):
            d = today - timedelta(days=1)
            return QueryFilters(date_from=d, date_to=d, period_label="yesterday")

        # ISO date
        m = _RE_ISO.search(text)
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # DD/MM/YYYY
        m = _RE_DMY.search(text)
        if m:
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # Month Day  (e.g. "Jun 20th")
        m = _RE_MDY.search(text)
        if m:
            month = _MONTH_NAMES[m.group(1).lower()[:3]]
            day = int(m.group(2))
            year = _resolve_year(m.group(3), month, day)
            d = date(year, month, day)
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

        # Day Month  (e.g. "20 June")
        m = _RE_DM.search(text)
        if m:
            day = int(m.group(1))
            month = _MONTH_NAMES[m.group(2).lower()[:3]]
            year = _resolve_year(m.group(3), month, day)
            d = date(year, month, day)
            return QueryFilters(date_from=d, date_to=d, period_label=m.group(0))

    except Exception:
        pass  # any date parse error → no filter

    return QueryFilters()
