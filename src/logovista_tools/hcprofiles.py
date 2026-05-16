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
                status="implemented",
                evidence=("HC013A exam.png template", "1f09 section 0011 example block branch"),
                implementation="raw HONMON section 0011 inserts the discovered exam image once per contiguous examples block",
                notes="HC013A decodes the 0011 section payload as decimal 11 and keeps the example block active across sections 0010, 0011, and 0012.",
            )
        )
    if code == "0158":
        rows.append(
            HcHookBehavior(
                name="archsic4_inline_style_gaiji",
                status="implemented",
                evidence=("HC0158 epwing2HtmlBodydata B353-B37E branches", "00000158.css span classes"),
                implementation="B3xx formatting-marker gaiji map to HC0158 CSS spans; normal B253/B347 image gaiji stay resource-backed",
                notes="B379 is conditional: labels before translation/usage/figure text select waku_red/back_red/waku variants from the next JIS pair.",
            )
        )
        rows.append(
            HcHookBehavior(
                name="archsic4_sound_icon_audio_link",
                status="implemented",
                evidence=("HC0158 lved.sond template", "Templates/sound.png"),
                implementation="PCMDATA audio ranges render as sound.png links for HC0158",
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
    if code == "0158":
        implemented.add("HC0158_inline_style_gaiji")
        implemented.add("HC0158_sound_icon_audio_link")

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
    hook_gap_names = tuple(sorted(hook.name for hook in hooks if not hook.status.startswith("implemented")))
    notes: list[str] = []
    if rendererdb_ok:
        notes.append("Entry body HTML is taken from the renderer/app sidecar, not reconstructed from raw HONMON controls.")
    if hook_gap_names:
        notes.append("Remaining hook gaps are named; exact HC parity is not claimed while these remain.")

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
        named_gaps=tuple(sorted(set(raw_gap_names + hook_gap_names))),
        notes=tuple(notes),
    )
