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

Current search profiles are deliberately simple: `native`, `exact`, `forward`,
and `backward`. They are reader profiles, not fuzzy search. Future app-level
search can be layered above them without changing raw index parsing.

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
