"""Run the process from the terminal. Same pipeline the web UI drives.

    python scripts/run_cli.py data/invoices/01_happy_path.pdf
    python scripts/run_cli.py --demo        # the full scripted demo sequence
    python scripts/run_cli.py --reset       # clear run history
"""
from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                      # noqa: E402
from app.catalogue import CATALOGUE     # noqa: E402
from app.config import INVOICE_DIR      # noqa: E402
from app.pipeline import run_pipeline   # noqa: E402

C = {"reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m", "green": "\033[32m",
     "yellow": "\033[33m", "red": "\033[31m", "blue": "\033[34m", "mag": "\033[35m"}
DECISION_COLOR = {"AUTO_APPROVE": "green", "NEEDS_REVIEW": "yellow",
                  "HOLD": "mag", "REJECT": "red"}
SEV_COLOR = {"info": "dim", "warn": "yellow", "block": "red"}

# Order matters: the split-billing and duplicate cases only behave correctly
# when the runs that build up the history have happened first.
DEMO_SEQUENCE = list(CATALOGUE)


def c(text: str, colour: str) -> str:
    return f"{C[colour]}{text}{C['reset']}"


async def run_one(path: Path) -> str:
    print(f"\n{c('━' * 78, 'dim')}")
    print(f"{c('▶', 'blue')} {c(path.name, 'bold')}")
    print(c("━" * 78, "dim"))
    decision = "?"
    scenario = CATALOGUE.get(path.name, {}).get("title")
    async for ev in run_pipeline(path, scenario=scenario):
        if ev["type"] == "stage":
            mark = {"started": "  ·", "done": c("  ✓", "green"),
                    "failed": c("  ✗", "red"), "degraded": c("  !", "yellow")}[ev["status"]]
            if ev["status"] == "started":
                print(f"{mark} {c(ev['stage'].ljust(10), 'dim')} {c(ev['detail'], 'dim')}")
            else:
                print(f"{mark} {ev['stage'].ljust(10)} {ev['detail']}")
                for f in (ev.get("data") or {}).get("findings", []):
                    print(f"      {c(f['severity'].upper().ljust(5), SEV_COLOR[f['severity']])} "
                          f"{c(f['rule_id'], 'dim')} {f['message']}")
        elif ev["type"] == "run_complete":
            d = ev["decision"]
            decision = d["decision"]
            print(f"\n  {c(d['decision'], DECISION_COLOR[d['decision']])}  "
                  f"{c(d['meaning'], 'dim')}")
            print(f"  {c('Next:', 'dim')} {d['next_action']}")
            print(f"  {c(str(ev['duration_ms']) + ' ms', 'dim')}")
        elif ev["type"] == "run_failed":
            decision = "ERROR"
            print(f"\n  {c('RUN FAILED', 'red')}: {ev['error']}")
    return decision


async def main() -> None:
    db.init()
    args = sys.argv[1:]

    if "--reset" in args:
        from app.config import DB_PATH
        DB_PATH.unlink(missing_ok=True)
        db.init()
        print("Run history cleared.")
        args = [a for a in args if a != "--reset"]
        if not args:
            return

    if "--demo" in args:
        results = []
        for name in DEMO_SEQUENCE:
            results.append((name, await run_one(INVOICE_DIR / name)))
        print(f"\n{c('═' * 78, 'dim')}\n{c('SUMMARY', 'bold')}\n")
        for name, d in results:
            print(f"  {name:38s} {c(d, DECISION_COLOR.get(d, 'red'))}")
        print(f"\n{db.stats()}")
        return

    if not args:
        print(__doc__)
        return
    await run_one(Path(args[0]))


if __name__ == "__main__":
    anyio.run(main)
