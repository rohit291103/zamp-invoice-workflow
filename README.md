# AP Invoice Process — Zamp case study (PS-1)

Takes a vendor invoice PDF and produces a reasoned payment decision, with every
step visible. Handles messy real-world formats including scans with no text layer,
and catches the mistakes that are only visible across invoices rather than within
one.

**Stack:** Python · FastAPI · SQLite · Claude Agent SDK · vanilla JS. One command
to run, no build step, no API key, no external services.

---

## Run it

```bash
cd invoice-agent
./run.sh                      # → http://127.0.0.1:8077
```

First-time setup, if the venv isn't there:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements-local.txt   # includes the Claude SDK + pytest
./.venv/bin/python scripts/generate_invoices.py    # builds the test invoices
```

**Auth:** extraction runs through the Claude Agent SDK, which uses the local
Claude Code CLI's OAuth session — a Claude subscription, no API key. Check with
`claude --version`; if it isn't logged in, run `claude` once. If the model is
unreachable the process degrades to a regex extractor rather than failing (D-05).

Other entry points:

```bash
./.venv/bin/python scripts/run_cli.py --demo                     # whole sequence in the terminal
./.venv/bin/python scripts/run_cli.py data/invoices/01_happy_path.pdf
./.venv/bin/python scripts/run_cli.py --reset                    # clear run history
./.venv/bin/python -m pytest tests/ -q                           # 69 tests, no network, 0.7s
```

---

## What it does

```
PDF ─→ Ingest ─→ Extract ─→ Normalise ─→ Identify vendor ─→ Match PO ─→ Validate ─→ Decide
        │          │                          │                │           │
   text layer   Claude                    vendor master     PO master   17 rules
   or scan?     (text or vision)          + tax ID          + history
```

New here? Start the app and click **Show me around** — it runs a real invoice and
narrates every stage.

The model reads documents. **All judgment is deterministic code** — testable,
explainable, and identical on every run (D-03).

Four outcomes, because approve/reject is too coarse for AP:

| Outcome | Meaning | Who acts next |
|---------|---------|---------------|
| `AUTO_APPROVE` | Released for payment, untouched | Nobody |
| `NEEDS_REVIEW` | Payable, but a human signs off | AP reviewer, minutes |
| `HOLD` | Undecidable with what we have | Vendor / procurement, days |
| `REJECT` | Must not be paid | AP notifies the vendor |

## Extraction backends

Three implementations behind one interface. The pipeline never learns which ran;
it only records the name, because how a figure was obtained is part of how far
it can be trusted.

| Backend | When it's used | Auth |
|---|---|---|
| `claude_agent_sdk` | Local development | Claude subscription via the CLI's OAuth session |
| `openai_compatible` | Deployed instances | Bearer token against any OpenAI-compatible endpoint |
| `heuristic` | Either, on failure | None — deterministic regex, capped at 0.55 confidence |

Selection is automatic: a bearer token in the environment wins, otherwise the
local CLI, otherwise the regex fallback. Force it with `EXTRACTION_PROVIDER`.

## Deploying

A server has no Claude Code CLI and no OAuth session, so a deployed instance
must use the HTTP backend. `render.yaml` is a working blueprint:

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → select the repo.
3. Set `OPENROUTER_API_KEY` in the dashboard (it is `sync: false`, so it is
   never committed).
4. Deploy.

Everything else — provider, base URL, models — is already set in the blueprint
and overridable from the dashboard without a code change.

**Two honest caveats.** Render's free tier sleeps after 15 minutes idle and
takes roughly 50 seconds to wake, so warm it before sharing the link. And the
free models are materially weaker than Claude at reading a skewed, grainy scan —
test `02_edge_scanned_freight.pdf` on whatever model you deploy with before
relying on it, and change `OPENAI_COMPAT_VISION_MODEL` if the reading is poor.


## The interface

Built for someone seeing it for the first time, not for someone who already
knows what a three-way match is.

- **Show me around** — a fourteen-step guided walkthrough that dims everything
  except the thing it is describing, runs a real invoice partway through, and
  explains each stage in plain language. It clears run history first so the
  walkthrough is reproducible.
- **Run** — picking an invoice does *not* run it. First you get the scenario in
  plain English (the situation, why it is tricky, what to watch for, the expected
  outcome) and **the actual document**, rendered as a page image beside the
  process. Then you run it, and each of the seven stages appears as it executes.
- **Findings** are split: anything needing attention is open, checks that passed
  quietly are folded away behind a disclosure, so validation is not a wall of green.
- **History** — touchless rate, exception counts, decision mix, every run. Click
  any row for the complete audit trail and raw event stream.
- **Orders & suppliers** — the two lists every invoice is checked against, with
  live consumption bars showing billed vs pending against each purchase order.
  This is the screen that exposed edge case 5.

## Edge cases

Five, each caught by a different mechanism. Full write-up in
[`docs/EDGE_CASES.md`](docs/EDGE_CASES.md).

| # | Scenario | Decision | Why it's hard |
|---|----------|----------|---------------|
| 1 | Scanned image, no text layer | `NEEDS_REVIEW` | Trust must depend on *how* the data was read |
| 2 | Split billing overruns a PO | `HOLD` | No single invoice is wrong |
| 3 | Duplicate on a new layout | `REJECT` | Defeats file-hash dedupe by design |
| 4 | No PO ref, no invoice number | `HOLD` | Knowing what may and may not be inferred |
| 5 | Same PO, new invoice number | `HOLD` | Passes every per-invoice check |

EC-5 wasn't designed — the build surfaced it. The PO consumption view showed a
48,600 order sitting at 97,500 with nothing flagged, because sixteen correct-looking
rules all reasoned about one invoice at a time.

## Layout

```
app/
  pipeline.py        the process — one async generator, seven stages
  rules.py           17 rules + the decision function (pure)
  matching.py        PO resolution, tolerance, cumulative billing
  masterdata.py      vendor/PO resolution, fuzzy names, free-text PO refs
  db.py              run store — process state, not logging (D-14)
  config.py          every threshold that drives a decision
  extractors/        ingest (text vs scan) · claude_agent · heuristic fallback
  main.py            HTTP + SSE. No process logic.
  static/            UI
data/
  vendors.csv, purchase_orders.csv, invoices/
scripts/
  generate_invoices.py, run_cli.py
tests/               69 tests, no network
docs/
  PROCESS_MAP.md · EDGE_CASES.md · DECISIONS.md · RULES.md
```

## Docs

- [Process map](docs/PROCESS_MAP.md) — stages, decision points, where the model is and isn't
- [Edge cases](docs/EDGE_CASES.md) — all five, and what I'd build next
- [Decisions](docs/DECISIONS.md) — 17 design decisions with costs, plus assumptions
- [Rules](docs/RULES.md) — the full rule catalogue and thresholds

## Known limits

Header-level matching only (no goods-receipt three-way match); one invoice per
PDF, first page on the vision path; no FX conversion; single-process SQLite;
reviewer overrides are recorded but don't feed back into thresholds.
