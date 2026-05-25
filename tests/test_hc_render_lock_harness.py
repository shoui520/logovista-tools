from pathlib import Path

from logovista_tools.hc_parity import CONFIRMED_RENDER_PARITY_DICT_IDS, RenderParityLock
from logovista_tools.hc_regression import HC_RENDER_LOCK_REFERENCE_QUERIES, check_hc_render_file


def _write_css(root: Path, text: str = "a{color:#5da24c}.midashi{color:#3021f9}") -> None:
    (root / "hc-renderer.css").write_text(text, encoding="utf-8")


def test_hc_render_lock_detects_asset_case_mismatch_and_missing_asset(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "b152.svg").write_text("<svg/>", encoding="utf-8")
    _write_css(tmp_path)
    html = tmp_path / "entry.html"
    html.write_text(
        '<link rel="stylesheet" href="hc-renderer.css">'
        '<a href="#entry">link</a>'
        '<img src="Templates/B152.svg">'
        '<object data="Templates/missing.svg"></object>',
        encoding="utf-8",
    )

    issues = check_hc_render_file(html, RenderParityLock("EJJE200", "_DCT_EJJE200", "test"))
    codes = {issue.code for issue in issues}

    assert "asset_case_mismatch" in codes
    assert "missing_asset" in codes


def test_hc_render_lock_accepts_exact_asset_casing_and_known_styles(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "b152.svg").write_text("<svg/>", encoding="utf-8")
    _write_css(tmp_path)
    html = tmp_path / "entry.html"
    html.write_text(
        '<link rel="stylesheet" href="hc-renderer.css">'
        '<a href="#entry">link</a>'
        '<img src="Templates/b152.svg">',
        encoding="utf-8",
    )

    assert check_hc_render_file(html, RenderParityLock("EJJE200", "_DCT_EJJE200", "test")) == ()


def test_hc_render_lock_detects_browser_hostile_tables_and_reader_chrome(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "backH.gif").write_bytes(b"gif")
    _write_css(tmp_path)
    html = tmp_path / "entry.html"
    html.write_text(
        '<link rel="stylesheet" href="hc-renderer.css">'
        '<table><tr><td><img src="Templates/backH.gif">',
        encoding="utf-8",
    )

    issues = check_hc_render_file(html, RenderParityLock("IBIO5", "_DCT_IBIO5", "test"))
    codes = {issue.code for issue in issues}

    assert "viewer_navigation_chrome" in codes
    assert "unbalanced_table_markup" in codes


def test_hc_render_lock_detects_huge_inline_svg_assets(tmp_path: Path) -> None:
    (tmp_path / "Templates").mkdir()
    (tmp_path / "Templates" / "math.svg").write_text("<svg/>", encoding="utf-8")
    _write_css(tmp_path)
    html = tmp_path / "entry.html"
    html.write_text(
        '<link rel="stylesheet" href="hc-renderer.css">'
        '<img class="figureRL" src="Templates/math.svg" width="117" height="94">',
        encoding="utf-8",
    )

    issues = check_hc_render_file(html, RenderParityLock("IBIO5", "_DCT_IBIO5", "test"))

    assert {issue.code for issue in issues} == {"huge_inline_asset"}


def test_hc_render_lock_reference_queries_only_target_confirmed_parity_dicts() -> None:
    assert set(HC_RENDER_LOCK_REFERENCE_QUERIES) <= CONFIRMED_RENDER_PARITY_DICT_IDS
    assert "PROYAL53" in HC_RENDER_LOCK_REFERENCE_QUERIES
    assert "KQLATINO" not in HC_RENDER_LOCK_REFERENCE_QUERIES
