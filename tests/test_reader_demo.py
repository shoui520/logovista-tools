import json
from pathlib import Path

from logovista_tools.reader_demo import _RenderedEntry, _panels_html, _rewrite_local_asset_refs


def test_rewrite_local_asset_refs_uses_copied_templates(tmp_path: Path) -> None:
    templates = tmp_path / "Templates"
    templates.mkdir()
    (templates / "b177.png").write_bytes(b"png")
    (tmp_path / "root.png").write_bytes(b"png")

    html = '<img src="b177.png"><img src="root.png"><img src="Templates/ok.png">'

    rewritten = _rewrite_local_asset_refs(html, tmp_path)

    assert 'src="Templates/b177.png"' in rewritten
    assert 'src="root.png"' in rewritten
    assert 'src="Templates/ok.png"' in rewritten


def test_reader_demo_manifest_json_escape_is_script_safe() -> None:
    data = json.dumps([{"html": "</script><img src=\"x.png\">"}], ensure_ascii=False, sort_keys=True)
    script_text = data.replace("<", "\\u003c")

    assert "</script>" not in script_text
    assert json.loads(script_text)[0]["html"] == '</script><img src="x.png">'


def test_panels_html_preserves_cell_hierarchy() -> None:
    entry = _RenderedEntry(
        key="00000002:0000",
        block=2,
        offset=0,
        label="A",
        source="panel",
        title="A",
        html="<p>A</p>",
        plain="A",
        links=(),
        audio=(),
    )
    panels = {
        "metadata_files": ["Panels.xml"],
        "inline_cells": [
            {"panel_index": "00000000", "label": "ABC", "ref": "10000000"},
            {"panel_index": "10000000", "label": "A", "ref": "10100000"},
        ],
        "rows": [
            {
                "panel_index": "10100000",
                "panel_title": "ABC:A",
                "label": "a",
                "target_key": "00000002:0000",
            }
        ],
    }

    rendered = _panels_html(panels, {"00000002:0000": entry})

    assert 'data-cell-panel-id="00000000" data-panel-root="1"' in rendered
    assert 'data-cell-panel-id="10000000"' in rendered
    assert 'data-panel-ref="10000000"' in rendered
    assert 'data-panel-ref="10100000"' in rendered
    assert 'data-panel-id="10100000"' in rendered
