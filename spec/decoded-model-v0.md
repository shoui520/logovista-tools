# Decoded LogoVista Model v0 Draft

The Decoded LogoVista Model v0 is the draft internal model for decoded
LogoVista/SystemSoft dictionaries.

The goal is not to invent a new interchange format. The goal is to give
parsers, validators, corpus audits, future exporters, and future writer
experiments one shared model for the facts recovered from a package.

This page is a draft contract. It is allowed to change while marked `v0`, but
new parser/exporter work should target this model rather than command-specific
JSON shapes.

## Status

```text
name:        Decoded LogoVista Model
version:     0 draft
scope:       decoded LogoVista dictionary packages and related package families
stability:   draft; not frozen
encoding:    JSON-compatible data model, UTF-8 when serialized
principle:   preserve raw provenance before renderer interpretation
```

Current command outputs map into parts of this model:

| Current command | Model coverage |
| --- | --- |
| `profile` | package, component, body-source, index summary, decode metrics |
| `honmon-bytes` | text-stream coverage and issue metrics |
| `component-forensics` | menu/title/index/gaiji/media component coverage |
| `dump-ir` | entry addresses and lossless text spans |
| `titles` | title rows |
| `indexes` | index rows and branch rows |
| `menus` | menu records and destination pointers |
| `gaiji-report` | gaiji mappings, occurrences, validation evidence |
| `colscr` / `pcmdata` | media references and media records |
| `fulldb` / `rendererdb` / `lved` | dereferenced body records from sidecar stores |
| `capability-matrix --model-dir` | writer/exporter capability view derived from model readiness |

`dump-ir` emits `logovista-lossless-entry-v1`. Treat that as an entry-span
subset of this package model, not as the full decoded package model.

## Design Rules

1. **Raw-first.** Every object that comes from dictionary bytes should carry
   enough provenance to find those bytes again: component, block/offset, byte
   offset, length, and raw bytes when the output mode allows them.
2. **Lossless before pretty.** Rendering decisions belong in renderers and
   exporters. The model stores raw controls, decoded fields, confidence, and
   optional display hints.
3. **No irreversible normalization in core records.** Keep original JIS bytes,
   decoded text, and normalized/search text as separate fields.
4. **Dictionary-local gaiji.** A gaiji code is meaningful only inside a
   dictionary and gaiji space. `A126` is not globally `é`.
5. **Unknowns are data.** Unknown controls, unparsed trailers, unresolved
   pointers, and unclassified payloads must be represented explicitly with
   issue records and raw provenance.
6. **One address model.** All block/offset, component-relative, dense-anchor,
   DB-row, and virtual-selector references should be converted into typed
   addresses instead of loose integer pairs.
7. **Dereference is layered.** Raw HONMON, title/index components, sidecar DBs,
   renderer DBs, and LVED payloads are different sources. The model can connect
   them, but should not pretend they are the same file.
8. **Writer/exporter views are derived.** Future exporters and writer
   experiments should consume this model. They should not re-parse raw bytes.

## Top-Level Package

A decoded package model describes one dictionary package or one dictionary
target inside a larger collection.

```json
{
  "schema": "logovista-decoded-model-v0",
  "model_version": 0,
  "stability": "research-draft",
  "package": {
    "dict_id": "SYNTH",
    "title": "Synthetic Dictionary",
    "path": "/dict/SYNTH",
    "idx": "/dict/SYNTH/SYNTH.IDX",
    "honmon": "/dict/SYNTH/HONMON.DIC"
  },
  "wrapper": {
    "package_family": "ssed",
    "platform": "windows",
    "markers": {}
  },
  "classification": {
    "package_family": "ssed",
    "platform": "windows",
    "honmon_shape": "body_stream_indexed",
    "body_source_hint": "honmon"
  },
  "components": [],
  "entry_spans": {},
  "titles": {},
  "indexes": {},
  "menus": {},
  "gaiji": {},
  "media": {},
  "sidecars": {},
  "families": {},
  "readiness": {},
  "writer_readiness": {},
  "notes": [],
  "inconsistencies": []
}
```

`package_family` values:

```text
ssed              SSEDINFO/SSEDDATA package with HONMON-style components
lved_sqlcipher    modern LVED/WebView2 SQLCipher payload family
multiview_sqlite      LVLMultiView package with SSEDINFO facade + SQLite bodies
mixed             package has both raw SSED anchors and renderer/database bodies
unknown           classified enough to report, not enough to decode
```

`platform` values:

```text
noplatform
windows
ios
android
unknown
```

The `platform` field records observed package-layer evidence, not the core
format family. SIZK is not a package-family or platform value; it is reported
through dictionary-family notes/markers while keeping `package_family=ssed`.
LVED and LVLMultiView use their own `package_family` values and ordinary
platform evidence such as `windows`.

`honmon_shape` values observed so far:

```text
body_stream_indexed
body_stream_marker_sliced
marker_rich_text_stream
text_stream_without_entry_markers
dense_marker_table
dense_numeric_id_table
dense_token_table
index_targets_without_sampled_body
marker_table_without_sampled_body
opaque_or_binary_honmon
missing
unknown
```

`body_source` values:

```text
honmon
honmon_anchor_dereference
sidecar
dictfulldb
renderer_db
lved_sqlcipher
multiview_sqlite
none
unknown
```

Wrapper markers are evidence, not mutually exclusive identities. For example,
some iOS and Mac OS X package copies can contain files also used by Windows
packages, such as `EXINFO.INI`, numeric auxiliary indexes, `SPINDEX.DIC`, or
`HANREI/`. Current package classification treats Android `resource/conf.ini`
and iOS plist evidence as platform-package evidence, SIZK as dictionary-family
metadata on top of `package_family=ssed`, and LVED/LVLMultiView as separate
non-SSED package families rather than platform wrappers.

## Address

`Address` is the most important shared object. It represents where something
came from or where a pointer resolves.

```json
{
  "kind": "component",
  "component": "HONMON.DIC",
  "component_type": "00",
  "block": 25769,
  "offset": 158,
  "component_offset": 1855006,
  "absolute_book_offset": 52736030
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `kind` | Address family. |
| `component` | Component filename when applicable. |
| `component_type` | `SSEDINFO` component type byte as two hex digits. |
| `block` | Logical 2048-byte block number when applicable. |
| `offset` | Offset within the logical block. |
| `component_offset` | Expanded byte offset inside the component. |
| `absolute_book_offset` | Expanded composed-book offset, if known. |
| `payload_hex` | Raw pointer payload for packed controls. |
| `selector` | Virtual selector id for side-panel/menu pseudo-targets. |
| `db` / `table` / `row_id` | Database row address for sidecar bodies. |
| `asset_id` | Asset/media identifier when address is content/resource based. |

Address `kind` values:

```text
component
book
dense_anchor
database_row
virtual_selector
resource
unknown
```

For writer work, `component` plus `component_offset` is the canonical internal
address. Block/offset fields are derived from component layout.

## Provenance

Most records include a `source` object:

```json
{
  "source": {
    "address": {
      "kind": "component",
      "component": "FHINDEX.DIC",
      "block": 44310,
      "offset": 4,
      "component_offset": 200708
    },
    "length": 28,
    "raw_hex": "00022422..."
  }
}
```

`raw_hex` may be omitted in redacted reports or size-limited outputs. If raw
bytes are omitted, `length` and address still remain.

## Component

Component records describe both declared SSED components and adjacent package
resources.

```json
{
  "id": "component:HONMON.DIC",
  "filename": "HONMON.DIC",
  "role": "honmon",
  "component_type": "00",
  "storage": "plain",
  "start_block": 25769,
  "end_block": 64404,
  "expanded_bytes": 79126528,
  "status": "ok",
  "coverage": {
    "bytes_total": 79126528,
    "bytes_covered": 79126528,
    "unknown_bytes": 0,
    "unknown_controls": 0,
    "unparsed_nonzero_bytes": 0
  }
}
```

`role` values:

```text
honmon
title
index
menu
multi_descriptor
text
colscr
pcmdata
gaiji_bitmap
component
unknown
```

`storage` values:

```text
plain
ssed
logofont_cipher
sqlcipher
sqlite
loose_file
missing
opaque
```

## Text Stream

A text stream is any expanded JIS/control byte stream: `HONMON.DIC`,
`MENU.DIC`, `*TITLE.DIC`, and the observed text-like `INDEX.DIC` outlier.

Text streams are represented as ordered spans. The span list must account for
every byte in the decoded slice unless the report explicitly says it is
redacted or sampled.

```json
{
  "kind": "text",
  "start": 12,
  "end": 14,
  "raw_hex": "2422",
  "text": "あ",
  "normalized": "あ",
  "encoding": "jis-x-0208"
}
```

Span `kind` values:

```text
text
ascii
control
section
break
gaiji
media_ref
padding
unknown_control
problem
```

Common span kind meanings:

| Kind | Meaning |
| --- | --- |
| `padding` | NUL padding bytes. |
| `text` | JIS X 0208 cell pair. |
| `ascii` | Literal ASCII byte. |
| `break` | LogoVista line break control or legacy `0x0a`. |
| `section` | `1f09 xxxx` section marker. |
| `control` | Structurally known control with conservative or known tag. |
| `media_ref` | Media/image/audio control with payload. |
| `gaiji` | Dictionary-local external character. |
| `unknown_control` | `0x1f` opcode not structurally classified. |
| `problem` | Invalid/truncated/unclassified bytes. |

Rendered-private directive spans do not require a separate span kind. They are
normal `text`, `gaiji`, `break`, `media_ref`, or `control` spans with
`hidden: true` while enclosed by `1fe2` / `1fe3`.

For text-stream rendering, `1f04` / `1f05` define halfwidth conversion mode.
Inside that mode, JIS row-3 fullwidth ASCII cells are narrowed in the
`normalized` field. Outside that mode, `text` and `normalized` preserve the
fullwidth character. HTML output preserves the control boundary with an
`lv-halfwidth` span. Index keys are a separate lookup representation and may
normalize row-3 cells even though they do not carry display-mode controls.

Every span has:

```text
kind
start        offset relative to containing slice
end          exclusive offset relative to containing slice
```

Optional span fields:

```text
raw_hex
text
normalized
encoding
op
payload_hex
tag
role
confidence
issue
address
target
gaiji_ref
media_ref
```

## Control

Control spans keep the byte-level opcode separate from renderer semantics.

```json
{
  "kind": "control",
  "start": 0,
  "end": 4,
  "raw_hex": "1f090001",
  "op": "09",
  "payload_hex": "0001",
  "tag": "section",
  "confidence": "proven"
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `op` | One-byte opcode after `0x1f`, as two lowercase hex digits. |
| `payload_hex` | Fixed or decoded argument bytes. |
| `tag` | Neutral structural label such as `section`, `media`, `jump`, `url_start`. |
| `role` | Higher-level semantic role when known, preferably dictionary-profile backed. |
| `confidence` | Confidence level from `spec/confidence.md`. |
| `target` | Resolved address for link/media/menu controls when known. |

Control `confidence` values:

```text
proven
strongly_inferred
corpus_inferred
dictionary_specific
structural_only
unknown
```

Structurally known controls can still have cautious semantics. For example,
`1f1a` is modeled as a nonprinting `tab_column` control with a two-byte
position payload, and `1f1c` is modeled as a nonprinting `media_layout`
control when it precedes media references. The model preserves the raw payload
even when a renderer/exporter does not yet reproduce the exact visual layout.

Private renderer directives are also preserved rather than flattened away.
`1fe2` / `1fe3` spans wrap hidden directive text such as `IMG:`, `RUB:`,
`SMC:`, `IDX:`, `HTM:`, `SQL:`, `GTH:`, and `<PlaySound>...`. Lossless spans
keep these bytes and mark the enclosed text with `hidden: true`; rendered
plain/HTML output suppresses the directive text until a higher-level resolver
converts a known prefix into an image, ruby annotation, sound link, or other
structured object.

## Entry

An entry represents one dictionary body unit. It can come directly from
`HONMON.DIC`, from a dense HONMON anchor plus a sidecar body, or from LVED.

```json
{
  "id": "entry:HONMON.DIC:25769:0158",
  "source": {
    "address": {
      "kind": "component",
      "component": "HONMON.DIC",
      "block": 25769,
      "offset": 158,
      "component_offset": 1855006
    },
    "length": 3048
  },
  "boundary": {
    "method": "index_derived",
    "start_address": {"kind": "component", "component": "HONMON.DIC", "block": 25769, "offset": 158},
    "end_address": {"kind": "component", "component": "HONMON.DIC", "block": 25770, "offset": 1158}
  },
  "headwords": [],
  "body": {
    "stream": "text_stream",
    "spans": []
  },
  "issues": []
}
```

Entry fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable package-local id. |
| `source` | Raw body source or sidecar source. |
| `boundary` | How start/end were determined. |
| `headwords` | Search/display keys attached through titles/indexes/body spans. |
| `body` | Text spans, renderer HTML, or sidecar structured body. |
| `anchors` | Dense HONMON IDs/tokens or body anchors. |
| `links_out` | Links/media/cross-refs emitted by body spans. |
| `links_in` | Index/title/menu rows that point to the entry. |
| `issues` | Entry-local problems. |

Boundary methods:

```text
entry_marker
index_derived
marker_and_index
dense_record
database_row
lved_row
manual
unknown
```

## Headword

Headword records are search/display keys attached to entries. They can be
derived from body head spans, title streams, index keys, renderer DB columns,
or LVED search rows.

```json
{
  "text": "辞典",
  "reading": "じてん",
  "display": "じてん　辞(事)典",
  "source": "index",
  "address": {"kind": "component", "component": "FHINDEX.DIC", "block": 1234, "offset": 56},
  "key_bytes_hex": "3c2d4535",
  "normalized": "辞典",
  "search_only": false
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `text` | Exact lookup key. |
| `reading` | Reading/pronunciation key when available. |
| `display` | Human display headword/title. |
| `source` | `body`, `title`, `index`, `rendererdb`, `lved`, `synthetic`. |
| `key_bytes_hex` | Raw encoded key bytes for index/title-derived rows. |
| `normalized` | Search-normalized form. |
| `search_only` | True for aliases not intended as display title. |

## Title Row

Title rows are decoded from `*TITLE.DIC` streams or renderer/LVED equivalents.

```json
{
  "id": "title:FHTITLE.DIC:4:1570",
  "component": "FHTITLE.DIC",
  "address": {"kind": "component", "component": "FHTITLE.DIC", "block": 4, "offset": 1570},
  "text": "synthetic title",
  "spans": [],
  "target_entry": {"kind": "component", "component": "HONMON.DIC", "block": 10, "offset": 2}
}
```

Title rows may not always have a resolved entry. The pointer relationship often
comes from an index row, not the title stream itself.

Large `dump-package-model` runs may emit title/index/menu summaries with
`"status": "skipped"` and component counts instead of row samples. This is a
bounded reporting mode, not a claim that the row grammars are unsupported.

## Index Row

Index rows describe lookup-tree leaf records and optional branch rows.

```json
{
  "id": "index:FHINDEX.DIC:page1:row1",
  "kind": "leaf",
  "component": "FHINDEX.DIC",
  "component_type": "91",
  "page": {
    "logical_block": 44310,
    "page_index": 98,
    "word": "c000"
  },
  "row_index": 1,
  "index_family": "simple",
  "key": {
    "text": "read",
    "raw_hex": "72656164"
  },
  "target_key": {
    "text": "read",
    "raw_hex": "72656164"
  },
  "body": {"kind": "component", "component": "HONMON.DIC", "block": 25769, "offset": 158},
  "title": {"kind": "component", "component": "FHTITLE.DIC", "block": 4, "offset": 1570},
  "target_count_hint": null,
  "continued_group": false
}
```

Index families:

```text
internal_branch
simple
tagged
body_only_simple
body_only_tagged
keyword_direct
keyword_grouped
cross_reference_direct
cross_reference_grouped
keyless_pointer_table
text_like_index
unknown
```

Writer/exporter implications:

- Index rows are not entries. They are lookup aliases/pointers.
- Key bytes and decoded key text must both be preserved.
- Body and title pointers may be equal or one may be missing depending on the
  index family.
- Branch-page rows matter for reimplementation and writer generation, but most
  exporters only need leaf rows.

## Menu

Menu records represent `MENU.DIC` lines, hierarchy, and link destinations.

```json
{
  "id": "menu:MENU.DIC:1",
  "component": "MENU.DIC",
  "line_index": 1,
  "section_code": "0001",
  "depth": 1,
  "path": ["Front Matter", "Preface"],
  "text": "Preface",
  "spans": [],
  "destination": {
    "payload_hex": "000256780002",
    "encoding": "bcd",
    "is_null": false,
    "target": {"kind": "component", "component": "HONMON.DIC", "block": 25678, "offset": 2}
  },
  "children": []
}
```

Menu `destination.target.kind` may be `component`, `virtual_selector`, or
`unresolved`. Payload `000000000000` is represented with `is_null: true` and
no target; it is a null/sentinel destination rather than an unresolved pointer.
Tree structure is derived from section-depth heuristics and should carry
confidence if used as semantic navigation.

## Gaiji

Gaiji records describe dictionary-local external characters, their mappings,
bitmap assets, image assets, and validation evidence.

```json
{
  "id": "gaiji:half:a126",
  "code": "a126",
  "space": "half",
  "placeholder": "<hA126>",
  "display": "é",
  "fallback": "e",
  "mapping_sources": [
    {
      "kind": "uni",
      "path": "DICT.uni",
      "section": "half",
      "record_index": 6,
      "raw_fields": ["a126", "0000", "00e9", "0000", "0065", "0000"]
    }
  ],
  "bitmap": {
    "component": "GA16HALF",
    "width": 8,
    "height": 16,
    "start_code": "a121",
    "glyph_index": 5
  },
  "image_asset": null,
  "validation": {
    "status": "validated_aligned",
    "evidence": []
  }
}
```

`space` values:

```text
half
full
unknown
```

Mapping source priority for display:

1. dictionary-local `.uni` / `.UNI`;
2. platform plist fallback, when present;
3. package image asset;
4. GA16 bitmap asset;
5. unresolved placeholder.

This priority is for display fallback only. The model keeps every source, even
when lower-priority sources are not used by default.

## Gaiji Occurrence

Gaiji occurrence records connect a gaiji code to a place in text.

```json
{
  "gaiji": "gaiji:half:a126",
  "address": {"kind": "component", "component": "HONMON.DIC", "block": 10, "offset": 42},
  "span": {"start": 40, "end": 42},
  "raw_hex": "a126",
  "resolved": "é",
  "image_backed": false
}
```

For lossless body spans, occurrence data can be embedded in the span as
`gaiji_ref`. Corpus-level gaiji reports may store occurrences separately to
avoid huge entry payloads.

## Static Package Resources

Static resource records describe package files that are not SSED components but
are still part of the product presentation: HTML help/front matter, CSS,
JavaScript, template images, panel assets, package image directories, and helper
pages declared by `EXINFO.INI`. Resource discovery also includes sibling
companion directories such as `_DCT_KANJIGN5_GAIJI` / `KANJIGN5_GAIJI`, because
some Windows packages keep image-backed gaiji outside the main dictionary
directory.

```json
{
  "resources": {
    "static_sidecars": {
      "root_files": ["select.html", "select2.html"],
      "directories": ["HANREI", "Templates"],
      "file_count": 294,
      "total_bytes": 1234567,
      "extension_counts": {
        ".html": 180,
        ".css": 2,
        ".js": 1,
        ".png": 90,
        ".svg": 90
      },
      "samples": [
        {"path": "HANREI/index.html", "bytes": 413, "extension": ".html"}
      ]
    }
  }
}
```

These resources should be preserved by converters even when they are not part
of the main lookup body. For example, KENROWA has a renderer DB for entries,
an auxiliary `0000015B.IDX` navigation tree, root helper pages for Russian
keyboard/example search, and `HANREI/` static HTML front matter.

## Media Reference

Media references are controls or renderer links that point to media records.

```json
{
  "id": "media-ref:HONMON.DIC:23193:0000",
  "kind": "audio",
  "source": {
    "address": {"kind": "component", "component": "HONMON.DIC", "block": 100, "offset": 20},
    "length": 18,
    "raw_hex": "1f4a00010000000231930000000231991579"
  },
  "payload_hex": "00010000000231930000000231991579",
  "target": {
    "kind": "component",
    "component": "PCMDATA.DIC",
    "block": 23193,
    "offset": 0
  },
  "target_end": {
    "kind": "component",
    "component": "PCMDATA.DIC",
    "block": 23199,
    "offset": 1579
  },
  "visible_label": "→音声1",
  "resolved_record": "media-record:PCMDATA.DIC:23193:0000"
}
```

Media reference kinds:

```text
image
audio
link
ziptomedia
renderer_asset
unknown
```

## Media Record

Media records describe the target payload and how it can be exported.

```json
{
  "id": "media-record:COLSCR.DIC:17649:0030",
  "kind": "image",
  "source": {
    "address": {"kind": "component", "component": "COLSCR.DIC", "block": 17649, "offset": 30},
    "length": 1024
  },
  "wrapper": "data_le32_size",
  "media_type": "bmp",
  "codec": "bmp",
  "extension": "bmp",
  "width": 64,
  "height": 175,
  "bits_per_pixel": 8,
  "compression": 0,
  "status": "resolved"
}
```

Known media statuses:

```text
resolved
referenced_but_missing
unreferenced_record
unclassified_payload
invalid_pointer
unsupported_codec
```

`PCMDATA.DIC` records may be self-contained records or shared-container
slices. Shared-container slices, observed in `ARCHSIC3`, keep the raw pointer
range as the media reference and resolve the output format from the
component-level WAVE `fmt ` header.

## Dereference

Dereference records connect raw anchors or pointers to resolved bodies/assets.
`dump-package-model` now emits these records directly in monolithic reports as
`dereferences`, and in chunked reports as `dereferences.jsonl`.

```json
{
  "id": "dereference:honmon-anchor:755",
  "kind": "body_link",
  "from": {
    "kind": "dense_anchor",
    "component": "HONMON.DIC",
    "block": 13,
    "offset": 1602,
    "row_id": 755
  },
  "to": {
    "kind": "database_row",
    "database": "DictFULLDB",
    "table": "contents",
    "row_id": 755
  },
  "method": "dictfulldb_id",
  "status": "resolved",
  "confidence": "proven"
}
```

Observed dereference kinds:

```text
dense_honmon_anchor  raw 32-byte HONMON anchor records, normally numeric IDs
body_link            raw HONMON or entry boundary to sidecar/database body row
index_pointer        *INDEX.DIC leaf row to body/title pointer
menu_destination     MENU.DIC link payload to component/body target
media_reference      HONMON media/audio control to COLSCR/PCMDATA record
```

Dereference methods:

```text
component_block_offset
packed_bcd_media_pointer
menu_destination
index_body_pointer
index_title_pointer
dense_numeric_id
dense_token
dictfulldb_id
rendererdb_data_id
android_rowid_times_5
lved_content_row
ziptomedia_name
unknown
```

Observed status values:

```text
observed            source anchor/pointer was decoded, but no target is attached
resolved            target was resolved or verified
null                decoded pointer is a known null/sentinel destination
unresolved          target pointer could not be mapped to a known component
unverified          target path/schema exists but was not opened in this run
missing_target      target store opened but the row/record was absent
invalid_pointer     pointer payload was structurally invalid or out of range
unsupported_schema  sidecar/database exists but its body schema is unsupported
error               parser or database access failed
```

Current coverage:

- dense HONMON anchor records from `HONMON.DIC`;
- DictFULLDB body links when the declared `t_contents` table is readable;
- renderer/app DB body links, with verified rows under `--deep-sidecars`
  and unverified structural links otherwise;
- Android body DB links using the observed `data_id = rowid * 5` rule;
- index-derived body/title pointers from emitted index rows;
- `MENU.DIC` destination pointers;
- `COLSCR.DIC` and `PCMDATA.DIC` media/audio references.

## Sidecar Body

Sidecar body records represent bodies that are not stored as readable HONMON
text, while retaining their raw anchor relationship.

```json
{
  "id": "body:rendererdb:12345",
  "source": {
    "address": {"kind": "database_row", "database": "vlpljblb", "table": "t_contents", "row_id": 12345}
  },
  "anchor": {
    "kind": "dense_anchor",
    "component": "HONMON.DIC",
    "block": 20,
    "offset": 2,
    "anchor_value": "12345"
  },
  "format": "html",
  "title": "synthetic title",
  "plain": "synthetic plain text",
  "html": "<div>synthetic body</div>",
  "media_refs": [],
  "issues": []
}
```

Sidecar bodies are allowed in the model as evidence. A future legacy SSED
writer subset may decide not to emit them.

## Issue

Issue records are first-class. They keep uncertainty measurable.

```json
{
  "kind": "vendor_title_stream_defect",
  "severity": "forensic",
  "address": {"kind": "component", "component": "FHTITLE.DIC", "component_offset": 4980735},
  "length": 2,
  "raw_hex": "1f1f",
  "message": "25IGAKU contains a malformed singleton title-stream sequence; report it without inferring global opcode semantics"
}
```

Issue severities:

```text
info             useful observation, not a parse problem
forensic         parsed/covered but semantically incomplete
warning          recoverable structural anomaly
error            failed to parse required structure
opaque           payload intentionally classified as opaque
```

Common issue kinds:

```text
unknown_control
unknown_byte
truncated_control
truncated_gaiji
invalid_jis_pair
unresolved_pointer
unresolved_gaiji
unresolved_media
unclassified_payload
unparsed_nonzero_bytes
component_missing
missing_component
component_tail
db_anchor_mismatch
renderer_semantics_unknown
```

## Metrics

Every package and component report should expose measurable coverage.

```json
{
  "metrics": {
    "bytes_total": 2048,
    "bytes_covered": 2048,
    "unknown_controls": 0,
    "unknown_bytes": 0,
    "invalid_jis_pairs": 0,
    "unresolved_gaiji": 0,
    "unresolved_media": 0,
    "unparsed_nonzero_bytes": 0
  }
}
```

Required metric groups:

```text
text         bytes, controls, JIS cells, gaiji, issues
index        pages, internal rows, leaf rows, residual bytes
gaiji        mappings, duplicates, bitmap coverage, unresolved codes
media        references, resolved records, unclassified payloads
dereference  anchors, resolved targets, missing targets, mismatches
package      component counts, missing files, body-source classification
```

## Readiness and Writer Fields

The decoded model exposes parser/exporter/writer readiness without committing
to writer behavior. Readiness is derived from the same package model object
that `dump-package-model` emits; downstream matrix/exporter/writer experiments
should consume this section instead of recomputing package shape names.

```json
{
  "readiness": {
    "schema": "logovista-model-readiness-v0",
    "capabilities": {
      "raw_honmon_body": {
        "status": "yes",
        "reason": "body_source_hint=honmon; shape=body_stream_indexed"
      },
      "indexes_fully_parsed": {"status": "yes", "reason": "index_components=4"},
      "titles_fully_parsed": {"status": "n/a", "reason": "no title components"},
      "gaiji_fully_resolved": {"status": "yes", "reason": "gaiji_readiness=yes"},
      "media_refs_resolved": {"status": "yes", "reason": "media_references=10"},
      "menu_pointers_resolved": {"status": "n/a", "reason": "no MENU.DIC"}
    },
    "metrics": {
      "unknown_controls": 0,
      "unknown_bytes": 0,
      "structural_text_issues": 0,
      "component_parse_errors": 0
    },
    "requirements": {
      "requires_sidecar_body": false
    },
    "writer_readiness": {}
  }
}
```

Capability status values:

```text
yes      modeled and ready for the relevant view
partial  usable only with degradation, fallback, or unresolved residuals
no       blocked by missing data, sidecar-only body, parse failure, or invalid refs
n/a      the package has no such structure
unknown  the model was bounded/skipped or evidence is insufficient
```

`raw_honmon_body = no` does not mean the package is unreadable. Dense-HONMON
packages deliberately use `HONMON.DIC` as an anchor/dereference layer, so the
reader profile can still be green when indexes, components, and sidecar body
relationships are modeled. The `export_existing` and
`lossless_repack_existing` profiles are stricter because they require a usable
body provider for external output or reproduction of the observed package
layout.

The top-level `writer_readiness` is a copy of
`readiness.writer_readiness` for convenience:

```json
{
  "writer_readiness": {
    "author_core_ssed_v0": "green",
    "author_core_ssed_v0_blockers": [],
    "lossless_repack_existing": "green",
    "lossless_repack_existing_blockers": [],
    "export_existing": "green",
    "export_existing_blockers": [],
    "read_existing": "green",
    "read_existing_blockers": [],
    "legacy_ssed_subset": "green",
    "legacy_ssed_subset_blockers": [],
    "lossless_repacker": "green",
    "lossless_repacker_blockers": [],
    "combined": "green",
    "combined_blockers": []
  }
}
```

The four profile names are intentionally distinct:

```text
read_existing             parse/read the existing package as observed
export_existing           convert the existing dictionary to an external format
author_core_ssed_v0       author a new plain/core SSED package
lossless_repack_existing  reproduce/repack the observed package structure
```

`author_core_ssed_v0` is intentionally about new plain-HONMON authoring. Dense
HONMON, renderer DBs, Android body DBs, `DictFULLDB`, platform manifests, and
compiled renderer sidecars are outside that writer profile.

The legacy alias fields remain for compatibility with older reports:
`legacy_ssed_subset` is currently an alias of `export_existing`,
`lossless_repacker` is an alias of `lossless_repack_existing`, and `combined`
is the worst of `export_existing` and `lossless_repack_existing`.

For non-SSED package families such as `lved_sqlcipher` and
`multiview_sqlite`, SSED writer readiness is deliberately `gray` with a
`non_ssed_package_family` blocker. Those families are classified into the
common package model so corpus runs can account for them, but planned writer
support applies only to core SSED packages.

Writer status values:

```text
green   no observed blocker for that profile
yellow  usable with degradation or profile-specific rules
red     not usable without additional reverse engineering or a sidecar path
gray    not applicable
unknown not enough evidence to classify
```

This is not a format claim. It is planning metadata for writer/exporter work.

## Serialization Shape

For large dictionaries, the model supports chunked JSONL files rather than a
single massive JSON document. `dump-package-model --chunked` and
`dump-package-models --chunked` write this layout.

Recommended directory layout:

```text
decoded-model/
  package.json
  components.jsonl
  entries.jsonl
  titles.jsonl
  indexes.jsonl
  menus.jsonl
  gaiji.jsonl
  media_refs.jsonl
  media_records.jsonl
  dereferences.jsonl
  issues.jsonl
  metrics.json
```

`package.json` contains package-level metadata and file references. JSONL files
contain one object per record. `package.json` keeps the same
`logovista-decoded-model-v0` schema so `capability-matrix --model-dir` can read
chunked and monolithic reports through the same model-loader path. Small
synthetic fixtures may inline everything into one JSON document for test
convenience.

## Relationship to Future Work

This is not a new dictionary format. It is the model used to understand
existing LogoVista packages and drive future tooling.

Potential future layers:

```text
LogoVista/SSED raw bytes  -> decoded model -> debug HTML
LogoVista/SSED raw bytes  -> decoded model -> writer-readiness report
LogoVista/SSED raw bytes  -> decoded model -> future exporter
LogoVista/SSED raw bytes  -> decoded model -> future legacy SSED writer subset
decoded model spec        -> future Rust reimplementation
```

The Python toolkit is allowed to remain exploratory. The later Rust core should
be a clean reimplementation of the stabilized model and format specification,
not a direct port of Python probes.

## Current Gaps

Decoded Model v0 intentionally names gaps instead of hiding them:

- Some control opcodes are structurally known, but exact presentation remains
  conservative rather than fully reproduced.
- Some media payloads are byte-addressed but not codec-classified.
- Some `.uni` files have parsed mappings plus small unclassified trailers.
- Dense-HONMON dictionaries require product-family dereference paths.
- Compatible-reader parity is not represented as a hard guarantee.
- Writer generation is now being tested through experimental Python primitives
  for the plain/core SSED subset. The implemented proof covers SSEDINFO,
  compressed SSEDDATA, body/title streams, simple/tagged index pages, and
  generated `.uni` / GA16 resources. The writer now also treats lookup aliases
  as separate index data from display text, including punctuation/space/hyphen
  stripping and Japanese katakana-to-hiragana aliases where appropriate.
  Remaining writer work is hardening those rules against broader fixtures, not
  changing Decoded Model v0 into a new dictionary format.

These gaps do not block the model draft. They define the work that must happen
before exporter and writer output can claim higher compatibility.
