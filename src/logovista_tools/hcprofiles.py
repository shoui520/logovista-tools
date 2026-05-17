"""Behavior profiles for LogoVista ``HC????.dll`` HTML renderers.

The profiles in this module are behavior-level records derived from PE
entrypoints/imports, embedded SQL/templates, sidecar schemas, and focused
decompilation notes.  They are intentionally not a byte-for-byte emulator of
the DLLs.  Their job is to keep the toolkit honest about which renderer
behavior is implemented, which behavior is structurally understood, and which
product hooks remain explicit gaps.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .windows import HcRendererClassification


BODY_SIDECAR_SUPPORT = "entry_body_or_media"


@dataclass(frozen=True)
class HcHookBehavior:
    """One renderer hook or product-specific behavior family."""

    name: str
    status: str
    evidence: tuple[str, ...] = ()
    implementation: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "evidence": list(self.evidence),
            "implementation": self.implementation,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class HcBehaviorProfile:
    """Renderer behavior that a reader/exporter can act on."""

    renderer_code: str | None
    renderer_name: str | None
    family: str
    body_strategy: str
    body_strategy_status: str
    exact_body_html_available: bool
    exact_hc_parity: bool
    implemented_semantics: tuple[str, ...] = ()
    hook_behaviors: tuple[HcHookBehavior, ...] = ()
    schema_sidecar_roles: dict[str, int] = field(default_factory=dict)
    named_gaps: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "logovista-hc-behavior-profile-v1",
            "renderer_code": self.renderer_code,
            "renderer_name": self.renderer_name,
            "family": self.family,
            "body_strategy": self.body_strategy,
            "body_strategy_status": self.body_strategy_status,
            "exact_body_html_available": self.exact_body_html_available,
            "exact_hc_parity": self.exact_hc_parity,
            "implemented_semantics": list(self.implemented_semantics),
            "hook_behaviors": [row.as_dict() for row in self.hook_behaviors],
            "schema_sidecar_roles": dict(sorted(self.schema_sidecar_roles.items())),
            "named_gaps": list(self.named_gaps),
            "notes": list(self.notes),
        }


def _sidecar_roles(schema_sidecars: dict[str, Any] | None) -> dict[str, int]:
    if not schema_sidecars:
        return {}
    role_counts = schema_sidecars.get("role_counts")
    if isinstance(role_counts, dict):
        return {str(key): int(value) for key, value in role_counts.items()}
    counter: Counter[str] = Counter()
    for row in schema_sidecars.get("sidecars", []):
        if isinstance(row, dict):
            role = row.get("role")
            if role:
                counter[str(role)] += 1
    return dict(counter)


def _has_body_sidecar(schema_sidecars: dict[str, Any] | None) -> bool:
    if not schema_sidecars:
        return False
    return any(
        row.get("hc_render_support") == BODY_SIDECAR_SUPPORT
        for row in schema_sidecars.get("sidecars", [])
        if isinstance(row, dict)
    )


def _rendererdb_ok(rendererdb_summary: dict[str, Any] | None) -> bool:
    if not rendererdb_summary:
        return False
    return str(rendererdb_summary.get("status", "")).startswith("ok")


def _family_for_code(code: str | None, renderer: HcRendererClassification | None) -> str:
    if code is None:
        return "no_hc_renderer"
    if code in {"015A", "015B", "015E", "015F"}:
        return "modern_dense_t_contents_renderer"
    if code == "014F":
        return "ejje_search_sidecar_renderer"
    if code == "0C80":
        return "britannica_yearbook_array_renderer"
    if code in {"00D3", "00D5", "00DE"}:
        return "britannica_panel_media_renderer"
    if code in {"013A", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C4", "02C5", "02C7"}:
        return "panel_enabled_renderer"
    if code == "0190":
        return "sizk_readaloud_renderer"
    if code == "009B":
        return "simple_htmls_vertical_renderer"
    if renderer and renderer.features.get("sql_hooks"):
        return "sql_hook_renderer"
    if renderer and renderer.features.get("vertical_renderer"):
        return "shared_vertical_renderer"
    return "shared_body_renderer"


def _known_code_hooks(code: str | None) -> list[HcHookBehavior]:
    rows: list[HcHookBehavior] = []
    if code in {"015A", "015B", "015E", "015F"}:
        rows.append(
            HcHookBehavior(
                name="t_contents_body_lookup",
                status="implemented_when_sidecar_present",
                evidence=("t_contents f_DataId/f_Html SQL", "dense HONMON decimal ID anchors"),
                implementation="rendererdb data_id join",
                notes="HC code selects f_Html/f_Title_SS/f_Keyword by f_DataId and related group IDs.",
            )
        )
    if code == "015F":
        rows.extend(
            [
                HcHookBehavior(
                    name="ziptomedia_audio_extraction",
                    status="implemented",
                    evidence=("createMediaFileFromZip export", "lved.ziptomedia HTML references"),
                    implementation="rendererdb ziptomedia reference extraction and LogoFontCipher decrypt",
                    notes="The DLL shells through a helper to create referenced media; toolkit resolves the same referenced files directly.",
                ),
                HcHookBehavior(
                    name="royal_example_search_helpers",
                    status="classified_not_emulated",
                    evidence=("t_fjseikuyourei/t_jfseikuyourei/t_english SQL strings", "pluginFunction2nd export"),
                    implementation=None,
                    notes="These are product UI/search result helpers, not normal entry rendering.",
                ),
                HcHookBehavior(
                    name="address_split_headword_modifier",
                    status="classified_not_emulated",
                    evidence=("modifyHeadwordEx export", "address threshold branch"),
                    implementation=None,
                    notes="The DLL applies product-specific headword transforms based on body address ranges.",
                ),
            ]
        )
    if code == "014F":
        rows.append(
            HcHookBehavior(
                name="t_search_category_lookup",
                status="classified_not_native_search",
                evidence=("t_Search_* SQL LIKE snippets", "pluginFunction export"),
                implementation="sidecar schema classification",
                notes="The helper queries category-specific search tables; it is distinct from native INDEX.DIC search.",
            )
        )
    if code == "0C80":
        rows.append(
            HcHookBehavior(
                name="array_no_t_contents_lookup",
                status="implemented_when_sidecar_present",
                evidence=("f_array_no SQL strings", "t_contents f_contents/f_media fields"),
                implementation="schema-backed rendererdb classification",
                notes="Body/media rows use array number indirection rather than the common f_DataId-only shape.",
            )
        )
    if code in {"00D3", "00D5", "00DE"}:
        rows.append(
            HcHookBehavior(
                name="britannica_panel_media_html",
                status="classified_partially_implemented",
                evidence=("initializePanel/finalizePanel exports", "Media/HTMLs body template strings"),
                implementation="Panel/Britannica auxiliary file decoders where available",
                notes="Panel lifecycle and media HTML paths are product behavior; exact viewer UI callbacks are not emulated.",
            )
        )
    if code in {"013A", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C4", "02C5", "02C7"}:
        rows.append(
            HcHookBehavior(
                name="panel_lifecycle",
                status="classified_not_emulated",
                evidence=("initializePanel/finalizePanel exports",),
                implementation="Panel file/label decoders only",
                notes="The DLL initializes product panel UI state; normal entry body rendering does not require running the panel hook.",
            )
        )
    if code == "013A":
        rows.append(
            HcHookBehavior(
                name="haespjpn_example_section_badge",
                status="branch_subset_implemented",
                evidence=("HC013A exam.png template", "1f09 section 0011 example block branch"),
                implementation="raw HONMON section 0011 inserts the discovered exam image once per contiguous examples block",
                notes="HC013A decodes the 0011 section payload as decimal 11 and keeps the example block active across sections 0010, 0011, and 0012.",
            )
        )
    if code == "00C6":
        rows.append(
            HcHookBehavior(
                name="dconci87_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00C6 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC00C6 A23C/A23D and A24C/A24D partwaku branches",
                    "HC00C6 A244 supAB marker state",
                    "Templates/000000c6.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/midashi_JE/yakugo/contents/exampleyakugo divs, "
                    "example sections insert exam.png once per contiguous block, A23C/A23D and "
                    "A24C/A24D create partwaku boxes, A244/A245 wraps A/B labels as supAB, and "
                    "template-backed gaiji markers use HC00C6 image classes"
                ),
                notes="The branch subset excludes unresolved DAT_* literal branches and the custom DIB transformation path.",
            )
        )
    if code == "02BE":
        rows.append(
            HcHookBehavior(
                name="kqdental_section_and_phonetic_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC02BE epwing2HtmlBodydataVertical ind_%04d section templates",
                    "HC02BE A/B gaiji branch ladder for phonetic accent image composites",
                    "HC02BE B928/B929 hatsuon and B92C/B92D yomigana branches",
                    "Templates/000002BE.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to ind_#### blocks, phonetic marker gaiji render "
                    "nowrap half/full accent image composites, B928/B929 render hatsuon, "
                    "B92C/B92D render yomigana, and B924/B925 are suppressed as renderer selectors"
                ),
                notes="Panel lifecycle, SQL/search hooks, modifyHeadword, and custom DIB paths remain named gaps.",
            )
        )
    if code == "02BC":
        rows.append(
            HcHookBehavior(
                name="stedman6_section_and_medical_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC02BC epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC02BC B121-B139 span/color marker branches",
                    "HC02BC A145/A146, A147/A148, A159/A15E/A160, and B126-B131 composite marker branches",
                    "Templates/000002bc.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/komidashi/honbun/contents blocks, "
                    "section 0002 emits fukumidashi.png where available, B121-B124/B125 "
                    "wrap blue text, B132/B133 small-cap text, B134-B139 color/bold spans, "
                    "B13C-B13E structural breaks/indent blocks, and the decoded A/B marker "
                    "set emits the DLL's inline chemistry/phonetic composites"
                ),
                notes="The branch subset excludes custom DIB generation, modifyHeadwordEx, and unverified vertical-navigation table scaffolding.",
            )
        )
    if code == "02C2":
        rows.append(
            HcHookBehavior(
                name="kqcolexp_section_icons_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC02C2 epwing2HtmlBodydataVertical 1f09 branch ladder",
                    "HC02C2 B13E-B15D template image branch exclusion set",
                    "HC02C2 1f41/1f4c skip branch and lineLink template",
                    "Templates/000002C2.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/honbun blocks, sections 0007-000A emit "
                    "1.png-4.png img_icon markers, section 0007 opens moji-down text, "
                    "B13E-B15D render as img_gaiji template images, and line links carry "
                    "the recovered lineLink class"
                ),
                notes="Panel lifecycle, modifyHeadwordEx, and custom DIB generation remain named gaps.",
            )
        )
    if code == "00A6":
        rows.append(
            HcHookBehavior(
                name="hkkigak6_sections_and_ruby_directives",
                status="branch_subset_implemented",
                evidence=(
                    "HC00A6 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC00A6 1fe2/1fe3 RUB:S/RUB:E ruby directive branch",
                    "HC00A6 1f42/1f43 lineLink branch",
                    "Templates/000000A6.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi, midashi_kana, midashi_eng, "
                    "red emphasis, header, image_caption, chosha, and indented "
                    "honbun containers; 1fe2/1fe3 RUB:S/RUB:E directives render "
                    "ruby7/rb7/rt7 markup; 1f42/1f43 links use lineLink; 1f6d is "
                    "consumed as renderer state"
                ),
                notes="Custom DIB generation, private HTM/IMG directive file loading, media-image special cases, and modifyHeadword remain named gaps.",
            )
        )
    if code in {"014A", "02C3"}:
        rows.append(
            HcHookBehavior(
                name="hkdk_medical_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    f"HC{code} epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    f"HC{code} 1f41 midashi branch",
                    f"HC{code} 1f42/1f43 lineLink/lineLink2/lineLink3 branch",
                    f"HC{code} image gaiji template branch",
                    "HKDKSR medical CSS class definitions",
                ),
                implementation=(
                    "1f09 sections map the understood medical-entry subset to "
                    "midashi-adjacent body blocks, title3, med/medblk/medprice/"
                    "medimage, mednamelist, table_pc, indentNN, clickmenu, and "
                    "hidden-field containers; 1f41/1f61 wrap midashi text, 1f42/"
                    "1f43 links use recovered lineLink classes, 1f6d is consumed "
                    "as renderer state, and template-backed gaiji use img_gaiji"
                ),
                notes="The subset does not emulate stateful local_cc/local_fc branches, JIS-content-triggered section images, custom DIB generation, modifyHeadword, panel hooks, or product SQL helpers.",
            )
        )
    if code == "009B":
        rows.append(
            HcHookBehavior(
                name="gen2001_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC009B epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC009B 1f41 midashi branch",
                    "HC009B 1f42/1f43 lineLink branch",
                    "HC009B image gaiji template branch",
                ),
                implementation=(
                    "1f09 sections open honbun margin containers or header blocks, "
                    "1f0a closes the active section, 1f41/1f61 wrap midashi text, "
                    "1f42/1f43 links use lineLink, image-backed gaiji use img_gaiji, "
                    "and 1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes fixed HTMLs/body fallback loading, custom DIB file generation, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "02C0":
        rows.append(
            HcHookBehavior(
                name="gen2015_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC02C0 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC02C0 1f41 midashi branch",
                    "HC02C0 1f42/1f43 lineLink branch",
                    "HC02C0 1fe2 2331-2334 img_icon directive branch",
                    "HC02C0 image-backed gaiji template branch",
                    "Templates/000002C0.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, other body sections "
                    "open honbun margin containers, section 000c opens footer, 1f0a "
                    "closes the active section, 1f41/1f61 wrap midashi text, internal "
                    "links use lineLink, 1fe2 2331-2334 emit 1.png-4.png img_icon "
                    "markers, image-backed gaiji use img_gaiji, and B138/B14C/B14D "
                    "plus 1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes JIS-content-triggered img_mark branches, custom DIB file generation, modifyHeadword, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "02CA":
        rows.append(
            HcHookBehavior(
                name="gen2019_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC02CA epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC02CA 1f41 midashi branch",
                    "HC02CA 1f42/1f43 lineLink branch",
                    "HC02CA 1fe2 2331-2334 img_icon directive branch",
                    "HC02CA B12D-B12F img_mark branches",
                    "HC02CA B135 literal &#x20BB7; branch",
                    "Templates/000002CA.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, other body sections "
                    "open honbun margin containers, section 000c opens footer, 1f0a "
                    "closes the active section, 1f41/1f61 wrap midashi text, internal "
                    "links use lineLink, 1fe2 2331-2334 emit 1.png-4.png img_icon "
                    "markers, B12D-B12F render as img_mark, B130/B131 are consumed as "
                    "renderer state markers, and B135 emits U+20BB7"
                ),
                notes="The subset excludes custom DIB generation, modifyHeadword, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "013C":
        rows.append(
            HcHookBehavior(
                name="gen2014_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC013C epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC013C 1f41 midashi branch",
                    "HC013C 1f42/1f43 lineLink branch",
                    "HC013C 1fe2 2331-2334 img_icon directive branch",
                    "HC013C image-backed gaiji template branch",
                    "HC013C modifyHeadword removes B121/B122/A435 and suppresses A436-delimited ranges",
                    "Templates/0000013C.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, body sections "
                    "open honbun margin containers, section 000c opens footer, 1f0a "
                    "closes the active section, 1f41/1f61 wrap midashi text, internal "
                    "links use lineLink, 1fe2 2331-2334 emit 1.png-4.png img_icon "
                    "markers, image-backed gaiji use img_gaiji, and A435/A436 plus "
                    "1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes JIS-content-triggered img_mark branches, exact generated custom-bitmap output, modifyHeadword application to external hit lists, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "00B3":
        rows.append(
            HcHookBehavior(
                name="gen2012_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC00B3 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC00B3 1f41 midashi branch",
                    "HC00B3 1f42/1f43 lineLink branch",
                    "HC00B3 COLSCR/PCMDATA branch templates",
                    "Templates/000000B3.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, body sections "
                    "open honbun margin containers, section 000c opens a header "
                    "container, 1f0a closes the active section, 1f41/1f61 wrap "
                    "midashi text, internal links use lineLink, image-backed gaiji "
                    "use img_gaiji, and 1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes exact previous/next navigation footer generation, fixed HTML/body fallback loading, exact generated custom-bitmap output, and broader visual parity.",
            )
        )
    if code == "00A0":
        rows.append(
            HcHookBehavior(
                name="gakken_phrase_sql_detail_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC00A0 epwing2HtmlBodydata internal SDicSQLSearchAndHtml/SDicGetBodyData path",
                    "HC00A0 body loop stores 1f09 section 0001/0002 text slots",
                    "HC00A0 private fullwidth <PlaySound> directive branch",
                    "HTMLs/Header.html and HTMLs/Detail.html template placeholders",
                    "Templates/GK*.db Data000000Ax Block/Offset menu mapping",
                ),
                implementation=(
                    "section 0001 renders through the Detail.html English slot, section 0002 "
                    "renders through the Japanese slot, the private PlaySound directive supplies "
                    "the mp3 filename, package image assets are remapped to copied Template paths, "
                    "and the profile records the SQL/menu and user-data hooks as non-emulated"
                ),
                notes="The subset excludes lved.sql interactive menu generation, user-data check/play-count persistence, and exact body%d.html lifecycle parity.",
            )
        )
    if code in {"02C4", "02C7"}:
        rows.append(
            HcHookBehavior(
                name="gen_year_section_icons_and_template_markers",
                status="branch_subset_implemented",
                evidence=(
                    f"HC{code} epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    f"HC{code} 1f42/1f43 lineLink branch",
                    f"HC{code} 1fe2 2331-2334 img_icon directive branch",
                    f"HC{code} B12D-B12F and B132-B137 template marker branches",
                    f"Templates/0000{code}.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/honbun/footer containers, 1f41/1f61 "
                    "wrap midashi text, internal links use lineLink, 1fe2 2331-2334 "
                    "emit 1.png-4.png img_icon markers, B12D-B12F render as img_mark, "
                    "B132-B137 render as img_mark2, and B130/B131/B138 are consumed as "
                    "renderer state markers"
                ),
                notes="The subset excludes custom DIB generation, modifyHeadword, exact fixed-HTML fallback selection, and vertical navigation table scaffolding.",
            )
        )
    if code == "0065":
        rows.append(
            HcHookBehavior(
                name="geniuseb_midashi_contents_and_grammar_labels",
                status="branch_subset_implemented",
                evidence=(
                    "HC0065 epwing2HtmlBodydata initial midashi/contents_body path",
                    "HC0065 1f41 anchor/transition branch",
                    "HC0065 A430-A433 grammar-label literal branches",
                    "GENIUSEB.UNI A174 fallback record",
                    "Templates/00000065.css class definitions",
                ),
                implementation=(
                    "entry bodies open with midashi, 1f41 transitions to contents_body, "
                    "internal links use lLink, A174/A430-A433 render as B/c/u/S/D grammar "
                    "labels, and A251/A253 template image markers use img_gaiji"
                ),
                notes="The example/collocation box branches, SQL original-search hooks, modifyHeadwordEx, and custom DIB paths remain named gaps.",
            )
        )
    if code == "009D":
        rows.append(
            HcHookBehavior(
                name="ceremony_section_and_kakomi_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC009D epwing2HtmlBodydata 1f09 lineinfo branch ladder",
                    "HC009D 1f09 section-8 lookahead for B140/B142/B144/B146/B148/B14A/B150/B152/B154/B156/B158 kakomi markers",
                    "HC009D B121/B125/B130-B13D literal and HTML marker branches",
                    "Templates/0000009d.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfoN blocks, section-8 B14x lookahead opens "
                    "the recovered kakomi wrappers and icon images, B121 renders the pointing-hand "
                    "literal, B125 renders checkbox HTML, B130/B131/B138-B13D render line breaks, "
                    "and internal links use lineLink"
                ),
                notes=(
                    "The subset excludes custom DIB generation for remaining gaiji, exact table "
                    "header/body lifecycle, loose HTMLs fallback, and broader representative visual parity."
                ),
            )
        )
    if code == "012E":
        rows.append(
            HcHookBehavior(
                name="nkgorin2_kanji_layout_and_gaijitemp_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC012E epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC012E B238/B239/B241/B242 color and sizedown marker branches",
                    "HC012E B136-B139 direct Gaijitemp hatsuon image branch",
                    "Templates/0000012E.css and Gaijitemp resource paths",
                ),
                implementation=(
                    "Gaijitemp image resources are discovered, common HC012E section codes map "
                    "to honbun/bushu/kaku_midashi/exam/kanji-table blocks, B238/B239/B241/B242 "
                    "map to color/sizedown spans, and B136-B139 render Gaijitemp images with "
                    "the recovered hatsuon class"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword, original-search SQL, "
                    "and full kanji stroke-order table lifecycle parity."
                ),
            )
        )
    if code == "012F":
        rows.append(
            HcHookBehavior(
                name="yhougo4_bunnya_section_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC012F epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC012F 1f62 bunnya image/link branch",
                    "HC012F 1f06 sizedown branch",
                    "Templates/0000012F.css and Templates/bunnya_*.png resource paths",
                ),
                implementation=(
                    "1f09 sections map to midashi/honbun/bunnya/menu blocks, section 0006 "
                    "is consumed as renderer state, 1f62 replaces bunnya numeric link labels "
                    "with bunnya_<id>.png images, section 0004/0005 insert link_1/link_2 "
                    "icons, 1f06/1f07 render sizedown spans, and template-backed gaiji use "
                    "the product image class"
                ),
                notes=(
                    "The subset excludes exact previous/next entry navigation tables, custom "
                    "DIB generation, modifyHeadword, and exact vertical fallback details."
                ),
            )
        )
    if code == "0131":
        rows.append(
            HcHookBehavior(
                name="kqebhou_section_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0131 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0131 1f41/1f61 midashi branch",
                    "HC0131 1f06/1f07 sizedown subscript branch",
                    "HC0131 template gaiji filename branches for %04x[_W][_C][_V].png",
                    "Templates/00000131.css and Templates/b132.png resource paths",
                ),
                implementation=(
                    "1f41/1f61 render midashi blocks, recovered 1f09 content_IND and "
                    "contents sections are consumed into product classes, section 0012 "
                    "injects the b132 marker image when the stream did not already carry "
                    "it, 1f06/1f07 render sizedown subscript spans, internal links use "
                    "lineLink, and template-backed gaiji use the product image class"
                ),
                notes=(
                    "The subset excludes exact conditional HR/state transitions, custom "
                    "DIB generation, SQL search hooks, modifyHeadword, and exact vertical "
                    "color-variant image generation."
                ),
            )
        )
    if code == "012D":
        rows.append(
            HcHookBehavior(
                name="meikyou2_section_and_inline_image_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC012D epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC012D link_k/kaisetsu_s/kaisetsu_m/link_t inline image branches",
                    "HC012D 1f41/1f61 midashi-to-honbun_user transition",
                    "Templates/0000012D.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi-adjacent honbun/honbun_start/yorei/yindex/"
                    "hinshi/kaisetsu/ruigo blocks, internal links use lineLink, 217E/2221/"
                    "222A-before-link/224E render recovered template images, and A134/A137 "
                    "spacing markers follow the DLL branches"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword hooks, SQL original-search "
                    "helpers, exact yindex/ruigo script lifecycle, and unexercised section-code branches."
                ),
            )
        )
    if code == "0145":
        rows.append(
            HcHookBehavior(
                name="rdrsp2_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0145 epwing2HtmlBodydataVertical 1f09 decimal section branch ladder",
                    "HC0145 B924/B925 bold-italic marker branch",
                    "HC0145 A921-A924 and B92A/B92B/B934/B936 literal marker branches",
                    "Templates/00000145.css class definitions",
                ),
                implementation=(
                    "1f09 decimal section values map to midashi/komidashi/honbun/contents "
                    "blocks, internal links use lineLink, B924/B925 wrap bold-italic spans, "
                    "and recovered bracket/superscript/parenthesis/spacing literals are emitted "
                    "while known renderer selectors are consumed"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadwordEx, SQL original-search "
                    "and D_Example/D_Idiom hooks, exact table/navigation wrapper lifecycle, and broader "
                    "representative visual parity."
                ),
            )
        )
    if code == "013D":
        rows.append(
            HcHookBehavior(
                name="hkdksr13_drug_layout_and_template_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC013D epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC013D 215A/2223 JIS-pair lookahead branches for subtitle/title images",
                    "HC013D 236D/2364/234C litre-unit branches",
                    "Templates/0000013d.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi-adjacent title3/medblk/med/medprice/"
                    "medimage/mednamelist/indent blocks, internal links use lineLink, "
                    "1f6d is consumed as renderer state, template-backed gaiji markers use "
                    "img_gaiji, and recovered syohatsu/midashi/title/litre/entity JIS "
                    "branches emit the DLL template HTML"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword, exact "
                    "contents2-5 state transitions, table_pc/clickmenu lifecycle, and "
                    "full COLSCR picture extraction into HTML."
                ),
            )
        )
    if code == "0144":
        rows.append(
            HcHookBehavior(
                name="rplusrev_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0144 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0144 B924/B925 bold-italic marker branch",
                    "HC0144 A921-A924 and B92A/B92B/B934/B936 literal marker branches",
                    "HC0144 B921/B926-B929/B92C-B92F/B931-B933/B935/B937 no-output marker branches",
                    "Templates/00000144.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/komidashi/honbun/contents blocks, "
                    "1f41 is consumed as renderer state, internal links use lineLink, "
                    "B924/B925 wrap bold-italic spans, recovered literal markers are "
                    "emitted, and known selector/image markers are consumed"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadwordEx, SQL "
                    "D_Example/D_Idiom helpers, exact HTMLs/fix fallback lifecycle, and "
                    "smallcap/custom-character image suffix selection."
                ),
            )
        )
    if code == "03E8":
        rows.append(
            HcHookBehavior(
                name="genkana5_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC03E8 epwing2HtmlBodydataVertical 1f09 section branch and lineLink rewrite paths",
                    "HC03E8 B924/B925 bold-italic marker branch",
                    "HC03E8 A921-A924, A130/A131, B92A/B92B/B934/B936 literal marker branches",
                    "HC03E8 B921/B939 and B926-B929/B92C-B92F/B931-B933/B935/B937 no-output marker branches",
                    "Templates/000003e8.css class definitions",
                ),
                implementation=(
                    "Observed 1f09 sections map to midashi/honbun/contents-style blocks, "
                    "1f41 is consumed as renderer state, internal links use lineLink, "
                    "B924/B925 wrap bold-italic spans, recovered literal markers are "
                    "emitted, B936 emits the closing bracket plus nonbreaking space seen "
                    "in the DLL string table, and known selector/image markers are consumed"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadwordEx, SQL full-text/"
                    "zenbun search hooks, exact HTMLs/fix fallback lifecycle, custom-character "
                    "image suffix selection, and unexercised dense-sidecar body behavior."
                ),
            )
        )
    if code == "0141":
        rows.append(
            HcHookBehavior(
                name="readers3_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0141 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0141 B924/B925 bold-italic marker branch",
                    "HC0141 A921-A924, A130/A131, B92A/B92B/B934/B936 literal marker branches",
                    "HC0141 B926-B929/B92C-B92F/B931-B933/B935 no-output marker branches",
                    "Templates/00000141.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/komidashi/honbun/contents blocks, "
                    "1f41 is consumed as renderer state, internal links use lineLink, "
                    "B924/B925 wrap bold-italic spans, recovered literal markers are "
                    "emitted, B936 emits the closing bracket plus nonbreaking space seen "
                    "in the DLL string table, and known selector markers are consumed"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword, dictionary-original "
                    "SQL search hooks, D_Example/D_Idiom helper integration, exact body-file/fix "
                    "fallback lifecycle, custom-character image suffix selection, and broader "
                    "representative visual parity."
                ),
            )
        )
    if code == "0190":
        rows.append(
            HcHookBehavior(
                name="sizk_html_template_section_substitution",
                status="branch_subset_implemented",
                evidence=(
                    "HC0190 epwing2HtmlBodydataVertical B121-B124 template selector branch",
                    "HC0190 section-bucket replacement of <!--&IND####;--> placeholders",
                    "HC0190 HTMLs/%04lx.html fallback path",
                    "HTMLs/b121.html through HTMLs/b124.html and Templates/00000190.css",
                ),
                implementation=(
                    "B121-B124 select package HTML templates, 1f09 sections are bucketed "
                    "by numeric section id, captured section HTML replaces matching "
                    "<!--&IND####;--> placeholders, and missing placeholders are left empty"
                ),
                notes=(
                    "The subset excludes exact JavaScript audio-player lifecycle, runtime "
                    "fix-directory override behavior, original viewer temp-file output, and "
                    "full visual parity across all read-aloud set volumes."
                ),
            )
        )
    if code == "009C":
        rows.append(
            HcHookBehavior(
                name="sesgrass_section_image_index_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC009C epwing2HtmlBodydataVertical 1f09 honbun/midashi section branch",
                    "HC009C B122/B128-B13A/B148-B14D marker branch ladder",
                    "HC009C private IMG:I########.PNG image-link path using images/images_thumb/images_icon directories",
                    "Templates/0000009C.css class definitions",
                ),
                implementation=(
                    "1f09 sections produce product midashi/honbun blocks, 1f41 is consumed "
                    "as renderer state, internal links use lineLink, decoded B12x/B13x/B14x "
                    "markers produce product image/table/comment classes or no-output selector "
                    "state, and private IMG directives resolve to package image thumbnails/full images"
                ),
                notes=(
                    "The subset excludes exact previous/next footer lifecycle, popup JavaScript "
                    "window behavior, full background-image toggle state, custom DIB fallback, "
                    "and representative visual parity for every SESGRASS entry shape."
                ),
            )
        )
    if code == "02C5":
        rows.append(
            HcHookBehavior(
                name="genius53_section_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC02C5 epwing2HtmlBodydata 1f09 section ladder",
                    "HC02C5 1f41 midashi/CB_Title branch",
                    "HC02C5 1f42/1f43 lLink branch and 1f5c/1f6d anchor-close branch",
                    "HC02C5 B146-B150/B373-B37B/B443-B44D strong-number branch",
                    "HC02C5 B353-B358/B37C-B423/B44E-B455 small-letter branch",
                    "HC02C5 B273/B347/B348/B372 img_hin branch",
                    "Templates/000002C5.css class definitions",
                ),
                implementation=(
                    "1f41 headings produce product midashi/CB_Title blocks, internal links "
                    "use lLink, 1f5c/1f6d anchor-close controls are consumed, decoded 1f09 "
                    "sections map to recovered product wrappers, and recovered marker gaiji "
                    "emit strong/small literals or img_hin images"
                ),
                notes=(
                    "The subset excludes exact select-menu lifecycle, full gohou/gohou2 "
                    "lookahead branches, custom character DIB generation, modifyHeadword, "
                    "Panel hooks, and SQL/search helper hooks."
                ),
            )
        )
    if code == "0151":
        rows.append(
            HcHookBehavior(
                name="ibio5_section_table_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0151 epwing2HtmlBodydata 1f09 indent/table section ladder",
                    "HC0151 1f41 midashi and 1f61 contents branch",
                    "HC0151 1f42 Link and 1f43 lineLink anchor branches",
                    "HC0151 B156/B157 small-text and B159 table-cell transition branches",
                    "Templates/00000151.css class definitions",
                ),
                implementation=(
                    "1f41/1f61 split entries into midashi and contents blocks, 1f09 "
                    "sections map to recovered indent/table wrappers, internal links use "
                    "Link/lineLink classes, 1f6d is consumed as renderer state, and "
                    "B156/B157/B159 produce recovered small/table-cell markup"
                ),
                notes=(
                    "The subset excludes exact previous/next navigation anchors, HTMLs/fix "
                    "fallback lifecycle, custom character DIB generation, modifyHeadwordEx, "
                    "Panel hooks, and SQL/search helper hooks."
                ),
            )
        )
    if code == "0142":
        rows.append(
            HcHookBehavior(
                name="yuecono5_panel_body_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0142 epwing2HtmlBodydataVertical 1f41/1f0a midashi-to-honbun transition",
                    "HC0142 1f42/1f43 lineLink anchor branch",
                    "HC0142 1f10 overline/rubar branch",
                    "HC0142 B177/B178 math span, B13F icotype_1, A164 margin, and B157/B16A-B170 plain_text branches",
                    "Templates/00000142.css class definitions",
                ),
                implementation=(
                    "1f41 starts midashi, 1f61 is consumed as renderer state, first 1f0a "
                    "closes midashi and opens the honbun container, later 1f0a emits line "
                    "breaks, internal links use lineLink, and recovered marker gaiji emit "
                    "math/plain_text/icotype/margin or classed gaiji image markup"
                ),
                notes=(
                    "The subset excludes exact Panel UI lifecycle, HTMLs/fix fallback file "
                    "selection, math image/formula media lookahead, custom character DIB "
                    "generation, modifyHeadwordEx, and vertical-mode template differences."
                ),
            )
        )
    if code == "02C1":
        rows.append(
            HcHookBehavior(
                name="kqjcollo_panel_body_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC02C1 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC02C1 1f42/1f43 lineLink branch",
                    "HC02C1 B121-B138 moji-down marker branch and B13E-B14D image-template branch",
                    "Templates/000002C1.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/honbun containers, sections 0003-0006 "
                    "insert recovered 1.png/2.png/3.png/5.png icon headers, B121-B138 "
                    "markers open moji-down paragraphs when they directly follow a section "
                    "state, B13E-B14D image markers use img_mark4, 1f41/1f61 are consumed "
                    "as renderer state, and internal links use lineLink"
                ),
                notes=(
                    "The subset excludes exact custom DIB generation, Panel lifecycle, "
                    "HTMLs/fix fallback lifecycle, modifyHeadwordEx, exact ruby/smallcap "
                    "state transitions, and full vertical template differences."
                ),
            )
        )
    if code == "02BF":
        rows.append(
            HcHookBehavior(
                name="kqlatino_panel_body_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC02BF epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC02BF 1f42/1f43 lineLink branch",
                    "HC02BF B128-B150 moji-down marker branch",
                    "Templates/000002BF.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/honbun containers, section 0005 "
                    "inserts the recovered hasei.png icon header, B128-B150 markers "
                    "open moji-down paragraphs when they directly follow section state, "
                    "1f41/1f61 are consumed as renderer state, and internal links use "
                    "lineLink"
                ),
                notes=(
                    "The subset excludes exact custom DIB generation, Panel and SQL "
                    "lifecycle hooks, HTMLs/fix fallback lifecycle, modifyHeadwordEx, "
                    "exact ruby/smallcap state transitions, and full vertical template "
                    "differences."
                ),
            )
        )
    if code == "0146":
        rows.append(
            HcHookBehavior(
                name="proyal43_inline_marker_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0146 epwing2HtmlBodydataVertical B232/B233 color-font branches",
                    "HC0146 B157-B159, B25A-B351, B23B, and B357-B424 image-template branches",
                    "Templates/00000146.css class definitions",
                ),
                implementation=(
                    "B232/B233 color_font delimiter pair, B240 literal abbreviation label, "
                    "nonprinting template selectors, and classed image gaiji templates"
                ),
                notes="The implementation covers branches whose destination template is recovered from the body loop and package CSS; unresolved BSS-backed open spans remain named gaps.",
            )
        )
    if code == "0158":
        rows.append(
            HcHookBehavior(
                name="archsic4_inline_style_gaiji",
                status="branch_subset_implemented",
                evidence=("HC0158 epwing2HtmlBodydata B353-B37E branches", "00000158.css span classes"),
                implementation="B3xx formatting-marker gaiji map to HC0158 CSS spans; normal B253/B347 image gaiji stay resource-backed",
                notes="B379 is conditional: labels before translation/usage/figure text select waku_red/back_red/waku variants from the next JIS pair.",
            )
        )
        rows.append(
            HcHookBehavior(
                name="archsic4_sound_icon_audio_link",
                status="branch_subset_implemented",
                evidence=("HC0158 lved.sond template", "Templates/sound.png"),
                implementation="PCMDATA audio ranges render as sound.png links for HC0158",
                notes="The href remains the toolkit resource address rather than claiming exact viewer URL parity.",
            )
        )
    if code == "0157":
        rows.append(
            HcHookBehavior(
                name="dconci98_inline_style_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0157 epwing2HtmlBodydataVertical A14D/A14E accent branches",
                    "HC0157 B156-B241 CSS span branch ladder",
                    "Templates/00000157.css class definitions",
                ),
                implementation=(
                    "A14D/A14E accent markers, B156-B241 CSS span delimiters, "
                    "and B22D-B23B red circled-number gaiji wrappers"
                ),
                notes="Branches that call the custom-character path render the same gaiji code inside the opened span instead of swallowing it as metadata.",
            )
        )
        rows.append(
            HcHookBehavior(
                name="dconci98_sound_icon_audio_link",
                status="branch_subset_implemented",
                evidence=("HC0157 lved.sond template", "sound.png/img_mark2 template strings", "1f4a/1f6a body loop branches"),
                implementation="PCMDATA audio ranges render as sound.png links for HC0157",
                notes="The href remains the toolkit resource address rather than claiming exact viewer URL parity.",
            )
        )
    if code == "0190":
        rows.append(
            HcHookBehavior(
                name="readaloud_htmls_template",
                status="classified",
                evidence=("HTMLs/%04lx.html template",),
                implementation="loose HTML/media extraction paths",
                notes="The renderer references read-aloud HTML/audio assets outside the body stream.",
            )
        )
    if code == "009B":
        rows.append(
            HcHookBehavior(
                name="block_offset_htmls_template",
                status="classified",
                evidence=("HTMLs/%d-%d.html template",),
                implementation="loose HTML/media extraction paths",
                notes="Shared GEN renderer can prefer prebuilt HTML files for some entry resources.",
            )
        )
    return rows


def build_hc_behavior_profile(
    renderer: HcRendererClassification | None,
    *,
    schema_sidecars: dict[str, Any] | None = None,
    rendererdb_summary: dict[str, Any] | None = None,
    raw_gaps: dict[str, Any] | None = None,
) -> HcBehaviorProfile:
    """Build a behavior profile for one HC renderer package."""

    code = renderer.code if renderer is not None else None
    name = renderer.path.name if renderer is not None else None
    features = renderer.features if renderer is not None else {}
    roles = _sidecar_roles(schema_sidecars)
    has_body_sidecar = _has_body_sidecar(schema_sidecars)
    rendererdb_ok = _rendererdb_ok(rendererdb_summary)

    implemented = {
        "common_sed_text_controls",
        "line_breaks_and_style_pairs",
        "address_link_placeholders",
        "COLSCR_picture_placeholders",
        "PCMDATA_audio_range_placeholders",
        "PCMDATA_sound_icon_when_asset_present",
        "gaiji_unicode_or_image_fallback",
        "private_renderer_directive_suppression",
    }
    hooks = _known_code_hooks(code)
    if rendererdb_ok:
        implemented.add("schema_backed_exact_entry_html")
        body_strategy = "rendererdb_html"
        body_status = "exact_entry_body_html"
    elif has_body_sidecar:
        body_strategy = "rendererdb_html"
        body_status = "sidecar_detected_but_unresolved"
    else:
        body_strategy = "raw_honmon_controls"
        body_status = "common_renderer_reimplementation"

    if rendererdb_summary and int(rendererdb_summary.get("ziptomedia_written", 0) or 0):
        implemented.add("ziptomedia_reference_extraction")
    if rendererdb_summary and int(rendererdb_summary.get("media_written", 0) or 0):
        implemented.add("sidecar_media_blob_extraction")
    if code == "013A":
        implemented.add("HC013A_example_section_badge")
    if code == "00C6":
        implemented.add("HC00C6_section_and_marker_layout")
    if code == "02BE":
        implemented.add("HC02BE_section_and_phonetic_markers")
    if code == "02BC":
        implemented.add("HC02BC_section_and_medical_markers")
    if code == "02C2":
        implemented.add("HC02C2_section_icons_and_template_gaiji")
    if code == "00A6":
        implemented.add("HC00A6_sections_and_ruby_directives")
    if code in {"014A", "02C3"}:
        implemented.add("HC_HKDKSR_medical_section_layout")
    if code == "009B":
        implemented.add("HC009B_honbun_margin_sections")
    if code == "00B3":
        implemented.add("HC00B3_honbun_margin_sections")
    if code == "00A0":
        implemented.add("HC00A0_phrase_detail_renderer")
    if code == "013C":
        implemented.add("HC013C_honbun_margin_sections")
    if code == "02C0":
        implemented.add("HC02C0_honbun_margin_sections")
    if code == "02CA":
        implemented.add("HC02CA_honbun_margin_sections")
    if code in {"02C4", "02C7"}:
        implemented.add("HC_GEN_YEAR_section_icons_and_template_markers")
    if code == "02C1":
        implemented.add("HC02C1_section_icons_and_template_gaiji")
    if code == "02BF":
        implemented.add("HC02BF_section_icon_and_moji_down_layout")
    if code == "0065":
        implemented.add("HC0065_midashi_contents_and_grammar_labels")
    if code == "009D":
        implemented.add("HC009D_section_and_kakomi_layout")
    if code == "012E":
        implemented.add("HC012E_kanji_layout_and_gaijitemp_markers")
    if code == "012F":
        implemented.add("HC012F_bunnya_section_and_template_gaiji")
    if code == "0131":
        implemented.add("HC0131_kqebhou_section_and_template_gaiji")
    if code == "012D":
        implemented.add("HC012D_section_and_inline_image_markers")
    if code == "0145":
        implemented.add("HC0145_section_and_marker_layout")
    if code == "013D":
        implemented.add("HC013D_hkdksr13_drug_layout_and_template_markers")
    if code == "0144":
        implemented.add("HC0144_section_and_marker_layout")
    if code == "03E8":
        implemented.add("HC03E8_section_and_marker_layout")
    if code == "0141":
        implemented.add("HC0141_section_and_marker_layout")
    if code == "0190":
        implemented.add("HC0190_html_template_section_substitution")
    if code == "009C":
        implemented.add("HC009C_section_image_index_layout")
    if code == "02C5":
        implemented.add("HC02C5_section_marker_layout")
    if code == "0151":
        implemented.add("HC0151_section_table_marker_layout")
    if code == "0142":
        implemented.add("HC0142_panel_body_marker_layout")
    if code == "0146":
        implemented.add("HC0146_inline_marker_gaiji")
    if code == "0158":
        implemented.add("HC0158_inline_style_gaiji")
        implemented.add("HC0158_sound_icon_audio_link")
    if code == "0157":
        implemented.add("HC0157_inline_style_gaiji")
        implemented.add("HC0157_sound_icon_audio_link")

    feature_gaps = {
        "panel_hooks": "panel_lifecycle_hook",
        "plugin_hooks": "plugin_function_hook",
        "user_data_hooks": "user_data_hook",
        "dictionary_original_search": "dictionary_original_search_hook",
        "fulltext_search": "fulltext_search_hook",
        "headword_modifier": "modify_headword_hook",
        "custom_gaiji_dib": "custom_gaiji_dib_hook",
    }
    for feature, hook_name in feature_gaps.items():
        if not features.get(feature):
            continue
        if feature == "headword_modifier" and any("headword" in hook.name for hook in hooks):
            continue
        if feature == "plugin_hooks" and any("plugin" in item for hook in hooks for item in (hook.name, *hook.evidence)):
            continue
        if feature == "panel_hooks" and any("panel" in hook.name for hook in hooks):
            continue
        hooks.append(
            HcHookBehavior(
                name=hook_name,
                status="classified_not_emulated",
                evidence=(f"feature:{feature}",),
                implementation=None,
            )
        )

    if features.get("sql_hooks") and not has_body_sidecar:
        hooks.append(
            HcHookBehavior(
                name="sql_hook",
                status="classified_not_emulated",
                evidence=("initializeSQL/finalizeSQL or SQL bridge imports",),
                implementation=None,
            )
        )

    raw_gap_names = tuple(sorted(str(key) for key in (raw_gaps or {}).keys()))
    non_gap_status_prefixes = ("implemented", "branch_subset_implemented")
    hook_gap_names = tuple(sorted(hook.name for hook in hooks if not hook.status.startswith(non_gap_status_prefixes)))
    branch_subset_names = tuple(sorted(hook.name for hook in hooks if hook.status.startswith("branch_subset_implemented")))
    notes: list[str] = []
    if rendererdb_ok:
        notes.append("Entry body HTML is taken from the renderer/app sidecar, not reconstructed from raw HONMON controls.")
    if branch_subset_names:
        notes.append("Decoded branch subsets are implemented, but product visual parity is still unverified/incomplete.")
    if hook_gap_names:
        notes.append("Remaining hook gaps are named; exact HC parity is not claimed while these remain.")

    profile_gap_names = raw_gap_names + hook_gap_names + (("visual_parity_unverified",) if branch_subset_names else ())

    return HcBehaviorProfile(
        renderer_code=code,
        renderer_name=name,
        family=_family_for_code(code, renderer),
        body_strategy=body_strategy,
        body_strategy_status=body_status,
        exact_body_html_available=rendererdb_ok,
        exact_hc_parity=False,
        implemented_semantics=tuple(sorted(implemented)),
        hook_behaviors=tuple(sorted(hooks, key=lambda row: row.name)),
        schema_sidecar_roles=roles,
        named_gaps=tuple(sorted(set(profile_gap_names))),
        notes=tuple(notes),
    )
