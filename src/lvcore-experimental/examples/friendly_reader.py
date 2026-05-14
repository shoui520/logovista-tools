"""Minimal app-facing lvcore reader example.

Run with:

    PYTHONPATH=src/lvcore-experimental \
      python3 src/lvcore-experimental/examples/friendly_reader.py /path/to/_DCT_DICT query

This example intentionally stays on the friendly reader API. It does not
inspect spans, opcodes, index pages, body offsets, or raw component bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lvcore import SearchProfile, detect_family, open_package


def read_first_entry(package_path: str | Path, query: str, *, profile: str = "native") -> dict[str, Any]:
    """Open a package, search it, and render the first hit for an app UI."""

    path = Path(package_path)
    detected = detect_family(path)
    package = open_package(path)
    body_source = package.body_source().to_dict()
    results = package.search(query, profile=SearchProfile(profile), limit=1)

    result: dict[str, Any] = {
        "package": {
            "family": detected.family.value,
            "dict_id": package.dict_id,
            "title": package.title,
        },
        "body_source": {
            "kind": body_source.get("ssed_kind"),
            "support": body_source.get("support"),
        },
        "search": {
            "query": results.query,
            "normalized_query": results.normalized_query,
            "profile": results.profile.value,
            "hit_count": len(results.hits),
            "diagnostics": [diagnostic.to_dict() for diagnostic in results.diagnostics],
        },
        "entry": None,
    }

    if not results.hits:
        return result

    hit = results.hits[0]
    entry = package.entry_for_hit(hit)
    result["entry"] = {
        "heading": hit.heading,
        "heading_source": hit.heading_source,
        "title_status": hit.title_status,
        "hit": hit.to_dict(),
        "html": entry.html(),
        "plain_text": entry.plain_text(),
        "diagnostics": [diagnostic.to_dict() for diagnostic in entry.diagnostics()],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the first lvcore search hit through the friendly API")
    parser.add_argument("package", type=Path)
    parser.add_argument("query")
    parser.add_argument("--profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    args = parser.parse_args()

    print(json.dumps(read_first_entry(args.package, args.query, profile=args.profile), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
