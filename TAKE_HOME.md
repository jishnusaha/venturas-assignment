# Take-Home Assignment: Automating Invoice Intake

**Position:** AI Agent Engineer
**Expected effort:** 8 hours
**Deadline:** 1 week from the day you receive this

Everything you need is in this folder: this document and the sample invoices.
Read the client request in section 1 first.

---

## 1. The client request

> This email is the **only requirement document** you will get.
> You cannot ask the client follow-up questions — this is a take-home, not an interview.
> Write down the questions you wanted to ask in your submission instead.

**From:** Seiichi Yamashita (CEO, Sample Trading Co., Ltd.)
**To:** Engineering
**Subject:** Can we do something about invoice data entry?

Hello,

Our accounting staff types invoices into our accounting system by hand, one by one,
as they arrive from suppliers every month. Every month-end close turns into overtime,
and last month a typo nearly caused us to pay the same invoice twice.

I hear AI can read invoices these days. Could we do that here?
I would like to see something working first.

The invoices come in all sorts of layouts depending on the supplier. Some arrive as PDFs,
some as scans from the office copier. Sometimes people write on them by hand.
I am attaching the last two months as samples — could you try with those?

We want to keep using our current accounting system. I am told it has an API,
so I asked our staff to send you the details.

Thanks.

---

**P.S. from the CEO's assistant**

Here are the accounting system API details from our IT contact.

- Endpoint: `http://localhost:8080`
- Authentication: `X-API-Key: demo-key-1234`
- How to start it: see section 3 of this document

The sample invoices are in the `invoices/` folder.

---

## 2. What we are looking at

**We are evaluating how you work, not how much you finish.**

The request above says only "read them with AI and enter them automatically."
Deciding *what to build* is the core of this assignment.
We care less about clean code than about the following.

- How you understood the goal behind the request, and what you decided to build
- Where you drew the line between automation and human review
- How you verified what the AI produced
- How you dealt with the constraints of the existing accounting system
- What it would cost, break, or risk once it runs in production

## 3. What is in this folder

| Path | Contents |
|---|---|
| `TAKE_HOME.md` | This document. The assignment, the API reference, and the submission template |
| `invoices/` | 12 sample invoices covering the last two months |

The invoices are:

| File | Type |
|---|---|
| `invoices/invoice_01.pdf` | PDF (text layer) |
| `invoices/invoice_02.pdf` | PDF (text layer) |
| `invoices/invoice_03.pdf` | PDF (text layer) |
| `invoices/invoice_04.jpg` | Scanned image |
| `invoices/invoice_05.jpg` | Scanned image |
| `invoices/invoice_06.jpg` | Scanned image |
| `invoices/invoice_07.jpg` | Scanned image |
| `invoices/invoice_08.jpg` | Scanned image |
| `invoices/invoice_09.pdf` | PDF (scanned image) |
| `invoices/invoice_10.jpg` | Scanned image |
| `invoices/invoice_11.jpg` | Scanned image |
| `invoices/invoice_12.jpg` | Scanned image |

Three of them are PDFs with an embedded text layer. Eight are scanned images.
One is a PDF that contains only a scanned image. Layouts differ by supplier.

### A note on language

The invoices are **Japanese business documents**, because the client is a Japanese company.
This reflects the real work of this role.

**You do not need to read Japanese yourself.** Current models handle Japanese invoices well,
and that is precisely what you are being asked to build on top of.
To make sure the language is not the obstacle, here are the field labels you will meet:

| Japanese | Meaning |
|---|---|
| 請求書 / 御請求書 | Invoice |
| 請求書番号 | Invoice number |
| 発行日 | Issue date |
| お支払期日 | Due date |
| 品名・摘要 | Description |
| 数量 | Quantity |
| 単位 | Unit |
| 単価 | Unit price |
| 金額 | Amount |
| 小計 | Subtotal |
| 消費税 | Consumption tax |
| 税率 | Tax rate |
| 合計 / 御請求金額 | Total |
| 登録番号 | Tax registration number |
| 御中 | Addressed to (company) |
| お振込先 | Bank transfer details |

Supplier names in the API's partner master are also in Japanese.
Matching what is printed on an invoice to that master is part of the assignment.

### Starting the accounting system API

The API is a small mock, but **you cannot change its specification.**
Treat it as the real system you have to integrate with.

It needs **Python 3.9 or newer and nothing else** — no pip install, no Docker.
Copy the code block at the end of this document (section 8) into a file named
`accounting_api.py`, then run:

```bash
python3 accounting_api.py
curl http://localhost:8080/health
```

Registered invoices are held in memory only. Restart the process, or call
`DELETE /invoices`, to start over. You can retry as often as you like.

## 4. The accounting system API

Every endpoint except `/health` requires the API key header:

```
X-API-Key: demo-key-1234
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check (no auth) |
| GET | `/partners` | Supplier master. **Only suppliers listed here can be registered** |
| GET | `/tax-codes` | Tax code list |
| POST | `/invoices` | Register an invoice |
| GET | `/invoices` | List registered invoices |
| DELETE | `/invoices` | Delete all (to start over) |

### Registering an invoice

```bash
curl -X POST http://localhost:8080/invoices \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-key-1234' \
  -d '{
    "partner_code": "P-1001",
    "invoice_number": "YM-2026-0107",
    "issue_date": "2026-01-07",
    "due_date": "2026-02-28",
    "currency": "JPY",
    "lines": [
      {"description": "Precision part A-100", "quantity": 120, "unit": "pcs",
       "unit_price": 1250, "amount": 150000, "tax_code": "T10"},
      {"description": "Packing and freight", "quantity": null, "unit": "lot",
       "unit_price": null, "amount": 18000, "tax_code": "T10"}
    ],
    "subtotal": 168000,
    "tax_amount": 16800,
    "total_amount": 184800
  }'
```

Every response uses the same envelope:

```jsonc
// success
{"success": true, "data": { /* ... */ }, "error": null}

// failure
{"success": false, "data": null,
 "error": {"code": "AMOUNT_MISMATCH", "message": "...", "details": { /* ... */ }}}
```

### What the API accepts

- **Dates**: `YYYY-MM-DD` only. Anything else is rejected
- **Amounts**: integers in JPY. No decimals
- **Tax**: a tax code (`T10` / `T08`), not a rate
- **Supplier**: a `partner_code` that exists in `GET /partners`
- **Lines**: at least one. `quantity` and `unit_price` may be `null`, but `amount` is required

### Error codes

| HTTP | code | Meaning |
|---|---|---|
| 401 | `UNAUTHORIZED` | Missing or wrong API key |
| 400 | `PARTNER_NOT_FOUND` | Supplier is not in the master |
| 400 | `UNKNOWN_TAX_CODE` | Unknown tax code |
| 400 | `DUE_DATE_BEFORE_ISSUE_DATE` | Due date precedes issue date |
| 409 | `DUPLICATE_INVOICE` | Same invoice number already registered for that supplier |
| 422 | `AMOUNT_MISMATCH` | Subtotal, tax, or total does not match the lines |
| 422 | `VALIDATION_ERROR` | Malformed types or formats |

`AMOUNT_MISMATCH` means the system did not take your numbers at face value:
it recalculated them from the line items. Tax is computed **per tax code, on the
subtotal for that code, rounded down**.

## 5. What to build

**Required**

1. Read the invoices and turn them into structured data
2. A way to verify what was read — at least one check, and you should be able to explain why you chose it
3. Registration into the accounting API
4. **A single command to start it** (document it in your own README)

**Optional — this is where candidates differentiate themselves**

- A screen for a human to review and correct the extracted data
- Handling for low-confidence extractions
- A cost estimate for running this in production, and what limits it hits at scale

## 6. Constraints

- **The expected effort is 8 hours, and you will not finish everything in 8 hours.**
  That is intentional. Tell us what you cut and why, in section 3 of your submission.
  We rate a submission that explains its priorities within 8 hours **higher** than one
  that implements every feature by spending far more time.
- The deadline is one week from the day you receive this.
- Python or TypeScript is preferred. If you choose something else, explain why.
- Any framework, library, or cloud service is fine.
- **Using AI (Claude, ChatGPT, Gemini, and so on) is assumed.** We are not testing whether
  you can work without it — we want to see how you use it. We do not expect raw AI output
  to be submitted as-is.
- **You need to provide your own LLM API key. We do not supply one.**
  A free tier, trial credit, or a local model is perfectly acceptable.
  Tell us which you chose and why, in section 4 of your submission.
  There are only 12 sample invoices, so the volume itself is small.

## 7. What to submit

1. **Your source code** (a Git repository or a zip)
2. **Your completed submission document** — copy the template below into `SUBMISSION.md`
3. **Something that shows it running** — a demo video of 3 minutes or less, or screenshots

The submission document carries the most weight. Code alone cannot be evaluated.

### Scoring

| Area | Points |
|---|---|
| Requirements and scoping | 30 |
| Design and technology choices | 20 |
| Implementation (does it run, verification of AI output, integration) | 30 |
| Production considerations (cost, limits, risk) | 10 |
| Communication (submission document, demo) | 10 |

### Questions

If anything about the assignment itself is unclear, ask us.
We will not answer questions about *what you should build* — that is part of the assignment.
Write those down in section 2 of your submission instead.

---

## Submission template

> Copy everything between the two lines below into a file named `SUBMISSION.md`.
> **Do not change the headings (`##` lines)** — we read them programmatically.
> Delete the quoted guidance as you fill each section in.
> Around 3-4 pages in total is a good target. We look at specificity, not length.

--- copy from here ---

# Submission

- Name:
- Submission date (YYYY-MM-DD):
- Hours actually spent:
- Repository / how to run it:

## 1. Understanding the request

> Reading the client's email, what did you decide was the problem worth solving?
> State both the problem the client described and the problem you actually set out to solve.

## 2. What you would have asked the client

> List the questions you wanted to ask, assuming you could not ask them.
> For each one, state **the assumption you made instead of waiting for an answer**,
> and why you made it. We are looking for questions paired with assumptions, not questions alone.

| What you wanted to ask | The assumption you made | Why |
|---|---|---|
|  |  |  |

## 3. Scoping decisions

> This assignment does not fit in 8 hours. What did you include, what did you cut,
> and why in that order?

**What you built**

**What you left out, and why**

## 4. Design and technology choices

> Describe the flow end to end and why you chose your main components.
> A diagram helps but is not required.
> Say what you chose **and what you decided against**.
> Include which LLM or OCR service you used and why — a free tier or local model is a valid answer.

## 5. How you used AI, and how you checked it

> Which parts of the work did you hand to AI, and how did you instruct it?
> Then: **where did you not trust the output**, and how did you check it?
> We care more about the reasoning here than the implementation.

**What you delegated to AI**

**How you verified the output**

**A case where the AI got it wrong** (one example is enough, if you have one)

## 6. Integrating with the accounting system

> How did you handle the API's constraints?
> If any invoice could not be registered, explain how your design handles it.

| Invoice | Result | How you handled it |
|---|---|---|
|  |  |  |

## 7. Cost, limits, and risk in production

> Rough numbers are fine, but show your reasoning.

- **Cost per invoice** (and what makes it up):
- **Monthly cost at 1,000 invoices per month**:
- **Processing time per invoice**:
- **Where this breaks first**:
- **How you would find out if something was registered incorrectly**:

## 8. What you would do with another 8 hours

> Up to three items, in priority order, and why that order.

1.
2.
3.

--- copy to here ---

---

## 8. Accounting system API source

Save this block as `accounting_api.py` and run `python3 accounting_api.py`.
Requires Python 3.9+ and no third-party packages.

You may read this code. You may not change its behaviour —
your submission is evaluated against the API exactly as it is here.

```python
"""配布用のモック会計 API（標準ライブラリのみ、単一ファイル）。

課題を Markdown 1 枚で配るため、Docker も pip も要らない形にしてある。
build_distribution.py が [
  {
    "partner_code": "P-1001",
    "name": "株式会社山田製作所",
    "aliases": [
      "ヤマダ製作所",
      "山田製作所"
    ],
    "registration_no": "T1010001000101"
  },
  {
    "partner_code": "P-1002",
    "name": "有限会社佐藤商店",
    "aliases": [
      "佐藤商店"
    ],
    "registration_no": "T2020002000202"
  },
  {
    "partner_code": "P-1003",
    "name": "東京フーズ株式会社",
    "aliases": [
      "東京フーズ"
    ],
    "registration_no": "T3030003000303"
  },
  {
    "partner_code": "P-1004",
    "name": "大阪機械工業株式会社",
    "aliases": [
      "大阪機械",
      "大阪機械工業"
    ],
    "registration_no": "T4040004000404"
  },
  {
    "partner_code": "P-1005",
    "name": "みらいITソリューションズ株式会社",
    "aliases": [
      "みらいIT",
      "みらいITソリューションズ"
    ],
    "registration_no": "T5050005000505"
  }
] を取引先マスタで置換し、
TAKE_HOME.md のコードブロックへ埋め込む。

挙動は旧 FastAPI 版と同じ。エラーコードと HTTP ステータスを変えないこと。
"""

import json
import math
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PARTNERS = json.loads(r"""[
  {
    "partner_code": "P-1001",
    "name": "株式会社山田製作所",
    "aliases": [
      "ヤマダ製作所",
      "山田製作所"
    ],
    "registration_no": "T1010001000101"
  },
  {
    "partner_code": "P-1002",
    "name": "有限会社佐藤商店",
    "aliases": [
      "佐藤商店"
    ],
    "registration_no": "T2020002000202"
  },
  {
    "partner_code": "P-1003",
    "name": "東京フーズ株式会社",
    "aliases": [
      "東京フーズ"
    ],
    "registration_no": "T3030003000303"
  },
  {
    "partner_code": "P-1004",
    "name": "大阪機械工業株式会社",
    "aliases": [
      "大阪機械",
      "大阪機械工業"
    ],
    "registration_no": "T4040004000404"
  },
  {
    "partner_code": "P-1005",
    "name": "みらいITソリューションズ株式会社",
    "aliases": [
      "みらいIT",
      "みらいITソリューションズ"
    ],
    "registration_no": "T5050005000505"
  }
]""")

TAX_RATES = {"T10": 0.10, "T08": 0.08}
API_KEY = "demo-key-1234"
PORT = 8080
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

STATUS_BY_CODE = {
    "UNAUTHORIZED": 401,
    "PARTNER_NOT_FOUND": 400,
    "UNKNOWN_TAX_CODE": 400,
    "DUE_DATE_BEFORE_ISSUE_DATE": 400,
    "DUPLICATE_INVOICE": 409,
    "AMOUNT_MISMATCH": 422,
    "VALIDATION_ERROR": 422,
    "NOT_FOUND": 404,
}

_records = []


def _error(code, message, details=None):
    return {"code": code, "message": message, "details": details}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _check_shape(payload):
    """型と書式の検証。FastAPI 版の pydantic スキーマに相当する。"""
    if not isinstance(payload, dict):
        return _error("VALIDATION_ERROR", "Request body must be a JSON object")

    for field in ("partner_code", "invoice_number", "issue_date", "due_date"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            return _error("VALIDATION_ERROR", f"'{field}' must be a non-empty string")

    for field in ("issue_date", "due_date"):
        if not DATE_PATTERN.match(payload[field]):
            return _error(
                "VALIDATION_ERROR",
                f"'{field}' must be formatted as YYYY-MM-DD",
                {"received": payload[field]},
            )
        try:
            date.fromisoformat(payload[field])
        except ValueError:
            return _error(
                "VALIDATION_ERROR",
                f"'{field}' is not a real date",
                {"received": payload[field]},
            )

    if payload.get("currency", "JPY") != "JPY":
        return _error(
            "VALIDATION_ERROR",
            "Only JPY is supported",
            {"received": payload.get("currency")},
        )

    for field in ("subtotal", "tax_amount", "total_amount"):
        if not _is_int(payload.get(field)):
            return _error(
                "VALIDATION_ERROR",
                f"'{field}' must be an integer amount in JPY (no decimals)",
                {"received": payload.get(field)},
            )

    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines:
        return _error("VALIDATION_ERROR", "'lines' must contain at least one entry")

    for index, item in enumerate(lines):
        if not isinstance(item, dict):
            return _error("VALIDATION_ERROR", f"lines[{index}] must be an object")
        if not isinstance(item.get("description"), str) or not item["description"]:
            return _error(
                "VALIDATION_ERROR", f"lines[{index}].description is required"
            )
        if not isinstance(item.get("unit"), str) or not item["unit"]:
            return _error("VALIDATION_ERROR", f"lines[{index}].unit is required")
        if not _is_int(item.get("amount")):
            return _error(
                "VALIDATION_ERROR",
                f"lines[{index}].amount must be an integer amount in JPY",
                {"received": item.get("amount")},
            )
        if not isinstance(item.get("tax_code"), str):
            return _error("VALIDATION_ERROR", f"lines[{index}].tax_code is required")
        for optional in ("quantity", "unit_price"):
            if item.get(optional) is not None and not _is_int(item[optional]):
                return _error(
                    "VALIDATION_ERROR",
                    f"lines[{index}].{optional} must be an integer or null",
                    {"received": item.get(optional)},
                )
    return None


def _find_partner(partner_code):
    return next((p for p in PARTNERS if p["partner_code"] == partner_code), None)


def _check_business_rules(payload):
    """中身の整合。送られてきた金額は信用せず明細から再計算する。"""
    if not _find_partner(payload["partner_code"]):
        return _error(
            "PARTNER_NOT_FOUND",
            f"Unknown partner code: {payload['partner_code']}",
            {"partner_code": payload["partner_code"]},
        )

    unknown = sorted(
        {item["tax_code"] for item in payload["lines"] if item["tax_code"] not in TAX_RATES}
    )
    if unknown:
        return _error(
            "UNKNOWN_TAX_CODE",
            f"Unknown tax code(s): {', '.join(unknown)}",
            {"unknown_tax_codes": unknown, "known": sorted(TAX_RATES)},
        )

    if date.fromisoformat(payload["due_date"]) < date.fromisoformat(
        payload["issue_date"]
    ):
        return _error(
            "DUE_DATE_BEFORE_ISSUE_DATE",
            "due_date is earlier than issue_date",
            {"issue_date": payload["issue_date"], "due_date": payload["due_date"]},
        )

    expected_subtotal = sum(item["amount"] for item in payload["lines"])
    if payload["subtotal"] != expected_subtotal:
        return _error(
            "AMOUNT_MISMATCH",
            "subtotal does not match the sum of the line amounts",
            {
                "expected_subtotal": expected_subtotal,
                "received_subtotal": payload["subtotal"],
            },
        )

    subtotal_by_code = {}
    for item in payload["lines"]:
        subtotal_by_code[item["tax_code"]] = (
            subtotal_by_code.get(item["tax_code"], 0) + item["amount"]
        )
    tax_by_code = {
        code: math.floor(subtotal * TAX_RATES[code])
        for code, subtotal in subtotal_by_code.items()
    }
    expected_tax = sum(tax_by_code.values())
    if payload["tax_amount"] != expected_tax:
        return _error(
            "AMOUNT_MISMATCH",
            "tax_amount does not match the tax recalculated from the lines",
            {
                "expected_tax": expected_tax,
                "received_tax": payload["tax_amount"],
                "expected_tax_by_code": tax_by_code,
            },
        )

    expected_total = expected_subtotal + expected_tax
    if payload["total_amount"] != expected_total:
        return _error(
            "AMOUNT_MISMATCH",
            "total_amount does not match the amount recalculated from the lines",
            {
                "expected_total": expected_total,
                "received_total": payload["total_amount"],
                "expected_tax_by_code": tax_by_code,
            },
        )
    return None


def _register(payload):
    global _records

    record = {
        "accounting_id": f"ACC-{len(_records) + 1:04d}",
        "partner_code": payload["partner_code"],
        "invoice_number": payload["invoice_number"],
        "issue_date": payload["issue_date"],
        "due_date": payload["due_date"],
        "subtotal": payload["subtotal"],
        "tax_amount": payload["tax_amount"],
        "total_amount": payload["total_amount"],
        "line_count": len(payload["lines"]),
    }
    _records = [*_records, record]
    return record


def _create_invoice(payload):
    """POST /invoices の本体。(status, body) を返す。"""
    error = _check_shape(payload)
    if error:
        return STATUS_BY_CODE[error["code"]], {
            "success": False,
            "data": None,
            "error": error,
        }

    already_registered = any(
        r["partner_code"] == payload["partner_code"]
        and r["invoice_number"] == payload["invoice_number"]
        for r in _records
    )
    if already_registered:
        error = _error(
            "DUPLICATE_INVOICE",
            "This invoice number is already registered for this partner",
            {
                "partner_code": payload["partner_code"],
                "invoice_number": payload["invoice_number"],
            },
        )
        return 409, {"success": False, "data": None, "error": error}

    error = _check_business_rules(payload)
    if error:
        return STATUS_BY_CODE[error["code"]], {
            "success": False,
            "data": None,
            "error": error,
        }

    return 201, {"success": True, "data": _register(payload), "error": None}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, message_format, *args):
        print(f"  {self.command} {self.path} -> {args[1]}")

    def _send(self, status, body):
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error_code(self, code, message):
        self._send(
            STATUS_BY_CODE[code],
            {"success": False, "data": None, "error": _error(code, message)},
        )

    def _authorized(self):
        if self.headers.get("X-API-Key") == API_KEY:
            return True
        self._send_error_code("UNAUTHORIZED", "Missing or invalid X-API-Key header")
        return False

    def do_GET(self):
        if self.path == "/health":
            self._send(
                200,
                {
                    "success": True,
                    "data": {"status": "ok", "registered_invoices": len(_records)},
                    "error": None,
                },
            )
            return
        if not self._authorized():
            return
        if self.path == "/partners":
            self._send(200, {"success": True, "data": {"partners": PARTNERS}, "error": None})
        elif self.path == "/tax-codes":
            tax_codes = [
                {"tax_code": code, "rate": rate, "label": f"Consumption tax {int(rate * 100)}%"}
                for code, rate in TAX_RATES.items()
            ]
            self._send(
                200, {"success": True, "data": {"tax_codes": tax_codes}, "error": None}
            )
        elif self.path == "/invoices":
            self._send(
                200, {"success": True, "data": {"invoices": list(_records)}, "error": None}
            )
        else:
            self._send_error_code("NOT_FOUND", f"No such endpoint: {self.path}")

    def do_POST(self):
        if not self._authorized():
            return
        if self.path != "/invoices":
            self._send_error_code("NOT_FOUND", f"No such endpoint: {self.path}")
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_error_code("VALIDATION_ERROR", "Request body is not valid JSON")
            return

        status, body = _create_invoice(payload)
        self._send(status, body)

    def do_DELETE(self):
        global _records

        if not self._authorized():
            return
        if self.path != "/invoices":
            self._send_error_code("NOT_FOUND", f"No such endpoint: {self.path}")
            return
        removed = len(_records)
        _records = []
        self._send(200, {"success": True, "data": {"removed": removed}, "error": None})


def main():
    print(f"Mock Accounting API listening on http://localhost:{PORT}")
    print(f"  API key: {API_KEY}")
    print("  Press Ctrl+C to stop.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
```
