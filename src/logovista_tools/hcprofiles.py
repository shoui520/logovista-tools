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
    if code in {"013A", "0137", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C4", "02C5", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"}:
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
    if code == "0159":
        rows.extend(
            [
                HcHookBehavior(
                    name="habgespa_t_contents_body_lookup",
                    status="implemented_when_sidecar_present",
                    evidence=("t_contents f_DataId/f_Html SQL", "dense HONMON decimal ID anchors", "vlpljblF LogoFontCipher sidecar"),
                    implementation="rendererdb data_id join",
                    notes="Normal entry body HTML is supplied by the renderer/app sidecar; raw HONMON records are decimal ID anchors.",
                ),
                HcHookBehavior(
                    name="habgespa_sql_search_helpers",
                    status="classified_not_emulated",
                    evidence=("execDicOrgSearchEx", "execDicZenbunSearch", "kisoku/t_contents SQL strings"),
                    implementation=None,
                    notes="The DLL includes product-specific dictionary-original and full-text search helpers distinct from normal entry body rendering.",
                ),
            ]
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
        rows.append(
            HcHookBehavior(
                name="ejje200_midashi_contents_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC014F epwing2HtmlBodydata 1f41 midashi and 1f61 contents branch",
                    "HC014F epwing2HtmlBodydata 1f09 section-state branch",
                    "HC014F 1f0a section-aware line-break suppression",
                    "HC014F 1f42 Link and 1f43 lineLink templates",
                    "HC014F 1fe0/1fe1 decoration mode branch",
                    "HC014F image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/0000014F.css class definitions",
                ),
                implementation=(
                    "1f41 opens midashi, 1f61 closes midashi and opens contents, "
                    "1f09 records renderer section state without emitting generic "
                    "section placeholders, 1f0a is suppressed in heading section 1 "
                    "and emitted elsewhere, 1f42 uses Link while 1f43 uses lineLink, "
                    "1fe0 modes 0/1/4 emit bold/italic/bold-italic, and image-backed "
                    "gaiji use dummy.gif plus img_gaiji or img_gaiji_midashi"
                ),
                notes="The subset excludes fixed HTML/fix fallback loading, exact previous/next page movement links, generated custom-character PNG output, SQL helper UI, modifyHeadwordEx, and broader visual parity.",
            )
        )
    if code == "0135":
        rows.append(
            HcHookBehavior(
                name="sinmei_section_and_private_marker_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC0135 epwing2HtmlBodydataVertical 1f09 section branch",
                    "HC0135 1f0a heading/body transition branch",
                    "HC0135 1f09 state-only section values 7/35 and conditional value 23 branch",
                    "HC0135 1f09 value 23 line-break lookbehind branch",
                    "HC0135 1f06/1f07 sizedown-subscript branch",
                    "HC0135 1f42/1f43 lineLink templates",
                    "HC0135 1fe2/1fe3 named private image directive branch",
                    "HC0135 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000135.css class definitions",
                ),
                implementation=(
                    "1f09 value 1 opens midashi, 1f0a closes midashi and opens "
                    "contents_body, values 9/11/12 map to content_IND0/1/2, "
                    "value 30 maps to contents_yourei, value 38 emits exam.png "
                    "when the template is present, values 7 and 35 are state-only, "
                    "value 23 emits a break only when it follows a halfwidth-close control, "
                    "1f06/1f07 emit sizedown subscript, "
                    "internal links use lineLink, named private directives render "
                    "template icons, and image-backed gaiji use dummy.gif plus "
                    "img_gaiji or img_gaiji_midashi"
                ),
                notes="The subset excludes fixed HTML/fix fallback loading, exact previous/next navigation, generated custom-character DIB output, SQL helper UI, modifyHeadword hooks, and broader visual parity.",
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
    if code in {"013A", "0137", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C4", "02C5", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"}:
        rows.append(
            HcHookBehavior(
                name="panel_lifecycle",
                status="classified_not_emulated",
                evidence=("initializePanel/finalizePanel exports",),
                implementation="Panel file/label decoders only",
                notes="The DLL initializes product panel UI state; normal entry body rendering does not require running the panel hook.",
            )
        )
    if code == "013F":
        rows.append(
            HcHookBehavior(
                name="block_offset_body_lookup",
                status="implemented_when_sidecar_present",
                evidence=(
                    "Block/Offset/Body SQL tables",
                    "HC013F SDicGetBodyData fallback and HTMLs path logic",
                    "HC013F 1f6d renderer-state branch in raw HONMON fallback",
                ),
                implementation="rendererdb block/offset body join plus raw HONMON fallback state-control suppression",
                notes="The renderer sidecar stores horizontal and vertical body HTML keyed by HONMON block/offset; panel/search/headword hooks remain outside the exact body path.",
            )
        )
    if code == "0132":
        rows.append(
            HcHookBehavior(
                name="ngfinanc_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0132 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0132 HTML template strings for midashi/honbun/gogi/sansho/kanren/example/kaisetsu blocks",
                    "Templates/00000132.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to the decoded finance dictionary div classes, "
                    "1f41 starts midashi, 1f04 uses hankaku, and address links use lineLink"
                ),
                notes="This is a raw HONMON branch subset; product-specific search and non-body helper hooks remain named gaps where present.",
            )
        )
    if code == "013A":
        rows.append(
            HcHookBehavior(
                name="haespjpn_example_section_badge",
                status="branch_subset_implemented",
                evidence=(
                    "HC013A exam.png template",
                    "1f09 section 0011 example block branch",
                    "HC013A B264 honbun2/strong branch",
                    "HC013A B26A/B26B suppressed marker branch",
                    "HC013A B263 custom bitmap branch",
                ),
                implementation=(
                    "raw HONMON section 0011 inserts the discovered exam image once per contiguous examples block, "
                    "B264 opens a honbun2/strong span until the next section, B26A/B26B are consumed as state, "
                    "and B263 emits the DLL-derived img_dummy plus generated-PNG template instead of a generic placeholder"
                ),
                notes="HC013A decodes the 0011 section payload as decimal 11 and keeps the example block active across sections 0010, 0011, and 0012; the custom DIB helper derives b263_M/b263_C/b263_V filenames from a base PNG when available, but exact generated bitmap bytes remain outside the reimplementation.",
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
                    "HC00C6 close-marker-in-halfwidth branch observed in Dconci87 entries",
                    "HC00C6 A244 supAB marker state",
                    "Templates/000000c6.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi/midashi_JE/yakugo/contents/exampleyakugo divs, "
                    "example sections insert exam.png once per contiguous block, A23C/A23D and "
                    "A24C/A24D create partwaku boxes including close markers that arrive inside "
                    "halfwidth spans with trailing text, A244/A245 wraps A/B labels as supAB, and "
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
                    "HC02BE 1f41 heading-anchor control is consumed as renderer state",
                    "HC02BE lineLink anchor template for 1f42/1f43 internal links",
                    "HC02BE A/B gaiji branch ladder for phonetic accent image composites",
                    "HC02BE B928/B929 hatsuon and B92C/B92D yomigana branches",
                    "Templates/000002BE.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to ind_#### blocks, phonetic marker gaiji render "
                    "nowrap half/full accent image composites, B928/B929 render hatsuon, "
                    "B92C/B92D render yomigana, B924/B925 are suppressed as renderer selectors, "
                    "1f41 is a nonprinting renderer state control, and internal links carry lineLink"
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
                    "wrap blue text, the rare B121/B122 pair closes blue spans without "
                    "B125 when no B125 appears before the section boundary, B132/B133 "
                    "small-cap text, B134-B139 color/bold spans, B13C-B13E structural "
                    "breaks/indent blocks, and the decoded A/B marker set emits the "
                    "DLL's inline chemistry/phonetic composites"
                ),
                notes=(
                    "The full STEDMAN6 raw-HONMON pass emits no raw render gaps after "
                    "the B122 implicit-close refinement. The branch subset still "
                    "excludes custom DIB generation, modifyHeadwordEx, and unverified "
                    "vertical-navigation table scaffolding."
                ),
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
    if code == "0147":
        rows.append(
            HcHookBehavior(
                name="yucogpsy_contents_bunken_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0147 epwing2HtmlBodydataVertical 1f09 BCD section branch ladder",
                    "HC0147 A12E/B141-B145 template image branch",
                    "HC0147 1f10 rubar/overline branch",
                    "HC0147 1f42/1f43 lineLink template",
                    "Templates/00000147.css class definitions",
                ),
                implementation=(
                    "1f09 BCD section values map to midashi, contents, contents_title, "
                    "contents_body, bunken/bunken_title, bunken_contents, and cyosha blocks; "
                    "9999 closes the active section; A12E/B141-B145 render as img_gaiji "
                    "template images; 1f10 renders rubar.png or an overline label; and "
                    "line links carry the recovered lineLink class"
                ),
                notes="Panel lifecycle, modifyHeadword, custom DIB generation, and exact nested bunken anchor bookkeeping remain named gaps.",
            )
        )
    if code == "0063":
        rows.append(
            HcHookBehavior(
                name="kqnewje5_contents_sections_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0063 epwing2HtmlBodydata 1f09 section branch ladder",
                    "HC0063 1f0a heading-to-contents_body branch",
                    "HC0063 1f41 midashi/anchor branch",
                    "HC0063 1f42/1f43 lineLink/lineLink2 branch",
                    "HC0063 A568/A569/B571/B65E/B661/B667 template-image branch",
                    "Templates/00000063.css class definitions",
                ),
                implementation=(
                    "the first 1f09/1f41 block opens midashi, the first 1f0a opens "
                    "contents_body, later 1f09/1f41 pairs are treated as invisible "
                    "anchors rather than visible headings, non-heading section values "
                    "open margin-left divs when present, 1f42 links use lineLink or "
                    "lineLink2 by neighboring marker context, 1f43 links use lineLink, "
                    "and the decoded template-image gaiji markers render package assets"
                ),
                notes="Exact custom DIB behavior and modifyHeadwordEx remain named gaps.",
            )
        )
    if code == "0093":
        rows.append(
            HcHookBehavior(
                name="gkgogen_lineinfo_sections_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0093 epwing2HtmlBodydata 1f09 lineinfo/contents_body branch",
                    "HC0093 1f04 hankaku/hankakuMidashi state branch",
                    "HC0093 1f42/1f43 lineLink branch",
                    "HC0093 B140/B148/B14A/B14C-B14E template-image branch",
                    "Templates/00000093.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfoN divs, the transition out of "
                    "lineinfo1 opens contents_body, section 5 opens the decoded "
                    "youreihan wrapper, 1f04 uses hankakuMidashi while the current "
                    "section is 1 and hankaku elsewhere, 1f42/1f43 links carry "
                    "lineLink, and decoded template-image markers render arrow, "
                    "meaning, etymology, and class_arrow assets"
                ),
                notes="Exact address-sensitive yourei/youreihan selection, ruby private directives, and custom DIB generation remain named gaps.",
            )
        )
        rows.append(
            HcHookBehavior(
                name="private_ruby_directive_hook",
                status="classified_not_emulated",
                evidence=(
                    "HC0093 epwing2HtmlBodydata 1fe2/1fe3 private directive string comparisons",
                    "ruby/ruby7 template strings recovered from HC0093",
                ),
                implementation=None,
                notes="The branch is classified but not emitted until directive text mapping is verified against representative entries.",
            )
        )
    if code == "0095":
        rows.append(
            HcHookBehavior(
                name="gksahou_lineinfo_sections_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0095 epwing2HtmlBodydata lineinfo%d-%d branch",
                    "HC0095 B138-B13C page-kind markers observed across GKSAHOU entries",
                    "HC0095 1f04 hankaku/hankakuMidashi branch",
                    "HC0095 1f42/1f43 lineLink branch",
                    "HC0095 B121/B123/B128-B12B/B131-B133/B12E/B12F template-image branches",
                    "Templates/00000095.css class definitions",
                ),
                implementation=(
                    "The first B138-B13C marker selects the lineinfo first axis "
                    "(ancillary, kana index, category, ordinary entry, or keigo column), "
                    "1f09 sections map to lineinfoX-N wrappers, leaving section 12 opens "
                    "contents_body, 1f04 uses hankakuMidashi while section 1 is active "
                    "and hankaku elsewhere, 1f42/1f43 links carry lineLink, and decoded "
                    "template markers render package assets with img_mark/img_mark2 classes."
                ),
                notes="Exact footer table lifecycle, private ruby directives, and custom DIB generation remain named gaps.",
            )
        )
    if code == "0096":
        rows.append(
            HcHookBehavior(
                name="gktisiki_lineinfo_sections_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0096 epwing2HtmlBodydata 1f09 lineinfo%d-%d branch",
                    "HC0096 1f04 hankaku/hankakuMidashi branch",
                    "HC0096 1f42/1f43 lineLink branch",
                    "HC0096 B121-B149 template-image branch",
                    "HC0096 B150-B152/B155-B15A reflow-state branch",
                    "Templates/00000096.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfo0-N divs for the decoded top-level "
                    "subset, leaving section 12 opens contents_body, 1f04 uses "
                    "hankakuMidashi while section 1 is active and hankaku elsewhere, "
                    "1f42/1f43 links carry lineLink, B121-B149 render template image "
                    "markers, and B150-B152/B155-B15A are treated as state/reflow "
                    "markers rather than visible gaiji"
                ),
                notes="Exact multi-group lineinfo first-axis selection, footer table lifecycle, private ruby directives, and custom DIB generation remain named gaps.",
            )
        )
        rows.append(
            HcHookBehavior(
                name="private_ruby_directive_hook",
                status="classified_not_emulated",
                evidence=(
                    "HC0096 epwing2HtmlBodydata 1fe2/1fe3 private directive string comparisons",
                    "ruby/ruby7 template strings recovered from HC0096",
                ),
                implementation=None,
                notes="The branch is classified but not emitted until directive text mapping is verified against representative entries.",
            )
        )
    if code == "009F":
        rows.append(
            HcHookBehavior(
                name="haisai_season_category_sections_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC009F epwing2HtmlBodydataVertical 1f09 section 6/7 lookahead branch",
                    "HC009F season JIS branch table maps 春/夏/秋/冬/新 labels to sp/su/au/wi/ny image stems and background colors",
                    "HC009F category JIS branch table maps 時/天/地/生/行/動/植 labels to marker suffixes 1..7",
                    "HC009F B121/B122 horizontal/vertical marker-image branches, B123 suppression, and B261-to-B167 alias",
                    "Templates/0000009F.css class definitions",
                ),
                implementation=(
                    "1f09 section 6 suppresses the following season label pair, emits the "
                    "orientation-specific season marker image, and opens a midashi block "
                    "with the recovered background color. 1f09 section 7 suppresses the "
                    "following category label pair and emits the current-season/category "
                    "marker image. B121/B122 render orientation-specific template markers, "
                    "B123 is nonprinting, B261 aliases to B167, and decoded template images "
                    "render as img_gaiji/img_gaiji_midashi with the renderer dummy image."
                ),
                notes=(
                    "Exact footer navigation lifecycle, private HTMLs/fix include handling, "
                    "generated custom-character DIB output, and representative visual parity remain named gaps."
                ),
            )
        )
    if code == "0091":
        rows.append(
            HcHookBehavior(
                name="kqsynonm_midashi_contents_marker_images",
                status="branch_subset_implemented",
                evidence=(
                    "HC0091 epwing2HtmlBodydata midashi/submidashi/contents_body template strings",
                    "HC0091 1f0a midashi-to-contents_body transition branch",
                    "HC0091 1f42/1f43 lineLink template",
                    "HC0091 JIS-sequence branches for rei/chikan/kaisetsu/hosoku template GIFs",
                    "HC0091 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000091.css class definitions",
                ),
                implementation=(
                    "1f41 opens midashi, 1f0a transitions to contents_body, 1f04 "
                    "uses hankakuMidashi while midashi is open and hankaku elsewhere, "
                    "1f42/1f43 links carry lineLink, decoded JIS label sequences render "
                    "rei/chikan/kaisetsu/hosoku marker images with dummy.gif, and "
                    "image-backed gaiji use img_gaiji or img_gaiji_midashi"
                ),
                notes="The subset excludes fixed HTML/fix fallback loading, exact submidashi continuation state, generated custom-character GIF output, modifyHeadword, and broader visual parity.",
            )
        )
    if code == "0090":
        rows.append(
            HcHookBehavior(
                name="kqejmed2_lineinfo_sections_and_gaiji_classes",
                status="branch_subset_implemented",
                evidence=(
                    "HC0090 epwing2HtmlBodydata 1f09 lineinfo/contents_body branch",
                    "HC0090 1f04 hankaku/hankakuMidashi state branch",
                    "HC0090 1f42/1f43 lineLink template",
                    "HC0090 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000090.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfoN wrappers, the transition out of "
                    "section 1 opens contents_body, section 5 opens the recovered yourei "
                    "wrapper, 1f04 uses hankakuMidashi while section 1 is active and "
                    "hankaku elsewhere, 1f42/1f43 links carry lineLink, and image-backed "
                    "gaiji use img_gaiji or img_gaiji_midashi"
                ),
                notes="The subset excludes address-sensitive yourei versus youreihan selection, private smallcap directives, fixed HTML/fix fallback loading, generated custom-character GIF output, and broader visual parity.",
            )
        )
    if code == "0020":
        rows.append(
            HcHookBehavior(
                name="kencollo_midashi_definition_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC0020 epwing2HtmlBodydata 1f09 margin branch",
                    "HC0020 1f41/1f0a midashi-to-contents_body branch",
                    "HC0020 2221/2126 diamond and nakaguro definition-list branch",
                    "HC0020 215A div_215a branch",
                    "HC0020 1f42/1f43 lineLink2/lineLink branch",
                    "HC0020 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000020.css class definitions",
                ),
                implementation=(
                    "Initial 1f41 opens midashi, first 1f0a opens contents_body, "
                    "non-heading 1f09 sections open 3-pixel-multiplied margin-left "
                    "containers, 2221/2126 create diamond/nakaguro definition-list "
                    "rows, 215A opens the div_215a block with hr_div2, 222A emits the "
                    "confer marker image, 1f42 uses lineLink2, 1f43 uses lineLink, and "
                    "image-backed gaiji use img_gaiji or img_gaiji_midashi"
                ),
                notes="The subset excludes conditional 1f42 lineLink class switching, exact previous-entry fallback rendering, address-threshold hr_div insertion, generated custom-character GIF output, and broader visual parity.",
            )
        )
    if code == "0094":
        rows.append(
            HcHookBehavior(
                name="gkkeigo_sections_color_blocks_and_template_gaiji",
                status="branch_subset_implemented",
                evidence=(
                    "HC0094 epwing2HtmlBodydata 1f09 section branch ladder",
                    "HC0094 B121-B13C template image branch",
                    "HC0094 B148 class_arrow.gif branch",
                    "HC0094 B150-B159 state marker branch",
                    "HC0094 custom-character bitmap branch",
                    "HC0094 B13E/B13F aka/beni color div branch",
                    "HC0094 1f42/1f43 lineLink template",
                    "Templates/00000094.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to midashi, contents_body, lineinfo, and footer blocks; "
                    "B121-B13C render as img_gaiji template images, B148 renders the class-arrow "
                    "asset, B150-B159 are consumed as renderer state markers, GA16/GAI16 custom "
                    "characters can be exported as BMP resources, B13E/B13F open aka/beni color "
                    "divs, and internal links carry the recovered lineLink class"
                ),
                notes="Exact footer previous/next table generation, custom DIB behavior, and visual parity remain named gaps.",
            )
        )
    if code == "00AA":
        rows.append(
            HcHookBehavior(
                name="hkbyoin_section_media_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00AA epwing2HtmlBodydataVertical 1f09 decimal-coded section ladder",
                    "HC00AA 1f42/1f43 lineLink template",
                    "HC00AA 1f4d media image placeholder branch",
                    "HC00AA Nurse.png, tejyun.png, indent102/105/109 template branches",
                    "Templates/000000aa.css class definitions",
                ),
                implementation=(
                    "1f09 sections map the understood hospital dictionary subset to "
                    "midashi, honbun, right-aligned honbun, boxed table, Nurse table, "
                    "tejyun image, and indent102/105/109 blocks; 1f42/1f43 links use "
                    "lineLink, 1f5c/1f6d are consumed as renderer state, and generic "
                    "media placeholders carry resolved COLSCR addresses"
                ),
                notes="Exact generated custom-character DIB output, footer navigation, fixed HTML fallback loading, and visual parity remain named gaps.",
            )
        )
    if code == "00A3":
        rows.append(
            HcHookBehavior(
                name="viku1000_quiz_answer_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00A3 epwing2HtmlBodydataVertical stateful 1f09 section ladder",
                    "HC00A3 quiz/answer/kaisetsu template strings",
                    "HC00A3 1f42/1f43 lineLink template",
                    "HC00A3 1f41/1f4c/1f5c/1f6d no-output renderer state branches",
                    "Templates/000000A3.css and 000000A3.js",
                ),
                implementation=(
                    "Adjacent 1f09 section values map the understood subset to honbun "
                    "margin blocks, nobr intro lines, hidden quiz/answer containers, "
                    "and kaisetsu reveal blocks; 1f42/1f43 links use lineLink and "
                    "HC00A3's renderer-state controls are consumed without visible output"
                ),
                notes="The subset does not emulate exact JavaScript lifecycle, previous/next footer buttons, generated custom-character DIB output, or visual parity.",
            )
        )
    if code == "00C5":
        rows.append(
            HcHookBehavior(
                name="gkkanyok_section_image_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00C5 epwing2HtmlBodydataVertical 1f09 section image ladder",
                    "HC00C5 1f41 midashi / 1f61 honbun_user branch",
                    "HC00C5 1f42/1f43 lineLink template",
                    "HC00C5 arrow/chui/imi/ruiku/sankou/tsuiku/yourei template images",
                    "Templates/000000C5.css class definitions",
                ),
                implementation=(
                    "1f09 sections map the understood idiom dictionary subset to "
                    "midashi state, honbun blocks, and labeled honbun image blocks; "
                    "1f41/1f61 wrap midashi and honbun_user, while internal links "
                    "use lineLink"
                ),
                notes="The subset does not emulate custom DIB generation, exact footer navigation tables, or representative visual parity.",
            )
        )
    if code == "00AD":
        rows.append(
            HcHookBehavior(
                name="kanjigen_large_character_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00AD epwing2HtmlBodydataVertical 1f09 section ladder",
                    "HC00AD large-character font-size branch",
                    "HC00AD paired explanation and HR-separated body branches",
                    "HC00AD 1f41 midashi branch and 1f42/1f43 lineLink template",
                    "Templates/000000AD.css class definitions",
                ),
                implementation=(
                    "1f09 sections map the understood subset to large-character "
                    "honbun blocks, paired explanation blocks, and HR-separated "
                    "body blocks; 1f41 wraps midashi text, 1f5c/1f6d are consumed "
                    "as renderer state, and links use lineLink"
                ),
                notes="The subset does not emulate exact horizontal table-cell lifecycle, custom DIB generation, footer navigation, or visual parity.",
            )
        )
    if code == "00BB":
        rows.append(
            HcHookBehavior(
                name="gen2000_honbun_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00BB epwing2HtmlBodydataVertical 1f09 section ladder",
                    "HC00BB honbun margin branch",
                    "HC00BB midashi and halfwidth span branches",
                    "HC00BB lineLink/media template strings",
                    "Templates/00000134.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to honbun/footer blocks with the recovered "
                    "4-pixel margin multiplier, 1f41/1f61 wrap midashi text, "
                    "1f04/1f05 handle hankaku spans, links use lineLink, and "
                    "1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset does not emulate fixed HTML/body fallback loading, generated custom DIB output, previous/next footer generation, or visual parity.",
            )
        )
    if code == "00AB":
        rows.append(
            HcHookBehavior(
                name="gkyojijk_honbun_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00AB epwing2HtmlBodydataVertical 1f09 section ladder",
                    "HC00AB honbun hanging-indent and normal-margin branches",
                    "HC00AB midashi and halfwidth span branches",
                    "HC00AB lineLink/media/template strings",
                    "Templates/000000AB.css class definitions",
                ),
                implementation=(
                    "1f09 sections map the understood subset to midashi state, "
                    "hanging honbun blocks, and normal honbun margin blocks; "
                    "1f41/1f61 wrap midashi text, 1f04/1f05 handle hankaku spans, "
                    "links use lineLink, and 1f4c/1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset does not emulate fixed HTML/body fallback loading, exact footer/table generation, generated custom-character DIB output, or visual parity.",
            )
        )
    if code == "004D":
        rows.append(
            HcHookBehavior(
                name="bmanner_midashi_honbun_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC004D epwing2HtmlBodydataVertical midashi/body branch",
                    "HC004D 1f41/1f61 midashi lifecycle",
                    "HC004D 1f09 honbun-open branch",
                    "HC004D 1f42/1f43 lineLink2/lineLink branch",
                    "Templates/0000004d.css class definitions",
                ),
                implementation=(
                    "1f41/1f61 wrap the heading in midashi, the first body 1f09 "
                    "opens honbun after heading state, 1f0a closes heading or emits "
                    "body breaks, 1f04/1f05 map to hankaku spans, and 1f42/1f43 "
                    "use the recovered lineLink2/lineLink classes"
                ),
                notes="The subset does not emulate generated custom-character GIF/DIB output, exact media wrapper lifecycle, or visual parity.",
            )
        )
    if code == "0076":
        rows.append(
            HcHookBehavior(
                name="hkebmbok_medical_body_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC0076 epwing2HtmlBodydata heading/body branch",
                    "HC0076 1f09 margin-left section branch",
                    "HC0076 1f41/1f61 midashi lifecycle",
                    "HC0076 1f42/1f43 lineLink2/lineLink/lineLink3 branch",
                    "Templates/00000076.css class definitions",
                ),
                implementation=(
                    "1f09 closes the previous block and opens a 3-pixel-multiplied "
                    "margin-left section, 1f41/1f61 wrap heading text in midashi, "
                    "1f0a closes heading sections or emits body breaks, 1f04/1f05 "
                    "map to hankaku spans, 1f42/1f43 use the recovered link classes, "
                    "2179/217a emit template image gaiji when assets are present, "
                    "and 1f5c/1f6d are consumed as renderer state"
                ),
                notes=(
                    "The subset does not emulate the five subtitle string-table branches, "
                    "generated custom-character GIF/DIB output, exact media wrapper lifecycle, "
                    "or visual parity."
                ),
            )
        )
    if code == "00C7":
        rows.append(
            HcHookBehavior(
                name="gkknjpzl_lineinfo_template_gaiji_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC00C7 FUN_100039b7 body loop 1f09 lineinfo branch",
                    "HC00C7 FUN_100039b7 B121-B12C img_mark4 branch",
                    "HC00C7 FUN_100039b7 B12D-B130/B135-B138 img_mark branch",
                    "HC00C7 FUN_100039b7 1f42/1f43 lineLink branch",
                    "Templates/000000C7.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfoN divs, section 22 maps to "
                    "lineinfo22 font state, transition out of sections 1-3 opens "
                    "contents_body, B121-B12C emit img_mark4 template images, "
                    "B12D-B130/B135-B138 emit dummy plus img_mark template images, "
                    "other template-backed gaiji emit dummy plus img_gaiji, "
                    "1f04/1f05 map to hankaku spans, 1f12/1f13 map to fontbold, "
                    "and 1f42/1f43 links use lineLink"
                ),
                notes=(
                    "The subset does not emulate fixed HTML/fix fallback loading, "
                    "previous/next navigation side buffers, SQL/search helper rendering, "
                    "generated custom-character DIB/GIF output, modifyHeadword/plugin/user hooks, "
                    "or visual parity."
                ),
            )
        )
    if code == "007D":
        rows.append(
            HcHookBehavior(
                name="kqnewej6_midashi_margin_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC007D body loop 1f09 heading-state and margin-section branches",
                    "HC007D body loop 1f41/1f61 midashi lifecycle branch",
                    "HC007D body loop 1f0a contents_body transition branch",
                    "HC007D body loop 1f42/1f43 lineLink2/lineLink branch",
                    "HC007D gaiji image branch selecting img_gaiji_midashi versus img_gaiji",
                    "Templates/0000007D.css class definitions",
                ),
                implementation=(
                    "1f09 followed by 1f41 is consumed as heading state, other "
                    "1f09 payloads open 3-pixel-multiplied margin-left divs, "
                    "1f41/1f61 wrap heading text in midashi, 1f0a transitions "
                    "the heading to contents_body, template-backed gaiji emit "
                    "dummy plus img_gaiji_midashi or img_gaiji according to the "
                    "current heading/body state, 1f04/1f05 map to hankaku spans, "
                    "1f42/1f43 links use lineLink2/lineLink, and 1f4c/1f5c/1f6d "
                    "are consumed as renderer state"
                ),
                notes=(
                    "The subset does not emulate generated custom-character DIB/GIF "
                    "output, exact media wrapper lifecycle, fixed HTML/fallback loading, "
                    "or representative visual parity."
                ),
            )
        )
    if code == "008F":
        rows.append(
            HcHookBehavior(
                name="kqbizej_jmidashi_margin_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC008F epwing2HtmlBodydata body loop 1f41 jMidashi branch",
                    "HC008F body loop 1f05 eMidashi_Japanese continuation branch",
                    "HC008F body loop 1f09 margin branch with 10-pixel multiplier",
                    "HC008F body loop 1f42/1f43 lineLink branch",
                    "HC008F gaiji image branch selecting img_gaiji_midashi versus img_gaiji",
                ),
                implementation=(
                    "1f41/1f61 wrap heading text in jMidashi, 1f05 opens "
                    "eMidashi_Japanese continuation text inside headings, 1f09 "
                    "opens margin divs using the recovered 10-pixel multiplier, "
                    "1f04/1f05 map to hankaku or hankakuMidashi spans according "
                    "to heading state, 1f42/1f43 links use lineLink, template-backed "
                    "gaiji emit dummy plus img_gaiji_midashi or img_gaiji, and "
                    "1f4c/1f5c are consumed as renderer state"
                ),
                notes=(
                    "The subset does not emulate generated custom-character DIB/GIF "
                    "output, fixed HTML fallback loading, exact image-link wrapper "
                    "lifecycle, or representative visual parity."
                ),
            )
        )
    if code == "0073":
        rows.append(
            HcHookBehavior(
                name="hkkigaku_midashi_margin_renderer",
                status="branch_subset_implemented",
                evidence=(
                    "HC0073 body loop 1f41/1f61 midashi lifecycle branch",
                    "HC0073 body loop 1f09 margin-left branch with 3-pixel multiplier",
                    "HC0073 body loop 1f0a contents transition branch",
                    "HC0073 body loop 1f42/1f43 lineLink2/lineLink3/lineLink branch",
                    "HC0073 gaiji image branch selecting img_gaiji_midashi versus img_gaiji",
                ),
                implementation=(
                    "1f41/1f61 wrap heading text in midashi, 1f0a opens a "
                    "contents block after the heading or closes the active margin "
                    "section, 1f09 opens margin-left divs using the recovered "
                    "3-pixel multiplier, 1f04/1f05 map to hankaku spans, 1f42/1f43 "
                    "links use recovered lineLink classes, template-backed gaiji "
                    "emit dummy plus img_gaiji_midashi or img_gaiji, and "
                    "1f4c/1f5c/1f6d are consumed as renderer state"
                ),
                notes=(
                    "The subset does not emulate the title/subtitle/shinryo/editor "
                    "JIS-trigger branches, generated custom-character DIB/GIF output, "
                    "exact media wrapper lifecycle, or representative visual parity."
                ),
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
    if code in {"014A", "02C3", "02C6"}:
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
    if code == "008C":
        rows.append(
            HcHookBehavior(
                name="hkdk_2010_medical_section_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC008C epwing2HtmlBodydata 1f09 section branch ladder",
                    "HC008C 1f41 midashi branch and 1f0a contents_body transition",
                    "HC008C 1f42/1f43/1f44 lineLink/lineLink2/lineLink3 branch",
                    "HC008C image gaiji template branch",
                ),
                implementation=(
                    "1f41 opens midashi and 1f61 is consumed as renderer state; "
                    "the first 1f0a transitions into contents_body; understood "
                    "1f09 body sections map to medblk, medblkcaution, medprice, "
                    "medimage, medcaution, and margin-left blocks; internal links "
                    "carry the recovered lineLink/lineLink2/lineLink3 classes and "
                    "image-backed or GA16/GAI16-backed gaiji use img_gaiji"
                ),
                notes=(
                    "The subset does not emulate HC008C's title/title2 text-trigger "
                    "state machine, product-specific generated GIF/DIB behavior beyond "
                    "GA16/GAI16 BMP fallback, fixed HTMLs fallback loading, or "
                    "previous/next navigation footer."
                ),
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
                    "HC02CA non-icon 1fe2/1fe3 renderer-state branch",
                    "HC02CA B12D-B12F img_mark branches",
                    "HC02CA B135 literal &#x20BB7; branch",
                    "HC02CA custom-character bitmap branch",
                    "Templates/000002CA.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, other body sections "
                    "open honbun margin containers, section 000c opens footer, 1f0a "
                    "closes the active section, 1f41/1f61 wrap midashi text, internal "
                    "links use lineLink, 1fe2 2331-2334 emit 1.png-4.png img_icon "
                    "markers, non-icon 1fe2/1fe3 controls are consumed as renderer "
                    "state markers, B12D-B12F render as img_mark, B130/B131 are consumed "
                    "as renderer state markers, GA16/GAI16 custom characters can be "
                    "exported as BMP resources, and B135 emits U+20BB7"
                ),
                notes="The subset excludes product-specific DIB generation, modifyHeadword, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "0136":
        rows.append(
            HcHookBehavior(
                name="gen2013_honbun_margin_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC0136 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0136 1f41 midashi branch",
                    "HC0136 1f42/1f43 lineLink branch",
                    "HC0136 1fe2 2331-2334 img_icon directive branch",
                    "HC0136 1fe2 0007 private state-block suppression branch",
                    "HC0136 1f5c/1f6d renderer-state branch",
                    "Templates/00000136.css class definitions",
                ),
                implementation=(
                    "1f09 section 0001 is treated as heading state, body sections "
                    "open honbun margin containers, section 000c opens footer, 1f0a "
                    "closes the active section, 1f41/1f61 wrap midashi text, internal "
                    "links use lineLink, 1fe2 2331-2334 emit 1.png-4.png img_icon "
                    "markers, 1fe2 0007 private state blocks are suppressed without "
                    "leaking their internal section/link controls, image-backed gaiji "
                    "use img_gaiji, and 1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes exact previous/next navigation footer generation and broader visual parity.",
            )
        )
    if code == "0048":
        rows.append(
            HcHookBehavior(
                name="gakken_speech_margin_heading_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC0048 epwing2HtmlBodydataVertical 1f09 margin branch",
                    "HC0048 JIS marker-triggered midashi branch",
                    "HC0048 1f42/1f43 lineLink branch family",
                    "HC0048 1f4a/1f6a sound.png branch",
                    "Templates/00000049.css class definitions",
                ),
                implementation=(
                    "1f09 sections open margin divs, selected leading JIS symbols open "
                    "midashi blocks, 1f0a transitions from midashi to honbun, internal "
                    "links use lineLink, 1f4d...1f6d media references are wrapped in "
                    "product-local media div placeholders, image-backed gaiji use "
                    "img_gaiji, and 1f41/1f5c renderer-state controls are consumed"
                ),
                notes="The subset excludes exact lineLink2/lineLink3 class selection, exact previous/next navigation footer generation, and broader visual parity.",
            )
        )
    if code == "00A4":
        rows.append(
            HcHookBehavior(
                name="ikuiiku_sections_ruby_and_resource_markers",
                status="branch_subset_implemented",
                evidence=(
                    "HC00A4 epwing2HtmlBodydataVertical 1f09 honbun/header branch ladder",
                    "HC00A4 1f41 midashi branch and 1f04 hankakuMidashi state",
                    "HC00A4 1fe2/1fe3 RUB:E/RUB:S private ruby directives",
                    "HC00A4 1fe2/1fe3 IMG:F and HTM:F private resource directive branches",
                    "HC00A4 B12C/B12D/B12E/B132/B133 state marker suppression",
                    "HC00A4 1f3c/1f4d inline image media template and 1f44 image-link branch",
                ),
                implementation=(
                    "1f09 sections open HC00A4 honbun/header wrappers, 1f41/1f61 "
                    "wrap midashi text, halfwidth inside midashi uses hankakuMidashi, "
                    "RUB:E/RUB:S private directives render ruby7 markup, B12C/B12D/"
                    "B12E/B132/B133 are consumed as renderer state markers, B12F emits "
                    "the product mark image, IMG:F/HTM:F private directives resolve "
                    "images/ and HTMLs/htmls resources, links use lineLink, and media "
                    "controls receive img_inline placeholders"
                ),
                notes=(
                    "The subset excludes exact previous/next navigation footer generation, "
                    "fixed HTML fallback loading, and broader visual parity."
                ),
            )
        )
        rows.extend(
            (
                HcHookBehavior(
                    name="fixed_html_fallback_loading",
                    status="classified_not_emulated",
                    evidence=("HC00A4 body loop checks fix/<address>/HTMLs and HTMLs/<id>.html paths",),
                    implementation=None,
                ),
                HcHookBehavior(
                    name="previous_next_navigation_footer",
                    status="classified_not_emulated",
                    evidence=("HC00A4 emits footer table links with back/forward images after body traversal",),
                    implementation=None,
                ),
            )
        )
    if code == "00A9":
        rows.append(
            HcHookBehavior(
                name="gen2011_header_honbun_link_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00A9 epwing2HtmlBodydataVertical 1f09 header/honbun branch ladder",
                    "HC00A9 1f41 midashi branch and 1f0a heading transition",
                    "HC00A9 1f04 hankaku/hankakuLink section-state branch",
                    "HC00A9 1f42/1f43 lineLink template",
                    "HC00A9 222A-in-header mlink.gif/mlinkV.gif marker branch",
                    "HC00A9 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/000000A9.css class definitions",
                ),
                implementation=(
                    "Initial 1f41 opens midashi until the first 1f0a, body 1f09 "
                    "sections open honbun wrappers with 4-pixel-multiplied margins, "
                    "section 000c opens header, 222A in header emits the mlink marker "
                    "image, 1f04 switches to hankakuLink in header and hankaku "
                    "elsewhere, internal links use lineLink, media placeholders use "
                    "img_inline, and 1f5c/1f6d are consumed as renderer state controls"
                ),
                notes="The subset excludes exact previous/next footer generation, fixed HTML/body fallback loading, generated custom-character DIB output, and broader visual parity.",
            )
        )
    if code == "00AC":
        rows.append(
            HcHookBehavior(
                name="gakken_kojikoto_honbun_sections",
                status="branch_subset_implemented",
                evidence=(
                    "HC00AC epwing2HtmlBodydataVertical 1f09 honbun margin branch",
                    "HC00AC 1f41/1f61 renderer-state branch",
                    "HC00AC 1f42/1f43 lineLink branch",
                    "HC00AC B139/B13A/B13B empty-output gaiji branch",
                    "Templates/000000AC.css class definitions",
                ),
                implementation=(
                    "1f09 sections open honbun margin containers, 1f0a emits "
                    "line breaks, 1f41/1f61 heading-state controls are consumed "
                    "instead of rendered as generic headings, internal links use "
                    "lineLink, image-backed gaiji use img_gaiji, and B139/B13A/B13B "
                    "are suppressed as renderer state markers"
                ),
                notes="The subset excludes exact indent constants, previous/next navigation footer generation, external HTML private directives, vertical-wrapper lifecycle, and broader visual parity.",
            )
        )
    if code == "0067":
        rows.append(
            HcHookBehavior(
                name="iphysical_chemistry_contents_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0067 epwing2HtmlBodydata 1f09 margin branch",
                    "HC0067 1f41 midashi branch",
                    "HC0067 1f0a contents_body transition branch",
                    "HC0067 1f42/1f43 lineLink/lineLink2 branch",
                    "HC0067 image gaiji template branch",
                    "Templates/00000067.css class definitions",
                ),
                implementation=(
                    "Initial 1f41 opens midashi, first 1f0a opens contents_body, "
                    "non-heading 1f09 sections open margin-left containers, 1f42 "
                    "uses lineLink2 by default, 1f43 uses lineLink, image-backed "
                    "gaiji use img_gaiji, and 1f6d is consumed as renderer state"
                ),
                notes="The subset excludes the exact neighboring-JIS conditional lineLink/lineLink2 selection, fixed HTMLs/fix fallback loading, generated gaiji GIF emission, and broader visual parity.",
            )
        )
    if code == "0068":
        rows.append(
            HcHookBehavior(
                name="ibio4_midashi_contents_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0068 epwing2HtmlBodydata 1f09 margin branch",
                    "HC0068 1f41 midashi branch",
                    "HC0068 1f0a contents_body transition branch",
                    "HC0068 1f42/1f43 lineLink/lineLink2 branch",
                    "HC0068 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000068.css class definitions",
                ),
                implementation=(
                    "Initial 1f41 opens midashi, first 1f0a opens contents_body, "
                    "non-heading 1f09 sections open margin-left containers using the "
                    "recovered 3-pixel multiplier, 1f42 uses lineLink2 by default, "
                    "1f43 uses lineLink, image-backed gaiji use dummy.gif plus "
                    "img_gaiji or img_gaiji_midashi, and 1f5c/1f6d are consumed as "
                    "renderer state controls"
                ),
                notes="The subset excludes the exact neighboring-JIS conditional lineLink/lineLink2 selection, fixed HTMLs/fix fallback loading, generated gaiji GIF emission, and broader visual parity.",
            )
        )
    if code == "0069":
        rows.append(
            HcHookBehavior(
                name="ibio4vrs_midashi_contents_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0069 epwing2HtmlBodydata 1f41 midashi branch",
                    "HC0069 epwing2HtmlBodydata 1f0a contents_body transition branch",
                    "HC0069 epwing2HtmlBodydata 1f09 margin-left branch",
                    "HC0069 1f42/1f43 lineLink/lineLink2 branch",
                    "HC0069 image-backed gaiji img_gaiji/img_gaiji_midashi branch",
                    "Templates/00000069.css class definitions",
                ),
                implementation=(
                    "Initial 1f41 opens midashi, first 1f0a opens contents_body, "
                    "body 1f09 sections open margin-left containers using the recovered "
                    "8-pixel multiplier, 1f42 uses lineLink2 by default, 1f43 uses "
                    "lineLink, and image-backed gaiji render with dummy.gif plus "
                    "img_gaiji or img_gaiji_midashi classes"
                ),
                notes="The subset excludes exact neighboring-JIS conditional lineLink/lineLink2 selection, fixed HTMLs/fix fallback loading, generated gaiji GIF emission, and broader visual parity.",
            )
        )
    if code == "008B":
        rows.append(
            HcHookBehavior(
                name="medical_expert_kaisou_contents_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC008B epwing2HtmlBodydata 1f09 section branch",
                    "HC008B 1f41/1f61 midashi branch",
                    "HC008B 1f42/1f43 lineLink/lineLink2 branch",
                    "HC008B image/media template branch",
                    "Templates/0000008B.css class definitions",
                ),
                implementation=(
                    "1f41/1f61 wrap midashi, 1f09 section 0002 opens kaisou, "
                    "section 0003 opens contents_body, 1f42 uses lineLink2 by "
                    "default, 1f43 uses lineLink, image-backed gaiji use img_gaiji, "
                    "and 1f5c/1f6d are consumed as renderer state"
                ),
                notes="The subset excludes exact neighboring-JIS conditional link class selection, medical icon marker branches, fixed HTMLs/fix fallback loading, generated gaiji GIF emission, and broader visual parity.",
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
    if code in {"02C4", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"}:
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
                    "B132-B137 render as img_mark2, B130/B131/B138 are consumed as "
                    "renderer state markers, and HC02CB/HC02CC/HC02CD special-case B135 "
                    "as U+20BB7 before the image-marker range"
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
                    "HC0065 1f6d post-media renderer-state close branch",
                    "HC0065 A430-A433 grammar-label literal branches",
                    "GENIUSEB.UNI A174 fallback record",
                    "Templates/00000065.css class definitions",
                ),
                implementation=(
                    "entry bodies open with midashi, 1f41 transitions to contents_body, "
                    "1f6d after media references is consumed as renderer state, internal "
                    "links use lLink, A174/A430-A433 render as B/c/u/S/D grammar labels, "
                    "and A251/A253 template image markers use img_gaiji"
                ),
                notes=(
                    "The full GENIUSEB raw-HONMON pass emits no raw render gaps after "
                    "the 1f6d refinement. The example/collocation box branches, SQL "
                    "original-search hooks, modifyHeadwordEx, and custom DIB paths "
                    "remain named gaps."
                ),
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
    if code == "00B6":
        rows.append(
            HcHookBehavior(
                name="genius43_section_and_template_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00B6 epwing2HtmlBodydata long decompile 1f09 section-state ladder",
                    "HC00B6 B23D-B24A state marker skip branch",
                    "HC00B6 B347/B348/B25C image marker branches and B146-B175/B353-B358 strong marker branches",
                    "Templates/000000b6.css and Templates image resources",
                ),
                implementation=(
                    "Common Genius43 section values map to midashi, indent h1, contents, CB, "
                    "and margin blocks; known no-output state markers are suppressed; B347/B348/B25C "
                    "use template image resources when present; strong number/letter markers render "
                    "their recovered literal labels; internal links use lLink"
                ),
                notes=(
                    "The subset excludes the generated custom-character bitmap path, exact index-menu "
                    "reordering, original-search SQL hooks, and representative visual parity."
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
                    "HC012D section 0007 yindex toggle branch with youreioff/youreion image state",
                    "HC012D link_k/kaisetsu_s/kaisetsu_m/link_t inline image branches",
                    "HC012D 1f41/1f61 midashi-to-honbun_user transition",
                    "Templates/0000012D.css class definitions",
                    "Templates/contents.js showIndex yindex field toggle behavior",
                ),
                implementation=(
                    "1f09 sections map to midashi-adjacent honbun/honbun_start/yorei/yindex/"
                    "hinshi/kaisetsu/ruigo blocks, internal links use lineLink, 217E/2221/"
                    "222A-before-link/224E render recovered template images, and A134/A137 "
                    "spacing markers follow the DLL branches; section 0007 emits the yindex toggle "
                    "icon, suppresses the label payload, opens the hidden yindex field before "
                    "section 0004, and closes it before body section 0002"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword hooks, SQL original-search "
                    "helpers, exact ruigo script lifecycle, and unexercised section-code branches."
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
                    "medimage/mednamelist/indent blocks, packed-BCD 0031-0034 sections "
                    "open gray multi-cell tables, 0042/0043 sections open product-code "
                    "tables, 0070-0072 sections implement the click-menu title/hidden-field "
                    "triplet, internal links use lineLink, 1f6d is consumed as renderer "
                    "state, template-backed gaiji markers use img_gaiji, and recovered "
                    "syohatsu/midashi/title/litre/entity JIS branches emit the DLL "
                    "template HTML"
                ),
                notes=(
                    "The subset excludes custom DIB generation, modifyHeadword, exact "
                    "contents2-5 state transitions, full COLSCR picture extraction into "
                    "HTML, and representative visual parity."
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
                    "HC0190 packed-BCD section-bucket replacement of <!--&IND####;--> placeholders",
                    "HC0190 packed-BCD 1f62/1f63 link target formatter",
                    "HC0190 image-anchor placeholder slots that leave </a> in the template",
                    "HC0190 HTMLs/%04lx.html fallback path",
                    "HTMLs/b121.html through HTMLs/b124.html and Templates/00000190.css",
                ),
                implementation=(
                    "B121-B124 select package HTML templates, 1f09 sections are bucketed "
                    "by packed-BCD section id, captured section HTML replaces matching "
                    "<!--&IND####;--> placeholders, link sections can fill image-anchor "
                    "prefix slots, and missing placeholders are left empty"
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
                    "HC0142 1f5c/1f6d renderer-state branch after media controls",
                    "HC0142 1f10 overline/rubar branch",
                    "HC0142 B177/B178 math span, B13F icotype_1, A164 margin, and B157/B16A-B170 plain_text branches",
                    "Templates/00000142.css class definitions",
                ),
                implementation=(
                    "1f41 starts midashi, 1f61 is consumed as renderer state, first 1f0a "
                    "closes midashi and opens the honbun container, later 1f0a emits line "
                    "breaks, internal links use lineLink, 1f5c/1f6d are consumed as "
                    "renderer state, and recovered marker gaiji emit "
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
                    "HC0146 epwing2HtmlBodydataVertical 1f09 body-section branch",
                    "HC0146 epwing2HtmlBodydataVertical B230/B231, B232/B233, "
                    "B234/B235, B238/B239, B244/B245, and B354/B355 style branches",
                    "HC0146 B157-B159, B25A-B351, B23B, and B357-B424 image-template branches",
                    "Templates/00000146.css class definitions",
                ),
                implementation=(
                    "Clear BCD 1f09 sections render recovered sub-caption, example, "
                    "translation, idiom, and column-frame templates; paired style "
                    "markers render plain_font, color_font, not_italic_font, under_line, "
                    "and small wrappers; B240 renders the literal abbreviation label; "
                    "nonprinting template selectors are consumed; and image-marker "
                    "branches use the recovered img_mark4/gaiji_icon classes."
                ),
                notes=(
                    "The implementation covers branches whose destination template is "
                    "recovered from the body loop and package CSS. The full PROYAL43 "
                    "raw-HONMON pass emits no raw behavior gaps, but product wrapper state, "
                    "custom hooks, and representative visual parity remain incomplete."
                ),
            )
        )
    if code == "0158":
        rows.append(
            HcHookBehavior(
                name="archsic4_inline_style_gaiji",
                status="branch_subset_implemented",
                evidence=("HC0158 epwing2HtmlBodydata B353-B37E branches", "00000158.css span classes"),
                implementation=(
                    "B3xx formatting-marker gaiji map to HC0158 CSS spans; B355 rank1 "
                    "midashi uses the second B354 when a bracketed title follows; the "
                    "B353/B35E guide-heading variant closes at 1f61; normal B253/B347 "
                    "image gaiji stay resource-backed"
                ),
                notes=(
                    "B379 is conditional: labels before translation/usage/figure text "
                    "select waku_red/back_red/waku variants from the next JIS pair. "
                    "The full ARCHSIC4 raw-HONMON pass emits no raw render gaps after "
                    "the rank-heading refinements."
                ),
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
                    "HC0157 epwing2HtmlBodydataVertical 1f09 section branch ladder",
                    "HC0157 epwing2HtmlBodydataVertical A14D/A14E accent branches",
                    "HC0157 packed-BCD 1f62/1f63 link target formatter",
                    "HC0157 B156-B241 CSS span branch ladder",
                    "Templates/00000157.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to komidashi/gogi/example/phrase/derivative/compound "
                    "layout blocks, 1f62/1f63 link target payloads decode as packed BCD, "
                    "A14D/A14E accent markers, B156-B241 CSS span delimiters, and B22D-B23B "
                    "red circled-number gaiji wrappers"
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
    if code == "00C4":
        rows.append(
            HcHookBehavior(
                name="gakkanan3_section_icon_and_gaiji_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC00C4 epwing2HtmlBodydataVertical 1f09 branch ladder",
                    "HC00C4 B137/B138/B13C custom-gaiji image class branch",
                    "HC00C4 214E/214F waku image branch",
                    "Templates/000000C4.css class definitions",
                ),
                implementation=(
                    "1f09 maps midashi, honbun, honbun_number, honbun_icon, "
                    "inline icon, and font_down states; 1f41/1f61 map heading "
                    "and honbun_user blocks; internal links use lineLink; image "
                    "gaiji use gaiji/gaiji_k/gaiji_b classes"
                ),
                notes=(
                    "The subset excludes exact fixed HTMLs/fix fallback loading, "
                    "prev/next footer generation, custom DIB generation, vertical "
                    "HTML differences, and broader visual parity."
                ),
            )
        )
    if code == "005C":
        rows.append(
            HcHookBehavior(
                name="kene7j5_heading_section_and_marker_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC005C epwing2HtmlBodydata heading 1f41/1f61 branches",
                    "HC005C 1f09 margin-section branch",
                    "HC005C 【語法】/【発音】 marker-image branch ladder",
                    "Templates/0000005C.css class definitions",
                ),
                implementation=(
                    "1f41/1f61 render eMidashi/jMidashi blocks, non-heading "
                    "1f09 sections open margin containers, internal links use "
                    "lineLink, image links use image.png, and custom gaiji "
                    "fallbacks use HC005C img_gaiji/img_gaiji_midashi templates"
                ),
                notes=(
                    "The subset uses body-local heading-script inference because "
                    "the DLL also consults entry address ranges. Context-sensitive "
                    "exam.gif/bunrei.gif content block generation remains a named gap."
                ),
            )
        )
    if code == "0092":
        rows.append(
            HcHookBehavior(
                name="kcompej2_lineinfo_sections_and_gaiji_classes",
                status="branch_subset_implemented",
                evidence=(
                    "HC0092 epwing2HtmlBodydata 1f09 lineinfo%d branch",
                    "HC0092 hankaku/hankakuMidashi span branches",
                    "HC0092 lineLink and media template strings",
                    "HC0092 b12x/b13x template-gaiji strings",
                    "Templates/00000092.css class definitions",
                ),
                implementation=(
                    "1f09 sections map to lineinfoN wrappers, 1f41/1f61 are "
                    "suppressed heading-anchor controls, 1f04/1f05 map to "
                    "hankaku or hankakuMidashi depending on section state, "
                    "and internal links use lineLink"
                ),
                notes=(
                    "The subset excludes address-specific marker-image branches, "
                    "exact contents transition behavior, fixed HTML/body fallback "
                    "loading, generated custom DIB output, and visual parity."
                ),
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
    if code == "0137":
        rows.append(
            HcHookBehavior(
                name="iwanami_section_margin_and_line_link_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC0137 epwing2HtmlBodydataVertical 1f09 decoded section branch ladder",
                    "HC0137 lineLink and image-backed gaiji template strings",
                    "HC0137 1f41/1f4c/1f5c/1f6d renderer-state branches",
                ),
                implementation=(
                    "1f09 sections map to midashi/font_midashi_sub/hidden/honbun/"
                    "honbunB/bracket wrappers, image-backed gaiji use img_gaiji, "
                    "and internal links carry the recovered lineLink class"
                ),
                notes="The subset excludes exact DAT_* formatting constants for every margin branch, SQL/helper hooks, Panel lifecycle, and custom DIB generation.",
            )
        )
    if code == "02C8":
        rows.append(
            HcHookBehavior(
                name="zukaiho_section_table_and_indent_layout",
                status="branch_subset_implemented",
                evidence=(
                    "HC02C8 epwing2HtmlBodydata 1f09 section branch ladder",
                    "HC02C8 Link/lineLink anchor templates",
                    "HC02C8 image-backed gaiji template strings",
                    "HC02C8 1f4d image-anchor close via 1f6d",
                ),
                implementation=(
                    "1f09 sections map to midashi_2nd, indent3/5/6/7/30/31/33, "
                    "header, contents, and table row/cell wrapper tags; 1f42 uses "
                    "Link, 1f43 uses lineLink, and 1f6d is consumed as the recovered "
                    "image/link anchor-close control"
                ),
                notes="The subset excludes exact page-movement footer generation, modifyHeadwordEx, SQL/helper hooks, and custom DIB generation.",
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
        implemented.add("HC013A_layout_markers_and_custom_bitmap_template")
    if code == "00C6":
        implemented.add("HC00C6_section_and_marker_layout")
    if code == "0094":
        implemented.add("HC0094_sections_color_blocks_and_template_gaiji")
        implemented.add("HC0094_class_arrow_state_and_bitmap_gaiji")
    if code == "02BE":
        implemented.add("HC02BE_section_and_phonetic_markers")
    if code == "02BC":
        implemented.add("HC02BC_section_and_medical_markers")
        implemented.add("HC02BC_rare_b122_implicit_close")
    if code == "02C2":
        implemented.add("HC02C2_section_icons_and_template_gaiji")
    if code == "0147":
        implemented.add("HC0147_contents_bunken_and_template_gaiji")
    if code == "0137":
        implemented.add("HC0137_iwanami_section_margin_and_line_links")
    if code == "02C8":
        implemented.add("HC02C8_zukaiho_section_table_and_indent_layout")
        implemented.add("HC02C8_image_link_close_control")
    if code == "00A6":
        implemented.add("HC00A6_sections_and_ruby_directives")
    if code == "00AA":
        implemented.add("HC00AA_hkbyoin_section_media_layout")
    if code == "00A3":
        implemented.add("HC00A3_quiz_answer_section_layout")
    if code == "00C5":
        implemented.add("HC00C5_section_image_layout")
    if code == "00AD":
        implemented.add("HC00AD_large_character_section_layout")
    if code == "00BB":
        implemented.add("HC00BB_honbun_section_layout")
    if code == "00AB":
        implemented.add("HC00AB_honbun_section_layout")
    if code == "004D":
        implemented.add("HC004D_midashi_honbun_renderer")
    if code == "0073":
        implemented.add("HC0073_hkkigaku_body_renderer")
    if code == "0076":
        implemented.add("HC0076_medical_body_renderer")
    if code == "007D":
        implemented.add("HC007D_midashi_margin_renderer")
    if code == "008F":
        implemented.add("HC008F_jmidashi_margin_renderer")
    if code == "00C7":
        implemented.add("HC00C7_lineinfo_template_gaiji_renderer")
    if code in {"014A", "02C3", "02C6"}:
        implemented.add("HC_HKDKSR_medical_section_layout")
    if code == "008C":
        implemented.add("HC008C_medical_section_layout")
        implemented.add("HC008C_conditional_link_classes")
    if code == "009B":
        implemented.add("HC009B_honbun_margin_sections")
    if code == "00B3":
        implemented.add("HC00B3_honbun_margin_sections")
    if code == "00A0":
        implemented.add("HC00A0_phrase_detail_renderer")
    if code == "0159" and rendererdb_ok:
        implemented.add("HC0159_t_contents_exact_body_html")
    if code == "013F" and rendererdb_ok:
        implemented.add("HC013F_block_offset_exact_body_html")
        implemented.add("HC013F_raw_honmon_state_controls")
    if code == "0132":
        implemented.add("HC0132_finance_section_layout")
    if code == "013C":
        implemented.add("HC013C_honbun_margin_sections")
    if code == "02C0":
        implemented.add("HC02C0_honbun_margin_sections")
    if code == "02CA":
        implemented.add("HC02CA_honbun_margin_sections")
        implemented.add("HC02CA_private_state_and_bitmap_gaiji")
    if code == "0136":
        implemented.add("HC0136_honbun_margin_sections")
        implemented.add("HC0136_private_state_block_suppression")
    if code == "0063":
        implemented.add("HC0063_contents_sections_and_template_gaiji")
    if code == "0093":
        implemented.add("HC0093_lineinfo_sections_and_template_gaiji")
    if code == "0095":
        implemented.add("HC0095_lineinfo_sections_and_template_gaiji")
    if code == "0096":
        implemented.add("HC0096_lineinfo_sections_and_template_gaiji")
    if code == "009F":
        implemented.add("HC009F_season_category_sections_and_template_gaiji")
    if code == "0090":
        implemented.add("HC0090_lineinfo_sections_and_gaiji_classes")
    if code == "0092":
        implemented.add("HC0092_lineinfo_sections_and_gaiji_classes")
    if code == "0135":
        implemented.add("HC0135_sinmei_sections_and_private_markers")
    if code == "014F":
        implemented.add("HC014F_midashi_contents_and_decoration_modes")
    if code == "0020":
        implemented.add("HC0020_midashi_definition_markers")
    if code == "0048":
        implemented.add("HC0048_margin_heading_sections")
        implemented.add("HC0048_media_div_placeholders")
    if code == "00A4":
        implemented.add("HC00A4_sections_ruby_and_resource_markers")
    if code == "00A9":
        implemented.add("HC00A9_header_honbun_link_layout")
    if code == "00AC":
        implemented.add("HC00AC_honbun_margin_sections")
        implemented.add("HC00AC_marker_suppression")
    if code in {"02C4", "02C7", "02C9", "02CB", "02CC", "02CD", "02D1"}:
        implemented.add("HC_GEN_YEAR_section_icons_and_template_markers")
    if code == "00C4":
        implemented.add("HC00C4_section_icon_and_gaiji_layout")
    if code == "005C":
        implemented.add("HC005C_heading_section_marker_and_gaiji_layout")
    if code == "02C1":
        implemented.add("HC02C1_section_icons_and_template_gaiji")
    if code == "02BF":
        implemented.add("HC02BF_section_icon_and_moji_down_layout")
    if code == "0065":
        implemented.add("HC0065_midashi_contents_and_grammar_labels")
        implemented.add("HC0065_media_close_state_control")
    if code == "0067":
        implemented.add("HC0067_midashi_contents_and_margin_sections")
    if code == "0068":
        implemented.add("HC0068_midashi_contents_and_margin_sections")
    if code == "0069":
        implemented.add("HC0069_midashi_contents_and_margin_sections")
    if code == "008B":
        implemented.add("HC008B_kaisou_contents_and_midashi_sections")
    if code == "009D":
        implemented.add("HC009D_section_and_kakomi_layout")
    if code == "012E":
        implemented.add("HC012E_kanji_layout_and_gaijitemp_markers")
    if code == "00B6":
        implemented.add("HC00B6_section_and_template_markers")
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
        implemented.add("HC0146_recovered_bcd_section_templates")
    if code == "0158":
        implemented.add("HC0158_inline_style_gaiji")
        implemented.add("HC0158_sound_icon_audio_link")
        implemented.add("HC0158_rank_heading_close_variants")
    if code == "0157":
        implemented.add("HC0157_inline_style_gaiji")
        implemented.add("HC0157_sound_icon_audio_link")
        implemented.add("HC0157_section_layout")

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

    raw_gap_names = () if rendererdb_ok else tuple(sorted(str(key) for key in (raw_gaps or {}).keys()))
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
