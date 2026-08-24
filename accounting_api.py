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
