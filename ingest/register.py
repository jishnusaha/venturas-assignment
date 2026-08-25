"""Register: POSTs each validated invoice to the accounting API and records the outcome.

This is the only stage that changes state outside this process, and the only one that
cannot be undone: the accounting API has no update endpoint, and its sole DELETE clears the
entire ledger (``accounting_api.py:410-421``), not one invoice.

``main.py`` calls it inline, the moment an invoice passes validate, rather than as a second
pass after the whole batch has been read. The tradeoff that buys and costs is recorded at
that call site: registration starts before ``output/*.json`` exists, so a crash mid-run can
leave invoices in the ledger with no audit file describing why they were sent, and
``GET /invoices`` is the recovery path.

Unlike validate, this stage makes a network call by design. That is not a leak of the
"no network below extract" rule ``DECISIONS.md`` pins for normalize and validate — it is
this stage's whole purpose. Every invoice reaching here already passed every check this
pipeline knows how to run locally; the only thing left to learn is whether the accounting
system agrees, and that answer only exists on the other side of ``POST /invoices``.
"""

from __future__ import annotations

import logging

from ingest.accounting import AccountingAPIError, register_invoice
from ingest.schema import (
    FailedRegistration,
    RegisteredInvoice,
    RegistrationReason,
    SkippedRegistration,
    ValidatedInvoice,
)

logger = logging.getLogger(__name__)

# Maps the accounting API's own error codes (accounting_api.py:111-119, STATUS_BY_CODE) to
# this pipeline's RegistrationReason. DUPLICATE_INVOICE is handled separately in register()
# since it routes to SkippedRegistration, not FailedRegistration, and so is not in this map.
_REASON_BY_CODE: dict[str, RegistrationReason] = {
    "PARTNER_NOT_FOUND": "partner_not_found",
    "UNKNOWN_TAX_CODE": "unknown_tax_code",
    "DUE_DATE_BEFORE_ISSUE_DATE": "due_date_before_issue_date",
    "AMOUNT_MISMATCH": "amount_mismatch",
    "VALIDATION_ERROR": "invalid_payload",
    "UNAUTHORIZED": "unauthorized",
    "NOT_FOUND": "endpoint_not_found",
    "MALFORMED_RESPONSE": "unconfirmed_registration",
}


def _detail_for(reason: RegistrationReason, invoice: ValidatedInvoice) -> str:
    """Clerk-facing plain language for one registration reason — what to DO, never an
    exception type or a stack trace, per the standing decision that `detail` is written for
    the accounting clerk. Names the invoice number and supplier where it helps the clerk
    find the document.
    """
    who = f"invoice {invoice.payload.invoice_number} ({invoice.supplier.partner_name})"

    if reason == "api_unreachable":
        return (
            f"Could not confirm whether {who} was registered — the accounting API gave no "
            "usable answer, either because it did not respond at all or because what came "
            "back could not be read. Check whether this invoice is already in the "
            "accounting system before re-running, since the request may still have been "
            "received."
        )
    if reason == "unconfirmed_registration":
        return (
            f"The accounting system accepted {who}, but its confirmation could not be read. "
            "It is almost certainly registered — confirm it in the ledger and do not re-send."
        )
    if reason == "amount_mismatch":
        return (
            f"The accounting system rejected {who}: the subtotal, tax, or total it "
            "recalculated from the line items does not match the figures sent, even though "
            "this pipeline's own arithmetic check agreed with them. This invoice needs "
            "keying by hand, and the disagreement needs reporting."
        )
    if reason == "partner_not_found":
        return (
            f"The accounting system rejected {who}: the partner_code "
            f"'{invoice.payload.partner_code}' is not recognized, even though this pipeline "
            "matched it against its own copy of the supplier master. This invoice needs "
            "keying by hand, and the disagreement needs reporting."
        )
    if reason == "unknown_tax_code":
        return (
            f"The accounting system rejected {who}: it does not recognize one of the tax "
            "codes on the line items, even though this pipeline's own check accepted them. "
            "This invoice needs keying by hand, and the disagreement needs reporting."
        )
    if reason == "due_date_before_issue_date":
        return (
            f"The accounting system rejected {who}: it says the due date is before the "
            "issue date, even though this pipeline's own check agreed the dates were in "
            "order. This invoice needs keying by hand, and the disagreement needs reporting."
        )
    if reason == "invalid_payload":
        return (
            f"The accounting system rejected {who} as malformed, even though this "
            "pipeline's own checks accepted it. This invoice needs keying by hand, and the "
            "disagreement needs reporting."
        )
    if reason in ("unauthorized", "endpoint_not_found"):
        return (
            f"{who} was not registered because of a configuration problem talking to the "
            "accounting API, not a problem with the document itself. Nothing is wrong with "
            "this invoice — re-run once the API URL/key is corrected."
        )
    # unexpected_error
    return (
        f"The accounting system rejected {who} with an error this pipeline does not "
        "recognize. This invoice needs keying by hand, and the disagreement needs reporting."
    )


def register(
    invoice: ValidatedInvoice,
) -> RegisteredInvoice | SkippedRegistration | FailedRegistration:
    """POST one validated invoice to the accounting API and route the outcome.

    ``RegistrationPayload`` is postable verbatim with no reshaping — that is the property it
    was designed to have (see its docstring) — so the body sent here is exactly
    ``invoice.payload``, dumped to the JSON-compatible shape the API expects.
    """
    payload = invoice.payload.model_dump(mode="json")

    try:
        record = register_invoice(payload)
    except AccountingAPIError as exc:
        if exc.code == "DUPLICATE_INVOICE":
            logger.info(
                "invoice %s (%s) already registered — accounting API returned 409",
                invoice.payload.invoice_number,
                invoice.supplier.partner_code,
            )
            return SkippedRegistration(
                file_name=invoice.file_name,
                file_path=invoice.file_path,
                reason="duplicate_invoice",
                detail=(
                    f"Invoice {invoice.payload.invoice_number} ({invoice.supplier.partner_name}) "
                    "was already registered in the accounting system. Compare the attached "
                    "payload against the existing ledger entry to confirm it is the same "
                    "invoice before assuming this is a duplicate submission."
                ),
                payload=invoice.payload,
                http_status=exc.status_code or 409,
            )

        # code is None for a transport failure (no response at all); _request uses "UNKNOWN"
        # when a response body carries no code at all. Handled explicitly, rather than left
        # to a dict miss, so the two stay distinguishable in the reason chosen below.
        if exc.code is None:
            reason: RegistrationReason = "api_unreachable"
        else:
            reason = _REASON_BY_CODE.get(exc.code, "unexpected_error")

        logger.error(
            "registration failed for invoice %s (%s): code=%s status=%s message=%s",
            invoice.payload.invoice_number,
            invoice.supplier.partner_code,
            exc.code,
            exc.status_code,
            exc.message,
        )
        return FailedRegistration(
            file_name=invoice.file_name,
            file_path=invoice.file_path,
            reason=reason,
            detail=_detail_for(reason, invoice),
            http_status=exc.status_code,
            api_code=exc.code,
            api_message=exc.message,
            payload=invoice.payload,
        )

    return RegisteredInvoice(
        file_name=invoice.file_name,
        file_path=invoice.file_path,
        source=invoice,
        record=record,
        http_status=201,
    )
