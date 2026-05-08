# LV-IR v0 Draft

`LV-IR v0` is the draft intermediate representation for decoded
LogoVista/SystemSoft dictionaries.

The goal is not to invent a new interchange format yet. The goal is to give
parsers, validators, exporters, and future writer experiments one shared model
for the facts recovered from a package.

This page is a draft contract. It is allowed to change while marked `v0`, but
new parser/exporter work should target this model rather than command-specific
JSON shapes.

## Status

```text
name:        LV-IR
version:     0 draft
scope:       decoded LogoVista/SSED and LVED dictionary packages
stability:   draft; not frozen
encoding:    JSON-compatible data model, UTF-8 when serialized
principle:   preserve raw provenance before renderer interpretation
```

Current command outputs map into parts of this model:

| Current command | LV-IR coverage |
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

`dump-ir` currently emits `logovista-lossless-entry-v1`. Treat that as an
entry-span subset of LV-IR, not as the full model.

## Design Rules

1. **Raw-first.** Every object that comes from dictionary bytes should carry
   enough provenance to find those bytes again: component, block/offset, byte
   offset, length, and raw bytes when the output mode allows them.
2. **Lossless before pretty.** Rendering decisions belong in renderers and
   exporters. The IR stores raw controls, decoded fields, confidence, and
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
   renderer DBs, and LVED payloads are different sources. The IR can connect
   them, but should not pretend they are the same file.
8. **Writer/exporter views are derived.** Yomitan, MDict, debug HTML, and a
   future writer should consume LV-IR. They should not re-parse raw bytes.

## Top-Level Package

An LV-IR package describes one dictionary package or one dictionary target
inside a larger collection.

```json
{
  "schema": "lv-ir-package-v0",
  "ir_version": 0,
  "dict_id": "SYNTH",
  "title": "Synthetic Dictionary",
  "package_family": "ssed",
  "platform": "windows",
  "classification": {
    "raw_core": "ssed",
    "honmon_shape": "body_stream_indexed",
    "body_source": "honmon",
    "confidence": "proven"
  },
  "components": [],
  "entries": [],
  "titles": [],
  "indexes": [],
  "menus": [],
  "gaiji": [],
  "media": [],
  "dereferences": [],
  "issues": []
}
```

`package_family` values:

```text
ssed              SSEDINFO/SSEDDATA package with HONMON-style components
lved_sqlcipher    modern LVED/WebView2 SQLCipher payload family
multiview_sqlite  LVLMultiView package with SSEDINFO facade + SQLite bodies
mixed             package has both raw SSED anchors and renderer/database bodies
unknown           classified enough to report, not enough to decode
```

`honmon_shape` values observed so far:

```text
body_stream_indexed
body_stream_marker_sliced
marker_rich_text_stream
text_stream_without_entry_markers
dense_marker_table
dense_numeric_id_table
dense_token_table
missing
not_applicable
unknown
```

`body_source` values:

```text
honmon
honmon_anchor_dereference
dictfulldb
rendererdb
androiddb
lved_sqlcipher
multiview_sqlite
none
unknown
```

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
component          SSED component block/offset address
component_offset   component-relative byte offset without block metadata
book_offset        composed expanded book offset
packed_bcd         raw packed-BCD pointer before resolution
dense_anchor       HONMON 32-byte ID/token anchor
db_row             SQLite/SQLCipher/renderer table row
asset              package asset or exported media record
virtual_selector   side-panel/menu selector, not a raw byte destination
external           URL or external reference
unresolved         pointer bytes exist but target is not known
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
  "role": "body",
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
body
menu
title
index
text_index
gaiji_bitmap
gaiji_unicode_map
media_image
media_audio
renderer_db
dictfulldb
lved_payload
platform_metadata
package_asset
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

Common span kinds:

| Kind | Meaning |
| --- | --- |
| `padding` | NUL padding bytes. |
| `text` | JIS X 0208 cell pair. |
| `ascii` | Literal ASCII byte. |
| `break` | LogoVista line break control or legacy `0x0a`. |
| `section` | `1f09 xxxx` section marker. |
| `control` | Structurally known control with neutral or known tag. |
| `control_start` | Start wrapper control. |
| `control_end` | End wrapper control. |
| `link_ref` | Link/jump control with payload and optional visible label. |
| `media_ref` | Media/image/audio control with payload. |
| `gaiji` | Dictionary-local external character. |
| `unknown_control` | `0x1f` opcode not structurally classified. |
| `problem` | Invalid/truncated/unclassified bytes. |

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

Unknown or not-yet-semantic controls are still valid IR records. For example,
`1f1a` and `1f1c` currently have fixed two-byte payloads and neutral tags; the
IR should preserve them without naming them `bold`, `color`, or `layout` until
renderer evidence supports that.

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
db_row
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
    "target": {"kind": "component", "component": "HONMON.DIC", "block": 25678, "offset": 2}
  },
  "children": []
}
```

Menu `destination.target.kind` may be `component`, `virtual_selector`, or
`unresolved`. Tree structure is derived from section-depth heuristics and
should carry confidence if used as semantic navigation.

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

This priority is for display fallback only. The IR keeps every source, even
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

`ARCHSIC3` currently uses `unclassified_payload` for valid `PCMDATA.DIC`
ranges that are not yet RIFF/WAVE, native ID3/MP3, or MPEG-in-WAVE.

## Dereference

Dereference records connect raw anchors or pointers to resolved bodies/assets.

```json
{
  "id": "dereference:honmon-anchor:755",
  "from": {
    "kind": "dense_anchor",
    "component": "HONMON.DIC",
    "block": 13,
    "offset": 1602,
    "anchor_type": "numeric_id",
    "anchor_value": "755"
  },
  "to": {
    "kind": "db_row",
    "db": "DictFULLDB",
    "table": "contents",
    "row_id": 755
  },
  "method": "dictfulldb_id",
  "confidence": "proven"
}
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

## Sidecar Body

Sidecar body records represent bodies that are not stored as readable HONMON
text, while retaining their raw anchor relationship.

```json
{
  "id": "body:rendererdb:12345",
  "source": {
    "address": {"kind": "db_row", "db": "vlpljblb", "table": "t_contents", "row_id": 12345}
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

Sidecar bodies are allowed in LV-IR. A legacy SSED writer v0 does not need to
emit them.

## Issue

Issue records are first-class. They keep uncertainty measurable.

```json
{
  "kind": "unknown_control",
  "severity": "forensic",
  "address": {"kind": "component", "component": "FHTITLE.DIC", "component_offset": 4980735},
  "length": 2,
  "raw_hex": "1f1f",
  "message": "0x1f control opcode has no classified argument length or semantics"
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

## Writer Readiness Fields

LV-IR should expose writer-readiness without committing to writer behavior.

```json
{
  "writer_readiness": {
    "legacy_ssed_subset": "green",
    "requires_generated_gaiji": true,
    "requires_sidecar_body": false,
    "requires_unknown_controls": false,
    "requires_unclassified_media": false,
    "blocking_issues": []
  }
}
```

Suggested values:

```text
green   usable by a minimal legacy writer/exporter
yellow  usable with degradation or profile-specific rules
red     not usable without additional reverse engineering
gray    not applicable
```

This is not a format claim. It is planning metadata for writer/exporter work.

## Serialization Shape

For large dictionaries, LV-IR should support chunked JSONL files rather than a
single massive JSON document.

Recommended directory layout:

```text
lv-ir/
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
contain one object per record. Small synthetic fixtures may inline everything
into one JSON document for test convenience.

## Relationship to Future Formats

LV-IR is not the proposed future open successor format. It is the model used to
understand existing packages and drive conversions.

Potential future layers:

```text
LogoVista/SSED raw bytes  -> LV-IR -> debug HTML
LogoVista/SSED raw bytes  -> LV-IR -> Yomitan / MDict
LogoVista/SSED raw bytes  -> LV-IR -> writer-readiness report
Yomitan / authored data   -> LV-IR -> legacy SSED writer subset
Yomitan / authored data   -> LV-IR -> future LVEX/SSEDX package
```

A future LVEX/SSEDX format should be designed separately. It can use LV-IR as
an input model, but should not inherit legacy SSED constraints such as JIS-only
text, fixed-size unknown controls, packed-BCD pointers, or 16x16 monochrome
gaiji unless compatibility specifically requires them.

## Current Gaps

LV-IR v0 intentionally names gaps instead of hiding them:

- Some control opcodes are structurally known but renderer-neutral.
- Some media payloads are byte-addressed but not codec-classified.
- Some `.uni` files have parsed mappings plus small unclassified trailers.
- Dense-HONMON dictionaries require product-family dereference paths.
- Official renderer parity is not represented as a hard guarantee.
- Writer generation requires additional rules for collation, page splitting,
  control selection, and generated gaiji allocation.

These gaps do not block the IR draft. They define the work that must happen
before exporter and writer output can claim higher compatibility.
