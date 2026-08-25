# Implementation Plan — Invoice Intake Pipeline

Source of truth for the requirement: [TAKE_HOME.md](TAKE_HOME.md).
---

## 1. The problem worth solving

The client asked to "read invoices with AI and enter them automatically". The sentence that
actually names the risk is *"a typo nearly caused us to pay the same invoice twice"* — and the
accounting API has **no update endpoint**: once a wrong number is registered, there is no way
to correct it, only `DELETE /invoices`, which wipes everything.

So the problem this pipeline solves is:

> **Decide, per invoice, whether it is safe to register automatically — register the ones that
> are, and hand a human the ones that are not, each with a specific, named reason.**

"All 12 registered" is explicitly **not** the success criterion. Forcing every document through
would be the wrong answer.

## 2. Scope

**In**

1. `extract → normalize → validate → register` over all 12 invoices, from one command
2. Verification of the AI's output: arithmetic re-checked against the printed figures, and a
   second independent reading of every date
3. A per-stage audit file on disk, plus one flat review report a clerk can read
4. Extraction cost and latency measured per document, not estimated

**Out, deliberately**

- A review UI — the review queue is a JSON file for now
- Retries, queues, a database, concurrency — 12 documents run sequentially in ~2 minutes
- A separate OCR fallback: one vision call handles text-layer PDFs, scans and scan-only PDFs
- Matching a supplier by printed name; only the 登録番号(registration number) is matched (see §4.4)

## 3. Architecture

One sequential pipeline. [ingest/main.py](ingest/main.py) walks `invoices/`, runs each file
through the same stages, and prints one row per document as it goes.

```
 source file ──► extract ──► handwriting? ─► normalize ──► validate ──► register ──┬─► 201 registered
              (LLM-failed)      (yes)        (failed)      (failed)     (failed)   ├─► 409 skipped
                  │              │              │             │            │       └─► 4xx failed
                  └──────────────┴──────────────┴─────────────┴────────────┴─────────► human review
```

**Hard rule: the LLM lives in the `extract` stage only.** Normalize, validate and register are
plain Python — deterministic, and reproducible from `output/extract.json` at zero API cost. If
the model also did the date math and the yen parsing there would be one source and nothing to
check it against.

```
ingest/
  main.py         driver: startup checks, the per-document loop, writes every output
  prompt.py       the extraction system prompt (10 numbered rules)
  extract.py      the vision call + per-call cost measurement
  schema.py       every pydantic model, one per stage, success and failure alike
  convert.py      pure conversion primitives: dates, numbers, tax marks, 登録番号
  normalize.py    printed strings -> API representations
  validate.py     the gate: 8 checks, supplier resolution, payload construction
  accounting.py   the ONLY module that talks to the accounting API
  register.py     POST /invoices, and how each response is routed
  report.py       folds the four stage files into output/review_report.json
run.sh            the single command
```

## 4. Stages

### 4.1 Extract — one vision call per document

Not raw OCR text: file → structured JSON in a single call, `claude-sonnet-5` through the
`anthropic` SDK, `client.messages.parse(..., output_format=InvoiceExtraction)` so the response
arrives schema-validated. The file goes in as base64 in a `document` block (PDF) or an `image`
block (JPG) — a scan-only PDF such as `invoice_09.pdf` needs no special path.

**The governing rule: the model returns verbatim printed strings, never converted values.**
`¥240,900` stays `¥240,900`; `令和8年2月5日` stays as printed. Every conversion happens in
deterministic, unit-testable Python downstream.

One deliberate exception: `issue_date_iso` / `due_date_iso` are the model's **own reading** of
the matching raw string. They never enter the payload — they exist only to be compared against
our own parser's reading of the same string (check 7 in §4.4).

The prompt's other rules earn their place from what the sample documents do: read every line
including a table continued on a later page; align a description to the row whose 金額 it
belongs to on a skewed scan; a blank cell is `""`, never an invented `1`; read `unit_raw` on
every line (it is the column a flattened PDF text layer loses silently); read `tax_mark` per
line only from a real 税率 column, never inferred from the 消費税 summary; report the
supplier's own 登録番号 and **never** propose a `partner_code`.

Failures are per document, so one bad call cannot end a run already paid for: `api_error`,
`incomplete_response`, `schema_invalid` (which means truncation — raise `MAX_TOKENS`).

**Cost is measured, not estimated.** Every call records input/output tokens, thinking tokens,
latency and USD cost at the published rate; `ExtractionRun.totals` rolls them into cost per
invoice and a projection per 1,000. An unpriced model leaves cost `None` rather than being
priced at a neighbour's rate. `extract.py` also carries a one-line dev switch that replays
`output/extract.json` instead of calling the API; replayed records are marked `from_cache`, so
a replayed run reports zero *billed* documents and never inflates the cost total.

### 4.2 The handwriting gate

Any handwriting anywhere on the document — a correction, a note, a 受領 stamp — stops the
document after extract and sends it to a human. It is the one column in the progress table
where a tick is bad news. A hand-altered bank account number is precisely the case no
arithmetic check can catch, so the rule is blunt on purpose.

### 4.3 Normalize — conversion, zero judgment

Pure functions in [ingest/convert.py](ingest/convert.py). No LLM, no network.

| Concern | Transform |
|---|---|
| Dates | Era (`令和8年1月7日`, `R8.1.7`, `令和元年`) and western forms → `date`; relative due dates (`翌月末日`, `月末締め翌月末払い`, `2026年2月末日`) anchored to the issue date |
| Numbers | NFKC fold, strip `¥ ￥ 円 ,`, `△`/`▲`/parens → negative, `Decimal` → `int` only when integral |
| Tax mark | printed rate wins (`10%` → `T10`, `8%` → `T08`), else `※`/`軽減` → `T08`, else `None` |
| 登録番号 | fold, strip whitespace and every hyphen variant, uppercase — the shape check belongs to validate |

**Rule: normalize converts, it never decides.** A blank cell and an unconvertible value both
become `None`; whether that matters is validate's question. Each `NormalizedInvoice` embeds the
whole verbatim extraction as `source`, so the printed string sits next to every converted value
and the record audits itself.

Normalize cannot fail on content. The two date conversions are wrapped as a net — if a
converter ever raises, that one document is recorded as `unreadable_issue_date` /
`unreadable_due_date` instead of taking down a run that has already paid for every document
before it.

### 4.4 Validate — the gate

Produces a **verdict with named reasons**, not a boolean. Every check that can be evaluated
runs, and every issue found is collected — a human fixing an invoice sees all of it in one
pass rather than one problem per re-run. A check whose own inputs are already broken is
skipped rather than evaluated on garbage.

| # | Step | Reason code(s) |
|---|---|---|
| 1 | **Supplier resolution** — 登録番号 shape-checked and looked up in the partner master | `supplier_not_in_master` |
| 2 | Shape / completeness — mirrors the API's `_check_shape` | `missing_invoice_number`, `missing_issue_date`, `missing_due_date`, `no_line_items`, `missing_line_field`, `missing_printed_amount` |
| 3 | Unit fill — a blank 単位 becomes `該当なし` | *(a note, never a failure)* |
| 4 | Tax code resolution | `tax_code_unresolved` |
| 5 | **Arithmetic vs the printed figures** | `subtotal_mismatch`, `tax_mismatch`, `total_mismatch` |
| 6 | `due_date >= issue_date` | `due_date_before_issue_date` |
| 7 | **Date agreement** — our parser vs the model's own reading | `date_disagreement` |
| 8 | Duplicate `(partner_code, invoice_number)` | `duplicate_invoice` |

**Check 5 is the one to explain.** Internal consistency proves nothing on its own: an invoice
with a whole line item missed still adds up perfectly, because the lines that *were* read sum
correctly. Comparing against the 小計/消費税/合計 **printed on the page** is the only check
that catches an *extraction* error rather than an arithmetic one. That is why an absent
printed subtotal, tax or total is a failure and is never computed from the lines — substituting
`sum(lines)` would leave validate comparing its own arithmetic against itself and always
agreeing. The tax recomputation floors per tax code exactly as `accounting_api.py` does.

**Check 7 verifies the AI against ourselves.** Two independent mechanisms read one string — a
language model, and regex plus calendar arithmetic. Agreement is evidence; disagreement means
one is wrong and we cannot tell which, so a human decides. A model reading that came back empty
is an abstention, not a disagreement — it is recorded as a note. Read honestly this is mostly a
free second opinion on *our own parser*, which is where it pays: era offsets and relative due
dates are where the parser is weakest.

**Supplier resolution carries the most weight of any check.** `partner_code` decides who
receives the money, and it is the one field whose being wrong produces no error at all — a bad
total returns 422 and a bad date returns 400, but a wrong partner registers cleanly and pays
the wrong company. So the **LLM never picks it**: the master is five deterministic rows, this
is a lookup, not an inference. 登録番号 is `T` + 13 digits, so an OCR slip produces a value
that fails to match rather than one that matches the *wrong* partner. The partner index is
built once per run from `GET /partners` and raises at startup if two partners collide — master
data to fix loudly, never to resolve silently at match time. Every match is recorded with its
raw value, normalized value and resolved code, so an auditor sees *how* the partner was chosen.

**Tax codes are derived, not defaulted.** 9 of the 10 invoices that reach validate print no
per-line 税率 column, so without deriving something almost nothing registers. A single
candidate is tried for every unmarked line — `T10`, then `T08` — and accepted **only if the tax
recomputed from the resulting split equals the printed 消費税**. That printed figure is a
second, independent, human-authored source, so a wrong guess surfaces as `tax_mismatch` instead
of registering silently. Where every line carries its own printed mark, those are used as-is
and nothing is derived — that is how a mixed-rate invoice registers on its own marks. The
`該当なし` unit default is safe for the opposite reason: it enters no arithmetic anywhere. Both
are recorded as notes naming the affected rows.

Passing produces a `RegistrationPayload` that is **postable verbatim** — no reshaping, no
provenance fields. No guard sits between the last check and the payload: the payload model
declares those fields non-optional, so a future edit that broke the invariant raises a pydantic
error naming the exact field, which an `assert` would not (it is stripped under `python -O`).

Every `detail` string is written for the accounting clerk who has to key the invoice by hand —
plain language naming the Japanese label to look for on the paper, never an exception type.

### 4.5 Register — the one irreversible step

[ingest/accounting.py](ingest/accounting.py) owns every HTTP call the pipeline makes and
**fails closed**: an unreachable server, a non-2xx, an unreadable body or `success: false` all
raise. An empty partner master raises too — silently failing every supplier lookup is exactly
the failure this pipeline exists to prevent.

`register` runs inline, the moment an invoice passes validate. Outcomes are sorted into three
buckets, not two:

| Response | Bucket | Why |
|---|---|---|
| `201` | `registered` | The ledger's own receipt is recorded |
| `409 DUPLICATE_INVOICE` | **`skipped`** | Not a failure — the accounting system independently reached validate's own verdict and wrote nothing. This is the guard against the double payment the client described |
| `400` / `422` | `failed` | Every one of these is a check validate already runs locally, so it means **our gate has a gap**; the reason code names which check disagreed |
| `401` / `404` | `failed` | A configuration problem, not a document problem — nothing is wrong with the invoice |
| no response, or an unreadable `201` | `failed` | `api_unreachable` / `unconfirmed_registration` |

**No retries.** A POST whose answer never arrived may still have registered; re-sending it is
how you pay twice. The clerk is told to check the ledger, never to resend. `FailedRegistration`
keeps the clerk's `detail` separate from the API's verbatim `api_message`, which is the
engineer's evidence.

Duplicates are normally caught earlier: `main.py` seeds validate's `seen_keys` from
`GET /invoices` at startup, and a key is added only after that invoice passes — so the *second*
copy within a batch is the one that fails, not the first.

### 4.6 Report — derive, never decide

[ingest/report.py](ingest/report.py) folds the four stage files into one flat list, one row per
document, in `output/review_report.json`:

```jsonc
{"file_name": "invoice_09.pdf", "extracted": true, "has_handwriting": false,
 "normalized": true, "validated": false, "registered": false,
 "reason": ["total_mismatch: The 合計 printed on this invoice is ¥147,497, but the subtotal and tax add up to ¥147,496. ..."]}
```

Every string here was written by the stage that produced it — nothing re-runs a check or
invents a verdict, so the report cannot disagree with the audit files. Flags mean *reached and
passed*, not *attempted*. A registered invoice carries **no reason at all**, which makes a
non-empty `reason` mean exactly one thing: somebody needs to look at this.

`python -m ingest.report` rebuilds it from whatever is on disk, with no pipeline run and no API
key.

## 5. Outputs

```
output/
  extract.json         verbatim model output per document + per-call tokens, latency, cost
  normalize.json       converted values, each next to the printed string it came from
  validate.json        the resolved supplier, the exact payload, every note and every issue
  register.json        registered / skipped / failed, with the body sent and the response
  review_report.json   one row per document — start here
```

`GET /invoices` returns only a `line_count`, so these files are the **only** record of what was
actually read. Each stage record embeds the one above it, so any single file audits itself
without a cross-reference.

## 6. API constraints, read from the source rather than the prose

- `unit` is **mandatory and non-empty** — §4 of the assignment never says so
- every amount must be a JSON `int`; a `1.5` quantity cannot be sent (it becomes `null`, and
  the value survives in `amount`)
- tax is **floored per tax code**, then summed — not floored on the grand total
- `subtotal` must equal `Σ line.amount` exactly
- the duplicate check runs **before** the business rules, so a 409 precedes any 400/422
- there is **no update and no single-invoice delete**; `DELETE /invoices` wipes everything

That last one is the argument for putting all judgment *before* the POST.

## 7. The single command

```bash
./run.sh
```

Creates or reuses `.venv`, installs `requirements.txt`, then reuses an accounting API already
listening on `:8080` — ledger and all — or starts one and stops it again at exit. It only ever
kills a server it started itself: a script that kills the server you are debugging in another
terminal is a script you cannot run. The ledger is never cleared automatically; a re-run
against a populated ledger is *supposed* to report duplicates, and that is the demo.

`ANTHROPIC_API_KEY` comes from `.env`. `ACCOUNTING_API_URL` / `ACCOUNTING_API_KEY` default to
`http://localhost:8080` and `demo-key-1234`, so the pipeline runs unconfigured.

## 8. Build order

| # | Step | Done means |
|---|---|---|
| 1 | `schema.py`, `prompt.py`, `extract.py` | All 12 produce schema-valid verbatim JSON |
| 2 | `convert.py`, `normalize.py` | Every raw value converts, or becomes `None` next to the string it came from |
| 3 | `accounting.py`, `validate.py` | Each invoice gets a verdict with named reasons, and a postable payload |
| 4 | `register.py` | 201s recorded; 409 separated from failures; no retries |
| 5 | Cost capture in `extract.py` | Cost per invoice is measured, not guessed |
| 6 | `report.py` | One file answers "what happened to the twelve invoices" |
| 7 | `run.sh` | One command, from a clean clone, end to end |

