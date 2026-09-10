"""Extraction via the Claude Agent SDK.

Why the Agent SDK rather than the Anthropic API SDK: it authenticates through
the local Claude Code CLI's OAuth session, so this runs on a Claude
subscription with no API key to provision or leak. The trade-off is documented
in docs/DECISIONS.md (D-02).

Three things keep the call lean and predictable:
  * system_prompt is overridden, which replaces Claude Code's own ~22k-token
    agent prompt with our ~250-token extraction prompt;
  * tools=[] - this is a pure extraction call, the model gets no filesystem
    or shell access whatsoever;
  * output_format pins a JSON schema, so we get a parsed dict back instead of
    prose we would have to regex.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from ..config import EXTRACTION_EFFORT, EXTRACTION_MODEL
from .schema import INVOICE_SCHEMA, SYSTEM_PROMPT


class ExtractionError(RuntimeError):
    pass


def _options(model: Optional[str] = None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        tools=[],                 # no tool access - extraction only
        setting_sources=None,     # ignore local CLAUDE.md / settings
        model=model or EXTRACTION_MODEL,
        effort=EXTRACTION_EFFORT,
        # 1 is too tight: the structured-output response can need its own turn,
        # and hitting the cap surfaces as a hard error, not a retry.
        max_turns=3,
        output_format=INVOICE_SCHEMA,
        # Force subscription (OAuth) auth even if a stray key is exported.
        env={**os.environ, "ANTHROPIC_API_KEY": ""},
    )


async def _run(prompt: Any, model: Optional[str] = None) -> dict[str, Any]:
    result: Optional[ResultMessage] = None
    async for message in query(prompt=prompt, options=_options(model)):
        if isinstance(message, ResultMessage):
            result = message

    if result is None:
        raise ExtractionError("model returned no result message")
    if result.is_error:
        raise ExtractionError(f"model error: {result.errors or result.subtype}")
    if not isinstance(result.structured_output, dict):
        raise ExtractionError("model did not return schema-valid JSON")

    data = dict(result.structured_output)
    data["_usage"] = {
        "input_tokens": result.usage.get("input_tokens"),
        "output_tokens": result.usage.get("output_tokens"),
        "duration_ms": result.duration_ms,
        "model": model or EXTRACTION_MODEL,
    }
    return data


async def extract_from_text(text: str) -> dict[str, Any]:
    """Path A - the PDF had a text layer, so the model reads exact characters."""
    prompt = (
        "Extract the invoice fields from this text, which was taken directly "
        "from a PDF's text layer (so the characters are exact).\n\n"
        f"<invoice_text>\n{text}\n</invoice_text>"
    )
    return await _run(prompt)


async def extract_from_image(png_b64: str) -> dict[str, Any]:
    """Path B - the PDF was a scan, so the model interprets pixels.

    Streaming-input form is required here: it is the only way to attach an
    image content block to the prompt.
    """
    async def message_stream():
        yield {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": png_b64}},
                    {"type": "text", "text":
                        "This is a scanned image of a vendor invoice - there is no text "
                        "layer, so read it visually. Extract the invoice fields. Be "
                        "conservative with field_confidence: anything blurred, skewed or "
                        "ambiguous should score below 0.9."},
                ],
            },
            "parent_tool_use_id": None,
            "session_id": "invoice-extraction",
        }

    return await _run(message_stream())
