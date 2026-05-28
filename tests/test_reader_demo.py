import json
from pathlib import Path
from types import SimpleNamespace

from logovista_tools.hcrender import HcTargetRenderResult
from logovista_tools.reader_demo import (
    _QueuedAddress,
    _ReaderDemoBuilder,
    _RenderedEntry,
    _auxiliary_data,
    _decode_text_component_lines,
    _entries_html,
    _hanrei_html,
    _hanrei_chm_navigation_files,
    _hanrei_data,
    _hanrei_links_from_file,
    _menu_html,
    _panel_metadata_paths,
    _panels_html,
    _rewrite_local_asset_refs,
)
from logovista_tools.windows import AuxIndexRow


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


def test_reader_demo_uses_dense_honmon_dereference_renderer(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def fake_render_hc_target_body(*args, **kwargs):
        calls["target_offset"] = args[5]
        return HcTargetRenderResult(
            html='<div class="rendererdb-entry">gentile body</div>',
            plain="gentile body\nvisible definition",
            stats={},
            links=(),
            media=(),
            audio=(),
            video=(),
            private_directives=(),
            named_behavior_gaps=(),
            body_source="rendererdb_t_contents_data_id",
            slice_start=100,
            slice_end=132,
            raw_media=(),
            exact_rendererdb={"strategy": "rendererdb_t_contents_data_id"},
            output_vertical=False,
        )

    monkeypatch.setattr("logovista_tools.reader_demo.target_needs_index_boundaries", lambda *_args, **_kwargs: False)
    monkeypatch.setattr("logovista_tools.reader_demo.render_hc_target_body", fake_render_hc_target_body)

    builder = _ReaderDemoBuilder.__new__(_ReaderDemoBuilder)
    builder.source = SimpleNamespace(honmon_start_block=2)
    builder.out_dir = tmp_path
    builder.reader = object()
    builder.renderer_code = "015F"
    builder.options = SimpleNamespace()
    builder.args = SimpleNamespace(vertical=False, no_audio=True)
    builder.boundary_offsets = None
    builder.failures = []

    entry = builder._render_address(_QueuedAddress(block=1565, offset=546, label="00100050", source="query:gentile"))

    assert calls["target_offset"] == (1565 - 2) * 2048 + 546
    assert entry is not None
    assert entry.title == "gentile body"
    assert entry.body_source == "rendererdb_t_contents_data_id"
    assert "00100050" not in entry.html


def test_entries_html_uses_resolved_title_not_dense_label() -> None:
    entry = _RenderedEntry(
        key="00001565:0546",
        block=1565,
        offset=546,
        label="00100050",
        source="query:gentile",
        title="gentilé /ʒɑ~-ti-le/ 女（国・都市・地域の）住民名",
        html="<p>body</p>",
        plain="gentilé",
        links=(),
        audio=(),
        body_source="rendererdb_t_contents_data_id",
    )

    rendered = _entries_html({"00001565:0546": entry})

    assert "gentilé" in rendered
    assert "00100050" not in rendered


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
            {"panel_index": "10000000", "label": "Direct", "target_key": "00000002:0000", "ref": ""},
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
    assert 'data-entry-key="00000002:0000">Direct</button>' in rendered
    assert 'data-panel-id="10100000"' in rendered


def test_reader_demo_panel_metadata_paths_honor_exinfo_panelxml(tmp_path: Path) -> None:
    idx = tmp_path / "DICT.IDX"
    idx.write_bytes(b"SSEDINFO")
    (tmp_path / "EXINFO.INI").write_text(
        "[GENERAL]\nPANELXML=BRI2019Panels.xml\n",
        encoding="cp932",
    )
    declared = tmp_path / "BRI2019Panels.xml"
    declared.write_text("<panels />", encoding="utf-8")

    assert _panel_metadata_paths(tmp_path, idx=idx) == [declared]


def test_menu_html_keeps_toc_navigation_buttons_clickable() -> None:
    menu = {
        "components": [{"component": "TOC.DIC", "status": "ok"}],
        "rows": [
            {
                "path": ["Root"],
                "text": "Root",
                "navigation_key": "TOC.DIC:1",
                "navigation_target_key": "TOC.DIC:2",
            },
            {
                "path": ["Root", "Child"],
                "text": "Child",
                "navigation_key": "TOC.DIC:2",
                "target_key": "00000002:0000",
            },
        ],
    }
    entry = _RenderedEntry(
        key="00000002:0000",
        block=2,
        offset=0,
        label="Child",
        source="menu",
        title="Child",
        html="<p>Child</p>",
        plain="Child",
        links=(),
        audio=(),
    )

    rendered = _menu_html(menu, {"00000002:0000": entry})

    assert 'data-nav-row-key="TOC.DIC:1" data-nav-target-key="TOC.DIC:2"' in rendered
    assert 'data-entry-key="00000002:0000">Root / Child</button>' in rendered


def test_hanrei_data_does_not_invent_missing_index_for_empty_directory(tmp_path: Path) -> None:
    package = tmp_path / "dict"
    package.mkdir()
    (package / "DICT.IDX").write_bytes(b"SSEDINFO")
    (package / "HANREI").mkdir()
    builder = SimpleNamespace(
        source=SimpleNamespace(idx=package / "DICT.IDX"),
        out_dir=tmp_path / "out",
        args=SimpleNamespace(hanrei_page_limit=10),
    )

    data = _hanrei_data(builder)

    assert data["sources"][0]["kind"] == "html_directory"
    assert data["sources"][0]["entry"] == ""
    assert data["sources"][0]["links"] == []


def test_menu_html_embeds_menu_target_previews() -> None:
    menu = {
        "components": [{"component": "MENU.DIC", "status": "ok"}],
        "rows": [
            {
                "path": ["Root"],
                "text": "Root",
                "navigation_key": "MENU.DIC:1",
                "navigation_preview_key": "MENU.DIC:10",
            },
        ],
        "previews": [
            {
                "navigation_key": "MENU.DIC:10",
                "title": "Sub menu",
                "rows": [
                    {
                        "path": ["Sub menu", "Child"],
                        "text": "Child",
                        "navigation_key": "MENU.DIC:11",
                        "target_key": "00000002:0000",
                    }
                ],
            }
        ],
    }
    entry = _RenderedEntry(
        key="00000002:0000",
        block=2,
        offset=0,
        label="Child",
        source="menu",
        title="Child",
        html="<p>Child</p>",
        plain="Child",
        links=(),
        audio=(),
    )

    rendered = _menu_html(menu, {"00000002:0000": entry})

    assert 'data-menu-preview-target="MENU.DIC:10"' in rendered
    assert 'data-menu-preview-id="MENU.DIC:10"' in rendered
    assert 'data-entry-key="00000002:0000">Sub menu / Child</button>' in rendered


def test_decode_text_component_lines_reads_bounded_prefix(monkeypatch, tmp_path: Path) -> None:
    reads: list[tuple[int, int]] = []

    class FakeReader:
        expanded_size = 32 * 1024 * 1024

        def __init__(self, path: Path) -> None:
            self.path = path

        def read(self, offset: int, size: int) -> bytes:
            reads.append((offset, size))
            return b"first\nsecond\nthird\n"

    monkeypatch.setattr("logovista_tools.reader_demo.SsedRandomReader", FakeReader)
    monkeypatch.setattr("logovista_tools.reader_demo.decode_tokens", lambda data, **_kwargs: (data, {}))
    monkeypatch.setattr("logovista_tools.reader_demo.tokens_to_text", lambda data: data.decode("ascii"))

    lines = _decode_text_component_lines(tmp_path / "KWTITLE.DIC", {}, 2)

    assert lines == ["first", "second"]
    assert reads == [(0, 128 * 1024)]


def test_auxiliary_data_includes_loose_right_dic(monkeypatch, tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    idx.write_bytes(b"")
    right = tmp_path / "RIGHT.DIC"
    right.write_bytes(b"placeholder")
    builder = SimpleNamespace(
        elements=[],
        source=SimpleNamespace(idx=idx, gaiji_map={}),
        args=SimpleNamespace(index_row_limit=10),
    )
    monkeypatch.setattr(
        "logovista_tools.reader_demo._decode_text_component_lines",
        lambda path, _gaiji_map, _limit: [f"decoded:{path.name}"],
    )

    auxiliary = _auxiliary_data(builder)

    assert auxiliary["text_streams"] == [
        {"component": "RIGHT.DIC", "type": "loose", "lines": ["decoded:RIGHT.DIC"]}
    ]
    assert auxiliary["failures"] == []


def test_auxiliary_data_includes_exinfo_text_indexes(monkeypatch, tmp_path: Path) -> None:
    idx = tmp_path / "TEST.IDX"
    idx.write_bytes(b"")
    aux = tmp_path / "1991.idx"
    aux.write_bytes(b"placeholder")
    enqueued: list[tuple[int, int, str, str]] = []

    class Builder:
        elements = []
        source = SimpleNamespace(idx=idx, gaiji_map={})
        args = SimpleNamespace(index_row_limit=10)

        def enqueue(self, block: int, offset: int, *, label: str, source: str, depth: int = 0) -> None:
            enqueued.append((block, offset, label, source))

    monkeypatch.setattr(
        "logovista_tools.reader_demo.load_exinfo_for_idx",
        lambda _idx: SimpleNamespace(path=tmp_path / "EXINFO.INI", general={}),
    )
    monkeypatch.setattr(
        "logovista_tools.reader_demo.iter_aux_index_specs",
        lambda _exinfo: [SimpleNamespace(index=0, name="1991年版", info="1991.idx", path=aux)],
    )
    monkeypatch.setattr(
        "logovista_tools.reader_demo.parse_aux_index_text",
        lambda _path, _elements: [
            AuxIndexRow(
                line_number=1,
                block=2,
                offset=90,
                depth=1,
                label="▽執筆者",
                path=("1991年版", "巻頭特集", "▽執筆者"),
                target={"kind": "body", "block": 2, "offset": 90},
            )
        ],
    )
    monkeypatch.setattr("logovista_tools.reader_demo.discover_numeric_aux_indexes", lambda _idx: [])

    auxiliary = _auxiliary_data(Builder())

    assert auxiliary["index_rows"][0]["component"] == "1991.idx"
    assert auxiliary["index_rows"][0]["type"] == "exinfo-text-index"
    assert auxiliary["index_rows"][0]["title"] == "1991年版"
    assert auxiliary["index_rows"][0]["rows"][0]["target_key"] == "00000002:0090"
    assert enqueued == [(2, 90, "▽執筆者", "aux:1991.idx")]


def test_hanrei_links_parse_chm_sitemap_objects(tmp_path: Path) -> None:
    hhc = tmp_path / "HANREI.hhc"
    hhc.write_text(
        """
        <html><body><ul>
        <li><object type="text/sitemap">
          <param name="Name" value="凡例について ">
          <param name="Local" value="Source/contents/hanrei.html">
        </object>
        <li><object type="text/sitemap">
          <param name="Name" value="外部">
          <param name="Local" value="javascript:bad()">
        </object>
        </ul></body></html>
        """,
        encoding="cp932",
    )

    links = _hanrei_links_from_file(hhc, "hanrei/chm", 10)

    assert links == [{"label": "凡例について", "href": "hanrei/chm/Source/contents/hanrei.html"}]


def test_hanrei_html_sets_initial_iframe_src() -> None:
    rendered = _hanrei_html(
        {
            "sources": [
                {
                    "kind": "chm",
                    "source": "HANREI.chm",
                    "entry": "hanrei/chm/Source/top.html",
                    "links": [
                        {"label": "凡例について", "href": "hanrei/chm/Source/contents/hanrei.html"}
                    ],
                }
            ],
            "failures": [],
        }
    )

    assert 'data-hanrei-href="hanrei/chm/Source/contents/hanrei.html"' in rendered
    assert 'iframe id="hanrei-frame" title="HANREI" src="hanrei/chm/Source/contents/hanrei.html"' in rendered


def test_hanrei_chm_navigation_files_accept_product_named_hhc(tmp_path: Path) -> None:
    source = tmp_path / "Source"
    source.mkdir()
    (source / "top.html").write_text("<html></html>", encoding="utf-8")
    (tmp_path / "ZYAKUKOG.hhc").write_text("<html></html>", encoding="utf-8")

    assert _hanrei_chm_navigation_files(tmp_path) == [tmp_path / "ZYAKUKOG.hhc"]


def test_hanrei_data_copies_macos_help_localized_bundle(tmp_path: Path) -> None:
    root = tmp_path / "MACDICT"
    bundle = root / "MACDICT_HELP.localized"
    contents = bundle / "contents"
    contents.mkdir(parents=True)
    (root / "MACDICT.IDX").write_bytes(b"")
    (root / "hanrei.html").write_text(
        '<html><frameset><frame name="leftFrame" src="MACDICT_HELP.localized/menu.html">'
        '<frame name="mainFrame" src="MACDICT_HELP.localized/top.html"></frameset></html>',
        encoding="cp932",
    )
    (bundle / "menu.html").write_text('<a href="contents/hanrei.html">凡例</a>', encoding="utf-8")
    (bundle / "top.html").write_text("<p>top</p>", encoding="utf-8")
    (contents / "hanrei.html").write_text("<p>body</p>", encoding="utf-8")
    (contents / "._hanrei.html").write_bytes(b"metadata")
    builder = SimpleNamespace(
        source=SimpleNamespace(idx=root / "MACDICT.IDX"),
        out_dir=tmp_path / "out",
        args=SimpleNamespace(hanrei_page_limit=10),
    )

    data = _hanrei_data(builder)

    assert (tmp_path / "out" / "hanrei" / "hanrei.html").is_file()
    assert (tmp_path / "out" / "hanrei" / "MACDICT_HELP.localized" / "contents" / "hanrei.html").is_file()
    assert not (tmp_path / "out" / "hanrei" / "MACDICT_HELP.localized" / "contents" / "._hanrei.html").exists()
    assert data["sources"][0]["kind"] == "mac_html_frameset"
    assert data["sources"][0]["links"][0]["href"] == "hanrei/MACDICT_HELP.localized/menu.html"
    assert data["sources"][1]["kind"] == "mac_help_localized"
    assert any(link["href"] == "hanrei/MACDICT_HELP.localized/contents/hanrei.html" for link in data["sources"][1]["links"])
