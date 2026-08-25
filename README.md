# Invoice Intake Pipeline

Reads Japanese supplier invoices (PDF and scanned images), checks what the AI read, and
registers only the ones that are safe to register automatically. Everything else stops with a
named reason for a human.


## Requirements

- Python 3.9+
- An Anthropic API key

## Setup

```bash
cp .env.example .env      # then put your key in it
```

`.env` needs one line:

```
ANTHROPIC_API_KEY=sk-ant-...
```

`ACCOUNTING_API_URL` and `ACCOUNTING_API_KEY` are optional and default to
`http://localhost:8080` and `demo-key-1234`.

## Run it

```bash
bash run.sh
```

That is the whole thing. It creates `.venv`, installs `requirements.txt`, starts
`accounting_api.py` if nothing is already listening on `:8080`, and runs the pipeline over
every file in `invoices/`.

If an accounting API is already running, it is used as it is and left running afterwards.
Only a server that `run.sh` started itself is stopped at the end.

Output looks like this:

```
accounting API ok (status=ok) · 5 partner(s) · 0 invoice(s) already in the ledger

file                 extract    handwriting   normalize     validate     register
---------------------------------------------------------------------------------
invoice_01.pdf          ✓            ✗            ✓            ✓            ✓
invoice_04.jpg          ✓            ✓
invoice_09.pdf          ✓            ✗            ✓            ✗
...

12 document(s): 7 registered, 5 need(s) human review
```

In the handwriting column a ✓ means handwriting **was** found, which is why the row stops
there. A column the document never reached is left blank.

## What you get

```
output/
  review_report.json   one row per document — start here
  extract.json         what the model read, verbatim, plus tokens/latency/cost per call
  normalize.json       converted values, each next to the printed string it came from
  validate.json        resolved supplier, the exact payload, every note and every issue
  register.json        registered / skipped / failed, with the body sent and the response
```

`review_report.json` is the file a clerk works from. A registered invoice carries an empty
`reason`, so any row with a non-empty `reason` is one somebody needs to look at:

```jsonc
{"file_name": "invoice_09.pdf", "extracted": true, "has_handwriting": false,
 "normalized": true, "validated": false, "registered": false,
 "reason": ["total_mismatch: The 合計 printed on this invoice is ¥147,497, but the subtotal and tax add up to ¥147,496. ..."]}
```

Rebuild that report from the files already on disk, with no pipeline run and no API key:

```bash
.venv/bin/python -m ingest.report
```

## Running it again

The ledger is never cleared automatically. A second run against the same ledger is *supposed*
to report every invoice as a duplicate — that is the double-payment guard working. To start
from an empty ledger:

```bash
curl -X DELETE http://localhost:8080/invoices -H 'X-API-Key: demo-key-1234'
```


## Layout

```
ingest/
  main.py         the driver: startup checks, the per-document loop, writes every output
  prompt.py       the extraction system prompt
  extract.py      the vision call, plus per-call cost measurement
  schema.py       every pydantic model, one per stage, success and failure alike
  convert.py      pure conversion primitives: dates, numbers, tax marks, 登録番号
  normalize.py    printed strings -> the representations the API accepts
  validate.py     the gate: 8 checks, supplier resolution, payload construction
  accounting.py   the only module that talks to the accounting API
  register.py     POST /invoices, and how each response is routed
  report.py       folds the stage outputs into output/review_report.json
run.sh            the single command
accounting_api.py the mock accounting system, copied verbatim from TAKE_HOME.md
```

## Cost

Measured over the 12 samples with `claude-sonnet-5`: **~$0.032 per invoice**, ~10s per
document, so roughly **$32 per 1,000 invoices/month** for extraction. The numbers come from
`output/extract.json` (`totals`), not from an estimate.
