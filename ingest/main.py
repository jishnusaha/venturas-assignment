"""Driver: runs the extraction pass, records its output, then hands off to normalize."""

import sys
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Running this file directly puts ingest/ on sys.path, not the project root, so the
# ingest package is not importable. Adding the root fixes `python ingest/main.py`;
# `python -m ingest.main` already has it and skips the insert. The entry point is the
# only place this belongs — modules imported from here inherit the fixed path.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingest.accounting import (
    AccountingAPIError,
    get_partners,
    get_registered_invoices,
    health,
)
from ingest.validate import build_partner_index, validate
from ingest.register import register
from ingest.report import build_review_rows, summarize, write_review_report
from ingest.schema import (
    ExtractedInvoice,
    FailedInvoice,
    ExtractionRun,
    NormalizationRun,
    FailedNormalization,
    FailedRegistration,
    FailedValidation,
    NormalizedInvoice,
    RegisteredInvoice,
    RegistrationRun,
    SkippedRegistration,
    ValidatedInvoice,
    ValidationRun,
    MEDIA_TYPES,
)  # noqa: E402
from ingest.extract import extract  # noqa: E402
from ingest.normalize import normalize  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "output"
EXTRACT_OUTPUT = OUTPUT_DIR / "extract.json"
NORMALIZE_OUTPUT = OUTPUT_DIR / "normalize.json"
VALIDATE_OUTPUT = OUTPUT_DIR / "validate.json"
REGISTER_OUTPUT = OUTPUT_DIR / "register.json"
INVOICE_DIR = REPO_ROOT / "invoices"

load_dotenv(REPO_ROOT / ".env")


def write_output(path: Path, payload: str) -> None:
    """Record one stage's output on disk, creating the output directory on first use.

    ``encoding`` is explicit because every output holds Japanese text and the platform
    default is not guaranteed to be UTF-8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    print(f"written {path}")


if __name__ == "__main__":
    # The run cannot proceed without a trustworthy supplier master and duplicate ledger, so
    # every accounting API call needed to build them happens up front and exits loudly on
    # failure rather than letting the loop below limp along with nothing to validate against.
    try:
        health_status = health()
    except AccountingAPIError as exc:
        sys.exit(
            f"accounting API is unreachable ({exc.code or 'no code'}): {exc.message}. "
            "Start accounting_api.py and retry."
        )

    try:
        partners = get_partners()
    except AccountingAPIError as exc:
        sys.exit(
            f"could not load the supplier master from the accounting API "
            f"({exc.code or 'no code'}): {exc.message}."
        )

    try:
        partner_index = build_partner_index(partners)
    except ValueError as exc:
        sys.exit(f"supplier master has a collision and cannot be used: {exc}")

    try:
        registered_invoices = get_registered_invoices()
    except AccountingAPIError as exc:
        sys.exit(
            f"could not load the registered-invoice ledger from the accounting API "
            f"({exc.code or 'no code'}): {exc.message}."
        )

    # Seeded here so an invoice already sitting in the ledger is caught by validate's
    # duplicate check rather than surfacing later as a 409 from the register stage.
    seen_keys: set[tuple[str, str]] = {
        (record["partner_code"], record["invoice_number"])
        for record in registered_invoices
    }

    print(
        f"accounting API ok (status={health_status.get('status')}); "
        f"{len(partners)} partner(s) loaded; {len(seen_keys)} invoice(s) already registered"
    )

    # extraction data
    extraction_success_list: list[ExtractedInvoice] = []
    extraction_failed_list: list[FailedInvoice] = []

    # normalization data
    normalization_success_list: list[NormalizedInvoice] = []
    normalization_failed_list: list[FailedNormalization] = []

    # validation data
    validation_success_list: list[ValidatedInvoice] = []
    validation_failed_list: list[FailedValidation] = []

    # registration data
    registration_success_list: list[RegisteredInvoice] = []
    registration_skipped_list: list[SkippedRegistration] = []
    registration_failed_list: list[FailedRegistration] = []

    for path in sorted(INVOICE_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in MEDIA_TYPES:
            print(f"{path.name}\t{path.resolve()}")

            extracted_data = extract(path)
            if isinstance(extracted_data, ExtractedInvoice):
                extraction_success_list.append(extracted_data)
                if not extracted_data.extraction.flags.handwriting_note:
                    normalized_data = normalize(extracted_data)
                    if isinstance(normalized_data, NormalizedInvoice):
                        normalization_success_list.append(normalized_data)
                        validated_data = validate(
                            normalized_data, partner_index, seen_keys
                        )
                        if isinstance(validated_data, ValidatedInvoice):
                            validation_success_list.append(validated_data)
                            # The *second* copy of a within-batch duplicate is the one that
                            # fails, so the key only enters the set once this one has passed.
                            seen_keys.add(
                                (
                                    validated_data.payload.partner_code,
                                    validated_data.payload.invoice_number,
                                )
                            )

                            # The one irreversible step in the pipeline, and the only one
                            # that changes state outside this process. Note what that costs
                            # here: the audit files are written after this loop, so a crash
                            # mid-run can leave invoices in the accounting ledger with no
                            # output/*.json describing why they were sent. Recovery is
                            # GET /invoices, which is the ledger's own record of what landed.
                            registered_data = register(validated_data)
                            if isinstance(registered_data, RegisteredInvoice):
                                registration_success_list.append(registered_data)
                            elif isinstance(registered_data, SkippedRegistration):
                                registration_skipped_list.append(registered_data)
                            else:
                                registration_failed_list.append(registered_data)

                        else:
                            validation_failed_list.append(validated_data)
                    else:
                        normalization_failed_list.append(normalized_data)

            else:
                extraction_failed_list.append(extracted_data)

    extraction_result = ExtractionRun(
        extracted=extraction_success_list, failed=extraction_failed_list
    )
    write_output(EXTRACT_OUTPUT, extraction_result.model_dump_json(indent=2))

    normalization_result = NormalizationRun(
        normalized=normalization_success_list, failed=normalization_failed_list
    )
    write_output(NORMALIZE_OUTPUT, normalization_result.model_dump_json(indent=2))

    validation_result = ValidationRun(
        validated=validation_success_list, failed=validation_failed_list
    )
    write_output(VALIDATE_OUTPUT, validation_result.model_dump_json(indent=2))

    registration_result = RegistrationRun(
        registered=registration_success_list,
        skipped=registration_skipped_list,
        failed=registration_failed_list,
    )
    write_output(REGISTER_OUTPUT, registration_result.model_dump_json(indent=2))

    # Last, because it is a view over the four files above rather than a fifth stage. Built
    # from the in-memory runs, not by re-reading what was just written — the two are the same
    # objects, and `python -m ingest.report` exists for rebuilding it from disk later.
    review_rows = build_review_rows(
        extraction_result, normalization_result, validation_result, registration_result
    )
    print(f"written {write_review_report(review_rows)}")
    print(summarize(review_rows))
