import base64
from pathlib import Path

from anthropic import Anthropic, APIError
from dotenv import load_dotenv
from pydantic import ValidationError

from ingest.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from ingest.schema import (
    ExtractionRun,
    ExtractedInvoice,
    FailedInvoice,
    InvoiceExtraction,
)


REPO_ROOT = Path(__file__).resolve().parent.parent

INVOICE_DIRECTORY = "invoices"

MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
EFFORT = "high"

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

load_dotenv(REPO_ROOT / ".env")

client = Anthropic()  # resolves ANTHROPIC_API_KEY from the environment


def file_block(path: Path) -> dict:
    """The document or image content block carrying one invoice file, inline as base64."""
    media_type = MEDIA_TYPES[path.suffix.lower()]
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "document" if media_type == "application/pdf" else "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def extract() -> ExtractionRun:
    extracted: list[ExtractedInvoice] = []
    failed: list[FailedInvoice] = []
    invoice_dir = REPO_ROOT / INVOICE_DIRECTORY
    for path in sorted(invoice_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in MEDIA_TYPES:
            print(f"{path.name}\t{path.resolve()}")
            try:
                response = client.messages.parse(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "adaptive"},
                    output_config={"effort": EFFORT},
                    system=SYSTEM_PROMPT,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                file_block(path),
                                {"type": "text", "text": USER_INSTRUCTION},
                            ],
                        }
                    ],
                    output_format=InvoiceExtraction,
                )
            except APIError as exc:
                # Covers the non-2xx statuses, the connection failures and the timeouts
                # alike. Recording it per document keeps one bad call from ending a run
                # that has already paid for the documents before it.
                failed.append(
                    FailedInvoice(
                        file_name=path.name,
                        file_path=path.resolve(),
                        reason="api_error",
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            except ValidationError as exc:
                # Schema-invalid JSON from a schema-constrained call means the text
                # was cut off mid-object — raise MAX_TOKENS rather than trusting it.
                failed.append(
                    FailedInvoice(
                        file_name=path.name,
                        file_path=path.resolve(),
                        reason="schema_invalid",
                        detail=f"{exc.error_count()} schema errors; the response was probably truncated at MAX_TOKENS={MAX_TOKENS}",
                    )
                )
                continue

            if response.parsed_output is None:
                failed.append(
                    FailedInvoice(
                        file_name=path.name,
                        file_path=path.resolve(),
                        reason="incomplete_response",
                        detail=f"model returned no parseable extraction (stop_reason={response.stop_reason})",
                    )
                )
                continue

            extracted.append(
                ExtractedInvoice(
                    file_name=path.name,
                    file_path=path.resolve(),
                    extraction=response.parsed_output,
                )
            )
    return ExtractionRun(extracted=extracted, failed=failed)
