from __future__ import annotations

import json
import re
from pathlib import Path

from test_lvcore_experimental import ga16_file, make_gaiji_package

from lvcore import GaijiPolicy, open_package, render_html


FORBIDDEN_FRIENDLY_KEYS = (
    "raw",
    "payload",
    "anchor_id",
    "source_path",
    "source_table",
    "source_row_id",
    "record_magic",
    "glyph_bytes",
    "image_path",
    "glyph_index",
)


def _assert_friendly(value: object) -> None:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    for key in FORBIDDEN_FRIENDLY_KEYS:
        assert f'"{key}"' not in text
    assert re.search(r'"op"\\s*:\\s*"[0-9a-fA-F]{2}"', text) is None
    assert "/tmp/" not in text
    assert "/mnt/" not in text
    assert "/home/" not in text


def test_public_reader_dicts_do_not_leak_debug_fields(tmp_path: Path) -> None:
    glyph = bytes([0x80] * 16)
    make_gaiji_package(
        tmp_path,
        raw_code=bytes.fromhex("a130"),
        uni_records=[("a130", "", "")],
        ga16_name="GA16HALF",
        ga16_payload=ga16_file(width=8, height=16, start_code=0xA121, glyphs=[glyph]),
    )
    package = open_package(tmp_path)
    results = package.search("gaiji", limit=1)
    hit = results.hits[0]
    entry = package.entry_for_hit(hit)
    document = entry.document()
    resource = document.resources[0]

    _assert_friendly(entry.to_dict())
    _assert_friendly(document.to_dict())
    _assert_friendly(results.to_dict())
    _assert_friendly(hit.to_dict())
    _assert_friendly(package.resource_info(resource).to_dict())
    _assert_friendly(package.gaiji_info(resource).to_dict())
    _assert_friendly(package.body_source().to_dict())

    friendly_html = render_html(document, gaiji_policy=GaijiPolicy.BITMAP_ONLY)
    assert f"lvcore-resource://{resource.id}" in friendly_html
    assert "a130" not in friendly_html
    assert "payload" not in friendly_html
