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
    if code in {"013A", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C5"}:
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
    if code in {"013A", "013F", "0142", "0146", "0147", "02BE", "02BF", "02C1", "02C2", "02C5"}:
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
    if code == "012E":
        implemented.add("HC012E_kanji_layout_and_gaijitemp_markers")
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
