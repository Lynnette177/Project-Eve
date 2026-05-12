"""Utilities for turning ChatAgent plain text into sendable messages."""

from __future__ import annotations

import re
from typing import Any


def normalize_chat_output(raw_content: str) -> dict[str, Any]:
    """ChatAgent now emits plain text; each non-empty line is one message."""
    text = (raw_content or "").strip()
    if not text:
        return {"should_send": False, "messages": []}

    text = re.sub(r"^```(?:text)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return {"should_send": False, "messages": []}

    messages = [
        {
            "type": "text",
            "content": line,
            "media_id": None,
        }
        for index, line in enumerate(lines)
    ]
    return {"should_send": True, "messages": messages}
