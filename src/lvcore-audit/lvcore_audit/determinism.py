"""Deterministic JSON helpers for audit artifacts."""

from __future__ import annotations

import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """Serialize audit data byte-stably."""

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def canonical_jsonl_row(obj: Any) -> str:
    """Serialize one JSONL audit row byte-stably."""

    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
