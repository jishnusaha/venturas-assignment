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

from ingest.schema import (
    ExtractedInvoice,
    FailedInvoice,
    ExtractionRun,
    NormalizationRun,
    FailedNormalization,
    NormalizedInvoice,
    MEDIA_TYPES,
)  # noqa: E402
from ingest.extract import extract  # noqa: E402
from ingest.normalize import normalize  # noqa: E402

OUTPUT_DIR = REPO_ROOT / "output"
EXTRACT_OUTPUT = OUTPUT_DIR / "extract.json"
NORMALIZE_OUTPUT = OUTPUT_DIR / "normalize.json"
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
    # extraction data
    extraction_success_list: list[ExtractedInvoice] = []
    extraction_failed_list: list[FailedInvoice] = []

    # normalization data
    normalization_success_list: list[NormalizedInvoice] = []
    normalization_failed_list: list[FailedNormalization] = []

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
