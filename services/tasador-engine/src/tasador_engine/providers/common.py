"""Shared provider helpers."""

from __future__ import annotations

import json


class ProviderError(RuntimeError):
    """A provider failed; the message is safe to return to the caller as a warning."""


def extract_json_object(text: str) -> dict:
    """The JSON object in a model reply, tolerating code fences or prose around it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value
