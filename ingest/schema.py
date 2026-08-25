from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ExtractedLine(BaseModel):
    """One row of the 品名・摘要 table, exactly as printed."""

    description_raw: str = Field(description="品名・摘要 text, verbatim.")
    quantity_raw: str = Field(
        description="数量 as printed, or an empty string if the cell is blank."
    )
    unit_raw: str = Field(
        description="単位 as printed (個, 式, 箱, セット, 時間, 件, 本, 袋 ...), or an empty string if blank."
    )
    unit_price_raw: str = Field(
        description="単価 as printed, or an empty string if the cell is blank."
    )
    amount_raw: str = Field(
        description="金額 as printed, including any △ or ▲ sign and any ¥ or comma."
    )
    tax_mark: str = Field(
        description=(
            "The tax rate shown for THIS line, verbatim: '10%', '8%', '※', '軽減税率', "
            "or an empty string when the table has no per-line 税率 column."
        )
    )


class ExtractionFlags(BaseModel):
    """What the model noticed about the document as an output, not its contents."""

    has_handwriting: bool = Field(
        description=(
            "True if ANY handwriting, handwritten stamp, or hand-drawn mark appears "
            "anywhere on the document, including marks that do not touch the invoice "
            "figures at all."
        )
    )
    handwriting_note: str = Field(
        description="Where the handwriting is and what it appears to say. Empty string when there is none."
    )
    illegible_fields: list[str] = Field(
        default_factory=list,
        description="Names of fields that could not be read at all (blur, crop, ink).",
    )
    low_confidence_fields: list[str] = Field(
        default_factory=list,
        description="Names of fields that were read but with genuine doubt about a character.",
    )


class InvoiceExtraction(BaseModel):
    """The complete structured reading of one invoice document."""

    registration_no_raw: str = Field(
        description="登録番号 as printed (normally 'T' followed by 13 digits). Empty string if absent."
    )
    supplier_name_raw: str = Field(
        description="The supplier's own name as printed. Recorded for the human reviewer; never used to pick a partner_code."
    )
    invoice_number_raw: str = Field(description="請求書番号 as printed.")

    issue_date_raw: str = Field(
        description="発行日 as printed, verbatim (e.g. '令和8年2月5日', '2026/01/18')."
    )
    issue_date_iso: str = Field(
        description="Your own reading of issue_date_raw as YYYY-MM-DD. Empty string if you cannot resolve it."
    )
    due_date_raw: str = Field(
        description="お支払期日 as printed, verbatim. May be a payment term such as '翌月末日' rather than a date."
    )
    due_date_iso: str = Field(
        description="Your own reading of due_date_raw as YYYY-MM-DD. Empty string if you cannot resolve it."
    )

    subtotal_raw: str = Field(
        description="小計 as printed. Empty string if the document has no 小計 row."
    )
    tax_raw: str = Field(
        description=(
            "消費税 as printed. When the document shows tax split across several rates, "
            "give the TOTAL of those rows."
        )
    )
    total_raw: str = Field(description="合計 / 御請求金額 as printed.")

    lines: list[ExtractedLine] = Field(
        description="Every row of the line-item table, in printed order, across every page."
    )
    flags: ExtractionFlags


class ExtractedInvoice(BaseModel):
    """One source document paired with the model's reading of it.

    The extraction itself is the model's contract (:class:`InvoiceExtraction`); the two
    path fields are the provenance a human reviewer needs to pull up the original page.
    """

    file_name: str = Field(
        description="The document's file name, e.g. 'invoice_08.jpg'."
    )
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    extraction: InvoiceExtraction = Field(
        description="The schema-validated reading of that document."
    )


class FailedInvoice(BaseModel):
    """A document the pipeline could not read, and what stopped it.

    A failure is a routing decision, not a log line: every one of these is a document
    a human still has to deal with, so the reason has to survive the run.
    """

    file_name: str = Field(description="The document's file name.")
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    reason: Literal["api_error", "incomplete_response", "schema_invalid"] = Field(
        description=(
            "'api_error': the call never returned a message. 'incomplete_response': a "
            "message came back carrying no parseable extraction, most often a refusal. "
            "'schema_invalid': JSON that does not satisfy the schema, i.e. truncation."
        )
    )
    detail: str = Field(
        description="The specific error, for the human who has to act on it."
    )


class ExtractionRun(BaseModel):
    """The result of one pass over the invoice directory.

    Successes and failures are kept apart so that consuming ``extracted`` never means
    checking for None, while no document can drop out of the run unaccounted for.
    """

    extracted: list[ExtractedInvoice] = Field(default_factory=list)
    failed: list[FailedInvoice] = Field(default_factory=list)


class FailedNormalization(BaseModel):
    """A conversion that could not be produced, because a converter raised.

    Unlike :class:`FailedInvoice`, this is not an expected outcome — extraction fails for
    reasons outside the pipeline's control (the API errors, the model returns nothing),
    while conversion is pure code operating on already-captured text and is not supposed to
    fail at all. An entry here means a converter raised on input it should have handled: a
    bug to fix, not a document to send back for re-reading. It still gets recorded rather
    than left to crash the run, for the same reason :func:`ingest.extract.extract` catches
    ``APIError`` per document: one malformed printed string must not take down a run that
    has already paid for every document before it.
    """

    file_name: str = Field(description="The document's file name.")
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    reason: Literal["unreadable_issue_date", "unreadable_due_date"] = Field(
        description=(
            "Which date could not be read. The two are separate so the reviewer is pointed "
            "at one field rather than told 'a date' failed. Dates are the only conversion "
            "doing calendar arithmetic, so they are the only step that can raise at all."
        )
    )
    detail: str = Field(
        description=(
            "A plain-language explanation for the accounting staff member who has to key "
            "this invoice by hand, naming the field to check against the paper. Never an "
            "exception type, stack trace, or field path — the technical cause goes to the "
            "log instead, where an engineer will look for it."
        )
    )


class NormalizedLine(BaseModel):
    """One line item after conversion, shaped for the API's ``lines[]`` entries.

    This model carries what conversion could produce from the printed row, not a guarantee
    that the row is complete — completeness is validate's job. A blank cell converts to its
    empty/None representation silently; a cell that printed something unconvertible also
    converts to None, the same as blank — normalize cannot fail, so the two are
    indistinguishable here. That distinction (and whether it matters) is recoverable from
    ``source.lines[index]``, the raw string paired with this line by ``index``.
    """

    index: int = Field(
        description="Position in the printed line-item table, so an issue can point a reviewer at a row."
    )
    description: str = Field(description="品名・摘要, folded. Empty string when the cell was blank.")
    quantity: int | None = Field(
        description="数量 as an integer, or None if the cell was blank — blank is legal, not an issue."
    )
    unit: str = Field(
        description="単位 as printed, folded. Empty string when the cell was blank — never defaulted, never a failure."
    )
    unit_price: int | None = Field(
        description="単価 as an integer, or None if the cell was blank — blank is legal, not an issue."
    )
    amount: int | None = Field(
        description="金額 as an integer, or None if the cell was blank — blank is legal, not an issue."
    )
    tax_code: Literal["T10", "T08"] | None = Field(
        description="This line's tax code, or None when no per-line 税率 was printed."
    )


class NormalizedInvoice(BaseModel):
    """One invoice after normalize: every printed field converted, none invented.

    This model carries what conversion could produce, not a guarantee that the invoice is
    complete — completeness is validate's guarantee, not this stage's. A blank field and a
    field that printed something unconvertible both come out as None here; the two are the
    same shape at this stage because normalize cannot fail and asks no question about
    acceptability. Telling them apart is what ``source`` is for: with no issue list left
    to say *why* a field is None, ``subtotal = None`` on its own is ambiguous between "no
    小計 was printed" and "one was printed and would not convert", and validate needs to
    treat those two cases differently. ``source`` is the whole verbatim
    :class:`InvoiceExtraction` this record was built from, so the raw string sits right
    next to the converted value instead of forcing a cross-reference back into
    ``output/extract.json`` — this record is a self-contained audit trail on its own.
    """

    file_name: str = Field(
        description="The document's file name, carried through from extract."
    )
    file_path: Path = Field(
        description="Absolute path to the source document on disk, carried through from extract."
    )
    source: InvoiceExtraction = Field(
        description=(
            "The verbatim extraction this record was converted from, unchanged. Every "
            "printed string this stage read — including issue_date_raw, due_date_raw, and "
            "flags — lives here rather than being duplicated onto this record, so there is "
            "exactly one copy to drift out of sync."
        )
    )

    registration_no: str = Field(
        description="登録番号, cleaned (folded, hyphens/whitespace stripped, uppercased). Empty string when blank. Shape-checked by validate, not here."
    )
    supplier_name: str = Field(
        description="Supplier's own name, folded. For the human reviewer only — never used to pick a partner_code. Empty string when blank."
    )
    invoice_number: str = Field(
        description="請求書番号, folded but not uppercased — the API's duplicate check matches it exactly. Empty string when blank."
    )

    issue_date: date | None = Field(
        description="This parser's own reading of source.issue_date_raw, or None when blank or unconvertible."
    )
    issue_date_model: date | None = Field(
        description=(
            "The extraction model's own reading of the same raw string, parsed leniently "
            "and carried through unchanged for validate's date-agreement check. None when "
            "the model's reading was blank or unparsable — a disagreement for validate to "
            "catch, not something this stage resolves."
        )
    )

    due_date: date | None = Field(
        description="This parser's own reading of source.due_date_raw, or None when blank or unconvertible."
    )
    due_date_model: date | None = Field(
        description=(
            "The extraction model's own reading of the same raw string, parsed leniently "
            "and carried through unchanged for validate's date-agreement check. None when "
            "the model's reading was blank or unparsable — a disagreement for validate to "
            "catch, not something this stage resolves."
        )
    )

    subtotal: int | None = Field(description="小計 as an integer, or None when blank or unconvertible.")
    tax_amount: int | None = Field(description="消費税 as an integer, or None when blank or unconvertible.")
    total_amount: int | None = Field(description="合計 as an integer, or None when blank or unconvertible.")

    lines: list[NormalizedLine] = Field(
        description="Every line item, converted, in printed order."
    )


class NormalizationRun(BaseModel):
    """The result of one pass over an :class:`ExtractionRun`'s successes.

    ``failed`` is not a routing decision the way :class:`ExtractionRun`'s is — an
    unconvertible printed string becomes ``None`` on the record, not a failure. It exists
    only to catch a converter raising, which should never happen and is a bug when it does;
    see :class:`FailedNormalization`.
    """

    normalized: list[NormalizedInvoice] = Field(default_factory=list)
    failed: list[FailedNormalization] = Field(default_factory=list)


class Partner(BaseModel):
    """One row of the accounting system's supplier master, as returned by ``GET /partners``.

    This is the trusted source for ``partner_code`` — the LLM never proposes one, because a
    model asked for a partner code emits one that merely looks right and that is
    unauditable (document-extract.md §3.3, "Supplier resolution"). ``aliases`` is
    display-only, carried for a human reviewer, and is never matched against — the same
    rule that keeps :attr:`NormalizedInvoice.supplier_name` out of supplier resolution.
    """

    partner_code: str = Field(
        description="The accounting system's identifier for this supplier, e.g. 'P-1001'. This is the value that decides who receives payment."
    )
    name: str = Field(description="The partner's registered legal name.")
    aliases: list[str] = Field(
        description="Alternate names this supplier may print on an invoice. For a human reviewer only — never used to resolve a partner_code."
    )
    registration_no: str = Field(
        description="The partner's own 登録番号 (T + 13 digits) — the key an invoice's normalized registration number is matched against."
    )


class SupplierResolution(BaseModel):
    """How one invoice's supplier was matched to a ``partner_code`` — the audit trail for
    the single highest-weight decision in the pipeline (document-extract.md §3.3, "Supplier
    resolution"). A wrong ``partner_code`` pays the wrong company and, unlike a bad amount
    or a bad date, produces no error from the accounting API at all — nothing downstream
    catches it, so this record is what lets an auditor see *how* the partner was chosen,
    not merely which one.
    """

    raw: str = Field(
        description="登録番号 exactly as printed on the invoice, before any cleaning — the same string as source.registration_no_raw."
    )
    normalized: str = Field(
        description="`raw` after normalize_registration_no: folded, hyphens and whitespace stripped, uppercased. This is the key looked up in the partner index."
    )
    partner_code: str = Field(description="The matched partner's accounting-system code.")
    partner_name: str = Field(
        description="The matched partner's name, carried through for a human reviewer even though name is never part of the match."
    )
    matched_by: Literal["registration_no"] = Field(
        description=(
            "How the partner was chosen. One member today, deliberately: this documents the "
            "method for an auditor rather than encoding a live choice, and leaves room for a "
            "future match strategy (e.g. by name) to be added without reshaping this field."
        )
    )


# Every reason validate can fail an invoice for, in the order the checks in ingest/validate.py
# run and therefore the order a reason can first appear in FailedValidation.issues:
#
#   supplier_not_in_master     — check 1: registration_no missing, malformed, or unmatched
#   missing_invoice_number     — check 2
#   missing_issue_date         — check 2
#   missing_due_date           — check 2
#   no_line_items              — check 2
#   missing_line_field         — check 2, per line: description or amount unreadable
#   missing_printed_amount     — check 2: subtotal, tax_amount, or total_amount absent
#   tax_code_unresolved        — check 4: no T10/T08 candidate matches the printed 消費税
#   subtotal_mismatch          — check 5
#   tax_mismatch                — check 5
#   total_mismatch              — check 5
#   due_date_before_issue_date — check 6
#   date_disagreement          — check 7: our parser vs the extraction model's own reading
#   duplicate_invoice          — check 8: (partner_code, invoice_number) already seen
#
# A module-level Literal alias rather than embedding the set twice, so ValidationIssue.code
# and FailedValidation.reason cannot drift out of sync with each other.
ValidationReason = Literal[
    "supplier_not_in_master",
    "missing_invoice_number",
    "missing_issue_date",
    "missing_due_date",
    "no_line_items",
    "missing_line_field",
    "missing_printed_amount",
    "tax_code_unresolved",
    "subtotal_mismatch",
    "tax_mismatch",
    "total_mismatch",
    "due_date_before_issue_date",
    "date_disagreement",
    "duplicate_invoice",
]


class ValidationIssue(BaseModel):
    """One problem found while validating an invoice — one entry per problem, not one per
    invoice. validate collects every issue it can evaluate in a single pass (see
    ingest/validate.py) rather than stopping at the first, so a human reviewing a failed
    invoice sees everything wrong with it at once instead of fixing one problem, re-running
    the pipeline, and discovering the next.
    """

    code: ValidationReason = Field(
        description="Which check failed. See ValidationReason for the full set and which check in ingest/validate.py produces each."
    )
    field: str = Field(
        description=(
            "Which field or row this issue concerns, e.g. 'registration_no', 'total_amount', "
            "or 'lines[2].amount' for a per-line problem — points a reviewer at the exact spot "
            "to check rather than the whole invoice."
        )
    )
    detail: str = Field(
        description="Plain language for the accounting clerk who keys this invoice by hand, naming the Japanese field label (登録番号, 小計, 消費税, 合計, お支払期日 ...) where it helps them find it on the paper."
    )


class RegistrationLine(BaseModel):
    """One line item exactly as the accounting API's ``lines[]`` entry expects it — see
    ``accounting_api.py``'s ``_check_shape`` (lines 133-201) for the fields it checks. No
    provenance fields live here (no row index, no printed string); carrying one would make
    this model not directly postable, which is the property :class:`RegistrationPayload`
    exists to have.
    """

    description: str = Field(description="品名・摘要. Required non-empty by the API.")
    quantity: int | None = Field(description="数量, or null — the API accepts either.")
    unit: str = Field(
        description="単位. Required non-empty by the API; validate defaults a blank cell to '該当なし' since the API rejects an empty string here."
    )
    unit_price: int | None = Field(description="単価, or null — the API accepts either.")
    amount: int = Field(
        description="金額. Required, and the figure every arithmetic check in validate is built from."
    )
    tax_code: Literal["T10", "T08"] = Field(
        description="This line's consumption-tax code — printed on the invoice, or derived by validate and confirmed against the printed 消費税 when nothing was printed. The API rejects any other value."
    )


class RegistrationPayload(BaseModel):
    """The exact body ``POST /invoices`` expects. ``model_dump(mode="json")`` on this model
    is directly postable with no reshaping — that is why it carries no provenance fields
    (file name, upstream record, review notes); those live one level up, on
    :class:`ValidatedInvoice`.

    ``issue_date`` and ``due_date`` are Python ``date`` objects; under ``mode="json"``
    pydantic serializes a ``date`` as ``YYYY-MM-DD``, which is the only format
    ``accounting_api.py``'s ``_check_shape`` (``DATE_PATTERN``) accepts.
    """

    partner_code: str = Field(
        description="Resolved by validate's supplier-resolution check (SupplierResolution.partner_code) — never supplied by the extraction model."
    )
    invoice_number: str = Field(description="請求書番号, checked non-empty by validate.")
    issue_date: date = Field(
        description="発行日, checked non-None by validate. Serializes to YYYY-MM-DD."
    )
    due_date: date = Field(
        description="お支払期日, checked non-None and >= issue_date by validate. Serializes to YYYY-MM-DD."
    )
    currency: Literal["JPY"] = Field(
        default="JPY",
        description="The only currency the accounting API accepts; fixed here since nothing in this pipeline reads or produces another.",
    )
    lines: list[RegistrationLine] = Field(
        description="Every line item, in printed order, with unit defaulted and tax_code resolved by validate."
    )
    subtotal: int = Field(
        description="小計, checked by validate against the sum of the line amounts."
    )
    tax_amount: int = Field(
        description="消費税, checked by validate against the per-tax-code recalculation, floored exactly as accounting_api.py computes it."
    )
    total_amount: int = Field(
        description="合計, checked by validate against subtotal + tax_amount."
    )


class ValidatedInvoice(BaseModel):
    """One invoice that passed every check in validate — a self-contained record of a
    registration decision. ``source`` embeds the whole upstream :class:`NormalizedInvoice`
    this was built from, the same reasoning as :attr:`NormalizedInvoice.source`: nothing
    about how a value was produced has to be cross-referenced back into
    ``output/normalize.json`` to audit it.
    """

    file_name: str = Field(
        description="The document's file name, carried through unchanged from every earlier stage."
    )
    file_path: Path = Field(
        description="Absolute path to the source document on disk, carried through unchanged."
    )
    source: NormalizedInvoice = Field(
        description="The whole upstream normalized record this was validated from, unchanged — the audit trail back to every printed value."
    )
    supplier: SupplierResolution = Field(
        description="How the partner_code in `payload` was resolved."
    )
    payload: RegistrationPayload = Field(
        description="The ready-to-POST registration body — see RegistrationPayload."
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Every value validate supplied rather than read off the page — a defaulted unit, "
            "a derived tax code — naming the affected rows, so a reviewer can see what was not "
            "printed on the paper even though the invoice registered cleanly."
        ),
    )


class FailedValidation(BaseModel):
    """An invoice that failed at least one check in validate.

    ``reason``/``detail`` are ``issues[0]``'s ``code``/``detail`` — the first problem found,
    in check order — mirroring :class:`FailedNormalization` so this record is routable (branch
    on ``reason``) and readable (read ``detail``) without opening ``issues`` at all. ``issues``
    carries everything validate found, not only the first, so a human fixing this invoice by
    hand sees every problem in one pass rather than fixing one, re-running the pipeline, and
    discovering the next.
    """

    file_name: str = Field(description="The document's file name.")
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    reason: ValidationReason = Field(
        description="issues[0].code — the first problem found, in check order, for routing."
    )
    detail: str = Field(
        description="issues[0].detail — plain language for the accounting clerk who has to key this invoice by hand."
    )
    issues: list[ValidationIssue] = Field(
        description="Every problem validate found on this invoice, in check order — not only the first."
    )


class ValidationRun(BaseModel):
    """The result of one pass of validate over a :class:`NormalizationRun`'s successes,
    mirroring ``NormalizationRun{normalized, failed}`` and ``ExtractionRun{extracted,
    failed}`` — see :class:`NormalizationRun`.
    """

    validated: list[ValidatedInvoice] = Field(default_factory=list)
    failed: list[FailedValidation] = Field(default_factory=list)
