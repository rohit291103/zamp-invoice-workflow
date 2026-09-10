# Process map

Written before any code, per the brief's first piece of guidance. This is the
map the implementation follows; `app/pipeline.py` is a direct transcription of it.

## Input → output

**In:** one vendor invoice as a PDF — machine-readable or scanned, any layout.
**Out:** one of four decisions, the reasoning that produced it, and the next action.

## The seven stages

| # | Stage | Question it answers | Can it change the decision? |
|---|-------|--------------------|------------------------------|
| 1 | **Ingest** | Is this machine-readable text or a picture of a page? | Yes — a scan can never auto-approve |
| 2 | **Extract** | What does the document say? | Yes — low confidence forces review |
| 3 | **Normalise** | What does it say in canonical form? | Indirectly — recovers derivable fields |
| 4 | **Identify vendor** | Who is billing us, and are they approved? | Yes — blocked vendor is an instant reject |
| 5 | **Match PO** | What did we agree to buy, and what is left on it? | Yes — no PO means no decision |
| 6 | **Validate** | Which of the 17 rules have something to say? | Yes — this is where findings come from |
| 7 | **Decide** | What is the single worst finding, and what do we do? | This *is* the decision |

## Decision points

```
                    ┌─ no text layer ──→ vision path, confidence capped at 0.80
   1. Ingest ───────┤
                    └─ text layer ─────→ text path, exact characters

                    ┌─ model available ──→ Claude extraction (schema-constrained)
   2. Extract ──────┤
                    └─ model failed ─────→ regex fallback, confidence 0.55

                    ┌─ tax ID matches ──→ certain
   4. Identify ─────┼─ name ≥ 80% ──────→ confident, recorded with its score
                    └─ nothing ≥ 80% ───→ HOLD, do not invent a vendor

                    ┌─ PO quoted and found ────→ verify it
   5. Match PO ─────┼─ not quoted, one fit ────→ infer it, flag for review
                    ├─ not quoted, many fits ──→ HOLD, refuse to pick
                    └─ quoted but unknown ─────→ HOLD

   7. Decide ── worst finding wins ── REJECT > HOLD > NEEDS_REVIEW > AUTO_APPROVE
```

## The four outcomes

Approve/reject is too coarse for AP. The two middle states carry most of the value,
because they distinguish *"a human should look at this"* from *"nobody can decide
this until something comes back from outside."*

| Outcome | Meaning | Who acts next |
|---------|---------|---------------|
| `AUTO_APPROVE` | Released for payment, untouched | Nobody |
| `NEEDS_REVIEW` | Payable, but a human signs off | AP reviewer, minutes |
| `HOLD` | Undecidable with what we have | Vendor or procurement, days |
| `REJECT` | Must not be paid | AP notifies the vendor |

## Where the model is, and is not

The model reads documents. It does not decide anything.

```
  PDF ──→ [ Claude: extraction only ] ──→ structured fields
                                              │
                                              ▼
                    [ deterministic rules engine ] ──→ decision
```

Every rule is ordinary Python against master data and run history. That means the
judgment layer is unit-tested in 0.7 seconds with no network, the same invoice
always produces the same decision, and any decision can be explained by naming the
rules that drove it. See `DECISIONS.md` D-03.
