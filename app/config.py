"""Policy configuration for the AP invoice process.

Every threshold that drives a decision lives here, not scattered through the
rule code. In a real deployment this is what a finance controller owns.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Read BASE_DIR/.env into the environment if it exists.

    Deliberately hand-rolled rather than adding a dependency: this reads six
    lines of KEY=value at startup. Real environment variables always win, so a
    stale .env can never override what a deployment platform sets.
    """
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()
DATA_DIR = BASE_DIR / "data"
INVOICE_DIR = DATA_DIR / "invoices"
DB_PATH = DATA_DIR / "runs.db"

# --- Matching tolerances -------------------------------------------------
# An invoice may exceed its PO line by the greater of these two before we
# stop treating it as a rounding/freight difference.
TOLERANCE_PCT = 2.0          # percent of PO amount
TOLERANCE_ABS = 25.00        # absolute floor, in PO currency

# Variance beyond this is not a tolerance question any more - it is a
# different invoice than the one we agreed to, so it blocks rather than warns.
HARD_VARIANCE_PCT = 10.0

# --- Duplicate detection -------------------------------------------------
NEAR_DUPLICATE_WINDOW_DAYS = 90
NEAR_DUPLICATE_AMOUNT_EPSILON = 0.01

# --- Extraction confidence ----------------------------------------------
# Below this, a human looks at it before money moves.
AUTO_APPROVE_CONFIDENCE = 0.85

# Deliberate policy choice: data recovered from a page image is never trusted
# enough to release payment unattended, however confident the model sounds.
# See docs/DECISIONS.md - D-07.
REQUIRE_TEXT_LAYER_FOR_AUTO_APPROVE = True
SCANNED_CONFIDENCE_CEILING = 0.80

# --- Model routing -------------------------------------------------------
EXTRACTION_MODEL = "claude-sonnet-5"
EXTRACTION_EFFORT = "medium"

# Which extraction backend to use.
#   "claude_agent"  - the Claude Agent SDK, authenticated through the local
#                     Claude Code CLI's OAuth session. No API key, but it only
#                     works on a machine where that CLI is installed.
#   "openai_compat" - any OpenAI-compatible HTTP endpoint with a bearer token.
#                     This is what a deployed instance uses, because a server
#                     has no CLI and no OAuth session.
#   "auto"          - prefer a bearer token if one is present, else the CLI.
EXTRACTION_PROVIDER = os.environ.get("EXTRACTION_PROVIDER", "auto")

OPENAI_COMPAT_BASE_URL = os.environ.get(
    "OPENAI_COMPAT_BASE_URL", "https://openrouter.ai/api/v1")
# Free-tier defaults so a deployed demo costs nothing. Both are environment
# variables because free model availability changes often - if extraction
# quality drops, change the model here rather than the code.
OPENAI_COMPAT_MODEL = os.environ.get(
    "OPENAI_COMPAT_MODEL", "nex-agi/nex-n2.5-pro:free")
OPENAI_COMPAT_VISION_MODEL = os.environ.get(
    "OPENAI_COMPAT_VISION_MODEL", OPENAI_COMPAT_MODEL)
OPENAI_COMPAT_TIMEOUT = float(os.environ.get("OPENAI_COMPAT_TIMEOUT", "120"))

# Fields we refuse to guess at. Missing any of these changes the decision.
CRITICAL_FIELDS = ("vendor_name", "invoice_number", "invoice_total", "currency")
