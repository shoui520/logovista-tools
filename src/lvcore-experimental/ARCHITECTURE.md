# lvcore Experimental Architecture

`lvcore` is a reader-only proof of concept for a future stable LogoVista reader
library. It is not an authoring library and it is not a writer/repacker.

The goal is a low-level LogoVista package engine with a friendly dictionary
reader side. Applications should be able to open a package, enumerate
dictionaries, search native indexes, obtain entries, render friendly HTML or
plain text, fetch resources, and inspect diagnostics without knowing SSED
component tables, HONMON offsets, opcodes, gaiji codes, or index page layout.

The current Python implementation intentionally does not import
`logovista_tools`. It is a disciplined reimplementation used to test the shape
of the future Rust core.

The writer proof-of-concept in the broader repository is separate. It is a
reverse-engineering checkpoint, not lvcore's compatibility target. lvcore should
be judged against real LogoVista packages.

## Layering

The body pipeline is:

```text
raw HONMON/body bytes
  -> text/opcode spans
  -> EntryDocument tree
  -> resource/gaiji/media resolver + diagnostics
  -> render profile
  -> friendly HTML / semantic HTML / LogoVista-like HTML / plain text / debug HTML
```

Friendly output is the default. Raw offsets, raw opcodes, raw bytes, component
internals, and index page internals are explicit inspection/debug features.

## Public Concepts

The stable conceptual model should remain:

```text
Package
Dictionary
SearchResults
SearchHit
Entry
EntryDocument
RenderedHtml
RenderedText
Resource
Diagnostic
BodySource
Inspector
```

The Python API is still experimental, but these concepts are the future Rust
and C ABI shape.

## Documents

`EntryDocument` is the center of rendering. It contains:

- block nodes such as paragraphs, headings, examples, media, and unknown blocks;
- inline nodes such as text, gaiji, media references, emphasis, links, and
  unknown controls;
- first-class resource references;
- diagnostics;
- debug metadata, including raw spans when an explicit debug renderer or
  inspector asks for them.

Unknown or unsupported opcodes do not appear in friendly HTML. They are
recorded as diagnostics and can be shown by debug renderers.

Private renderer-directive spans are hidden from friendly content by default.
They remain visible through explicit diagnostics/debug output.

## Diagnostics

Hard failures still use exceptions when a package cannot be opened or a
required component cannot be read. Recoverable body/opcode/gaiji/media/render
problems are diagnostics:

- severity: `info`, `warning`, `error`;
- area: package, component, index, body, opcode, gaiji, media, render,
  validation;
- stable code/message;
- optional location and details.

Friendly APIs should continue rendering through recoverable diagnostics.

## Gaiji and Resources

Unicode gaiji mappings are preferred by default. GA16 bitmap or image resources
are fallbacks or explicit render-profile choices. If no display fallback is
available, friendly rendering uses a harmless placeholder and records a
diagnostic. Debug rendering can expose raw gaiji codes.

Media, image, audio, and unresolved payload references are first-class resource
references. Friendly HTML may use stable placeholders such as
`lvcore-resource://media-1`; raw media opcode payloads must not leak into
friendly output.

## Search

Native LogoVista/EPWING-style index search remains a core reader capability.
Future enhanced/fuzzy/full-text app search can be added as a separate profile,
but it should not replace native forward/backward index traversal.

The current Python proof of concept exposes reader-facing `SearchResults` and
`SearchHit` objects. Normal callers should follow:

```text
Package.search(query, profile)
  -> SearchResults
  -> SearchHit.entry()
  -> Entry.document()
  -> friendly HTML / plain text
```

Friendly hit dictionaries expose headings, display keys, matched keys,
component names, and diagnostics. Raw page numbers, row numbers, body/title
pointers, and parsed row internals are debug/inspection output only.

Current search profiles are:

- `exact`: exact match against decoded native row keys and target keys, with
  conservative normalization;
- `forward`: prefix lookup over forward-compatible indexes such as `FK*`,
  `FH*`, and `KW*`;
- `backward`: suffix lookup over backward-compatible indexes such as `BK*` and
  `BH*`. Backward row keys may be stored reversed; friendly output presents a
  natural display key where possible;
- `native`: default reader lookup combining exact, forward, and backward paths
  while deduplicating hits that resolve to the same body/title target.

Query normalization is intentionally conservative: Unicode compatibility
normalization, whitespace/dash separator removal, fullwidth/halfwidth ASCII
folding through NFKC, ASCII case folding, and katakana-to-hiragana folding. It
is not fuzzy search and it should not rewrite CJK text in meaning-changing
ways.

The Python implementation may parse indexes into row caches for simplicity.
The future Rust core should preserve the same model while traversing native
index pages efficiently.

Entry dereferencing uses body pointers, known body-pointer tables, marker
offsets, and component bounds before falling back to a maximum byte range. If
the fallback is needed, the entry carries a recoverable diagnostic.

Reader-side validation samples both marker-discovered entries and
search-hit-to-entry-to-render paths. It reports sampled index rows, dereference
counts, render counts, and diagnostic counts. It is reader-safety validation,
not writer verification.

## Body Sources

Package-family detection and SSED body-source classification are separate.
LVED SQLCipher and LVLMultiView are package families and remain deferred. SSED
dictionaries can still have several body-source shapes:

- `body_stream`: native indexes point directly into readable `HONMON.DIC`
  entries;
- `dense_anchor_table` / `dense_marker_table`: `HONMON.DIC` holds compact
  marker or anchor records. Those bytes are not final dictionary bodies;
- `dense_anchor_with_sidecar`: dense anchors can be mapped to a sibling body
  store;
- `renderer_sqlite_sidecar`, `dictfulldb_sidecar`, `honbun_sidecar`, and
  `vlpljbl_sidecar`: specific sidecar-backed body sources observed around SSED
  packages.

Search hits keep their native body/title pointers, but entry resolution is
body-source-aware. Direct body-stream packages use `HONMON.DIC` slicing. Dense
anchor packages inspect the anchor and attempt a supported sidecar lookup. If
no provider can resolve the body, friendly rendering receives a placeholder
entry with diagnostics instead of raw anchor bytes.

Current sidecar support is deliberately conservative. lvcore can resolve tiny
synthetic and observed SQLite body stores with `t_contents`, `HONBUN`, or
dict-code-named `main` schemas when the dense anchor ID maps cleanly to the
sidecar row. Observed `t_contents` key columns include `f_DataId`,
`f_contents_id`, and `f_order_id`; observed `main` tables use `ID`.
Unmapped, encrypted, or schema-unknown sidecars are classified and reported as
deferred; the reader does not fake a body by decoding anchor records.

`body-source`, `validate`, and `corpus-validate` expose body-source information
in JSON. Debug output may include anchor IDs, raw pointers, sidecar names, and
mapping status. Friendly output must not.

## Future Rust and C ABI

The future Rust library should preserve this layering. The future C ABI should
be handle-oriented:

```text
lv_package_t
lv_dictionary_t
lv_search_results_t
lv_search_hit_t
lv_entry_t
lv_entry_document_t
lv_rendered_html_t
lv_rendered_text_t
lv_resource_t
lv_diagnostics_t
lv_body_source_t
lv_error_t
```

The C ABI should use opaque handles, explicit open/free lifecycle, no borrowed
Rust references across FFI, stable status codes, explicit diagnostic retrieval,
simple rendering/search profile enums, resource handles, and ABI versioning.

Inspection functions should be separate and clearly named, for example:

```text
lv_inspect_entry_raw_body(...)
lv_inspect_entry_opcode_trace(...)
lv_inspect_dictionary_index_page(...)
lv_inspect_package_component_table(...)
```

The Python proof of concept does not implement a C ABI. It should avoid API
choices that would make this later mapping awkward.
