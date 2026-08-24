"""Pure conversion primitives for the normalize stage.

No imports from ``ingest.*``, no I/O, no LLM. Every function here takes a printed string and
converts it — never a guess, never a fallback figure.

Most return ``value | None``, where callers check for an empty raw string before calling, so a
``None`` coming back always means "present but unconvertible", not "absent". :func:`fold` and
:func:`normalize_registration_no` are the exceptions: cleaning a string always succeeds, so
they return ``str`` and say nothing about whether the result is *acceptable* — that is
validate's question, not this module's.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date
from decimal import Decimal

# Era year N -> Gregorian year is offset + N. 令和8年 -> 2026, 平成31年 -> 2019, 昭和 -> pre-1989.
ERA_OFFSET = {"令和": 2018, "平成": 1988, "昭和": 1925}
_ERA_ALIASES = {"令和": "令和", "R": "令和", "平成": "平成", "H": "平成", "昭和": "昭和", "S": "昭和"}

_WHITESPACE_RUN = re.compile(r"\s+")

# NFKC does not fold these onto ASCII '-': U+2212 MINUS SIGN and U+2010 HYPHEN stay as
# themselves, and ー (U+30FC, the kana long-vowel mark) is routinely used as a dash in
# printed registration numbers. The fullwidth hyphen-minus U+FF0D *does* fold to '-' by NFKC,
# so it never needs listing here.
_HYPHEN_VARIANTS = "-‐−ー"
_NEGATIVE_MARKS = "△▲-−"
_CURRENCY_CHARS = "¥￥円,"

_ERA_DATE_PATTERN = re.compile(
    r"^(令和|平成|昭和|R|H|S)(\d{1,2}|元)[年.\-/](\d{1,2})[月.\-/](\d{1,2})日?$"
)
_WESTERN_DATE_PATTERN = re.compile(r"^(\d{4})[年.\-/](\d{1,2})[月.\-/](\d{1,2})日?$")

# The shape a cleaned registration number must have. Kept here, public, because this is
# where the cleaning happens — but normalize no longer applies it; validate fullmatches
# against it once it has the cleaned string. A shape check is a validity rule, not a
# conversion, so it moved out of this stage.
REGISTRATION_PATTERN = re.compile(r"T\d{13}")

_CLOSING_MARKER = "締"
_MONTH_OFFSET_MARKERS = (("翌々月", 2), ("翌月", 1), ("当月", 0), ("今月", 0), ("本月", 0))
_EXPLICIT_DAY_PATTERN = re.compile(r"(\d{1,2})日")
_EXPLICIT_YEAR_MONTH_END_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月末日?")

_TAX_PERCENT_PATTERN = re.compile(r"(\d{1,3})%")


def fold(raw: str) -> str:
    """NFKC-normalize, strip, and collapse interior whitespace runs to one space.

    Every other function here folds first, so full-width digits, full-width parentheses,
    and stray double-width spaces never reach a regex written against ASCII.
    """
    folded = unicodedata.normalize("NFKC", raw).strip()
    return _WHITESPACE_RUN.sub(" ", folded)


def parse_number(raw: str) -> Decimal | None:
    """Sign-aware money/quantity parser. Decimal, never float — money is never approximate."""
    folded = fold(raw)
    if not folded:
        return None

    negative = False
    if folded.startswith("(") and folded.endswith(")") and len(folded) > 1:
        negative = True
        folded = folded[1:-1]

    for mark in _NEGATIVE_MARKS:
        if mark in folded:
            negative = True
            folded = folded.replace(mark, "")

    for ch in _CURRENCY_CHARS:
        folded = folded.replace(ch, "")
    folded = folded.replace(" ", "")

    if not re.fullmatch(r"\d+(\.\d+)?", folded):
        return None

    value = Decimal(folded)
    return -value if negative else value


def to_int(value: Decimal | None) -> int | None:
    """The int when ``value`` is integral (1234.00 -> 1234), else None. Never rounds."""
    if value is None:
        return None
    integral = value.to_integral_value()
    if integral != value:
        return None
    return int(integral)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        # Constructing through date() is what catches an impossible calendar date like
        # 2026-02-30 — string slicing alone would happily carry it through unchecked.
        return None


def parse_date(raw: str) -> date | None:
    """Era pattern -> western pattern -> None. Relative due-date text is a separate ladder."""
    # Whitespace inside a date carries no meaning ('令和 8年 1月 7日' is ordinary Japanese
    # typesetting), so it comes out before matching. Leaving it in would send a perfectly
    # legible date to human review purely over spacing.
    folded = _WHITESPACE_RUN.sub("", fold(raw))
    if not folded:
        return None

    era_match = _ERA_DATE_PATTERN.match(folded)
    if era_match:
        era_key, year_token, month_token, day_token = era_match.groups()
        era_name = _ERA_ALIASES[era_key]
        year_number = 1 if year_token == "元" else int(year_token)
        return _safe_date(ERA_OFFSET[era_name] + year_number, int(month_token), int(day_token))

    western_match = _WESTERN_DATE_PATTERN.match(folded)
    if western_match:
        year, month, day = (int(group) for group in western_match.groups())
        return _safe_date(year, month, day)

    return None


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + offset
    return total // 12, total % 12 + 1


def _last_day_of_month(year: int, month: int) -> date | None:
    # normalize is total (see ingest/normalize.py), so this must fail closed to None
    # instead of raising: calendar.monthrange rejects a month outside 1-12 with
    # IllegalMonthError, and date() rejects a year outside 1..9999 with ValueError, and
    # either one raising out of a single malformed string would crash the whole run.
    if not 1 <= month <= 12:
        return None
    return _safe_date(year, month, calendar.monthrange(year, month)[1])


def parse_relative_due_date(raw: str, issue_date: date) -> date | None:
    """A due date printed as a payment term (翌月末払い, ...) rather than a date, anchored
    to ``issue_date``. Only called once ``parse_date`` has already failed on the same text.
    """
    folded = fold(raw)
    if not folded:
        return None

    if _CLOSING_MARKER in folded:
        # '月末締め翌月末払い': the payment date is the 翌月末 part after the closing (締)
        # marker, not the closing date itself.
        folded = folded.rsplit(_CLOSING_MARKER, 1)[1]

    explicit_year_month = _EXPLICIT_YEAR_MONTH_END_PATTERN.search(folded)
    if explicit_year_month:
        year, month = int(explicit_year_month.group(1)), int(explicit_year_month.group(2))
        return _last_day_of_month(year, month)

    month_offset = None
    for marker, offset in _MONTH_OFFSET_MARKERS:
        if marker in folded:
            month_offset = offset
            break
    if month_offset is None:
        if "月末" in folded or "末日" in folded:
            month_offset = 0
        else:
            return None

    year, month = _add_months(issue_date.year, issue_date.month, month_offset)

    day_match = _EXPLICIT_DAY_PATTERN.search(folded)
    if day_match:
        day = int(day_match.group(1))
        if day > calendar.monthrange(year, month)[1]:
            return None
        return _safe_date(year, month, day)

    if "末" in folded:
        return _last_day_of_month(year, month)

    return None


def normalize_registration_no(raw: str) -> str:
    """Fold, strip all whitespace and every hyphen variant, uppercase. No shape check here —
    whether the cleaned result matches ``REGISTRATION_PATTERN`` is validate's call, not this
    stage's; a badly-shaped-but-printed value is still a value normalize converted.
    """
    folded = fold(raw)
    folded = _WHITESPACE_RUN.sub("", folded)
    for ch in _HYPHEN_VARIANTS:
        folded = folded.replace(ch, "")
    return folded.upper()


def to_tax_code(mark: str) -> str | None:
    """A NON-empty per-line 税率 mark -> the API's tax code, or None if it maps to nothing."""
    folded = fold(mark)

    # A printed rate is the more specific signal, so it is read first and it is final: a mark
    # reading '軽減税率5%' must not fall through to the keyword branch and become T08 on the
    # strength of the word alone. An unrecognised rate fails closed to None.
    percent_match = _TAX_PERCENT_PATTERN.search(folded)
    if percent_match:
        rate = int(percent_match.group(1))
        return {10: "T10", 8: "T08"}.get(rate)

    # No rate printed, only the reduced-rate marker: '※' and '軽減税率' both mean 8%. Matched
    # by containment because they commonly appear inside a longer cell ('※軽減税率対象').
    if "軽減" in folded or "※" in folded:
        return "T08"

    return None
