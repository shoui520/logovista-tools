"""Explicit lvcore debug/inspection example.

Run with:

    PYTHONPATH=src/lvcore-experimental \
      python3 src/lvcore-experimental/examples/debug_inspection.py /path/to/_DCT_DICT query

This example is intentionally not the normal dictionary-app path. It shows how
tools can opt into raw inspection when investigating package behavior.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lvcore import InspectorRenderer, SearchProfile, open_package


def inspect_first_hit(package_path: str | Path, query: str, *, profile: str = "native") -> dict[str, Any]:
    """Return explicit debug information for the first search hit."""

    package = open_package(Path(package_path))
    results = package.search(query, profile=SearchProfile(profile), limit=1, debug=True)
    result: dict[str, Any] = {
        "search": results.to_dict(debug=True),
        "entry": None,
    }
    if not results.hits:
        return result

    hit = results.hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    result["entry"] = {
        "hit_inspection": hit.inspect(),
        "entry_inspection": entry.inspect(),
        "document": document.to_dict(debug=True),
        "debug_html": InspectorRenderer().render_html(document),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the first lvcore search hit with explicit debug output")
    parser.add_argument("package", type=Path)
    parser.add_argument("query")
    parser.add_argument("--profile", choices=[profile.value for profile in SearchProfile], default=SearchProfile.NATIVE.value)
    args = parser.parse_args()

    print(json.dumps(inspect_first_hit(args.package, args.query, profile=args.profile), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
