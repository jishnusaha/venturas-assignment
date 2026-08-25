"""Client for the mock accounting API (``accounting_api.py``). Owns every network call the
pipeline makes to that API — nothing else in ``ingest`` may reach the network.

**Fail closed.** Every function here raises :class:`AccountingAPIError` on an unreachable
server, a non-2xx status, an unreadable body, or an envelope with ``success: false`` — never
on returning an empty list to paper over the failure. That distinction matters because an
empty result is not always a problem: :func:`get_registered_invoices` legitimately returns
``[]`` the first time the mock API runs, before anything has been registered. What must never
happen is a *failed* call being indistinguishable from that legitimate empty state. An empty
partner master would silently fail every supplier lookup in validate, and a silently-empty
duplicate ledger would let a double payment through — both are exactly the failure this
pipeline exists to prevent, so both raise rather than returning ``[]``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from ingest.schema import Partner

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

logger = logging.getLogger(__name__)

BASE_URL = os.environ.get("ACCOUNTING_API_URL", "http://localhost:8080").rstrip("/")
API_KEY = os.environ.get("ACCOUNTING_API_KEY", "demo-key-1234")
REQUEST_TIMEOUT_SECONDS = 10


class AccountingAPIError(Exception):
    """Raised for every way this client can fail to get a trustworthy answer: connection
    refused, timeout, a non-2xx status, an unreadable body, or an envelope with
    ``success: false``. Carries the API's own ``code`` when there is one (e.g.
    ``"UNAUTHORIZED"``, ``"PARTNER_NOT_FOUND"``) so a caller can react to it without parsing
    ``str(exc)``.
    """

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _request(method: str, path: str) -> Any:
    """One HTTP call to the accounting API, envelope-unwrapped, timeout enforced.

    Every response — success or failure — arrives as ``{"success", "data", "error"}``; this
    is the one place that shape gets interpreted. A transport failure, a non-2xx status, an
    unreadable body, and ``success: false`` in an otherwise-200 body all become
    :class:`AccountingAPIError` alike, so every caller checks one thing (did this raise?)
    instead of three.
    """
    url = f"{BASE_URL}{path}"
    headers = {"X-API-Key": API_KEY}
    try:
        response = requests.request(
            method, url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        logger.exception("accounting API request failed: %s %s", method, url)
        raise AccountingAPIError(
            f"Could not reach the accounting API at {url}. Is accounting_api.py running?"
        ) from exc

    try:
        body = response.json()
    except ValueError as exc:
        logger.exception("accounting API returned a non-JSON response: %s %s", method, url)
        raise AccountingAPIError(
            f"The accounting API at {url} returned a response that was not valid JSON "
            f"(HTTP {response.status_code})."
        ) from exc

    if not response.ok or not body.get("success"):
        error = body.get("error") or {}
        code = error.get("code", "UNKNOWN")
        message = error.get("message", f"HTTP {response.status_code}")
        logger.error("accounting API error on %s %s: %s %s", method, url, code, message)
        raise AccountingAPIError(message, code=code)

    return body.get("data")


def health() -> dict[str, Any]:
    """``GET /health`` — the reachability probe, called before anything else at startup."""
    return _request("GET", "/health")


def get_partners() -> list[Partner]:
    """``GET /partners`` — the supplier master, fetched fresh every run so a rename or a new
    partner takes effect immediately. Raises rather than returning ``[]`` if the master comes
    back empty — see the module docstring for why that specific failure mode is fatal.
    """
    data = _request("GET", "/partners")
    partners_raw = (data or {}).get("partners")
    if not partners_raw:
        raise AccountingAPIError(
            "The accounting API returned an empty supplier master; every supplier lookup "
            "would fail silently if this were treated as a normal result."
        )
    return [Partner.model_validate(row) for row in partners_raw]


def get_registered_invoices() -> list[dict[str, Any]]:
    """``GET /invoices`` — the current registration ledger, used to seed validate's duplicate
    check. An empty ledger (a fresh mock API, nothing registered yet) is a legitimate result
    and is returned as ``[]``. What raises instead is a malformed response — no ``invoices``
    key at all — since that is indistinguishable from a real empty ledger unless it is
    caught here.
    """
    data = _request("GET", "/invoices")
    invoices = (data or {}).get("invoices")
    if invoices is None:
        raise AccountingAPIError(
            "The accounting API returned a malformed /invoices response (no 'invoices' key)."
        )
    return invoices
