# Rule catalogue

17 rules. Each is a small named function in `app/rules.py` returning a `Finding`
or `None`. The decision is a pure function of the findings — the worst one wins:
`REJECT > HOLD > NEEDS_REVIEW > AUTO_APPROVE`.

Every message is written for an AP clerk, not a developer.

| ID | Rule | Severity | Fires when |
|----|------|----------|-----------|
| R01 | Vendor resolved | info / **block → HOLD** | Letterhead matches no vendor above 80% similarity |
| R02 | Vendor is blocked | **block → REJECT** | Vendor status is `blocked` |
| R03 | Vendor is on hold | warn | Vendor status is `on_hold` |
| R04 | Invoice number present | **block → HOLD** | No invoice number on the document |
| R05 | Invoice date valid | warn | Missing, unparseable, or in the future |
| R06 | Invoice total present | **block → HOLD / REJECT** | No total (HOLD); zero or negative (REJECT — credit note) |
| R07 | PO matched | info / warn / **block → HOLD** | Explicit (info), inferred (warn), none or ambiguous (HOLD) |
| R08 | PO is open | **block → HOLD** | PO is closed |
| R09 | PO belongs to this vendor | **block → REJECT** | PO's vendor ≠ invoice's vendor |
| R10 | Currency matches PO | **block → HOLD** | Invoice currency ≠ PO currency — never silently converted |
| R11 | Amount within tolerance | info / warn / **block → HOLD** | Inside band (info); over band (warn); over 10% (HOLD) |
| R12 | PO not over-billed | info / **block → HOLD** | Cumulative billing exceeds a partial-billing PO |
| R18 | PO not already invoiced | **block → HOLD** | A single-invoice PO has already been billed |
| R13 | Not a duplicate | **block → REJECT** | Same vendor + invoice number already on the ledger |
| R14 | Not a near-duplicate | warn | Same vendor + amount within 90 days, different number |
| R15 | Invoice arithmetic | warn | Subtotal + tax ≠ stated total |
| R16 | Extraction confidence | warn | Any critical field read below 0.85 |
| R17 | Document is a scan | warn | No text layer — every figure was interpreted |

R18 is listed next to R12 because they are two halves of one question (may this
PO take another invoice?); it is numbered 18 because it was added last — see
`EDGE_CASES.md` EC-5.

## Thresholds

All in `app/config.py` — a finance controller's settings, not scattered constants.

| Setting | Value | Rationale |
|---------|-------|-----------|
| `TOLERANCE_PCT` | 2.0% | Freight and rounding on a normal order |
| `TOLERANCE_ABS` | $25 | Floor, so small POs aren't held over pennies (D-10) |
| `HARD_VARIANCE_PCT` | 10% | Beyond this it isn't the invoice we agreed to |
| `AUTO_APPROVE_CONFIDENCE` | 0.85 | Below it, a human verifies before money moves |
| `SCANNED_CONFIDENCE_CEILING` | 0.80 | Caps vision output under the bar by construction (D-07) |
| `NEAR_DUPLICATE_WINDOW_DAYS` | 90 | A quarter — long enough for a re-issued invoice |

## Testing

All 17 rules are covered without a single model call — the judgment layer needs no
network.

```
./.venv/bin/python -m pytest tests/ -q      # 69 passed in 0.7s
```
