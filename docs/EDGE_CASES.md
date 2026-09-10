# Edge cases

The brief asks for 2–4 non-trivial edge cases. There are five, because the fifth
was found by the build rather than designed up front — see EC-5.

Each is a realistic AP scenario where the process must behave differently from
the happy path, and each is caught by a *different mechanism*. None is a
malformed-file test; every one of them is a document that looks perfectly
legitimate on its own.

---

## EC-1 · Scanned image with no text layer
**File:** `02_edge_scanned_freight.pdf` · **Decision:** `NEEDS_REVIEW`

A Dutch freight invoice that arrived as a flatbed scan — rotated 0.55°, blurred,
grain added, greyscale. It has **zero** extractable characters.

**What changes:** ingest detects the missing text layer and routes to the vision
path (an image content block rather than text). Every field is then *interpreted*
rather than read, so extraction confidence is capped at 0.80 regardless of how
certain the model sounds, which puts it under the 0.85 auto-approve bar.

**Why it is not trivial:** the naive version of this either fails on the scan or —
worse — succeeds and pays it. The interesting question isn't "can you OCR it",
it's "how much should a correct-looking OCR result be trusted with money".
The answer here is a policy, not a confidence score: **figures recovered from
pixels never release payment unattended** (`REQUIRE_TEXT_LAYER_FOR_AUTO_APPROVE`).

**Rules that fire:** R16 (low confidence), R17 (scanned document).

---

## EC-2 · Split billing that overruns its PO
**Files:** `03a` → `03b` → `03c` · **Decisions:** `AUTO_APPROVE`, `AUTO_APPROVE`, `HOLD`

Vertex Packaging bills a 27,000 PO in three releases: 12,000, then 10,500, then
6,200. The PO explicitly permits partial billing, so the first two are correct
and release automatically. The third is also individually reasonable — a 6,200
invoice against a 27,000 PO — but it takes the cumulative total to 28,700.

**What changes:** for a partial-billing PO the tolerance check applies to
everything charged to date, not to the invoice in hand. R12 reads the run
history, sums prior invoices, and blocks.

**Why it is not trivial:** **no single invoice is wrong.** This is only visible
if the process keeps state across runs, which is why run history is process
state rather than logging (D-14). The invoice that gets caught is the one where
nothing is individually suspicious.

**Rules that fire:** R11 (cumulative variance), R12 (over-billing) → HOLD.

---

## EC-3 · Duplicate re-issued on a different layout
**File:** `04_edge_duplicate_resubmit.pdf` · **Decision:** `REJECT`

Kestrel's invoice INV-KS-8841 arrives a second time — same number, same 3,150.00
— but rendered in a completely different template, so the file bytes and hash
differ entirely.

**What changes:** duplicate detection keys on *(vendor, invoice number)* resolved
from content, never on the file. The vendor is resolved by tax ID first, so a
re-typed letterhead doesn't defeat it either.

**Why it is not trivial:** hash-based dedupe is the obvious implementation and it
fails on exactly this case, which is also the common one — a vendor's system
re-generates the PDF on resend. The duplicate check also has to know that a
*rejected* invoice never went on the ledger, so a corrected resubmission isn't
falsely flagged as a duplicate of the thing we already refused.

**Rules that fire:** R13 (exact duplicate) → REJECT. R18 independently catches it too.

---

## EC-4 · No PO reference and no invoice number
**File:** `05_edge_no_po_no_number.pdf` · **Decision:** `HOLD`

An Acme Steel export invoice for 48,600.00. The "Invoice No.:" field is printed
on the form but was never filled in, and there is no PO reference — just
"Buyer's Order Ref.: as per contract".

**What changes:** two different kinds of missing data, handled differently.
- The **PO is inferable**: Acme has exactly one open PO whose value matches this
  amount, so the process infers PO-4401 — and flags that it inferred rather than
  read it. Had two POs fit, it would refuse to choose.
- The **invoice number is not inferable**, and it is the key duplicate detection
  runs on. Without it we cannot promise this won't be paid twice.

**Why it is not trivial:** the tempting behaviour is to auto-approve — the vendor
is approved, the amount matches a PO exactly, everything reconciles. The process
refuses anyway, and says precisely what would unblock it: *"ask the vendor to
re-issue with an invoice number."* Knowing which gaps you can close by inference
and which you must not is the whole judgment.

**Rules that fire:** R04 (no invoice number) → HOLD; R07 (PO inferred) → warn.

---

## EC-5 · A second invoice against a single-invoice PO
**File:** `09_edge_same_po_new_number.pdf` · **Decision:** `HOLD`

Kestrel bills PO-4403 again for exactly 3,150.00 under a genuinely new invoice
number, INV-KS-8907, a week later.

**What changes:** R18 checks whether a PO that does not permit partial billing
has already been invoiced.

**Why it is not trivial — and why it exists:** I did not design this one. It
surfaced while reviewing the live PO-consumption view, which showed PO-4401 at
**97,500 against a 48,600 order** with nothing flagged. Two invoices had each
matched the same non-partial PO in full and both passed, because R12 only guards
partial-billing POs and R11 only ever compares a single invoice to the PO value.

Every individual check is satisfied here: not a duplicate (the number is real and
new), vendor approved, PO open and correct, amount exactly on the PO, tolerance
untouched. Only a PO-level "has this order already been settled?" question sees
it. That is the double-payment pattern that costs real money, and it was invisible
to sixteen rules that all looked correct.

**Rules that fire:** R18 (PO already invoiced) → HOLD; R14 (near-duplicate) → warn.

---

## Coverage summary

| Edge case | Caught by | Mechanism that makes it possible |
|-----------|-----------|----------------------------------|
| EC-1 Scan | R16, R17 | Modality detection gates autonomy |
| EC-2 Split billing | R12 | Cumulative state across runs |
| EC-3 Duplicate | R13 | Content-keyed identity, not file hash |
| EC-4 Missing IDs | R04, R07 | Knowing what may and may not be inferred |
| EC-5 Same PO twice | R18 | PO-level settlement check |

Three of the five are invisible to any process that treats each invoice in
isolation. That was the design thesis: **most expensive AP mistakes are only
visible in context.**

## What I would add next

- **Credit notes.** Negative totals are rejected today; they deserve their own path.
- **Multi-page and multi-invoice PDFs.** Ingest reads page 1 only on the vision path.
- **Line-item level three-way match** against goods receipts, not just header totals.
- **FX tolerance** for a genuine cross-currency invoice, instead of a flat HOLD.
- **A learning loop**: reviewer overrides are recorded but not yet fed back into
  the thresholds they disagree with.
