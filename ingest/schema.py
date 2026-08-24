from pathlib import Path
from __future__ import annotations
from typing import Literal

from pydantic import BaseModel, Field


class ExtractedLine(BaseModel):
    """One row of the 品名・摘要 table, exactly as printed."""

    description_raw: str = Field(description="品名・摘要 text, verbatim.")
    quantity_raw: str = Field(description="数量 as printed, or an empty string if the cell is blank.")
    unit_raw: str = Field(description="単位 as printed (個, 式, 箱, セット, 時間, 件, 本, 袋 ...), or an empty string if blank.")
    unit_price_raw: str = Field(description="単価 as printed, or an empty string if the cell is blank.")
    amount_raw: str = Field(description="金額 as printed, including any △ or ▲ sign and any ¥ or comma.")
    tax_mark: str = Field(
        description=(
            "The tax rate shown for THIS line, verbatim: '10%', '8%', '※', '軽減税率', "
            "or an empty string when the table has no per-line 税率 column."
        )
    )


class ExtractionFlags(BaseModel):
    """What the model noticed about the document as an artifact, not its contents."""

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

    issue_date_raw: str = Field(description="発行日 as printed, verbatim (e.g. '令和8年2月5日', '2026/01/18').")
    issue_date_iso: str = Field(description="Your own reading of issue_date_raw as YYYY-MM-DD. Empty string if you cannot resolve it.")
    due_date_raw: str = Field(description="お支払期日 as printed, verbatim. May be a payment term such as '翌月末日' rather than a date.")
    due_date_iso: str = Field(description="Your own reading of due_date_raw as YYYY-MM-DD. Empty string if you cannot resolve it.")

    subtotal_raw: str = Field(description="小計 as printed. Empty string if the document has no 小計 row.")
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

