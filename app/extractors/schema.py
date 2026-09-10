"""The contract the model must fill. Shared by the text and vision paths so
both produce identically-shaped output for the stages that follow."""

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}

INVOICE_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "vendor_name": STR,
            "vendor_tax_id": STR,
            "invoice_number": STR,
            "invoice_date": {**STR, "description": "ISO-8601 YYYY-MM-DD, or null"},
            "po_reference": STR,
            "currency": {**STR, "description": "ISO 4217 code, e.g. USD"},
            "subtotal": NUM,
            "tax_amount": NUM,
            "invoice_total": NUM,
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": STR,
                        "quantity": NUM,
                        "unit_price": NUM,
                        "amount": NUM,
                    },
                    "required": ["description", "quantity", "unit_price", "amount"],
                    "additionalProperties": False,
                },
            },
            "field_confidence": {
                "type": "object",
                "description": "0.0-1.0 per field you populated",
                "properties": {
                    "vendor_name": {"type": "number"},
                    "invoice_number": {"type": "number"},
                    "invoice_date": {"type": "number"},
                    "po_reference": {"type": "number"},
                    "currency": {"type": "number"},
                    "invoice_total": {"type": "number"},
                },
                "required": ["vendor_name", "invoice_number", "invoice_date",
                             "po_reference", "currency", "invoice_total"],
                "additionalProperties": False,
            },
            "extraction_notes": {**STR, "description": "Anything ambiguous, in one sentence"},
        },
        "required": ["vendor_name", "vendor_tax_id", "invoice_number", "invoice_date",
                     "po_reference", "currency", "subtotal", "tax_amount",
                     "invoice_total", "line_items", "field_confidence", "extraction_notes"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = """You extract structured data from vendor invoices for an accounts-payable process.

Rules you must follow:
- Report only what the document actually says. If a field is absent, return null.
- NEVER invent or infer an invoice number, PO reference, or date. A missing
  invoice number is a real and important signal downstream; a guessed one
  causes duplicate payments.
- Amounts are plain numbers: no currency symbols, no thousands separators.
  "USD 4,250.00" -> 4250.00. A figure in parentheses is negative.
- currency is the ISO 4217 code (USD, EUR, INR, GBP).
- invoice_total is the final amount payable, after tax and any discount. If the
  document shows both a subtotal and a gross total, invoice_total is the gross.
- If tax is embedded in the line items rather than shown separately, set
  tax_amount to null and say so in extraction_notes.
- A PO reference may be written many ways ("PO-4401", "P.O. 4401", "Ref 4401",
  "Order 4401"). Return it as written; do not normalise it.
- field_confidence is your honest read of legibility and ambiguity, 0.0-1.0.
  Lower it when a figure is blurred, cropped, or could be read two ways.

Output JSON only, matching the provided schema."""
