import base64
from pathlib import Path

from anthropic import Anthropic, APIError
from dotenv import load_dotenv
from pydantic import ValidationError

from ingest.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from ingest.schema import (
    ExtractedInvoice,
    FailedInvoice,
    InvoiceExtraction,
    MEDIA_TYPES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
EFFORT = "high"

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


def extract(path: Path) -> ExtractedInvoice | FailedInvoice:

    print(f"extracting: {path.name}\t{path.resolve()}")
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
        return FailedInvoice(
            file_name=path.name,
            file_path=path.resolve(),
            reason="api_error",
            detail=f"{type(exc).__name__}: {exc}",
        )

    except ValidationError as exc:
        # Schema-invalid JSON from a schema-constrained call means the text
        # was cut off mid-object — raise MAX_TOKENS rather than trusting it.
        return FailedInvoice(
            file_name=path.name,
            file_path=path.resolve(),
            reason="schema_invalid",
            detail=f"{exc.error_count()} schema errors; the response was probably truncated at MAX_TOKENS={MAX_TOKENS}",
        )

    if response.parsed_output is None:
        return FailedInvoice(
            file_name=path.name,
            file_path=path.resolve(),
            reason="incomplete_response",
            detail=f"model returned no parseable extraction (stop_reason={response.stop_reason})",
        )

    return ExtractedInvoice(
        file_name=path.name,
        file_path=path.resolve(),
        extraction=response.parsed_output,
    )
