"""Extraction backends, and the rule for choosing between them.

Three implementations share one interface - `extract_from_text(str)` and
`extract_from_image(b64_png)`, both returning the same dictionary shape. The
pipeline never learns which one ran; it only records the name for the audit
trail, because how a figure was obtained is part of how far it can be trusted.
"""
from __future__ import annotations

import shutil
from typing import Any, Awaitable, Callable, NamedTuple

from ..config import EXTRACTION_PROVIDER


class Backend(NamedTuple):
    name: str                                              # for the audit trail
    text: Callable[[str], Awaitable[dict[str, Any]]]
    image: Callable[[str], Awaitable[dict[str, Any]]]


def _claude_agent() -> Backend:
    from . import claude_agent
    return Backend("claude_agent_sdk",
                   claude_agent.extract_from_text,
                   claude_agent.extract_from_image)


def _openai_compat() -> Backend:
    from . import openai_compat
    return Backend("openai_compatible",
                   openai_compat.extract_from_text,
                   openai_compat.extract_from_image)


def _has_bearer_token() -> bool:
    import os
    return bool(os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def resolve() -> Backend:
    """Pick a backend. Explicit configuration wins; otherwise infer.

    The inference order matters: a bearer token is checked first because it is
    the deliberate, deployment-shaped signal. The CLI is the developer-machine
    convenience, so it is the fallback rather than the default.
    """
    if EXTRACTION_PROVIDER == "claude_agent":
        return _claude_agent()
    if EXTRACTION_PROVIDER == "openai_compat":
        return _openai_compat()

    if _has_bearer_token():
        return _openai_compat()
    if shutil.which("claude"):
        return _claude_agent()
    # Neither available - the pipeline will catch this and degrade to regex,
    # which is exactly the documented behaviour when the model path is down.
    return _claude_agent()
