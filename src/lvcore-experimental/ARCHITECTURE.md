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

`LogoVistaPackage` is a thin owner of package identity, catalog components,
shared caches, and composed reader services. The SSED reader services are
gaiji, sidecars, entries/body resolution, indexes/search, and resources. Public
dictionary operations are exposed through a `Dictionary` handle, with
package-level wrappers kept as convenience delegates for current
single-dictionary SSED packages. This is the intended Rust shape: an owning
package handle, dictionary handles, and focused sub-stores rather than
mixin-style inheritance.

## Layering

The body pipeline is:

```text
raw HONMON/body bytes
  -> text/opcode spans
  -> control behavior atlas
  -> EntryDocument tree
  -> resource/gaiji/media resolver + diagnostics
  -> render profile
  -> friendly HTML / semantic HTML / LogoVista-like HTML / plain text / debug HTML
```

Friendly output is the default. Raw offsets, raw opcodes, raw bytes, component
internals, and index page internals are explicit inspection/debug features.

HTML rendering is profile-driven:

- `friendly` is the default reader profile. It emits safe, readable HTML and
  hides raw opcodes, offsets, pointer payloads, dense-anchor bytes, hidden
  directives, and diagnostic details unless diagnostics are explicitly
  requested.
- `semantic` emits app-neutral HTML with stable `lv-block-*`, `lv-inline-*`,
  and `data-*` structure. It is intended for dictionary applications that want
  their own styling while still receiving a predictable document tree.
- `logovista-like` is a conservative visual-intent profile. It uses
  `lv-lvlike-*` classes for behavior we currently understand, while avoiding
  claims of exact renderer parity.
- `debug` is explicit inspection output. It may expose control IDs, raw span
  metadata, body-source details, gaiji codes, media IDs, link payloads, and full
  diagnostics.

Plain text rendering is not an HTML profile. It stays readable, prefers
Unicode gaiji, and avoids raw control or offset leakage.

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

The v1 document dictionary shape is explicitly versioned as
`lvcore.entry_document.v1`. Public `to_dict()` output is reader-facing by
default: it includes semantic blocks, inline nodes, resources, diagnostics,
and stable metadata, but it strips raw span payloads, raw control payloads, and
debug-only resource details. `to_dict(debug=True)` and `to_debug_dict()` are
the inspection forms and may include span summaries, opcode payload previews,
body-source internals, gaiji codes, and resource payload identifiers. Debug
span output is bounded by default: it reports offsets, lengths, hashes, and
small previews instead of unbounded body-byte dumps.

Sidecar HTML bodies are parsed into normal text/style/break spans before
document construction. Raw sidecar HTML is not carried on the span timeline or
deferred to renderer-time sanitization. If a sidecar row also has a plain body
column, lvcore prefers the plain body. If HTML is the only body form, the parser
preserves readable text plus supported inline emphasis while dropping unknown
HTML structure instead of exposing raw markup as reader text.

Node records deliberately use simple enum-like strings plus tuples/lists of
children. This maps cleanly to Rust enums and to a future opaque C ABI where
callers enumerate blocks, inlines, resources, and diagnostics through handles.
Open-ended `attrs` and resource `details` remain an escape hatch for research
fields, but friendly/public serialization filters debug-only keys. New stable
fields should become typed node/resource fields before they are treated as
reader API contract.

Resources are document-level records with stable IDs. Inline gaiji and media
nodes refer to those IDs rather than embedding payloads. Applications can use
the resource list to fetch or substitute images/audio/bitmap gaiji, while
debug output can expose the original LogoVista code or payload needed for
inspection.

Unknown or unsupported opcodes do not appear in friendly HTML. They are
recorded as diagnostics and can be shown by debug renderers.

Private renderer-directive spans are hidden from friendly content by default.
They remain visible through explicit diagnostics/debug output.

## Control Behavior Atlas

SSED body controls are modeled through a small lvcore-local behavior atlas. The
atlas assigns behavior-level names, categories, argument shapes, friendly
visibility, plain-text behavior, debug behavior, diagnostic codes, and
confidence levels to observed `0x1f` controls.

The atlas is not a renderer clone. It exists to keep decoding, document
construction, rendering, diagnostics, and tests aligned around stable concepts:

- private renderer directives are hidden in friendly output and shown only by
  diagnostics/debug profiles;
- literal/preformatted spans preserve readable text;
- URL and pointer-bearing reference spans become semantic link nodes when safe;
- tab/column controls are nonprinting layout hints;
- media-layout controls are nonprinting resource hints;
- media references become first-class resources or placeholders;
- unknown controls stay out of friendly output and remain visible in debug
  output with diagnostics.

Exact visual layout is still profile-specific future work. Friendly rendering
prioritizes readable, escaped output and avoids raw opcode leakage.

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

Unicode gaiji mappings are preferred by default. GA16/GAI16 bitmap resources
and image-backed gaiji assets are app-ready fallback resources or explicit
render-profile choices. lvcore classifies gaiji display readiness as
`unicode_mapped`, `bitmap_backed`, `image_backed`, `formatting_helper`,
`renderer_entry_backed`, or `unresolved`. Blank bitmap glyphs and other
formatting-helper candidates are not treated as missing display glyphs. If no
display fallback is available, friendly rendering uses a harmless placeholder
and records a diagnostic. Debug rendering can expose raw gaiji codes, lookup
source, glyph index, and reason.

Gaiji source discovery is deterministic. It uses catalog-declared gaiji
components, declared `EXINFO.INI` `.uni` references, direct package `.uni` /
`.UNI` files, direct GA16/GAI16 resources, package-local plist metadata, sibling
`*_GAIJI` directories, and a bounded set of known image/resource directories
such as `Templates/`, `img/`, `res/`, platform resource image folders, and
manual/appendix image folders. The reader does not perform unbounded recursive
filesystem discovery during normal package operation.

Media, image, audio, and unresolved payload references are first-class resource
references. Friendly HTML may use stable placeholders such as
`lvcore-resource://media-1`; raw media opcode payloads must not leak into
friendly output. Resource records carry stable IDs plus resolution status,
reason-level diagnostics, and debug-only payload metadata.

For SSED media stores, lvcore resolves exact original extents where the corpus
structure is understood. `COLSCR.DIC` `data` records expose native image/media
payload bytes and wrapper metadata. `PCMDATA.DIC` audio/media controls expose
the original addressed byte range without adding RIFF/WAVE wrappers or
transcoding. Byte access is explicit through package resource APIs and CLI
resource commands; render output only contains safe resource references and
metadata.

Resource rendering is caller-mappable: the default renderer emits stable
`lvcore-resource://...` URLs, while applications can provide their own mapper
when serving bitmap gaiji, images, audio, or other resources. Plain text remains
readable, uses Unicode gaiji where available, suppresses formatting helpers,
and uses compact media labels. Package-level resource APIs can report original
GA16/GAI16 glyph bytes resolved by JIS-grid or `.uni` record-order lookup,
package-local image-backed gaiji bytes, resolved COLSCR payloads, resolved
PCMDATA ranges, or precise unresolved reasons where lvcore does not yet know an
exact extent. Media and gaiji resource bytes are not transformed, transcoded,
resized, wrapped, or copied by the reader model.

URL spans and pointer-bearing reference spans are represented as semantic link
nodes with typed targets such as external URL, body reference, menu navigation,
TOC/internal reference, extended reference, and jump/audio range. External URL
links are emitted only for safe URL schemes. Internal references use opaque
`lvcore-entry://ref-*` placeholders in non-debug HTML when a target pointer is
recoverable. Unresolved links preserve visible label text and diagnostics,
while debug rendering may expose decoded payloads and pointer details.

## Search

Native LogoVista/EPWING-style index search remains a core reader capability.
Future enhanced/fuzzy/full-text app search can be added as a separate profile,
but it should not replace native forward/backward index traversal.

Reader-facing search and entry enumeration use lazy scans by default. Full
index materialization remains available through audit/inspection paths. Scans
that touch HONMON, native index pages, or sidecar rows accept optional
byte-budget/cancel plumbing and report `scan_truncated` diagnostics when a
budgeted operation stops early.

The current Python proof of concept exposes reader-facing `SearchResults` and
`SearchHit` objects. Normal callers should follow:

```text
Package.search(query, profile)
  -> SearchResults
  -> Package.entry_for_hit(SearchHit)
  -> Entry.document()
  -> friendly HTML / plain text
```

Friendly hit dictionaries expose headings, heading source, title status,
display keys, matched keys, and diagnostics. Component names, raw page numbers,
row numbers, body/title pointers, title-resolution traces, and parsed row
internals are debug/inspection output only.

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

The SSED index parser has explicit families rather than a best-effort text
fallback:

- simple forward/backward/alternate rows: `0x71`, `0x72`, `0x91`, `0x92`;
- body-only simple rows: `0x60`;
- tagged forward/backward rows: `0x70`, `0x90`;
- body-only tagged rows: `0x30`;
- keyword rows: `0x80`;
- cross-reference rows: `0x81`;
- MULTI selector rows: `0xa1`.

The observed type-`0x27` `INDEX.DIC` outlier is not treated as a native index.
It is classified as a text-like resource component and reported through
validation counters. Partial physical page tails after complete index pages are
reported separately from malformed rows so valid rows remain usable.

Grouped tagged, keyword, cross-reference, and MULTI selector indexes may carry
their active group key, count hint, and inherited title pointer across leaf page
boundaries. Parser output keeps that context in debug row metadata while
friendly search hits expose only reader-facing headings and status. Unknown or
malformed index rows become diagnostics and validation counters rather than
silent empty results.

Entry dereferencing uses body pointers, known body-pointer tables, marker
offsets, and component bounds before falling back to a maximum byte range. If
the fallback is needed, the entry carries a recoverable diagnostic.

Title dereferencing is independent from body dereferencing. A title pointer may
resolve through a title component, fail with a reason-coded diagnostic, or be a
known fallback shape. In observed SSED index rows, the title-address field can
hold the same body pointer used for the entry itself. lvcore records that as a
`fallback` title status and derives the friendly heading from the native index
display key instead of warning that no title component contains the pointer.

Reader-side validation samples both marker-discovered entries and
search-hit-to-entry-to-render paths. It reports sampled index rows, dereference
counts, render counts, diagnostic counts, sidecar resolution, reason-level
gaiji/media/link status, title status counts, heading-source counts, and
title-dereference reason counters. Corpus validation also emits a closure
scorecard that separates hard SSED body-source failures, compatibility-significant
sidecar gaps, sampled native search misses, and true display-unresolved gaiji.
It is reader-safety validation, not writer verification.

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
  packages;
- `sidecar_unknown`: dense anchors plus SQLite-like sidecars are present, but
  no supported body table schema has been identified;
- `missing_body_component`: the local package catalog does not provide a
  readable `HONMON.DIC` body component. This is a package/component integrity
  residual, not evidence for a new body format. When corpus evidence confirms
  the local package copy is broken, closure scorecards count it separately from
  reader compatibility blockers.

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

`body-source` exposes reader body-source information in JSON. The sibling
`lvcore-audit` package owns validation and corpus scorecards, using public
reader APIs rather than reader-private helpers. Debug output may include anchor
IDs, raw pointers, sidecar names, and mapping status, including attempted query
values and selected table/column names. Public body-source JSON must not include
sidecar table names, column names, row counts, or per-table schema summaries;
it exposes only reader-facing role/support/status information. Audit reports
sampled sidecar resolution counters so corpus runs can distinguish resolved
rows, missing rows, missing anchor IDs, and unsupported body-source
placeholders.

Sidecar files are also classified by role when the structure is visible. The
current role vocabulary separates body-critical stores from media/resource
stores, examples/idioms stores, native/full-text search stores, kanji-support
stores, ancillary databases, non-SQLite payloads, and unknown schemas. Only
body-critical schemas with understood anchor mapping are used for body
replacement. Other roles remain visible to validation and debug tooling without
being treated as missing body support. Address-mapped supplemental tables, such
as observed example/idiom, usage, search, or navigation schemas with
block/offset columns, can attach experimental supplement blocks or typed link
metadata to `EntryDocument` when the mapping is structurally clear. Sidecar BLOB
media tables with clear name/blob columns are exposed as package-level
`ResourceRef` resources with explicit byte access to the untouched BLOB.
Ambiguous schemas remain classified, diagnosed, and counted rather than
fake-rendered.

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
