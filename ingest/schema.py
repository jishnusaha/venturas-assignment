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
