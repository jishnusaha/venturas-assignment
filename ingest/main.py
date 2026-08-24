"""Driver: runs the extraction pass and prints what came back."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Running this file directly puts ingest/ on sys.path, not the project root, so the
# ingest package is not importable. Adding the root fixes `python ingest/main.py`;
# `python -m ingest.main` already has it and skips the insert. The entry point is the
# only place this belongs — modules imported from here inherit the fixed path.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ingest.extract import extract  # noqa: E402
from ingest.normalize import normalize  # noqa: E402

if __name__ == "__main__":
    extraction_result = extract()
    normalize = normalize(extraction_result.extracted)

    # for invoice in run.extracted:
    #     print(invoice.model_dump_json(indent=2))

    # for failure in run.failed:
    #     print(f"FAILED {failure.file_name} [{failure.reason}] {failure.detail}")

    # print(f"\nextracted {len(run.extracted)}, failed {len(run.failed)}")
