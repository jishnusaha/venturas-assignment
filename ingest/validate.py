"""Validate: the gate between normalize and registration.

Produces a verdict with a *named reason*, not a boolean. This stage makes no network call
and is a pure function of its arguments — same ``invoice``, ``partner_index`` and
``seen_keys``, same verdict, reproducible from ``output/normalize.json`` alone.
``DECISIONS.md`` pins normalize as network-free for exactly this reason ("everything after
extract is reproducible from output/extract.json at zero API cost"); the same logic binds
here, so the partner master and the duplicate ledger arrive as arguments the caller fetched
once at startup (see ``ingest/accounting.py``), rather than this module fetching them itself.

Every check below runs even after an earlier one has already failed the invoice, and every
issue found is collected rather than only the first — see :class:`~ingest.schema.
ValidationIssue` and :class:`~ingest.schema.FailedValidation`. A check whose own inputs are
already missing or already reported is skipped rather than evaluated on garbage: reporting
``subtotal_mismatch`` against a sum that includes an unreadable line amount would tell a
reviewer nothing they do not already know from the ``missing_line_field`` issue on that line.
"""

from __future__ import annotations

import logging
import math

from ingest.convert import REGISTRATION_PATTERN, normalize_registration_no
from ingest.schema import (
    FailedValidation,
    NormalizedInvoice,
    NormalizedLine,
    Partner,
    RegistrationLine,
    RegistrationPayload,
    SupplierResolution,
    ValidatedInvoice,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

# The API rejects an empty 単位; this value enters no arithmetic anywhere, so it is safe to
# supply. See _resolve_units.
DEFAULT_UNIT = "該当なし"

# Mirrors accounting_api.py:106 (TAX_RATES) exactly — the same rates, the same two codes.
TAX_RATES = {"T10": 0.10, "T08": 0.08}


def build_partner_index(partners: list[Partner]) -> dict[str, Partner]:
    """Index the supplier master by normalized 登録番号, once per run.

    Raises if two partners normalize to the same key. That is a master-data problem to
    surface loudly at startup — never one to resolve silently at match time by picking
    whichever partner happened to be inserted first (document-extract.md §3.3).
    """
    index: dict[str, Partner] = {}
    for partner in partners:
        key = normalize_registration_no(partner.registration_no)
        if key in index:
            raise ValueError(
                f"Two partners in the supplier master normalize to the same registration "
                f"number '{key}': {index[key].partner_code} ({index[key].name}) and "
                f"{partner.partner_code} ({partner.name}). Fix the master data before "
                "running validate."
            )
        index[key] = partner
    return index


def _resolve_units(lines: list[NormalizedLine]) -> tuple[dict[int, str], list[str]]:
    """Fill a blank 単位 with :data:`DEFAULT_UNIT`.

    The accounting API rejects an empty ``unit`` string, and this value enters no
    arithmetic anywhere downstream — it is inert, unlike a defaulted amount or tax code
    would be. One note names every affected row rather than one note per row: an invoice
    with dozens of blank units would otherwise bury a real issue under near-identical noise
    (see DECISIONS.md, "Column-wide defaults are recorded once per invoice, not once per
    row" — the same reasoning normalize used to reject before defaults moved here).
    """
    resolved: dict[int, str] = {}
    defaulted_rows: list[int] = []
    for line in lines:
        if line.unit:
            resolved[line.index] = line.unit
        else:
            resolved[line.index] = DEFAULT_UNIT
            defaulted_rows.append(line.index)

    notes: list[str] = []
    if defaulted_rows:
        rows = ", ".join(str(index) for index in defaulted_rows)
        notes.append(
            f"単位 (unit) was blank on line(s) {rows}; defaulted to '{DEFAULT_UNIT}' since "
            "nothing was printed there and the accounting API requires a non-empty value."
        )
    return resolved, notes


def _resolve_tax_codes(
    lines: list[NormalizedLine], printed_tax_amount: int
) -> tuple[dict[int, str] | None, list[str]]:
    """Fill in every line's tax code, deriving one where nothing was printed.

    This is the crux of the stage. 9 of the 10 sample invoices that reach validate print no
    per-line 税率 column at all — without deriving something, nothing registers. But a
    *defaulted* code would be a value no human ever printed and no reviewer ever sees
    questioned, which is exactly what DECISIONS.md rules out everywhere else in this
    pipeline ("Normalize invents nothing"). The difference here is that a derived code is
    not a fallback: it is only accepted once the tax recomputed from the resulting per-code
    split equals the printed 消費税, which is a second, independent, human-printed figure.
    A wrong guess becomes a visible mismatch (``tax_mismatch``) instead of a silent
    registration, so deriving is safe in a way that defaulting never was.

    When every line already carries a tax_code from a printed per-line 税率 mark, those are
    used as-is and nothing is derived — this is what lets a mixed-rate invoice such as
    invoice_03.pdf (T08 and T10 lines together) register on its own printed marks with no
    note at all. Otherwise, a single candidate code is tried for every line still missing
    one — T10 first, then T08, per document-extract.md §3.3 — and accepted only if it
    reconciles. Neither candidate reconciling returns ``None``, which the caller reports as
    ``tax_code_unresolved``.
    """
    missing = [line for line in lines if line.tax_code is None]
    if not missing:
        return {line.index: line.tax_code for line in lines if line.tax_code is not None}, []

    for candidate in ("T10", "T08"):
        trial = {line.index: (line.tax_code or candidate) for line in lines}
        subtotal_by_code: dict[str, int] = {}
        for line in lines:
            code = trial[line.index]
            subtotal_by_code[code] = subtotal_by_code.get(code, 0) + (line.amount or 0)
        computed_tax = sum(
            math.floor(subtotal * TAX_RATES[code]) for code, subtotal in subtotal_by_code.items()
        )
        if computed_tax == printed_tax_amount:
            rows = ", ".join(str(line.index) for line in missing)
            note = (
                f"税率 (tax code) was not printed on line(s) {rows}; {candidate} was derived "
                "and confirmed against this invoice's printed 消費税 — no per-line rate was "
                "read there."
            )
            return trial, [note]

    return None, []


def validate(
    invoice: NormalizedInvoice,
    partner_index: dict[str, Partner],
    seen_keys: set[tuple[str, str]] | None = None,
) -> ValidatedInvoice | FailedValidation:
    """Run every check in order, collecting every issue that can be evaluated.

    Checks 1-8 mirror document-extract.md §3.3. When ``issues`` is non-empty at the end,
    ``reason``/``detail`` on the returned :class:`~ingest.schema.FailedValidation` are taken
    from ``issues[0]`` — the first problem found, in check order — while ``issues`` itself
    carries everything found, so one human review can fix all of it rather than discovering
    problems one re-run at a time.
    """
    issues: list[ValidationIssue] = []
    notes: list[str] = []

    # 1. Supplier resolution. Carries the most weight of any check here: partner_code
    # decides who receives the money, and it is the one field whose being wrong produces no
    # API error at all — a bad total gets a 422, a bad date gets a 400, but a wrong partner
    # registers cleanly and pays the wrong company. Absent, malformed, and not-in-master all
    # collapse to the same reason deliberately: the invoice needs a human either way, and
    # `detail` says which of the three it was.
    registration_no = invoice.registration_no
    partner: Partner | None = None
    if not registration_no:
        issues.append(
            ValidationIssue(
                code="supplier_not_in_master",
                field="registration_no",
                detail=(
                    "No 登録番号 (supplier registration number) could be read from this "
                    "invoice, so the supplier could not be identified automatically. "
                    "Please confirm the supplier and key this invoice by hand."
                ),
            )
        )
    elif not REGISTRATION_PATTERN.fullmatch(registration_no):
        issues.append(
            ValidationIssue(
                code="supplier_not_in_master",
                field="registration_no",
                detail=(
                    f"The 登録番号 printed on this invoice ('{invoice.source.registration_no_raw}') "
                    "is not in the expected 'T' + 13 digit format, so the supplier could not be "
                    "matched automatically. Please confirm the supplier and key this invoice by hand."
                ),
            )
        )
    else:
        partner = partner_index.get(registration_no)
        if partner is None:
            issues.append(
                ValidationIssue(
                    code="supplier_not_in_master",
                    field="registration_no",
                    detail=(
                        f"The 登録番号 '{registration_no}' printed on this invoice does not "
                        "match any supplier in the accounting master. Please confirm the "
                        "supplier and key this invoice by hand."
                    ),
                )
            )

    # 2. Shape / completeness — mirrors accounting_api.py's _check_shape (133-201).
    if not invoice.invoice_number:
        issues.append(
            ValidationIssue(
                code="missing_invoice_number",
                field="invoice_number",
                detail=(
                    "The 請求書番号 (invoice number) could not be read from this invoice. "
                    "Please check the document and key it by hand."
                ),
            )
        )

    if invoice.issue_date is None:
        issues.append(
            ValidationIssue(
                code="missing_issue_date",
                field="issue_date",
                detail=(
                    "The 発行日 (issue date) could not be read from this invoice. Please "
                    "check the document and key it by hand."
                ),
            )
        )

    if invoice.due_date is None:
        issues.append(
            ValidationIssue(
                code="missing_due_date",
                field="due_date",
                detail=(
                    "The お支払期日 (payment due date) could not be read from this invoice. "
                    "Please check the document and key it by hand."
                ),
            )
        )

    lines_ok = bool(invoice.lines)
    if not invoice.lines:
        issues.append(
            ValidationIssue(
                code="no_line_items",
                field="lines",
                detail=(
                    "No line items could be read from this invoice. Please check the "
                    "document and key it by hand."
                ),
            )
        )

    for line in invoice.lines:
        if not line.description:
            lines_ok = False
            issues.append(
                ValidationIssue(
                    code="missing_line_field",
                    field=f"lines[{line.index}].description",
                    detail=(
                        f"Row {line.index + 1} of the 品名・摘要 (line-item) table has no "
                        "readable description. Please check the document and key it by hand."
                    ),
                )
            )
        if line.amount is None:
            lines_ok = False
            issues.append(
                ValidationIssue(
                    code="missing_line_field",
                    field=f"lines[{line.index}].amount",
                    detail=(
                        f"Row {line.index + 1} of the line-item table has no readable 金額 "
                        "(amount). Please check the document and key it by hand."
                    ),
                )
            )

    if invoice.subtotal is None:
        issues.append(
            ValidationIssue(
                code="missing_printed_amount",
                field="subtotal",
                detail=(
                    "No 小計 (subtotal) is printed on this invoice, so the arithmetic cannot "
                    "be checked. Please check the document and key it by hand."
                ),
            )
        )
    if invoice.tax_amount is None:
        issues.append(
            ValidationIssue(
                code="missing_printed_amount",
                field="tax_amount",
                detail=(
                    "No 消費税 (tax amount) is printed on this invoice, so the arithmetic "
                    "cannot be checked. Please check the document and key it by hand."
                ),
            )
        )
    if invoice.total_amount is None:
        issues.append(
            ValidationIssue(
                code="missing_printed_amount",
                field="total_amount",
                detail=(
                    "No 合計 (total) is printed on this invoice, so the arithmetic cannot be "
                    "checked. Please check the document and key it by hand."
                ),
            )
        )

    # 3. Unit default — always runs; it is inert and cannot itself produce an issue.
    resolved_units, unit_notes = _resolve_units(invoice.lines)
    notes.extend(unit_notes)

    # 4. Tax code resolution — only meaningful once every line amount is readable and the
    # printed 消費税 is present to check a derived candidate against.
    resolved_tax_codes: dict[int, str] | None = None
    if lines_ok and invoice.tax_amount is not None:
        resolved_tax_codes, tax_notes = _resolve_tax_codes(invoice.lines, invoice.tax_amount)
        if resolved_tax_codes is None:
            issues.append(
                ValidationIssue(
                    code="tax_code_unresolved",
                    field="tax_amount",
                    detail=(
                        "No consumption-tax rate (10% or 8%) could be derived that matches "
                        "the 消費税 (tax amount) printed on this invoice. Please check the "
                        "tax rate against the document by hand."
                    ),
                )
            )
        else:
            notes.extend(tax_notes)

    # 5. Arithmetic — mirrors accounting_api.py:236-278 exactly, including math.floor per
    # tax code. Independent of step 4's derivation: the total check compares the printed
    # figure against subtotal + recomputed tax regardless of how the tax code was resolved,
    # so a printed total that is off by even ¥1 (invoice_09.pdf: ¥147,497 printed vs
    # ¥147,496 recomputed) fails as total_mismatch, not as an unresolved tax code.
    computed_subtotal: int | None = None
    computed_tax: int | None = None

    if lines_ok:
        computed_subtotal = sum(line.amount or 0 for line in invoice.lines)
        if invoice.subtotal is not None and computed_subtotal != invoice.subtotal:
            issues.append(
                ValidationIssue(
                    code="subtotal_mismatch",
                    field="subtotal",
                    detail=(
                        f"The 小計 (subtotal) printed on this invoice is ¥{invoice.subtotal:,}, "
                        f"but the line items add up to ¥{computed_subtotal:,}. Please recheck "
                        "the line items against the document."
                    ),
                )
            )

    if lines_ok and resolved_tax_codes is not None:
        subtotal_by_code: dict[str, int] = {}
        for line in invoice.lines:
            code = resolved_tax_codes[line.index]
            subtotal_by_code[code] = subtotal_by_code.get(code, 0) + (line.amount or 0)
        computed_tax = sum(
            math.floor(subtotal * TAX_RATES[code]) for code, subtotal in subtotal_by_code.items()
        )
        if invoice.tax_amount is not None and computed_tax != invoice.tax_amount:
            issues.append(
                ValidationIssue(
                    code="tax_mismatch",
                    field="tax_amount",
                    detail=(
                        f"The 消費税 (tax amount) printed on this invoice is "
                        f"¥{invoice.tax_amount:,}, but the tax recalculated from the line "
                        f"items is ¥{computed_tax:,}. Please recheck the tax amount against "
                        "the document."
                    ),
                )
            )

    if (
        computed_subtotal is not None
        and computed_tax is not None
        and invoice.total_amount is not None
    ):
        computed_total = computed_subtotal + computed_tax
        if computed_total != invoice.total_amount:
            issues.append(
                ValidationIssue(
                    code="total_mismatch",
                    field="total_amount",
                    detail=(
                        f"The 合計 (total) printed on this invoice is "
                        f"¥{invoice.total_amount:,}, but the subtotal and tax add up to "
                        f"¥{computed_total:,}. Please recheck the total against the document."
                    ),
                )
            )

    # 6. Business rule: due_date >= issue_date. Mirrors accounting_api.py:227-234.
    if (
        invoice.issue_date is not None
        and invoice.due_date is not None
        and invoice.due_date < invoice.issue_date
    ):
        issues.append(
            ValidationIssue(
                code="due_date_before_issue_date",
                field="due_date",
                detail=(
                    f"The お支払期日 (payment due date) of {invoice.due_date.isoformat()} is "
                    f"earlier than the 発行日 (issue date) of {invoice.issue_date.isoformat()}. "
                    "Please recheck both dates against the document."
                ),
            )
        )

    # 7. Date agreement: our parser's reading vs the extraction model's own reading of the
    # same printed string. Disagreement means one of two independent mechanisms is wrong and
    # we cannot tell which, so it goes to a human. The model reading None while ours is
    # present is not a failure — it means the model abstained on a term it could not resolve
    # (e.g. a relative due date like 翌月末日), not that it contradicted our reading.
    if invoice.issue_date is not None:
        if invoice.issue_date_model is not None and invoice.issue_date != invoice.issue_date_model:
            issues.append(
                ValidationIssue(
                    code="date_disagreement",
                    field="issue_date",
                    detail=(
                        "Two independent readings of the 発行日 (issue date) on this invoice "
                        f"disagree ({invoice.issue_date.isoformat()} vs "
                        f"{invoice.issue_date_model.isoformat()}). Please confirm the issue "
                        "date against the document."
                    ),
                )
            )
        elif invoice.issue_date_model is None:
            notes.append(
                "発行日 (issue date) could not be independently confirmed by a second "
                "reading; only one reading was available."
            )

    if invoice.due_date is not None:
        if invoice.due_date_model is not None and invoice.due_date != invoice.due_date_model:
            issues.append(
                ValidationIssue(
                    code="date_disagreement",
                    field="due_date",
                    detail=(
                        "Two independent readings of the お支払期日 (payment due date) on "
                        f"this invoice disagree ({invoice.due_date.isoformat()} vs "
                        f"{invoice.due_date_model.isoformat()}). Please confirm the due date "
                        "against the document."
                    ),
                )
            )
        elif invoice.due_date_model is None:
            notes.append(
                "お支払期日 (payment due date) could not be independently confirmed by a "
                "second reading; only one reading was available."
            )

    # 8. Duplicate: (partner_code, invoice_number) already seen this run or already
    # registered. This is the check that catches the client's near-miss — invoice_01.pdf and
    # invoice_07.jpg print the same partner and the same invoice number YM-2026-0107.
    if seen_keys and partner is not None and invoice.invoice_number:
        key = (partner.partner_code, invoice.invoice_number)
        if key in seen_keys:
            issues.append(
                ValidationIssue(
                    code="duplicate_invoice",
                    field="invoice_number",
                    detail=(
                        f"An invoice numbered '{invoice.invoice_number}' for {partner.name} "
                        f"({partner.partner_code}) has already been registered or seen "
                        "earlier in this run. Please confirm this is not the same invoice "
                        "before it is paid twice."
                    ),
                )
            )

    if issues:
        first = issues[0]
        return FailedValidation(
            file_name=invoice.file_name,
            file_path=invoice.file_path,
            reason=first.code,
            detail=first.detail,
            issues=issues,
        )

    # Reaching here means no issue was collected, which by construction of the checks above
    # guarantees partner, resolved_tax_codes, both dates, all three printed amounts and every
    # line's description/amount are populated — each one being absent appends its own issue.
    # Nothing re-checks that here: RegistrationPayload and RegistrationLine declare those
    # fields non-optional (issue_date: date, subtotal: int, tax_code: Literal[...]), so if the
    # invariant were ever broken by a future edit, pydantic raises a ValidationError naming the
    # exact field. That is a real net — unlike an assert, it is not stripped under `python -O`.
    payload_lines = [
        RegistrationLine(
            description=line.description,
            quantity=line.quantity,
            unit=resolved_units[line.index],
            unit_price=line.unit_price,
            # Deliberately not `line.amount or 0`: this is the figure that reaches the
            # accounting system, so an unreadable amount must become a loud ValidationError,
            # never a silent ¥0 line that registers and reconciles against nothing.
            amount=line.amount,
            tax_code=resolved_tax_codes[line.index],
        )
        for line in invoice.lines
    ]

    payload = RegistrationPayload(
        partner_code=partner.partner_code,
        invoice_number=invoice.invoice_number,
        issue_date=invoice.issue_date,
        due_date=invoice.due_date,
        lines=payload_lines,
        subtotal=invoice.subtotal,
        tax_amount=invoice.tax_amount,
        total_amount=invoice.total_amount,
    )

    supplier = SupplierResolution(
        raw=invoice.source.registration_no_raw,
        normalized=registration_no,
        partner_code=partner.partner_code,
        partner_name=partner.name,
        matched_by="registration_no",
    )

    return ValidatedInvoice(
        file_name=invoice.file_name,
        file_path=invoice.file_path,
        source=invoice,
        supplier=supplier,
        payload=payload,
        notes=notes,
    )
