"""Extraction via any OpenAI-compatible chat endpoint — used for OpenRouter.

Why this exists: the Agent SDK path authenticates through the local Claude Code
CLI, which only exists on a developer's machine. A deployed instance has no CLI
and no OAuth session, so it needs a plain HTTP path with a bearer token.

Deliberately written against the OpenAI-compatible shape rather than one vendor,
so the hosted build can point at OpenRouter, or anything else speaking the same
protocol, by changing two environment variables.

Two things are handled defensively, because small free models are less
disciplined than Claude:
  * JSON is recovered from the response even when wrapped in prose or fences;
  * every field is coerced to the type the pipeline expects, so a model that
    returns "3,150.00" as a string does not poison the arithmetic downstream.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

from ..config import (OPENAI_COMPAT_BASE_URL, OPENAI_COMPAT_MODEL,
                      OPENAI_COMPAT_TIMEOUT, OPENAI_COMPAT_VISION_BASE_URL,
                      OPENAI_COMPAT_VISION_MODEL)
from .schema import INVOICE_SCHEMA, SYSTEM_PROMPT


class ExtractionError(RuntimeError):
    pass


def _api_key(vision: bool = False) -> str:
    """Resolve the bearer token for this path.

    The vision path may sit on a different provider from the text path, so it
    gets its own optional key and falls back to the shared one when both paths
    use the same provider.
    """
    if vision:
        vision_key = os.environ.get("OPENAI_COMPAT_VISION_API_KEY", "")
        if vision_key:
            return vision_key

    key = (os.environ.get("OPENROUTER_API_KEY")
           or os.environ.get("NVIDIA_API_KEY")
           or os.environ.get("OPENAI_API_KEY", ""))
    if not key:
        raise ExtractionError(
            "no API key found - set OPENROUTER_API_KEY, NVIDIA_API_KEY "
            "or OPENAI_API_KEY")
    return key


def _schema_hint() -> str:
    """Small models follow an example better than they follow a spec."""
    return (
        "Return a single JSON object and nothing else — no prose, no markdown "
        "fences. Use exactly these keys:\n"
        '{"vendor_name": str|null, "vendor_tax_id": str|null, '
        '"invoice_number": str|null, "invoice_date": "YYYY-MM-DD"|null, '
        '"po_reference": str|null, "currency": "USD"|"EUR"|..., '
        '"subtotal": number|null, "tax_amount": number|null, '
        '"invoice_total": number|null, '
        '"line_items": [{"description": str, "quantity": number|null, '
        '"unit_price": number|null, "amount": number|null}], '
        '"field_confidence": {"vendor_name": 0.0-1.0, "invoice_number": 0.0-1.0, '
        '"invoice_date": 0.0-1.0, "po_reference": 0.0-1.0, "currency": 0.0-1.0, '
        '"invoice_total": 0.0-1.0}, "extraction_notes": str|null}'
    )


def _loads(text: str) -> dict[str, Any]:
    """Recover a JSON object from a response that may carry extra prose."""
    text = (text or "").strip()
    if not text:
        raise ExtractionError("model returned an empty response")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost balanced object in the response.
    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += (ch == "{") - (ch == "}")
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
    raise ExtractionError(f"could not parse JSON from response: {text[:200]!r}")


_NUM_KEYS = ("subtotal", "tax_amount", "invoice_total")
_STR_KEYS = ("vendor_name", "vendor_tax_id", "invoice_number",
             "invoice_date", "po_reference", "currency", "extraction_notes")


def _coerce(raw: dict[str, Any]) -> dict[str, Any]:
    """Force the shape the pipeline expects, whatever the model returned."""
    out: dict[str, Any] = {}
    for k in _STR_KEYS:
        v = raw.get(k)
        out[k] = None if v in (None, "", "null", "N/A") else str(v).strip()
    for k in _NUM_KEYS:
        v = raw.get(k)
        if isinstance(v, (int, float)):
            out[k] = round(float(v), 2)
        elif isinstance(v, str):
            cleaned = re.sub(r"[^0-9.\-]", "", v)
            try:
                out[k] = round(float(cleaned), 2)
            except ValueError:
                out[k] = None
        else:
            out[k] = None

    items = raw.get("line_items")
    out["line_items"] = items if isinstance(items, list) else []

    conf = raw.get("field_confidence")
    clean_conf: dict[str, float] = {}
    if isinstance(conf, dict):
        for k, v in conf.items():
            try:
                clean_conf[str(k)] = max(0.0, min(1.0, float(v)))
            except (TypeError, ValueError):
                continue
    # A model that reports no confidence is not thereby certain. Assume the
    # auto-approve bar is not met rather than assuming it is.
    out["field_confidence"] = clean_conf or {
        k: 0.70 for k in ("vendor_name", "invoice_number", "invoice_date",
                          "po_reference", "currency", "invoice_total")}
    return out


async def _post(messages: list[dict[str, Any]], model: str,
                base_url: str, vision: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        # Honoured where supported; harmlessly ignored elsewhere, which is why
        # _loads() still has to be forgiving.
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {_api_key(vision)}",
        "Content-Type": "application/json",
        # OpenRouter uses these for attribution; harmless on other providers.
        "HTTP-Referer": "https://github.com/rohit291103/zamp-invoice-workflow",
        "X-Title": "AP Invoice Workflow",
    }
    async with httpx.AsyncClient(timeout=OPENAI_COMPAT_TIMEOUT) as client:
        r = await client.post(f"{base_url.rstrip('/')}/chat/completions",
                              json=payload, headers=headers)
    if r.status_code != 200:
        raise ExtractionError(f"{model} returned HTTP {r.status_code}: {r.text[:200]}")

    body = r.json()
    if "choices" not in body or not body["choices"]:
        raise ExtractionError(f"no choices in response: {str(body)[:200]}")
    content = body["choices"][0].get("message", {}).get("content", "")

    data = _coerce(_loads(content))
    usage = body.get("usage") or {}
    data["_usage"] = {
        "model": body.get("model", model),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }
    return data


async def extract_from_text(text: str) -> dict[str, Any]:
    """Path A — the PDF had a text layer."""
    return await _post([
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + _schema_hint()},
        {"role": "user", "content":
            "Extract the invoice fields from this text, taken directly from a "
            f"PDF's text layer.\n\n<invoice_text>\n{text}\n</invoice_text>"},
    ], OPENAI_COMPAT_MODEL, OPENAI_COMPAT_BASE_URL)


async def extract_from_image(png_b64: str) -> dict[str, Any]:
    """Path B — the PDF was a scan, so the model has to read pixels."""
    return await _post([
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + _schema_hint()},
        {"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
            {"type": "text", "text":
                "This is a scanned image of a vendor invoice — there is no text "
                "layer, so read it visually. Extract the invoice fields. Be "
                "conservative with field_confidence: anything blurred, skewed or "
                "ambiguous should score below 0.9."},
        ]},
    ], OPENAI_COMPAT_VISION_MODEL, OPENAI_COMPAT_VISION_BASE_URL, vision=True)


# Kept so the schema import is not flagged as unused; the strict schema is used
# by the Agent SDK path and documents the same contract this module fulfils.
_CONTRACT = INVOICE_SCHEMA
