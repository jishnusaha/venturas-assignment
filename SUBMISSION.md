# Submission

- Name: Jishnu Saha
- Submission date (YYYY-MM-DD): 2026-08-25
- Hours actually spent: 9.5-10 hours
- Repository / how to run it: this repository — `cp .env.example .env`, add `ANTHROPIC_API_KEY`, then `./run.sh`. See [README.md](README.md).

## 1. Understanding the request

The email describes a data entry problem: staff type invoices in by hand and month-end close
turns into overtime. That is the problem the client stated. The sentence that stuck with me was
the other one, about a typo nearly causing the same invoice to be paid twice.

Before deciding what to build I read `accounting_api.py`. There is no update endpoint, and the
only delete wipes the whole ledger. So a registered invoice cannot be corrected — you would
have to clear everything and re-enter the month. That changed how I read the request. Typing
faster is the easy half. Typing faster and occasionally typing the wrong thing into a system
with no undo is worse than what they do today.

So, requirement is not to build a data entry robot. I have to build a pipeline that will somewhat validate the data reading from the invoice, filter out invalid invoices(wrong calculation, hand written modifications, duplicate invoice) and only register the invoices those are safe to be registered.


## 2. What you would have asked the client

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
| If an invoice from a supplier not in the master, what to do?  | Route to human review; never auto-create a partner | It is business call what to with the new partner. If they will be registered or not.  |
|  When the printed total/ subtotal/ tax disagrees with the sum/ calculation of the lines, what to do? | Routes to review | If calculation doesn't match then it must have some issues with the invoices. May be mistake from supplier or cheat from them.  |
| What is a duplicate — same invoice number, or same supplier and number? | Same, route to review `(partner_code, invoice_number)` | Two suppliers can legitimately use the same invoice number; the accounting API itself scopes its 409 the same way |
| A line with no unit printed, when the API requires one — what should it say? | `該当なし` ("not applicable"), recorded as default | The API rejects an empty `unit`, and this value enters no arithmetic anywhere, so a placeholder cannot corrupt a figure |
| Some invoices print no per-line tax at all. What rate applies? | Derive one rate and accept it **only if** the recomputed tax with that rate agrees with the the printed total | A defaulted rate would be a number nobody usually printed. So, if all the calculation is ok then we can try to derive a rate that has to reconcile against a human-printed figure. If we can't derive one then send it to human review |

## 3. Scoping decisions

**What you built**

1. `extract → normalize → validate → register` over all 12 invoices, in one command
2. Verification of the AI's reading: arithmetic recomputed against the printed values exactly as the accounting API recomputes it, plus a second independent reading of every date
3. A named-reason review queue: it says what to check for the invoices that failed to register 
4. An audit file per stage, so any registered figure can be traced back to the printed string
5. Extraction cost, tokens and latency measured per document rather than estimated


**What you left out, and why**

- **A review UI.** The queue is `output/review_report.json`. Next we can build a UI that will show a form for reviewed invoice where human can change data and submit. This requires more time to do
- **Retries, a queue, a database, concurrency.** if everything is passed until registration then there may be some case that calculation is wrong, or may be we derived wrong tax or wrong suppler. We may scope some auto retry behaviour to update the validation step. This will be a agentic feature we can implement where our agent can backtrack to old step and self correct itself and try again. Requires more time. And, of course a duplicate invoice will fail registration and we will not retry that.
- **Supplier matching by printed name.** Only the registration number is matched. A name-similarity fallback we can build when registration number is not available. We can scope it to next.
- **Unit tests.** We did some checking and some calculation. But did not write any testcase. Again time contraint. We have to scope this in next iteration.

## 4. Design and technology choices

```
 source file ──► extract ──► handwriting? ─► normalize ──► validate ──► register ──┬─► 201 registered
              (LLM-failed)      (yes)        (failed)      (failed)     (failed)   ├─► 409 skipped
                  │              │              │             │            │       └─► 4xx failed
                  └──────────────┴──────────────┴─────────────┴────────────┴─────────► human review
```

Python, four dependencies (`anthropic`, `pydantic`, `python-dotenv`, `requests`), no framework,
no database. Each stage is a function from the previous stage's model to its own, and each
stage's output is written to `output/` as JSON.

**The LLM lives in the extract stage only, and it returns verbatim printed strings.** `¥240,900`
comes back as `¥240,900`, `令和8年2月5日` as printed. Deterministic Python does every
conversion. If the model also did the date math and the yen parsing there would be one source
and nothing to check it against; splitting them means the conversion is reproducible from
`output/extract.json` at zero API cost, and every stage after extract is a pure function.

**Model: Claude Sonnet 5** (my own API key), one vision call per document via
`client.messages.parse(..., output_format=InvoiceExtraction)`, so the response comes back
already validated against the pydantic schema. It reads Japanese documents well, takes PDFs and
images natively — no rasterizing a scan-only PDF myself — and measured out at ~$0.03 per
invoice.


## 5. How you used AI, and how you checked it

**What you delegated to AI**

I used AI in two different place:

- In the product, exactly one step: extracting the data from the invoice and save according to schema. The system prompt is ten numbered rules ([ingest/prompt.py](ingest/prompt.py)) — verbatim
strings only; 
- In the development, I used Claude Code to write most of the implementation against a design. With the given invoices and [TAKE_HOME.md](TAKE_HOME.md) I first analysed the requirement and planned in different approaches how I can build it. Then I defined my scope, what to build, what not to build and most importantly how to build. A predefined target helped me to implement the things more faster cause I don't need to think here and there during the development time. I instructed claude code how to follow the plan and how to implement the plan step by step. The design plan: [plan/implementation-plan.md](plan/implementation-plan.md) .

**How you verified the output**

Four checks, all of them running on every invoice, not on a sample:

1. **Arithmetic against the printed figures.** The lines are re-summed, tax is recomputed per
   tax code and floored exactly as `accounting_api.py` does it, and the result is compared with
   the total/subtotal *printed on the page*.  The printed total is the
   independent second source, and it is the check that would have caught the client's near-miss.
2. **Two independent readings of every date.** The model returns both the printed string and its own reading of it as ISO; my parser converts the same string with
   regex plus calendar arithmetic. Two mechanisms, one expected answer. Disagreement means one
   is wrong and I cannot tell which, so it goes to a human.
3. **Supplier by exact key.** registration number, normalized, looked up in the master from `GET /partners`.
   No match is a review reason, never a guess.
4. **Duplicates before the POST.** `(partner_code, invoice_number)`, seeded from `GET /invoices`
   at startup so an invoice already in the ledger is caught before anything is sent.

A derived tax code has to survive check 1 before it is accepted, which is what makes deriving
one safe rather than a fallback.

**A case where the AI got it wrong**

Did not notice anything yet.


## 6. Integrating with the accounting system

Constraints I read out of `accounting_api.py` rather than the prose, because they are not all in
the prose: `unit` is mandatory and non-empty; every amount must be a JSON integer, so a quantity
of `1.5` cannot be sent at all; tax is floored **per tax code** and then summed, not floored on
the grand total; `subtotal` must equal the sum of the line amounts exactly; the duplicate check
runs *before* the business rules; and there is no update endpoint and no per-invoice delete.

My validator mirrors each of those rules locally and runs before the POST. That is the whole
design consequence of "no update endpoint": every judgment happens while a mistake is still
cheap. It also means a 4xx error from the API is not merely a bad invoice — it is a **gap in my own
gate**, a place where my pipeline need correction/improvement. So each registration failure reason names which of my checks disagreed with the ledger.
On this run there were none.

| Invoice | Result | How you handled it |
|---|---|---|
| 01, 02, 03, 05, 06, 11, 12 | Registered (`ACC-0001`–`ACC-0007`) | All checks passed. 6 of them had no per-line tax printed; `T10` was derived and accepted only because the recomputed tax matched the printed total value. `invoice_03.pdf` carried real mixed 8%/10% marks and those are defined cleary, although I checked them, did not believed blindly |
| 04 | Review — handwriting | A blue text stamp near the addressee. Stopped before normalize; the note says where the mark is |
| 08 | Review — handwriting | A red handwritten text on the bottom of the page. Sent for review |
| 07 | Review — `duplicate_invoice` | Same supplier and invoice number (`YM-2026-0107`, P-1001) as `invoice_01.pdf`. Caught locally in validation step before any registration request to accounting api |
| 09 | Review — `total_mismatch` | Printed ¥147,497 against ¥147,496 recomputed from its own lines. Caught in validation step and sent for review |
| 10 | Review — `supplier_not_in_master` | `T9090009000909` is in not in partner record. Never auto-created sent for review |

Registration failures that the API *could* return are sorted into buckets that need different
people: a `409` is its own bucket (the system agreeing with my own duplicate verdict — nothing
was written), a `400`/`422` is a defect in my gate, and a `401` or a connection failure is
infrastructure, not a document problem, so it never enters the clerk's queue. A `201` whose
confirmation could not be read is reported as "check the ledger, do not resend".

## 7. Cost, limits, and risk in production

Numbers below are measured from `output/extract.json`, not estimated — every call records its
own tokens and latency.

- **Cost per invoice**: **$0.032**. 12 documents used 80,114 input and 9,369 output tokens —
  about 6,700 input tokens (the image or PDF plus the prompt) and 780 output tokens per invoice. At
  Sonnet 5 list price ($3.00 / $15.00 per Mtok) that is $0.020 input + $0.012 output. 
- **Monthly cost at 1,000 invoices per month**: **~$32** in model spend. 
- **Processing time per invoice**: 9.95s mean API latency; the 12 documents ran in about two
  minutes, sequentially.
- **Where this breaks first**: 
   - the **handwriting rule** — any mark at all routes to a human,
  and a company that handwrite on everything would send its whole month to review. 
  - An invoice with **mixed tax rates and no per-line 税率 column** is not resolvedin the current system — one rate is
  tried for all unmarked lines, if a rate don't satisfy the line then tried with other rate. Mixture of rates in different line item is not resolved. 
  - The loop is sequential
  and single-process — fine at 1,000/month, not at 10,000. 
  - The duplicate check depends on
  `GET /invoices` at startup, and the mock's ledger is in memory. 
- **How you would find out if something was registered incorrectly**:  we are logging every step output in a json file inside /output folder. So if anything goes wrong we can traceback where it goes wrong and where we need to fix.

## 8. What you would do with another 8 hours

1. **A review screen for the five invoices that stopped.** The reasons, a proposed payload and
   the source document side by side, where human can update and resubmit. Everything it needs already  exists in `output/`; today a clerk reads JSON. 
2. **Supplier fallback matching by name and alias.** Today a missing or unmatched registration number is a
   dead stop (`invoice_10.jpg`). `GET /partners` already returns aliases; a name match, proposed
   but never auto-registered, would turn a dead stop into a one-click confirmation. Second
   because it is the most common review reason that a human resolves in seconds.
3. **Run in async way.** Currently invoices are handled one by one synchronously. Registering invoice is indepened task. No invoice depend on other. And the root bottlneck is the LLM api call that takes almost 10 second per invoice, rest of the step is done almost instantly. As it is I/O bound task, we can convert this to async and finish the task more faster. 
4. **Close the loop on registration failures.** Every 4xx from the accounting API means my
   validator missed something it already knows how to check. If any conditing that accounting server has but we missed it in our pipeline we investigate it and fix. 