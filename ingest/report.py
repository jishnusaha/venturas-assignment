"""Report: the four stage outputs folded into one row per document.

Reads ``output/extract.json``, ``normalize.json``, ``validate.json`` and ``register.json``
and writes ``output/review_report.json`` — a flat list, one :class:`~ingest.schema.ReviewRow`
per source document, saying which stages it cleared and why it stopped where it did.

This stage **derives, it never decides**. Every string it emits was written by the stage that
produced it; nothing here re-runs a check, re-reads a document, or invents a verdict. That is
what makes it safe to regenerate at any time:

    python -m ingest.report

which rebuilds the report from the four files on disk without re-running the pipeline, at no
API cost. It also means the report cannot disagree with the audit files — if it says an
invoice failed validation, ``validate.json`` says so too, in the same words.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import TypeAdapter

from ingest.schema import (
    ExtractionRun,
    NormalizationRun,
    RegistrationRun,
    ReviewRow,
    ValidationRun,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "output"
REVIEW_OUTPUT = OUTPUT_DIR / "review_report.json"

# One row per document, no envelope: the file is a queue to scan, and a wrapper object would
# only add a level of nesting between a reader and the twelve rows they came for.
_ROWS = TypeAdapter(list[ReviewRow])


def _note(code: str, detail: str) -> str:
    """One reason line: a machine-readable code, then the sentence written for the clerk.

    Both halves earn their place — the code is what you scan a column of these for, the
    sentence is what tells someone what to do about it. Neither is written here; they are
    copied off the stage record that produced them.
    """
    return f"{code}: {detail}"


def build_review_rows(
    extraction: ExtractionRun,
    normalization: NormalizationRun,
    validation: ValidationRun,
    registration: RegistrationRun,
) -> list[ReviewRow]:
    """Fold the four runs into one row per document, in file-name order.

    The document set comes from the extraction run — every file the pipeline opened appears
    there, in one bucket or the other — so a document cannot go missing from this report by
    failing early.
    """
    # Every lookup below is by file name, which is unique per run: main.py walks the invoice
    # directory once and each file is processed exactly once.
    normalized_ok = {item.file_name for item in normalization.normalized}
    normalize_failed = {item.file_name: item for item in normalization.failed}
    validated_ok = {item.file_name: item for item in validation.validated}
    validate_failed = {item.file_name: item for item in validation.failed}
    registered_ok = {item.file_name for item in registration.registered}
    register_skipped = {item.file_name: item for item in registration.skipped}
    register_failed = {item.file_name: item for item in registration.failed}

    rows: list[ReviewRow] = []

    # A document that never produced a reading stops here: there is no extraction to carry
    # a handwriting flag, so the flag is False because nothing was seen, not because the
    # page was clean. The reason line says which of the three extraction failures it was.
    for failure in extraction.failed:
        rows.append(
            ReviewRow(
                file_name=failure.file_name,
                extracted=False,
                has_handwriting=False,
                normalized=False,
                validated=False,
                registered=False,
                reason=[_note(failure.reason, failure.detail)],
            )
        )

    for item in extraction.extracted:
        name = item.file_name
        reasons: list[str] = []

        # Read from the extraction rather than inferred from where the document stopped, so
        # the column stays true even for an invoice that went all the way through.
        has_handwriting = item.extraction.flags.has_handwriting
        if has_handwriting:
            reasons.append(
                _note(
                    "handwriting",
                    item.extraction.flags.handwriting_note
                    or "Handwriting was seen on this document but not described.",
                )
            )

        normalized = name in normalized_ok
        if not normalized and name in normalize_failed:
            failure = normalize_failed[name]
            reasons.append(_note(failure.reason, failure.detail))

        validated = name in validated_ok
        if name in validate_failed:
            # Every issue, not just the one that named the reason: an invoice with three
            # faults should cost a reviewer one pass, not three re-runs.
            reasons.extend(
                _note(issue.code, issue.detail) for issue in validate_failed[name].issues
            )
        elif validated:
            # The values validate supplied rather than read off the page — a derived tax
            # code, a defaulted 単位. Kept for an invoice that passed validate but did not
            # reach the ledger, where they are context for the failure sitting next to
            # them; dropped again below if the registration succeeded.
            reasons.extend(_note("note", text) for text in validated_ok[name].notes)

        registered = name in registered_ok
        if name in register_skipped:
            skipped = register_skipped[name]
            reasons.append(_note(skipped.reason, skipped.detail))
        elif name in register_failed:
            failure = register_failed[name]
            reasons.append(_note(failure.reason, failure.detail))

        # Reached no stage past extraction and said nothing about why. Today this is the
        # handwriting gate in main.py, which drops a flagged document without recording an
        # outcome for it; the branch is written to catch any such gap, not that one, so a
        # future filter cannot silently swallow a document either. It states only what is
        # observable here — the document stopped — and never guesses the cause.
        if not normalized and name not in normalize_failed:
            reasons.append(
                _note(
                    "not_processed",
                    "This document was read but never reached the normalize stage, and no "
                    "stage recorded a reason. It has not been registered — check it by hand.",
                )
            )

        rows.append(
            ReviewRow(
                file_name=name,
                extracted=True,
                has_handwriting=has_handwriting,
                normalized=normalized,
                validated=validated,
                registered=registered,
                # A clean registration says nothing. The invoice is in the ledger and no
                # human has anything to do with it, so it carries no reason at all — which
                # makes a non-empty `reason` mean exactly one thing when scanning the file:
                # somebody needs to look at this. Notes about a derived tax code or a
                # defaulted 単位 are not lost, they stay on the ValidatedInvoice in
                # validate.json for anyone auditing why a registered figure reads as it does.
                reason=[] if registered else reasons,
            )
        )

    return sorted(rows, key=lambda row: row.file_name)


def load_runs() -> tuple[ExtractionRun, NormalizationRun, ValidationRun, RegistrationRun]:
    """Read the four stage outputs off disk, each through its own model.

    Parsing through the run models rather than plain ``json.load`` is deliberate: a stage
    file that has drifted from the schema fails here, loudly, instead of producing a report
    with quietly missing rows.
    """
    missing = [
        path.name
        for path in (
            OUTPUT_DIR / "extract.json",
            OUTPUT_DIR / "normalize.json",
            OUTPUT_DIR / "validate.json",
            OUTPUT_DIR / "register.json",
        )
        if not path.exists()
    ]
    if missing:
        sys.exit(
            f"missing stage output(s) in {OUTPUT_DIR}: {', '.join(missing)} — "
            "run the pipeline first (python ingest/main.py)"
        )

    def read(model, name: str):
        return model.model_validate_json(
            (OUTPUT_DIR / name).read_text(encoding="utf-8")
        )

    return (
        read(ExtractionRun, "extract.json"),
        read(NormalizationRun, "normalize.json"),
        read(ValidationRun, "validate.json"),
        read(RegistrationRun, "register.json"),
    )


def write_review_report(rows: list[ReviewRow]) -> Path:
    """Serialize the rows to ``output/review_report.json`` and return the path written.

    ``ensure_ascii`` is off by way of pydantic's JSON encoder, which emits UTF-8 — the reason
    lines carry Japanese field names (登録番号, 消費税) and escaping them would make the file
    unreadable to the person it is written for.
    """
    REVIEW_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_OUTPUT.write_bytes(_ROWS.dump_json(rows, indent=2))
    return REVIEW_OUTPUT


def summarize(rows: list[ReviewRow]) -> str:
    """The one line printed at the end of a run — the shape of the outcome, not its detail."""
    registered = sum(1 for row in rows if row.registered)
    review = len(rows) - registered
    return (
        f"{len(rows)} document(s): {registered} registered, {review} need(s) human review"
    )


if __name__ == "__main__":
    # Standalone entry point: rebuild the report from whatever is already in output/,
    # without re-running the pipeline and without an API key.
    report_rows = build_review_rows(*load_runs())
    print(f"written {write_review_report(report_rows)}")
    print(summarize(report_rows))
