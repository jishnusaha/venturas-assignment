"""Normalize: verbatim printed strings -> the representations the accounting API accepts.

This stage only converts. An unparsable date, an unreadable amount, an unmappable tax mark
and a blank cell all come out as ``None``; whether that matters is validate's question.
``source`` on :class:`~ingest.schema.NormalizedInvoice` keeps the printed string next to
every converted value, which is what tells "nothing was printed" from "could not read it".
"""

from __future__ import annotations

import logging
from datetime import date

from ingest.convert import (
    fold,
    normalize_registration_no,
    parse_date,
    parse_number,
    parse_relative_due_date,
    to_int,
    to_tax_code,
)
from ingest.schema import (
    ExtractedInvoice,
    ExtractedLine,
    FailedNormalization,
    NormalizedInvoice,
    NormalizedLine,
)

logger = logging.getLogger(__name__)


def _normalize_amount(raw: str) -> int | None:
    if not fold(raw):
        return None
    return to_int(parse_number(raw))


def _normalize_model_date(raw_iso: str) -> date | None:
    # The model's own reading, carried for validate to compare against ours. Never repaired.
    if not fold(raw_iso):
        return None
    return parse_date(raw_iso)


def _normalize_line(index: int, line: ExtractedLine) -> NormalizedLine:
    description = fold(line.description_raw)
    amount = _normalize_amount(line.amount_raw)

    # Blank 数量/単価 is normal on a 式 service line, and the API takes null for both.
    quantity = None
    if fold(line.quantity_raw):
        quantity = to_int(parse_number(line.quantity_raw))

    unit_price = None
    if fold(line.unit_price_raw):
        unit_price = to_int(parse_number(line.unit_price_raw))

    unit = fold(line.unit_raw)

    tax_code = None
    if fold(line.tax_mark):
        tax_code = to_tax_code(line.tax_mark)

    return NormalizedLine(
        index=index,
        description=description,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        amount=amount,
        tax_code=tax_code,
    )


def normalize(invoice: ExtractedInvoice) -> FailedNormalization | NormalizedInvoice:
    extraction = invoice.extraction

    invoice_number = fold(extraction.invoice_number_raw)
    registration_no = normalize_registration_no(extraction.registration_no_raw)

    # For the reviewer only — the partner_code lookup belongs to the next stage.
    supplier_name = fold(extraction.supplier_name_raw)

    try:
        issue_date = None
        if fold(extraction.issue_date_raw):
            issue_date = parse_date(extraction.issue_date_raw)
        issue_date_model = _normalize_model_date(extraction.issue_date_iso)
    except Exception:
        logger.exception("could not read the issue date on %s", invoice.file_name)
        return FailedNormalization(
            file_name=invoice.file_name,
            file_path=invoice.file_path,
            reason="unreadable_issue_date",
            detail=(
                "The issue date (発行日) on this invoice could not be read. Please check it "
                "against the document."
            ),
        )

    try:
        due_date = None
        if fold(extraction.due_date_raw):
            due_date = parse_date(extraction.due_date_raw)
            if due_date is None and issue_date is not None:
                # Relative terms ('翌月末払い') are counted from the issue date.
                due_date = parse_relative_due_date(extraction.due_date_raw, issue_date)
        due_date_model = _normalize_model_date(extraction.due_date_iso)
    except Exception:
        logger.exception("could not read the due date on %s", invoice.file_name)
        return FailedNormalization(
            file_name=invoice.file_name,
            file_path=invoice.file_path,
            reason="unreadable_due_date",
            detail=(
                "The payment due date (お支払期日) on this invoice could not be read. Please "
                "check it against the document."
            ),
        )

    # Never recomputed from the lines: validate needs the printed figure as its own check.
    subtotal = _normalize_amount(extraction.subtotal_raw)
    tax_amount = _normalize_amount(extraction.tax_raw)
    total_amount = _normalize_amount(extraction.total_raw)

    normalized_lines = [
        _normalize_line(index, line) for index, line in enumerate(extraction.lines)
    ]

    return NormalizedInvoice(
        file_name=invoice.file_name,
        file_path=invoice.file_path,
        source=extraction,
        registration_no=registration_no,
        supplier_name=supplier_name,
        invoice_number=invoice_number,
        issue_date=issue_date,
        issue_date_model=issue_date_model,
        due_date=due_date,
        due_date_model=due_date_model,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        lines=normalized_lines,
    )
