# Design decisions

Each entry: what was decided, why, and what it costs. Code references these by ID.

---

### D-01 · Problem statement 1 (invoice processing)
Chosen over vendor onboarding and outreach because the hard part is deterministic
logic I fully control, so a **live demo cannot fail on a third-party lookup**. It
also has the richest natural edge cases and the most legible run view.
**Cost:** less impressive "agentic" surface than a research-driven build.

### D-02 · Claude Agent SDK, not the Anthropic API SDK
The Agent SDK drives the local Claude Code CLI, which authenticates with a Claude
subscription over OAuth. **No API key to provision, leak, or expense**, and no
billing surprise mid-demo.

Three settings keep the call lean and safe (`app/extractors/claude_agent.py`):
- `system_prompt` overridden → replaces Claude Code's ~22k-token agent prompt with
  a ~250-token extraction prompt. Measured: **24,473 → 817 input tokens per call.**
- `tools=[]` → the model gets no filesystem or shell access. It reads text and
  returns JSON. That is the entire attack surface.
- `setting_sources=None` → no local `CLAUDE.md` or settings can alter behaviour.

**Cost:** requires the CLI installed and logged in; subscription rate limits apply.
Swapping to an API key is a client change in one file — the pipeline never sees it.

### D-03 · The model extracts; code decides
The model turns pixels and text into structured fields. Every rule after that is
ordinary Python against master data and history.

Why: the judgment layer is **unit-tested in 0.7s with no network**, the same
invoice always produces the same decision, and any outcome is explained by naming
the rules that drove it. An LLM asked to "decide whether to pay this" gives you a
plausible paragraph you cannot test, diff, or audit.
**Cost:** rules must be written by hand and won't generalise to an unseen policy.

### D-04 · Schema-constrained output, not prose parsing
`output_format` pins a JSON schema, so `structured_output` arrives as a parsed
dict. No regex over model prose, no "sometimes it wraps it in a code fence".

### D-05 · Degrade rather than fail
If the model path errors, a deterministic regex extractor takes over, labelled
`heuristic` in the UI with confidence pinned at **0.55 — deliberately below the
0.85 auto-approve bar**, so nothing extracted this way can release payment.
An AP queue that keeps moving at reduced autonomy beats one that stops.
This fired for real during testing when a turn limit was hit; the invoice still
processed and the UI showed the amber degraded marker.

### D-06 · Four outcomes, not approve/reject
`HOLD` and `NEEDS_REVIEW` are different problems: one needs five minutes from a
reviewer, the other needs something from outside the building. Collapsing them
hides the only number an AP lead cares about — how much is stuck, and on whom.

### D-07 · A scan never auto-approves
Referenced from `config.py`. Confidence from a page image is confidence about an
*interpretation*, and a wrong digit is a wrong payment. So extraction modality
gates autonomy independently of the model's self-reported score, which is capped
at 0.80 on the vision path.
**Cost:** every scanned invoice costs a human glance. Configurable in one line if
a client disagrees — `REQUIRE_TEXT_LAYER_FOR_AUTO_APPROVE`.

### D-08 · A missing invoice number blocks, however good the rest looks
It is the key duplicate detection runs on. Without it, "we will not pay this
twice" stops being a guarantee. EC-4 is exactly this case with everything else
perfect, and it still holds.

### D-09 · Infer a PO only when exactly one candidate fits
Zero candidates → HOLD. Two or more → HOLD, listing them. A confidently wrong PO
match is worse than no match, because it produces a payment nobody questions.
Inference is always flagged as inference and never auto-approves.

### D-10 · Tolerance is the greater of 2% and $25
Percentage alone is meaningless on small POs (2% of $890 is $17.80, less than a
freight rounding). A floor alone is meaningless on large ones.

### D-11 · Pending invoices consume PO budget
Amounts in the review queue are counted as *encumbered* against their PO, not
ignored until approved. Money that will almost certainly be paid has to count, or
a PO gets over-committed while three invoices sit in a queue. Billed and
encumbered are tracked separately and shown separately.

### D-12 · Duplicate detection is semantic, not file-based
Keyed on *(vendor, invoice number)* from content, with the vendor resolved by tax
ID first. File hashes are recorded but never used to decide — EC-3 defeats hashing
by design. A **rejected** invoice is excluded, so a corrected resubmission isn't
flagged as a duplicate of the thing we already refused.

### D-13 · The extractor reports what the page says; the matcher tolerates it
The prompt says *do not normalise the PO reference*. `find_po` handles the
variation instead — `PO-4404`, `po#4404`, `Ref: PO-4404 / release 1 of 3`.

This one was a bug first: the original matcher stripped every non-digit from the
whole string, turning `"PO-4404 / release 1 of 3"` into `440413`, matching
nothing, and **silently falling through to PO inference** — which still found the
right PO, so the run looked fine while quietly downgrading an exact reference to
a guess. Normalisation belongs on one side of the boundary, and it is not the
model's.

### D-14 · Run history is process state, not logging
Three rules read it (R12, R13, R18) and EC-2, EC-3 and EC-5 are undetectable
without it. Consequence: a client that disconnects mid-run must not leave a row
stuck at `running`, or every PO balance and dashboard figure is quietly wrong —
hence explicit `aclose()` on the stream plus a startup sweep for the
process-killed case.

### D-15 · One pipeline, three front-ends
`run_pipeline()` is an async generator of stage events. The web UI renders it over
SSE, the CLI prints it, the tests assert on it. **The UI contains no process
logic** — there is nothing that can work in the demo but not in the real thing.

### D-16 · SQLite and a single process
The whole thing runs from one `uvicorn` command with no external services. For a
build that has to run live in an interview, the failure modes I can't see are the
ones that matter.
**Cost:** not concurrent-safe beyond one worker. Real deployment swaps the store
behind `app/db.py`.

### D-17 · The rules engine fails safe
A rule that raises produces a `warn` finding rather than vanishing, so a broken
check downgrades the invoice to review instead of silently letting it through.
Tested (`test_a_broken_rule_fails_safe`).

---

## Assumptions

Per the brief, ambiguity was resolved by assumption and noted rather than asked.

1. **Header-level matching.** Totals are matched against PO values, not line items
   against goods receipts. Real three-way match needs a GRN feed.
2. **Master data is trusted.** Vendor and PO CSVs stand in for an ERP; the
   resolver logic is what would survive that swap.
3. **One invoice per PDF**, first page on the vision path.
4. **No FX.** A currency mismatch holds rather than converting.
5. **Tax is validated arithmetically, not against jurisdiction rules.**
6. **Reviewer overrides are recorded but do not train anything** — the data is
   there for it, the loop is not built.
