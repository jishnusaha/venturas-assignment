from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field

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


class ExtractionUsage(BaseModel):
    """What one call to the extraction model actually consumed.

    Recorded per document because "what does this cost to run" is a question the client
    asked in production terms, and a number derived from a token-count guess is not an
    answer to it. Every field here is measured — the token counts come from the API's own
    ``usage`` object and ``latency_seconds`` is wall-clock around the call — except the
    three cost fields, which apply a published per-token rate to those measured counts.

    ``cost_usd`` is ``None`` rather than an estimate when the served model is not in
    ``ingest.extract.PRICE_PER_MTOK``: the token counts stay valid and auditable either
    way, and a priced-at-a-guessed-rate figure is exactly the kind of number that gets
    quoted back later as if it had been measured.
    """

    model: str = Field(
        description="The model ID the API reports as having served this call."
    )
    input_tokens: int = Field(description="Billed input tokens: the prompt and the document.")
    output_tokens: int = Field(
        description="Billed output tokens, thinking tokens included."
    )
    thinking_tokens: int | None = Field(
        default=None,
        description=(
            "The share of output_tokens spent on extended thinking, when the API reports "
            "the breakdown. Already counted inside output_tokens — never added to it."
        ),
    )
    cache_read_input_tokens: int = Field(
        default=0,
        description="Input tokens served from the prompt cache. Zero: this stage sets no cache_control.",
    )
    cache_creation_input_tokens: int = Field(
        default=0, description="Input tokens written to the prompt cache. Zero, for the same reason."
    )
    latency_seconds: float = Field(
        description="Wall-clock seconds around the API call, SDK retries included."
    )
    input_cost_usd: float | None = Field(
        default=None, description="Input tokens at the published rate. None if the model is unpriced."
    )
    output_cost_usd: float | None = Field(
        default=None, description="Output tokens at the published rate. None if the model is unpriced."
    )
    cost_usd: float | None = Field(
        default=None,
        description="input_cost_usd + output_cost_usd. None if the model is unpriced.",
    )
    from_cache: bool = Field(
        default=False,
        description=(
            "True when this record was replayed from a previous run's output/extract.json "
            "rather than measured on this run — the measurement is real, but no API call "
            "was made and nothing was billed this run. See ingest/extract.py."
        ),
    )


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
    usage: ExtractionUsage | None = Field(
        default=None,
        description=(
            "What the call that produced this extraction cost. Optional because an "
            "extract.json written before this field existed must still load — an absent "
            "record means unmeasured, never free."
        ),
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
    usage: ExtractionUsage | None = Field(
        default=None,
        description=(
            "What the failed call consumed, when a response came back at all — a refusal "
            "or a truncated extraction is billed like any other. None where the call never "
            "returned a message, since there is nothing to measure."
        ),
    )


class ExtractionTotals(BaseModel):
    """The extraction stage's cost and latency for one run, in the terms the client asked.

    Derived, never stored: :class:`ExtractionRun` recomputes this from the per-document
    :class:`ExtractionUsage` records every time it is built or loaded, so the summary
    cannot drift away from the measurements it summarizes.
    """

    documents_measured: int = Field(
        description="Documents carrying a usage record, whether measured now or replayed."
    )
    documents_billed_this_run: int = Field(
        description=(
            "Documents that actually hit the API on this run. Lower than "
            "documents_measured whenever extract.py replayed a cached extraction — that "
            "run cost nothing, and the averages below still describe the calls as measured."
        )
    )
    input_tokens: int = Field(description="Summed across every measured document.")
    output_tokens: int = Field(description="Summed across every measured document.")
    api_seconds: float = Field(
        description=(
            "Summed call latency. Not the run's wall-clock time — the pipeline calls the "
            "API one document at a time, so the two are close, but this measures the API."
        )
    )
    mean_seconds_per_document: float | None = Field(
        default=None, description="Processing time per invoice. None with nothing measured."
    )
    cost_usd: float | None = Field(
        default=None,
        description=(
            "Summed cost across measured documents. None if any of them was served by a "
            "model with no published rate in the table, since a partial sum reported as a "
            "total would understate it silently."
        ),
    )
    mean_cost_per_document_usd: float | None = Field(
        default=None, description="Cost per invoice. None when cost_usd is None."
    )
    projected_cost_usd_per_1000: float | None = Field(
        default=None,
        description=(
            "mean_cost_per_document_usd x 1000 — the client's monthly volume question, "
            "answered by straight extrapolation from this sample of documents. Extraction "
            "only: it excludes the accounting API, which is free, and any human review."
        ),
    )


class ExtractionRun(BaseModel):
    """The result of one pass over the invoice directory.

    Successes and failures are kept apart so that consuming ``extracted`` never means
    checking for None, while no document can drop out of the run unaccounted for.
    """

    extracted: list[ExtractedInvoice] = Field(default_factory=list)
    failed: list[FailedInvoice] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def totals(self) -> ExtractionTotals:
        """Roll the per-document usage records up into one summary.

        A computed property rather than a stored field so it is written into
        ``output/extract.json`` for a reader, recomputed on load, and impossible to leave
        stale. Failed extractions are counted too — a truncated response is billed.
        """
        records = [
            item.usage
            for item in (*self.extracted, *self.failed)
            if item.usage is not None
        ]

        # None, not 0.0, when even one record is unpriced — and equally when there is
        # nothing to price at all. A run whose extractions were all replayed from cache
        # has measured no cost, which is not the same fact as having cost nothing, and
        # only one of the two is true of the pipeline.
        priced = [record.cost_usd for record in records]
        cost = (
            None
            if not records or any(value is None for value in priced)
            else sum(priced)  # type: ignore[arg-type]
        )

        count = len(records)
        return ExtractionTotals(
            documents_measured=count,
            documents_billed_this_run=sum(1 for r in records if not r.from_cache),
            input_tokens=sum(r.input_tokens for r in records),
            output_tokens=sum(r.output_tokens for r in records),
            api_seconds=round(sum(r.latency_seconds for r in records), 3),
            mean_seconds_per_document=(
                round(sum(r.latency_seconds for r in records) / count, 3) if count else None
            ),
            cost_usd=round(cost, 6) if cost is not None else None,
            mean_cost_per_document_usd=(
                round(cost / count, 6) if cost is not None and count else None
            ),
            projected_cost_usd_per_1000=(
                round(cost / count * 1000, 2) if cost is not None and count else None
            ),
        )


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


class AccountingRecord(BaseModel):
    """The accounting system's own record of a registration, exactly what
    ``accounting_api.py``'s ``_register`` (lines 282-298) stores and returns in the 201
    body. This is the ledger's receipt, not ours — modelled rather than kept as a raw dict
    so an unreadable 201 body is caught by pydantic and reported, instead of a malformed
    record being silently carried through the rest of this pipeline as an opaque blob.
    """

    accounting_id: str = Field(
        description="The accounting system's own identifier for this registration, e.g. 'ACC-0001'."
    )
    partner_code: str = Field(description="Echoed back from the posted payload.")
    invoice_number: str = Field(description="Echoed back from the posted payload.")
    issue_date: date = Field(description="Echoed back from the posted payload.")
    due_date: date = Field(description="Echoed back from the posted payload.")
    subtotal: int = Field(description="Echoed back from the posted payload.")
    tax_amount: int = Field(description="Echoed back from the posted payload.")
    total_amount: int = Field(description="Echoed back from the posted payload.")
    line_count: int = Field(
        description="Number of line items the accounting system recorded — a count, not the lines themselves."
    )


# Every reason register can fail (or skip) an invoice for. Apart from the last three, every
# one of these means validate passed something the accounting system rejected — validate
# mirrors each of these checks locally (see ingest/validate.py), so the reason code names
# which of our own checks disagreed with the ledger, which makes a registration failure a
# defect in our gate rather than merely a bad invoice.
#
#   partner_not_found          — PARTNER_NOT_FOUND (400)
#   unknown_tax_code           — UNKNOWN_TAX_CODE (400)
#   due_date_before_issue_date — DUE_DATE_BEFORE_ISSUE_DATE (400)
#   amount_mismatch            — AMOUNT_MISMATCH (422)
#   invalid_payload            — VALIDATION_ERROR (422)
#   unauthorized               — UNAUTHORIZED (401)
#   endpoint_not_found         — NOT_FOUND (404)
#   api_unreachable            — no response at all: connection refused, timeout, unreadable body
#   unconfirmed_registration   — MALFORMED_RESPONSE: a 201 whose record could not be read
#   unexpected_error           — an error code this pipeline does not know
RegistrationReason = Literal[
    "partner_not_found",
    "unknown_tax_code",
    "due_date_before_issue_date",
    "amount_mismatch",
    "invalid_payload",
    "unauthorized",
    "endpoint_not_found",
    "api_unreachable",
    "unconfirmed_registration",
    "unexpected_error",
]


class RegisteredInvoice(BaseModel):
    """One invoice the accounting system accepted — the terminal, successful record of this
    pipeline.
    """

    file_name: str = Field(
        description="The document's file name, carried through unchanged from every earlier stage."
    )
    file_path: Path = Field(
        description="Absolute path to the source document on disk, carried through unchanged."
    )
    source: ValidatedInvoice = Field(
        description=(
            "The whole upstream validated record this was registered from, unchanged — the "
            "same reasoning as NormalizedInvoice.source and ValidatedInvoice.source. "
            "source.notes records every value validate *supplied* rather than read off the "
            "page (a derived tax code, a defaulted 単位), so this record answers 'why does "
            "this registered invoice say T10 when the paper prints no 税率 column' without a "
            "cross-reference."
        )
    )
    record: AccountingRecord = Field(description="The accounting system's own receipt for this registration.")
    http_status: int = Field(
        description="201. Recorded rather than assumed, so the audit log states the outcome it observed."
    )


class SkippedRegistration(BaseModel):
    """A ``409 DUPLICATE_INVOICE`` from the accounting API. Not a failure: the accounting
    system independently reached validate's own duplicate verdict, and nothing was written.

    This bucket should normally stay empty. ``main.py`` seeds validate's ``seen_keys`` from
    ``GET /invoices`` at startup, so a duplicate is normally caught at validate, before
    register ever runs. A 409 here means the ledger changed after startup — another process
    registered the same invoice mid-run — and this is the last line of defence against a
    double payment.
    """

    file_name: str = Field(description="The document's file name.")
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    reason: Literal["duplicate_invoice"] = Field(
        description=(
            "Why this invoice was skipped. One member deliberately, the same reasoning as "
            "SupplierResolution.matched_by: this documents the outcome for an auditor rather "
            "than encoding a live choice, and leaves room for a future skip reason to be "
            "added without reshaping this field."
        )
    )
    detail: str = Field(description="Plain language for the accounting clerk.")
    payload: RegistrationPayload = Field(
        description=(
            "What we would have sent, so a human can compare it against the invoice already "
            "in the ledger and confirm it really is the same one."
        )
    )
    http_status: int = Field(description="409.")


class FailedRegistration(BaseModel):
    """An invoice the accounting API rejected, or one register could not confirm one way or
    the other. Mirrors :class:`FailedValidation`'s routable/readable shape: ``reason`` for
    branching, ``detail`` for a human to read without opening anything else.
    """

    file_name: str = Field(description="The document's file name.")
    file_path: Path = Field(description="Absolute path to the source document on disk.")
    reason: RegistrationReason = Field(description="See RegistrationReason for the full set.")
    detail: str = Field(
        description=(
            "Plain language for the accounting clerk who has to act on this, naming what to "
            "do — never an exception type or a stack trace, per the standing decision that "
            "detail is written for the clerk, not the engineer."
        )
    )
    http_status: int | None = Field(
        description="The API's HTTP status, or None when no response arrived at all (api_unreachable)."
    )
    api_code: str | None = Field(
        description="The API's own error code, verbatim, or None when no response arrived."
    )
    api_message: str | None = Field(
        description=(
            "The API's own error message, verbatim — engineer-facing evidence, deliberately "
            "kept out of `detail` per the standing decision that detail is written for the "
            "accounting clerk, not the engineer. An engineer debugging a registration defect "
            "reads this field; the clerk reads detail."
        )
    )
    payload: RegistrationPayload = Field(
        description="The exact body sent, so the rejection can be reproduced."
    )


class RegistrationRun(BaseModel):
    """The result of one pass of register over a :class:`ValidationRun`'s successes.
    Mirrors ``ValidationRun{validated, failed}`` / ``NormalizationRun{normalized, failed}`` /
    ``ExtractionRun{extracted, failed}``, but with a third bucket: a 409 duplicate is neither
    a success nor a defect in this pipeline's gate, so it is kept apart from both.
    """

    registered: list[RegisteredInvoice] = Field(default_factory=list)
    skipped: list[SkippedRegistration] = Field(default_factory=list)
    failed: list[FailedRegistration] = Field(default_factory=list)



class ReviewRow(BaseModel):
    """One source document's trip through the four stages, as a flag per stage.

    Derived entirely from ``output/*.json`` — it carries no fact those four files do not
    already hold, and exists so that "what happened to the twelve invoices" is one file to
    read rather than four to reconcile by hand. See ``ingest/report.py``.

    The flags are *reached and passed*, not *attempted*: an invoice held at validate has
    ``normalized: true, validated: false``, and everything after a false is false too.
    ``registered`` is therefore the only one that means the ledger changed.
    """

    file_name: str = Field(description="The document's file name, e.g. 'invoice_08.jpg'.")
    extracted: bool = Field(description="The vision model returned a schema-valid reading.")
    has_handwriting: bool = Field(
        description=(
            "The model saw handwriting anywhere on the page, including marks touching no "
            "figure. Not a stage — a property of the document that routes it to a human."
        )
    )
    normalized: bool = Field(description="Every printed string converted without a converter raising.")
    validated: bool = Field(description="All 8 checks passed; safe to register automatically.")
    registered: bool = Field(description="The accounting API accepted it (HTTP 201).")
    reason: list[str] = Field(
        default_factory=list,
        description=(
            "Every note and every failure recorded against this document, each as "
            "'code: sentence'. Always empty when `registered` is true: the invoice is in "
            "the ledger and nobody has anything to do with it, so a non-empty reason means "
            "exactly one thing — somebody needs to look at this. Notes about values the "
            "pipeline supplied rather than read (a derived tax code, a defaulted 単位) stay "
            "on the ValidatedInvoice in validate.json for anyone auditing a registration."
        ),
    )
