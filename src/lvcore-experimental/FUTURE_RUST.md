# Future Rust lvcore Architecture

This note describes the intended shape of a future Rust `lvcore` implementation
and its optional C ABI wrapper. It is a public architecture target, not a claim
that the current Python proof of concept already provides every behavior below.

## Identity

`lvcore` is a reader-only LogoVista dictionary engine.

It should be a low-level format library with a friendly dictionary-reader API on
top. Applications should be able to open packages, detect package families,
search native indexes, dereference entries, render reader-facing output, fetch
resources, inspect diagnostics, and validate packages without understanding
SSED component tables, compressed chunk layout, HONMON offsets, body opcodes,
gaiji code spaces, index pages, or sidecar body stores.

`lvcore` is not:

- a writer or authoring library;
- a package repacker;
- a GUI toolkit;
- a frontend framework;
- a wrapper around proprietary reader libraries;
- a compatibility layer that depends on platform-specific LogoVista binaries.

## Why Python Comes First

The Python implementation is the proof of concept because the model is still
being stabilized. It is allowed to remain practical, exploratory, and easy to
change while the project finishes separating format facts from implementation
choices.

The future Rust implementation should start only after the Python model has
made the important boundaries boring:

- package-family detection;
- SSED body-source classification;
- native index search semantics;
- body-source-aware entry resolution;
- entry document structure;
- gaiji/media/resource behavior;
- renderer profiles;
- diagnostics and validation;
- debug/inspection separation.

Rust should then reimplement the stable model cleanly. It should not inherit
Python internals, historical research shortcuts, or writer-specific structures.

## Concepts To Preserve

The future Rust API should preserve these core concepts:

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

These concepts are deliberately compatible with both a Rust-native API and an
opaque-handle C ABI. The exact Rust structs and enums can differ from the
Python classes, but the conceptual boundaries should remain stable.

### Package

`Package` owns the opened dictionary directory or package root. It should
detect the package family, expose dictionaries, hold component/resource access,
and coordinate validation.

Package-family detection and SSED body-source classification must remain
separate. LVED and LVLMultiView are package families. Dense or sidecar-backed
HONMON is an SSED body-source issue.

### Dictionary

`Dictionary` represents a searchable dictionary within a package. A simple SSED
package may expose one dictionary. More complex formats may expose multiple
dictionaries or logical volumes later.

The reader-facing API should prefer dictionary-level operations:

```text
dictionary.search(...)
dictionary.entry(...)
dictionary.validate(...)
```

Package-level convenience wrappers are acceptable, but app developers should
not need to choose raw component files manually.

### SearchResults And SearchHit

`SearchResults` owns a query, normalized query, search profile, diagnostics,
and ordered hits.

`SearchHit` represents one native index hit. It should expose friendly fields
such as display key, heading, match key, and search profile. Raw page numbers,
row numbers, component names, title/body pointers, and parsed row details are
inspection fields, not default reader output.

Heading and title status should remain explicit. A hit may have a resolved
title-stream heading, a clean fallback heading derived from the native index
key, or a reason-coded title dereference failure. The future Rust API should
not force callers to inspect raw title pointers to tell those cases apart.

Native search profiles should remain explicit:

- exact native row-key and target-key match;
- forward prefix lookup;
- backward suffix lookup;
- native default lookup that combines native profiles conservatively;
- future enhanced search only as a separate profile.

Native index search is a core capability. A future application-side full-text
or fuzzy index should not replace the ability to traverse LogoVista indexes.

### Entry

`Entry` is the resolved dictionary entry body plus metadata and diagnostics.
Entry resolution must be body-source-aware. A native body pointer does not
always mean the pointer can be sliced directly from `HONMON.DIC`.

An entry may be:

- fully renderable;
- partially renderable with diagnostics;
- a clean placeholder for an unsupported body source;
- a hard failure only when the package cannot be read safely.

Friendly output must never render dense anchor records, raw pointer payloads,
or undecoded binary bytes as dictionary text.

### EntryDocument

`EntryDocument` is the semantic middle layer:

```text
raw body bytes or sidecar payload
  -> spans and controls
  -> EntryDocument
  -> resource resolution and diagnostics
  -> renderer profile
  -> HTML or plain text
```

The document should contain:

- block nodes for paragraphs, headings, examples, lists, tables, media blocks,
  and unknown blocks;
- inline nodes for text, gaiji, line breaks, emphasis, links, media references,
  and unknown controls;
- document-level resources;
- diagnostics;
- bounded debug metadata.

The model should map naturally to Rust enums. Open-ended maps can exist for
research metadata, but stable reader-facing behavior should be represented by
typed fields and enums before it becomes part of the API contract.

### Resource

`Resource` represents gaiji bitmaps, images, audio, media payloads, unresolved
external references, and other addressable objects.

Resources should have stable IDs within the rendered document. Inline nodes
should reference resource IDs instead of embedding raw payloads. Applications
can then map those IDs to URLs, blobs, streams, or platform resources.

Gaiji resources should carry display-readiness status separately from byte
availability. Unicode mappings, bitmap-backed GA16/GAI16 glyphs,
image-backed package assets, formatting helpers, renderer-entry-backed
contextual cases, and true unresolved display failures are different API
states. A future Rust API should keep those states as simple enum-like values
and expose original glyph/image bytes only through explicit resource access.

### Diagnostic

Diagnostics are first-class. They should distinguish hard failures from
recoverable rendering or parsing issues.

A diagnostic should have:

- severity;
- area;
- stable code;
- message;
- optional location;
- recoverable flag;
- structured details.

Friendly APIs should continue through recoverable diagnostics. Debug and
validation APIs should make diagnostics easy to enumerate.

### BodySource

`BodySource` describes how entries are resolved:

- direct SSED body stream;
- dense HONMON anchor or marker table;
- dense anchor with supported sidecar body store;
- sidecar-backed SSED body store with known schema;
- unsupported or deferred sidecar source;
- deferred non-SSED package family.

The body-source model should be explicit because it affects search-hit
dereferencing, rendering, validation, diagnostics, and compatibility reporting.

### Inspector

Friendly APIs should be the default. Raw access should be available through
explicit inspection objects and methods.

Inspection APIs may expose:

- component names;
- page and row positions;
- title/body pointers;
- body-source details;
- anchor IDs;
- sidecar mapping details;
- opcode/control traces;
- bounded span summaries;
- diagnostics.

Inspection should not dump unbounded raw bytes by default.

## Loading And Performance Goals

The Rust implementation should prioritize lazy loading and bounded memory use.

Important goals:

- memory-map or lazily read large component stores where practical;
- avoid expanding entire multi-gigabyte body stores unless explicitly requested;
- cache parsed index pages or row summaries only when useful;
- stream validation and corpus audits;
- keep resource payload loading lazy;
- keep entry rendering lazy and per-entry;
- expose cancellation or bounded iteration points where long scans are likely.

Zero-copy parsing is desirable where it improves real workloads, but it is not
an excuse to make the API hard to use. Borrowed data must not leak across FFI
boundaries.

Rust `unsafe` should be avoided by default. Use it only when a measured
bottleneck cannot be solved cleanly with safe Rust, and keep any unsafe blocks
small, audited, and covered by tests.

## Body-Source-Aware Entry Resolution

The search-to-render path should be:

```text
Dictionary.search(query, profile)
  -> SearchResults
  -> SearchHit
  -> body-source-aware entry resolver
  -> Entry
  -> EntryDocument
  -> renderer
```

The resolver should:

- keep native title/body pointers from the hit;
- determine the active body source;
- resolve direct body streams through safe component bounds;
- resolve supported dense-anchor sidecars through typed providers;
- return clean placeholders and diagnostics for unsupported sources;
- avoid treating anchor records as readable body text;
- expose detailed inspection data only through debug APIs.

## Rendering Profiles

The Rust renderer should preserve the current profile separation:

- `friendly`: default reader-facing HTML; safe, readable, no raw internals;
- `semantic`: app-neutral HTML with stable classes and structure;
- `logovista_like`: conservative visual-intent profile, not a parity claim;
- `debug`: explicit inspection output with raw useful details.

Plain text rendering should remain separate from HTML profiles. It should
prefer readable text and Unicode gaiji, not raw controls or offsets.

All renderers must escape text and attributes correctly.

## Gaiji, Media, And Links

Unicode gaiji should be preferred by default when a mapping is available.
Bitmap/resource gaiji should be an explicit policy or fallback. Missing gaiji
should produce diagnostics and harmless placeholders.

Media, image, audio, and other payload references should be first-class
resources. Friendly HTML can use caller-mappable placeholder URLs such as
`lvcore-resource://...`; applications can replace those with their own resource
loader output.

Links should be semantic nodes where possible:

- external URLs only for safe URL schemes;
- internal references as opaque entry/resource targets;
- unresolved targets as visible label text plus diagnostics;
- raw pointer payloads only in debug output.

## Validation

Validation is reader-safety validation. It answers:

```text
Can this package be opened, searched, dereferenced, decoded, rendered, and
inspected without unsafe behavior or accidental raw leakage?
```

Validation should report:

- package family;
- body-source classification;
- component/index/title parse status;
- sampled search-hit dereference status;
- sampled render status;
- diagnostic counts by severity, area, and code;
- unresolved gaiji/media/link counts;
- unsupported or deferred body sources.

Validation is not writer verification and not authoring validation.

## Future C ABI

The C ABI should wrap the Rust implementation through opaque handles. It should
be stable, explicit, and hard to misuse.

Potential handle types:

```c
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
lv_error_t
```

Principles:

- opaque handles, not exposed Rust structs;
- explicit create/open/free lifecycle;
- no borrowed Rust references crossing the ABI boundary;
- library-owned strings and buffers must have explicit free functions;
- stable status codes plus retrievable diagnostic messages;
- simple enum values for search, render, body-source, and gaiji policies;
- friendly reader calls should be the easiest path;
- raw inspection calls should be explicitly named and separate;
- diagnostics should be enumerable without exposing Rust internals;
- resources should be retrievable by stable handles or IDs;
- ABI versioning should be planned before the first public C ABI release.

Example shape:

```c
lv_status_t lv_package_open(const char *path, lv_package_t **out);
void lv_package_free(lv_package_t *package);

lv_status_t lv_package_dictionary_count(lv_package_t *package, size_t *out);
lv_status_t lv_package_dictionary(
    lv_package_t *package,
    size_t index,
    lv_dictionary_t **out
);

lv_status_t lv_dictionary_search(
    lv_dictionary_t *dictionary,
    const char *query,
    lv_search_profile_t profile,
    lv_search_results_t **out
);

lv_status_t lv_search_results_count(lv_search_results_t *results, size_t *out);
lv_status_t lv_search_results_hit(
    lv_search_results_t *results,
    size_t index,
    lv_search_hit_t **out
);

lv_status_t lv_search_hit_entry(lv_search_hit_t *hit, lv_entry_t **out);
lv_status_t lv_entry_render_html(
    lv_entry_t *entry,
    lv_html_profile_t profile,
    lv_rendered_html_t **out
);
lv_status_t lv_entry_render_text(
    lv_entry_t *entry,
    lv_rendered_text_t **out
);

lv_status_t lv_entry_diagnostics(
    lv_entry_t *entry,
    lv_diagnostics_t **out
);
```

Inspection calls should be separate:

```c
lv_status_t lv_inspect_entry_raw_spans(...);
lv_status_t lv_inspect_entry_opcode_trace(...);
lv_status_t lv_inspect_dictionary_index_page(...);
lv_status_t lv_inspect_package_component_table(...);
lv_status_t lv_inspect_body_source(...);
```

The C ABI should not force callers to understand dictionary internals for
ordinary rendering. It should expose internals only when an application asks for
inspection.

## Non-Goals

The future Rust `lvcore` should not expand into unrelated products.

Non-goals:

- writing or authoring dictionaries;
- repacking existing packages;
- GUI/frontend implementation;
- platform installer generation;
- depending on proprietary reader DLLs or platform-specific LogoVista code;
- exact visual parity claims before behavior is independently tested;
- exposing raw internals through friendly APIs;
- combining native index search with fuzzy/full-text app search without clear
  profile separation.

The purpose of Rust `lvcore` is narrower and stronger: a robust reader engine
with stable concepts, clear diagnostics, lazy performance characteristics, and
APIs that dictionary applications can use without becoming reverse-engineering
tools themselves.
