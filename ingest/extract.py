import base64
import time
from pathlib import Path

from anthropic import Anthropic, APIError
from anthropic.types import Usage
from dotenv import load_dotenv
from pydantic import ValidationError

from ingest.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from ingest.schema import (
    ExtractedInvoice,
    ExtractionRun,
    ExtractionUsage,
    FailedInvoice,
    InvoiceExtraction,
    MEDIA_TYPES,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

import sys
from pathlib import Path


MODEL = "claude-sonnet-5"
MAX_TOKENS = 16000
EFFORT = "high"

# Published list price in USD per million tokens, as (input, output). Checked against
# Anthropic's pricing page on 2026-08-24 — a rate table is a fact about the world and goes
# stale silently, so it is dated here rather than buried in a cost figure downstream.
#
# Sonnet 5 is also running an introductory rate of $2.00 / $10.00 through 2026-08-31. The
# standard rate is used deliberately: this number exists to answer "what does it cost to
# run this in production", and production outlives the promotion. Costing the pipeline at
# the promo rate would understate it by a third from September onwards.
#
# Cache rates are not modelled because this stage sets no cache_control — every document is
# a different image, so there is no shared prefix long enough to be worth caching. If that
# changes, the cache token counts are already recorded per call and this table needs the
# 0.1x read / 1.25x write multipliers before the cost fields mean anything again.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


load_dotenv(REPO_ROOT / ".env")

client = Anthropic()  # resolves ANTHROPIC_API_KEY from the environment

OUTPUT_DIR = REPO_ROOT / "output"
EXTRACT_OUTPUT = OUTPUT_DIR / "extract.json"
NORMALIZE_OUTPUT = OUTPUT_DIR / "normalize.json"
INVOICE_DIR = REPO_ROOT / "invoices"


def _rate_for(model: str) -> tuple[float, float] | None:
    """The (input, output) rate for a served model ID, or None if it is not priced here.

    Matched by prefix so a dated snapshot ID ('claude-sonnet-5-20260101') prices the same
    as the alias that was requested. Returning None rather than a default rate is the
    fail-closed half of this: an unpriced model leaves ``cost_usd`` empty instead of
    quietly costing the run at whatever the nearest model happens to charge.
    """
    for name, rate in PRICE_PER_MTOK.items():
        if model.startswith(name):
            return rate
    return None


def measure(usage: Usage, model: str, latency_seconds: float) -> ExtractionUsage:
    """Turn the API's own usage object plus a stopwatch reading into one cost record.

    Kept separate from the call itself so it can serve both the success path and the
    incomplete-response path, which is billed exactly the same and must not be recorded
    as free.
    """
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens

    rate = _rate_for(model)
    input_cost = output_cost = total_cost = None
    if rate is not None:
        input_rate, output_rate = rate
        input_cost = round(input_tokens / 1_000_000 * input_rate, 6)
        output_cost = round(output_tokens / 1_000_000 * output_rate, 6)
        total_cost = round(input_cost + output_cost, 6)

    return ExtractionUsage(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=(
            usage.output_tokens_details.thinking_tokens
            if usage.output_tokens_details is not None
            else None
        ),
        # Both are Optional[int] on the SDK's Usage model and arrive as None rather than 0
        # when no caching was involved, which is every call this stage makes today.
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        latency_seconds=round(latency_seconds, 3),
        input_cost_usd=input_cost,
        output_cost_usd=output_cost,
        cost_usd=total_cost,
    )


def file_block(path: Path) -> dict:
    """The document or image content block carrying one invoice file, inline as base64."""
    media_type = MEDIA_TYPES[path.suffix.lower()]
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "document" if media_type == "application/pdf" else "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def return_from_local(path: Path) -> ExtractedInvoice | FailedInvoice:
    if not EXTRACT_OUTPUT.exists():
        sys.exit(f"no output at {EXTRACT_OUTPUT} — run the extract stage first")

    extraction_result = ExtractionRun.model_validate_json(
        EXTRACT_OUTPUT.read_text(encoding="utf-8")
    )
    for data in extraction_result.extracted:
        if data.file_name == path.name:
            if data.usage is not None and not data.usage.from_cache:
                return data.model_copy(
                    update={"usage": data.usage.model_copy(update={"from_cache": True})}
                )
            return data
    sys.exit("nothing found locally")


def extract(path: Path) -> ExtractedInvoice | FailedInvoice:
    # return from local if already run (skip llm call for testing)
    # return return_from_local(path)

    started = time.perf_counter()
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

    # Only reached on a response, since both handlers above return. Stopped here rather
    # than after the branching below so the figure is the call's latency and not this
    # function's — and read from the response's own `model`, not from MODEL, so an alias
    # the API resolves to something else is priced as what actually served the request.
    usage = measure(response.usage, response.model, time.perf_counter() - started)

    if response.parsed_output is None:
        # Billed like any other call: a refusal or a stop short of a parseable object
        # still consumed the document's input tokens. Carrying the record onto the failure
        # keeps the run's cost total honest instead of counting only what succeeded.
        return FailedInvoice(
            file_name=path.name,
            file_path=path.resolve(),
            reason="incomplete_response",
            detail=f"model returned no parseable extraction (stop_reason={response.stop_reason})",
            usage=usage,
        )

    return ExtractedInvoice(
        file_name=path.name,
        file_path=path.resolve(),
        extraction=response.parsed_output,
        usage=usage,
    )
