# logovista-tools

Raw-first tools for inspecting and extracting data from LogoVista/SystemSoft
SSED dictionary packages.

This project exists because many LogoVista dictionaries are only *EPWING-like*.
They often ship a friendly SQLite cache for app search, but the real packaged
dictionary assets are the proprietary-looking `.IDX` / `.DIC` files. These
tools work from those raw files first.

Current status: alpha, useful for research and bulk extraction. It can already
decompress SSED data, compose EPWING-like book images, extract readable
`HONMON.DIC` body entries for many dictionaries, transparently decrypt observed
Windows LogoFontCipher body streams, extract raw title/headword streams from
`*TITLE.DIC`, and follow raw HONMON numeric ID records into LogoVista
`DictFULLDB` body payloads for products such as KOJIEN7 and other dense-HONMON
dictionaries. It also parses `MENU.DIC` menu trees, the common `*INDEX.DIC`
search-tree formats, raw lookup keys with body/title pointers, and
dictionary-specific image resources used for image-backed gaiji and inline
badges. It can also decode `COLSCR.DIC` media pointers and extract referenced
BMP/JPEG records used by inline figures and stroke-order panels. For body-stream
dictionaries, it can also use raw index body pointers as additional entry
boundaries, which is required for packages whose real entries do not all start
with the common `1f09 0001` marker. The `audit-honmon` command is a raw-only
corpus probe for checking whether a dictionary's `HONMON.DIC` is a readable body
stream, a dense raw ID/token table, or an opaque/stub component; it does not read
SQLite body text. Windows packages with `EXINFO.INI` side panels and encrypted
renderer databases are also supported: `extras` parses auxiliary index trees,
and `rendererdb` follows raw HONMON ID anchors into observed `t_contents`
renderer SQLite payloads such as DAIJIRN4's `vlpljblb`. Android body databases
that preserve the same raw HONMON ID relationship are supported as a separate
app-cache shape. Standalone Windows `SPINDEX.DIC` suffix-index resources can be
inspected without treating them as dictionary body components.

No dictionary data is included in this repository.

## Install

Use Python 3.10 or newer.

```bash
git clone https://github.com/shoui520/logovista-tools.git
cd logovista-tools
python -m pip install -e .
```

Encrypted Windows body streams require AES support:

```bash
python -m pip install -e ".[crypto]"
```

Verify the CLI:

```bash
logovista-tools --help
```

You can also run without installing:

```bash
PYTHONPATH=src python -m logovista_tools --help
```

## Quick Start

Scan a LogoVista collection:

```bash
logovista-tools scan /path/to/LogoVista
```

Inspect one dictionary catalog:

```bash
logovista-tools info /path/to/DICT/DICT.IDX --all
```

Expand one compressed component:

```bash
logovista-tools expand /path/to/DICT/HONMON.DIC expanded-honmon.bin
```

Decrypt a LogoFontCipher sidecar without expanding it:

```bash
logovista-tools decrypt /path/to/DICT/vlpljblF vlpljblF.sqlite
```

Parse Windows `EXINFO.INI` side panels and auxiliary text indexes:

```bash
logovista-tools extras /path/to/DICT --out-dir out/extras
```

Compose an EPWING-like book image from an `.IDX` and its sibling `.DIC` files:

```bash
logovista-tools compose /path/to/DICT/DICT.IDX expanded-book.bin
```

Extract readable `HONMON.DIC` bodies as JSONL:

```bash
logovista-tools entries /path/to/LogoVista --out-dir out/bodies
```

Audit raw `HONMON.DIC` / `*INDEX.DIC` readability across a collection without
using SQLite body text:

```bash
logovista-tools audit-honmon /path/to/LogoVista --out-dir out/honmon-audit
```

Extract bodies while preserving PNG-backed gaiji as inline HTML:

```bash
logovista-tools entries /path/to/LogoVista --dict HAESPJPN --image-gaiji --html --out-dir out/html-bodies
```

For dictionaries where section markers correspond to named style images, add a
section-image rule:

```bash
logovista-tools entries /path/to/LogoVista --dict HAESPJPN --image-gaiji --html --section-image 0011=exam --out-dir out/html-bodies
```

List image resources and image-backed gaiji assets:

```bash
logovista-tools resources /path/to/LogoVista --dict HAESPJPN
```

Write a gaiji validation report using raw streams plus SQL/`DictFULLDB`
evidence where available:

```bash
logovista-tools gaiji-report /path/to/LogoVista --dict HAESPJPN --out-dir out/gaiji-report
```

Inspect a `.uni` gaiji mapping file:

```bash
logovista-tools uni /path/to/DICT/DICT.uni --limit 20
```

Render bitmap-only `GA16HALF` / `GA16FULL` gaiji to PNG assets:

```bash
logovista-tools ga16 /path/to/DICT /path/to/out/gaiji --variants
```

Extract images referenced by `HONMON.DIC` media controls from `COLSCR.DIC`:

```bash
logovista-tools colscr /path/to/DICT/DICT.IDX --out-dir out/colscr --write-media
```

Extract audio/media referenced by `HONMON.DIC` controls from `PCMDATA.DIC`:

```bash
logovista-tools pcmdata /path/to/DICT/DICT.IDX --out-dir out/pcmdata --write-audio
```

Extract raw title/headword streams:

```bash
logovista-tools titles /path/to/LogoVista --out-dir out/titles
```

Extract `MENU.DIC` menu trees and destination pointers:

```bash
logovista-tools menus /path/to/LogoVista --dict GENIUSEB --out-dir out/menus
```

Extract raw search-index rows:

```bash
logovista-tools indexes /path/to/LogoVista --dict KOJIEN7 --out-dir out/indexes
```

Extract formatted bodies from products that use raw HONMON ID records plus
`DictFULLDB`:

```bash
logovista-tools fulldb /path/to/LogoVista --dict KOJIEN7 --out-dir out/fulldb
```

Extract formatted bodies from renderer/app SQLite sidecars linked by raw HONMON
ID anchors:

```bash
logovista-tools rendererdb /path/to/LogoVista --dict DAIJIRN4 --out-dir out/rendererdb
```

Inspect standalone Windows `SPINDEX.DIC` suffix-index resources:

```bash
logovista-tools spindex /path/to/LogoVista --out-dir out/spindex
```

Smoke-test one dictionary:

```bash
logovista-tools entries /path/to/LogoVista --dict GENIUSEB --limit 20
```

## CLI Commands

### `scan`

Find SSED dictionaries under one or more roots.

```bash
logovista-tools scan /path/to/LogoVista
logovista-tools scan /path/to/LogoVista --json
```

The scanner looks for `.IDX` / `.idx`, validates the `SSEDINFO` magic, and
keeps only dictionaries with a discoverable `HONMON.DIC`.

### `info`

Print metadata for either an `.IDX` or `.DIC`.

```bash
logovista-tools info DICT.IDX --all
logovista-tools info HONMON.DIC
```

For `.IDX`, this lists component records, block ranges, component types, and
filenames. For `.DIC`, this prints the SSED chunk count, declared logical block
range, and storage mode (`plain` or `logofont_cipher`).

### `expand`

Decompress one plain or LogoFontCipher-encrypted `SSEDDATA` file:

```bash
logovista-tools expand HONMON.DIC honmon.expanded
```

The output is the expanded EPWING/JIS-style byte stream for that component.

### `decrypt`

Decrypt an observed Windows LogoFontCipher file without SSED expansion:

```bash
logovista-tools decrypt HONMON.DIC honmon.ssed
logovista-tools decrypt vlpljblF vlpljblF.sqlite
```

Use this for sidecars that decrypt to another container such as SQLite. For
encrypted `HONMON.DIC`, `info`, `expand`, `entries`, and `audit-honmon` already
decrypt transparently when the optional crypto dependency is installed.
The command streams input to output, so large sidecars such as DAIJIRN4's
610 MB `vlpljblb` do not need to be loaded into memory.

### `compose`

Create an EPWING-like image by expanding each component listed by the `.IDX`
and placing it at its declared logical block.

```bash
logovista-tools compose DICT.IDX book.expanded --quiet
```

This is useful for binary comparison and for understanding the book layout.
It does not turn LogoVista into a fully standard EPWING book; it just writes
the expanded components into the block positions specified by `SSEDINFO`.

### `entries`

Extract readable body entries from expanded `HONMON.DIC`.

```bash
logovista-tools entries /path/to/LogoVista --out-dir bodies
```

Output layout:

```text
bodies/
  summary.json
  DICT_ID/
    summary.json
    raw_entries.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "GENIUSEB",
  "dict_title": "ジーニアス英和大辞典",
  "entry_index": 2,
  "block": 25769,
  "offset": 158,
  "length": 3048,
  "heading": "a, A <hA235>ei<hA235>【名】(複 →[語法])",
  "body": "a, A <hA235>ei<hA235>【名】...\n1英語アルファベットの第1字."
}
```

Useful options:

```bash
--dict NAME                         extract only matching dictionary ids
--limit N                           stop after N emitted entries per dictionary
--gaiji drop                        omit all unresolved gaiji
--gaiji h-placeholder               keep half-width gaiji placeholders only
--gaiji placeholder                 keep half-width and full-width placeholders
--image-gaiji                       preserve unresolved PNG-backed gaiji as <img:code>
--html                              also emit body_html with inline HTML img tags
--media-placeholder                 preserve 1f4d media payloads as placeholders
--section-markers                   preserve 1f09 section markers as placeholders
--section-image CODE=IMAGE_KEY      insert a named image at a section marker in HTML output
--no-index-boundaries               slice only on HONMON entry markers
--no-skip-dense-marker-honmon       force extraction on placeholder HONMON
```

When `--html` is used, rows include `body_html` in addition to `body`.
PNG-backed gaiji are rendered as package-relative image tags such as:

```html
<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d">
<img src="resource/kmkimges/b167_1.png" alt="b167" class="lv-gaiji lv-gaiji-b167">
```

Exporters for Yomitan, MDict, or another HTML-capable format should copy the
referenced PNG files into the target package and rewrite `src` paths if their
archive layout differs.

`--section-image` is intentionally explicit. For HAESPJPN, `0011=exam` is a
useful first-pass rule because `0011` marks Spanish example lines and
`exam.png` is the dictionary's `用例` badge. Other dictionaries may use the
same section code differently.

### `audit-honmon`

Audit whether raw `HONMON.DIC` plus raw `*INDEX.DIC` pointers can produce
coherent body text.

```bash
logovista-tools audit-honmon /path/to/LogoVista --out-dir honmon-audit
logovista-tools audit-honmon /path/to/LogoVista --dict KOJIEN7 --json
```

Output layout:

```text
honmon-audit/
  honmon_audit.json
```

This command is intentionally raw-first. It expands `HONMON.DIC`, parses
index-derived body boundaries, samples decoded body slices, probes 32-byte
HONMON ID/token records, checks `*TITLE.DIC` availability, and records
`DictList.plist` declarations. It does not read SQLite or `DictFULLDB` body
text. The database metadata is included only to explain where dense raw IDs
would be dereferenced by the separate `fulldb` command.

Useful options:

```bash
--dict NAME                         audit only matching dictionary ids
--sample-limit N                    keep at most N readable body samples
--max-slices N                      inspect at most N candidate raw slices per dictionary
--max-id-records N                  probe at most N 32-byte HONMON records; 0 = full scan
--no-index-boundaries               sample marker-only slicing
--no-skip-dbc                       include opaque .dbc products in the report
--json                              also print the JSON report
```

Important status values:

```text
raw_honmon_body_stream              HONMON/IDX yields readable entries
mixed_or_dense_but_raw_slices_readable
                                    dense signals exist, but raw slices are readable
dense_honmon_id_table_dictfulldb    HONMON stores raw numeric body IDs
dense_honmon_token_table_dictfulldb HONMON stores opaque raw tokens/anchors
dense_honmon_id_table_rendererdb    HONMON stores raw numeric IDs that resolve
                                    to a Windows renderer SQLite sidecar
dense_honmon_id_table_androiddb     HONMON stores raw numeric IDs that resolve
                                    to an Android app body database
idx_title_only_no_readable_honmon_body
                                    indexes/titles exist, but sampled HONMON bodies do not
skipped_dbc                         .dbc payload skipped by default
```

### `resources`

Discover package image resources.

```bash
logovista-tools resources /path/to/LogoVista --dict HAESPJPN
logovista-tools resources /path/to/LogoVista --dict HAESPJPN --json
```

LogoVista packages often include a top-level `img` directory plus
`resourcesCopy.plist` and `gaijiicon.plist`. Windows packages can put HTML
renderer assets in `Templates`; Android packages can omit plist manifests and
put images in `resource/kmkimges`, `appendix/img`, or `manual/contents/img`. The
resource scanner checks all of those locations, groups theme variants such as
`b13d_n.png` / `b13d_w.png` and Android-style `b167_1.png` / `b167_3.png`, and
reports code-like resources such as `b13d` or `b167` as image-backed gaiji.
PNG, GIF, JPEG, WebP, BMP, and SVG files are all treated as portable package
resources.
Named images such as `exam.png`, `esp.png`, or `jpn.png` are reported as package
resources for format exporters to use when reconstructing dictionary-specific
styling.

### `colscr`

Inspect or extract images stored in `COLSCR.DIC` and referenced by raw
`HONMON.DIC` media controls.

```bash
logovista-tools colscr /path/to/LogoVista --dict OUKOKU11 --out-dir colscr
logovista-tools colscr /path/to/DICT/DICT.IDX --write-media --out-dir colscr
```

Output layout:

```text
colscr/
  summary.json
  DICT_ID/
    colscr_summary.json
    colscr_manifest.jsonl
    media/
      00001_0200_00017649_0030.bmp
```

Each manifest row records the HONMON media-control position, section code,
raw 18-byte payload, decoded logical block/offset, image type, dimensions,
bit depth, BMP compression mode when relevant, and optional output filename.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           stop after N media references per dictionary
--write-media                       write referenced BMP/JPEG files
--json                              emit a machine-readable summary
```

### `pcmdata`

Inspect or extract audio/media records stored in `PCMDATA.DIC` and referenced
by raw `HONMON.DIC` controls.

```bash
logovista-tools pcmdata /path/to/LogoVista --dict HAESPJPN --out-dir pcmdata
logovista-tools pcmdata /path/to/DICT/DICT.IDX --write-audio --out-dir pcmdata
```

Output layout:

```text
pcmdata/
  summary.json
  DICT_ID/
    pcmdata_summary.json
    pcmdata_manifest.jsonl
    audio/
      00001_00023193_0000.wav
```

The manifest includes the raw 16-byte payload, decoded logical block range,
visible link label when present, codec, chunk layout, sample rate, channel
count, bit depth, source (`honmon` or `unreferenced`), and optional output
filename.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--limit N                           stop after N HONMON audio references per dictionary
--write-audio                       write portable .wav/.mp3 files
--no-include-unreferenced           skip sequential records not referenced by HONMON
--json                              emit a machine-readable summary
```

For `fmt `/`data` PCM records, `--write-audio` wraps the raw chunks in a
standard `RIFF/WAVE` container. For MPEG Layer III records stored inside WAVE
chunks, it writes the `data` chunk as `.mp3`. For native `ID3`/MP3 records, it
writes the MP3 payload directly.

### `extras`

Parse Windows `EXINFO.INI` metadata and auxiliary side-panel files.

```bash
logovista-tools extras /path/to/DICT --out-dir extras
logovista-tools extras /path/to/LogoVista --dict DAIJIRN4 --json
```

Windows packages often declare side UI through fields such as `IDXCOUNT`,
`IDXNAME0`, and `IDXINFO0`. HTML entries are reported as HTML files. Text
auxiliary indexes such as DAIJIRN4's `0000015E.IDX` are parsed as CP932 tab
trees whose first two columns are eight-digit hexadecimal block/offset
pointers. Rows are resolved against the `.IDX` component ranges when possible.

Output layout:

```text
extras/
  summary.json
  DICT_ID/
    extras_summary.json
    aux_0_0000015E.IDX.jsonl
```

### `rendererdb`

Extract renderer/app SQLite bodies by following raw HONMON ID anchors.

```bash
logovista-tools rendererdb /path/to/DICT --out-dir rendererdb
logovista-tools rendererdb /path/to/DICT --limit 20 --no-html
logovista-tools rendererdb /path/to/DICT --write-media --media-limit 100
```

This command handles layouts where `HONMON.DIC` is a dense 32-byte anchor
table, not a body stream. It still starts from raw HONMON:

1. Expand `HONMON.DIC`.
2. Decode 32-byte records whose visible field is a full-width decimal ID.
3. Discover a sibling body SQLite payload.
4. For Windows renderer DBs, decrypt observed LogoFontCipher sidecars such as
   `vlpljblb` when needed, query `t_contents`, and emit only rows whose
   `f_DataId` exists in raw HONMON.
5. For the observed Android body DB shape, query the `DICTID(Html)` table and
   emit rows where `rowid * 5` exists in raw HONMON.

Output layout:

```text
rendererdb/
  summary.json
  DICT_ID/
    rendererdb_summary.json
    rendererdb_entries.jsonl
    vlpljblb.sqlite
    media/
      00001_3djr_0002.gif
```

Rows include `data_id`, raw HONMON block/offset, type, group id, title,
search title, keyword text, plain body text, and HTML unless `--no-html` is
used. `--write-media` exports BLOBs from the sidecar `media` table using magic
bytes to choose `.gif`, `.png`, `.jpg`, `.bmp`, or `.bin`.

### `spindex`

Inspect standalone `SPINDEX.DIC` resources.

```bash
logovista-tools spindex /path/to/DICT --out-dir spindex
logovista-tools spindex /path/to/LogoVista --limit 200 --json
```

The observed `SPINDEX.DIC` files are not listed in product `SSEDINFO`
component tables, so this command handles them as separate SSED containers. It
expands only the compressed chunks that are physically present, parses the
internal reversed-key index pages, and reports whether child pages are present,
missing from the physical file, or outside the declared logical range.

Output layout:

```text
spindex/
  summary.json
  DICT_ID/
    SPINDEX_summary.json
    SPINDEX_internal_rows.jsonl
```

Internal row output includes both the stored reversed key and its forward
spelling:

```json
{
  "key_reversed": "CITEROHPAID",
  "key": "DIAPHORETIC",
  "child_block": 55753,
  "child_status": "present_full_page"
}
```

### `gaiji-report`

Write per-dictionary gaiji audit reports.

```bash
logovista-tools gaiji-report /path/to/LogoVista --dict HAESPJPN --out-dir gaiji-report
logovista-tools gaiji-report /path/to/LogoVista --dict KOJIEN7 --no-sql-cache --max-sql-rows 1000
logovista-tools gaiji-report /path/to/LogoVista --dict DAIJIRN4 --renderer-sidecars
```

Output layout:

```text
gaiji-report/
  summary.json
  DICT_ID/
    gaiji_report.json
```

The report combines:

- raw gaiji occurrence counts from expanded `HONMON.DIC` and `*TITLE.DIC`;
- dictionary-local `.uni` mappings and plist fallback mappings;
- `GA16HALF` / `GA16FULL` bitmap coverage;
- package PNG/image gaiji coverage;
- SQLite text evidence from declared `DictFULLDB` and sibling app-cache
  databases;
- optional Windows renderer SQLite sidecar evidence when `--renderer-sidecars`
  is used;
- aligned validation for cache tables that expose `Block` and `Offset`.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--no-sql-cache                      use declared DictFULLDB only
--renderer-sidecars                 decrypt/use Windows renderer SQLite sidecars
--max-sql-rows N                    scan at most N rows per SQLite table; 0 = full scan
--max-aligned-entries N             align at most N raw HONMON entries; 0 = full scan
--alignment-tolerance N             byte tolerance for Block/Offset cache matching
--include-unused-mapped             include mapped codes not seen in raw scans
```

Per-code rows include fields such as:

```json
{
  "code": "a126",
  "placeholder": "<hA126>",
  "raw_count": 17690,
  "mapped": "é",
  "mapping_source": "uni",
  "bitmap": {"resource": "GA16HALF", "width": 8, "height": 16},
  "sqlite_text_hits_for_display": 5249,
  "aligned_hits": 5232,
  "aligned_misses": 0,
  "status": "validated_aligned"
}
```

Status values are deliberately conservative. `validated_aligned` is strongest:
the raw entry pointer matched a SQLite row by `Block`/`Offset`, and the mapped
display text was present in that row. `db_text_evidence` means the display text
appeared somewhere in SQL/`DictFULLDB`, but not in an aligned row. SQL evidence
is not treated as authority because several app caches normalize or flatten
characters. For example, GENIUSEB validates many IPA/symbol gaiji through SQL,
but its cache omits some accent display forms that are present in `.uni`.

### `uni`

Inspect LogoVista `.uni` / `.UNI` gaiji mapping files.

```bash
logovista-tools uni /path/to/DICT/DICT.uni
logovista-tools uni /path/to/DICT/DICT.uni --json --limit 50
```

This command reports the detected layout, half/full record counts, mapped
records, fallback/search records, legacy/alternate fields, and raw 16-bit
fields for each record.

### `ga16`

Render LogoVista bitmap gaiji resources to portable PNG files.

```bash
logovista-tools ga16 /path/to/DICT/GA16HALF out/gaiji
logovista-tools ga16 /path/to/DICT out/gaiji --variants
logovista-tools ga16 /path/to/LogoVista out/gaiji --limit 10 --json
```

The input may be one resource file, one dictionary directory, or a collection
root. Directory inputs are grouped under `out_dir/DICT_ID/`. File names are
code-stable:

```text
hA126.png       half-width resource, single-variant output
zB121.png       full-width resource, single-variant output
hA126_n.png     black-theme variant
hA126_w.png     white-theme variant
```

Useful options:

```bash
--code A126                         render only one code; may be repeated
--limit N                           render at most N glyphs per resource
--variants                          write black _n.png and white _w.png variants
--foreground RRGGBB[AA]             ink color for single-variant output
--background RRGGBB[AA]             background color for single-variant output
--prefix auto|h|z|g                 choose filename prefix
--json                              emit a machine-readable summary
```

Default output is black ink on transparent background. `--variants` mirrors
the theme pattern used by packaged image resources such as `b13d_n.png` and
`b13d_w.png`.

### `titles`

Extract headword/title lines from expanded `*TITLE.DIC` components:

```bash
logovista-tools titles /path/to/LogoVista --out-dir titles
```

Output layout:

```text
titles/
  summary.json
  DICT_ID/
    titles_summary.json
    raw_titles.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "component": "FKTITLE.DIC",
  "line_index": 2,
  "text": "はこべ‐じお【塩】"
}
```

Title extraction is especially useful for dictionaries whose `HONMON.DIC` is a
placeholder table rather than a body stream.

### `menus`

Extract `MENU.DIC` menu lines, hierarchy, link labels, and destination
pointers:

```bash
logovista-tools menus /path/to/LogoVista --out-dir menus
logovista-tools menus /path/to/LogoVista --dict HAIKSAIJ --limit 50
```

Output layout:

```text
menus/
  summary.json
  DICT_ID/
    menus_summary.json
    raw_menus.jsonl
    menu_tree.json
```

Each JSONL row looks like:

```json
{
  "dict_id": "GENIUSEB",
  "dict_title": "ジーニアス英和大辞典",
  "component": "MENU.DIC",
  "line_index": 1,
  "section_code": null,
  "depth": 1,
  "path": ["はしがき"],
  "text": "はしがき",
  "links": [
    {
      "label": "はしがき",
      "destination": {
        "payload": "000256780002",
        "encoding": "bcd",
        "block": 25678,
        "offset": 2,
        "absolute_offset": 52586498,
        "target": {
          "component": "HONMON.DIC",
          "component_type": "00",
          "kind": "body",
          "start_block": 25678,
          "end_block": 64404,
          "relative_offset": 2,
          "offset_in_block": 2
        }
      }
    }
  ],
  "destination": {
    "payload": "000256780002",
    "encoding": "bcd",
    "block": 25678,
    "offset": 2,
    "absolute_offset": 52586498,
    "target": {
      "component": "HONMON.DIC",
      "component_type": "00",
      "kind": "body",
      "start_block": 25678,
      "end_block": 64404,
      "relative_offset": 2,
      "offset_in_block": 2
    }
  }
}
```

`menu_tree.json` contains the same records nested by inferred section depth.
Section depth is derived from the numeric ordering of section control codes in
that menu component, so `0001`/`0002`/`0003`, `0022`/`0023`, and similar style
families become practical tree levels.

Each destination is resolved against the `.IDX` component block ranges. The
`target` object names the component and classifies it as `body`, `menu`,
`title`, `index`, `media-image`, `media-audio`, `gaiji-resource`, or generic
`component`. `relative_offset` is the byte offset inside the expanded target
component.

### `indexes`

Extract lookup keys and pointer rows from expanded `*INDEX.DIC` components:

```bash
logovista-tools indexes /path/to/LogoVista --out-dir indexes
logovista-tools indexes /path/to/LogoVista --dict KOJIEN7 --component FHINDEX.DIC --limit 50
```

Output layout:

```text
indexes/
  summary.json
  DICT_ID/
    indexes_summary.json
    raw_indexes.jsonl
```

Each leaf row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "kind": "leaf",
  "component": "FHINDEX.DIC",
  "page_index": 98,
  "logical_block": 44310,
  "row_index": 12,
  "key": "?ASHURA'",
  "target_key": "?Ashura'",
  "body": {"block": 284, "offset": 1794},
  "title": {"block": 38005, "offset": 334},
  "tagged": true,
  "target_count_hint": 1,
  "continued_group": false
}
```

Useful options:

```bash
--dict NAME                         extract only matching dictionary ids
--component NAME                    extract only matching index filename(s)
--include-internal                  also emit binary-search tree branch rows
--limit N                           stop writing JSONL rows after N rows
--gaiji drop                        omit all unresolved gaiji in keys
--gaiji h-placeholder               keep half-width gaiji placeholders only
--gaiji placeholder                 keep half-width and full-width placeholders
```

### `fulldb`

Extract formatted bodies from LogoVista products that declare a `DictFULLDB`
payload in `DictList.plist`.

```bash
logovista-tools fulldb /path/to/LogoVista --dict KOJIEN7 --out-dir fulldb
```

This command still starts from the raw files:

1. Find a dictionary with `SSEDINFO` / `SSEDDATA`.
2. Expand `HONMON.DIC`.
3. Decode the 32-byte HONMON slots that contain decimal body IDs.
4. Resolve `DictFULLDB` from `DictList.plist`.
5. Emit HTML/plain body rows whose IDs were present in raw HONMON.

By default, this command only follows a `DictFULLDB` path declared by
LogoVista metadata. It does not grab arbitrary neighboring database files.
`--allow-db-fallback` is available for experiments, but it is intentionally
opt-in.

Output layout:

```text
fulldb/
  summary.json
  DICT_ID/
    fulldb_summary.json
    fulldb_entries.jsonl
```

Each JSONL row looks like:

```json
{
  "dict_id": "KOJIEN7",
  "dict_title": "広辞苑 第七版",
  "data_id": 755,
  "record_index": 754,
  "block": 13,
  "offset": 1602,
  "type": 2,
  "title": "アイ‐アイ 【aye-aye】",
  "html": "<rn></rn><a name=\"000007550000\"></a><div class=\"midashi\">...",
  "plain": "アイ‐アイ 【aye-aye】（啼なき声に由来。一説に..."
}
```

## What Works Today

Known working layers:

- `SSEDINFO` `.IDX` parsing.
- `SSEDDATA` `.DIC` expansion.
- Observed Windows LogoFontCipher AES-CBC decryption for encrypted
  `HONMON.DIC` and sidecars, including streaming decrypt for large files.
- EPWING-like component composition.
- JIS X 0208 text decoding.
- Common `0x1f` stream controls for line breaks, headword spans, links,
  menu links, emphasis-ish spans, superscript/subscript, and media/link
  wrappers.
- Dictionary-specific `.uni` gaiji mapping using primary Unicode sequences,
  including UTF-16 surrogate-pair sequences and older 12-byte `.uni` files.
- Plist gaiji fallback mapping when `Gaiji.plist` or `GaijiS.plist` is
  present.
- `GA16HALF` / `GA16FULL` bitmap resource header parsing, glyph slicing, and
  PNG rendering.
- Package image discovery from top-level `img`, Windows `Templates`, Android
  `resource/kmkimges`, `appendix/img`, and `manual/contents/img`, including
  `_n` / `_w` and `_1` / `_3` theme variants.
- Image-backed gaiji preservation as placeholders or inline HTML `<img>` tags.
- `COLSCR.DIC` media pointer decoding and extraction of referenced BMP/JPEG
  image records from raw `HONMON.DIC` media controls.
- `PCMDATA.DIC` audio/media pointer decoding, referenced-record extraction,
  unreferenced sequential-record discovery, and portable WAV/MP3 writing.
- SQL/`DictFULLDB`-assisted gaiji validation reports, including aligned
  `Block`/`Offset` checks where cache tables expose those columns.
- Optional encrypted Windows renderer sidecar use in gaiji validation reports.
- Windows `EXINFO.INI` parsing and CP932 auxiliary text-index tree extraction.
- Windows renderer SQLite extraction through raw HONMON ID anchors and
  `t_contents` rows, with optional `media` BLOB export.
- Android body DB extraction through raw HONMON ID anchors and the observed
  `rowid * 5` mapping, with optional SVG/media BLOB export.
- Standalone `SPINDEX.DIC` inspection for observed Windows suffix-index
  resources.
- Structured `MENU.DIC` extraction with menu hierarchy, link labels,
  packed-BCD destination pointers, and named component/body targets.
- Common `*TITLE.DIC` extraction, including `KWTITLE.DIC` keyword-title
  streams and `CRTITLE.DIC` cross-reference-title streams.
- Common `*INDEX.DIC` branch-page and leaf-row parsing, including forward,
  backward, keyword, and cross-reference indexes.
- Index-derived HONMON body boundaries for entries whose first section is not
  `0001`.
- Raw HONMON/IDX corpus auditing that distinguishes readable body streams,
  mixed readable streams, dense ID/token tables, and `.dbc` outliers without
  reading SQLite body text.
- Placeholder preservation for unresolved gaiji, for example `<hA126>`.
- Full-width ASCII normalization to half-width ASCII.
- Dense HONMON ID-table detection.
- Raw HONMON numeric ID decoding for `DictFULLDB` extraction.

Known limitations:

- Not all dictionaries store definitions in `HONMON.DIC`.
- Some Windows titles store only body IDs in `HONMON.DIC` and put renderer HTML
  in an encrypted SQLite sidecar. In that case raw HONMON remains the anchor
  table, but the body text requires dereferencing the sidecar.
- Not every product that declares `DictFULLDB` has an unreadable `HONMON.DIC`;
  several still have readable raw body streams. Audit the raw layer first.
- `MENU.DIC` destinations are resolved to components, but semantic target
  labels inside the target body/menu stream are still dictionary-specific.
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is still dictionary-specific.
- Output is JSONL, not a final Yomitan/MDict exporter.
- Some control opcodes are recognized only enough to avoid corrupt text.
- `DictFtsDB` `.dbc` payloads such as `OXFPEU4.dbc` are opaque; the observed
  file has no recoverable SSED, SQLite, HTML, or fixed-XOR structure.
- LogoFontCipher support covers the key schedule observed in EJJE200's Windows
  decryptor. Treat unrelated encrypted-looking payloads separately until their
  reader or key schedule is identified.

## Format Deep Dive

This section documents the current reverse-engineered understanding of the
LogoVista/SystemSoft files handled by this project.

### Big Picture

A typical raw dictionary core looks like:

```text
DICT.IDX
HONMON.DIC
KWTITLE.DIC
KWINDEX.DIC
FKTITLE.DIC
FKINDEX.DIC
FHTITLE.DIC
FHINDEX.DIC
BKTITLE.DIC
BKINDEX.DIC
GA16HALF
GA16FULL
DICT.uni
```

That core is the stable part. Platform packages wrap it differently:

```text
iOS       DictList.plist, Gaiji.plist, GaijiS.plist, resourcesCopy.plist,
          gaijiicon.plist, img/, html/, OTHER/, *.sql
Android   *.db, resource/conf.ini, resource/kmkimges/, manual/, innerdata/
Windows   EXINFO.INI, HC*.dll, Templates/, HANREI/, *.chm, vlpljbl*,
          sometimes standalone auxiliary SPINDEX.DIC
```

`Gaiji.plist` and `GaijiS.plist` are therefore not generic LogoVista files.
They are iOS packaging fallbacks observed in some products. Cross-platform
gaiji handling should start from the dictionary-local `.uni`/`.UNI` file and
then use platform-specific image/plist/font assets where present.

The core raw format has two layers:

1. A container/compression layer: `SSEDINFO` + `SSEDDATA`.
2. An expanded dictionary stream layer: EPWING/JIS-like bytes with text,
   controls, gaiji, links, and index records.

The SQLite database, when present, is best understood as an application cache
or search database. It may contain useful full text, but it is not the only
raw dictionary source, and using it alone loses format information.

### `SSEDINFO` `.IDX`

The `.IDX` file is the catalog for the compressed components. It starts with:

```text
offset  size  meaning
0x00    8     ASCII magic: SSEDINFO
0x0c    1     dictionary title byte length
0x0d    var   CP932 dictionary title
0x4d    1     component count
0x80    ...   component records
```

Each component record is `0x30` bytes:

```text
record offset  size  meaning
0x02           1     multi/resource flag
0x03           1     EPWING-ish component type
0x04           4     start logical block, big endian
0x08           4     end logical block, big endian
0x0c           4     component metadata bytes
0x11           var   NUL-terminated ASCII filename
```

The logical block size is 2048 bytes. If a component starts at logical block
`N`, a composed book image places it at:

```text
(N - 1) * 2048
```

Component types observed so far:

```text
0x00  HONMON.DIC body/main text component
0x01  MENU.DIC
0x03  KWTITLE.DIC
0x04  FKTITLE.DIC
0x05  FHTITLE.DIC
0x06  BKTITLE.DIC
0x07  BHTITLE.DIC
0x0a  CRTITLE.DIC
0x70  BKINDEX.DIC
0x71  BHINDEX.DIC
0x80  KWINDEX.DIC
0x81  CRINDEX.DIC
0x90  FKINDEX.DIC
0x91  FHINDEX.DIC
0xd2  COLSCR.DIC media/image resource stream
0xd8  PCMDATA.DIC audio/media resource stream
0xf1  GA16FULL resource
0xf2  GA16HALF resource
```

The exact semantic names vary by dictionary, but the broad pattern is stable:
title components store readable headword/title streams, index components store
binary search data and pointers, and `HONMON.DIC` often stores bodies.

### `SSEDDATA` `.DIC`

Every compressed `.DIC` component starts with:

```text
offset  size  meaning
0x00    8     ASCII magic: SSEDDATA
0x0f    1     component kind/flags, not fully classified
0x16    2     chunk count, big endian
0x18    4     first logical block number, big endian
0x1c    4     last logical block number, big endian
0x40    ...   chunk offset table
```

The chunk offset table has `chunk_count` big-endian 32-bit offsets. Offsets
are from the beginning of the `.DIC` file.

Each compressed chunk starts with two unused/padding bytes, then:

```text
offset  size  meaning
0x02    2     command count, big endian
0x04    1     initial byte used to fill the sliding window
0x05    ...   command stream
```

Each command is three bytes:

```text
byte0, byte1, literal
```

The first two command bytes are split into:

```text
window_offset = (byte0 << 4) | (byte1 >> 4)
copy_length   = byte1 & 0x0f
```

Expansion uses:

```text
window size: 0xff0 bytes
chunk max:   0x8000 bytes
block size:  2048 bytes
```

For every command:

1. Copy `copy_length` bytes from the sliding window into the output.
2. Write `literal` into both the output and the window.
3. Stop a chunk at `0x8000` bytes, or at a 2048-byte boundary for the final
   command of a short final chunk.

This reproduces known expanded `HONMON.DIC` bytes for tested dictionaries.

### Expanded `HONMON.DIC`

After SSED expansion, `HONMON.DIC` is not Shift-JIS and not UTF-8. It is an
EPWING/JIS-like stream.

Text is mostly JIS X 0208 pairs:

```text
0x21..0x7e 0x21..0x7e
```

The decoder wraps these pairs in ISO-2022-JP escape sequences and lets Python
decode the character.

The stream also contains `0x1f` control opcodes. Important controls observed:

```text
1f 02             entry/wrapper start in some streams
1f 03             entry/wrapper end in some streams
1f 04             style or text span start
1f 05             style or text span end
1f 06 / 1f 07     subscript start/end
1f 09 xx xx       entry marker, commonly 1f 09 00 01
1f 0a             line break
1f 0e / 1f 0f     superscript start/end
1f 10 / 1f 11     italic-ish start/end
1f 12 / 1f 13     emphasis-ish start/end
1f 41 xx xx       headword span start
1f 61             headword span end
1f 42             link-ish start
1f 62 ...         link-ish end with payload
1f 4a ...         jump/link/media start with a 16-byte payload
1f 4d ...         media/reference start with an 18-byte payload
1f e0 xx xx       bold-ish start
1f e1             bold-ish end
1f e2 xx xx       color/style start
1f e3             color/style end
```

The current extractor does not claim full semantic knowledge of every control.
It uses enough structure to preserve line breaks and avoid mixing payload bytes
into visible text.

`1f 4a` starts are followed by 16 bytes of binary target metadata before
visible link text resumes. In PCMDATA dictionaries, the same payload encodes a
sound/media start and end range. In HAESPJPN, treating this as a 15-byte
payload leaks one binary byte into the text stream and produces mojibake before
labels such as `→音声1`. `1f 4d` media starts have an 18-byte payload in the
same dictionary family.

### Entry Slicing

Many body streams use this marker near many entry boundaries:

```text
1f 09 00 01
```

A marker-only strategy is insufficient for some body streams. OUKOKU11 real
entries can begin with other `1f09` section codes, including `0008`, `0003`,
`0004`, `0002`, and `1001`. For example, the first two raw body entries in
OUKOKU11 start at:

```text
block 2 offset 2    1f09 0008  あ ア
block 2 offset 146  1f09 0003  あ【亜】【亞】
```

Those entries are discoverable from raw `*INDEX.DIC` body pointers, not from
the `0001` marker scan. The current `entries` command therefore collects body
pointers from parsed index leaf rows, converts them to HONMON-relative byte
offsets, sorts and deduplicates them with marker starts, then slices from each
boundary to the next. `--no-index-boundaries` restores marker-only slicing for
debug comparison.

This works well for dictionaries where `HONMON.DIC` really is a body stream,
including dictionaries such as GENIUSEB, HAESPJPN, and OUKOKU11.

### Dense HONMON Tables

Some products have a large expanded `HONMON.DIC`, but it is not a definition
body stream. Instead it is a dense run of 32-byte records that look like:

```text
1f09 0001 1f41 .... 1f04 [blank JIS cells] 1f05 1f61 1f0a
```

Blank slots contain repeated JIS blank cells (`2121`). Populated slots contain
body IDs in the same span:

```text
1f0a 1f09 0001 1f41 0160 1f04 2330 2330 ... 1f05 1f61
```

Decoded as text, the ID span can be:

```text
00000755
00197570
00851665
```

Those IDs correspond to `DictFULLDB` body rows such as:

```text
00000755 -> アイ‐アイ 【aye-aye】
00197570 -> か・ける 【掛ける・懸ける】
00851665 -> にほん 【日本】
```

The model for these dictionaries is:

- direct HONMON slicing does not recover definitions;
- HONMON is still meaningful as a raw numeric ID/address table;
- `DictList.plist` can name a sibling `DictFULLDB` payload;
- Windows `EXINFO.INI` / renderer sidecars can provide a `t_contents` payload;
- the full body is recovered by following raw HONMON IDs into the appropriate
  payload.

Other dense products use the same 32-byte HONMON structure but store opaque
tokens rather than decimal body IDs. HOUGAKU5 is currently in this class: raw
slices decode as short tokens such as `K0NVOzjh`, not readable legal dictionary
definitions. Those records are still useful as raw linkage evidence, but they
need a separate dereference model.

Observed non-body HONMON dictionaries in the local corpus include:

| Dictionary | Dense raw payload | Lookup/title text available without body DB text |
| --- | --- | --- |
| `HABGESPA` | Numeric ID table | No title components; Spanish keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `HAFRAN` | Numeric ID table | No title components; French keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `HOUGAKU5` | Opaque token table | Index/title linkage exists, but sampled HONMON slices are not definitions. |
| `IWKOKUG8` | Numeric ID table | `*TITLE.DIC` streams expose Japanese lookup titles such as `ああ【嗚呼】`. |
| `JSSAURU2` | Numeric ID table | Index/title linkage exists; sampled HONMON slices are not definitions. |
| `KENROWA` | Numeric ID table | `*TITLE.DIC` streams expose Russian/Japanese lookup titles. |
| `KOJIEN7` | Numeric ID table | `*TITLE.DIC` streams expose Japanese lookup titles; HONMON IDs resolve to `DictFULLDB`. |
| `NANMED20` | Numeric ID table | `*TITLE.DIC` streams expose alias triples such as `見出し|読み|表示見出し`. |
| `DAIJIRN4_WIN` | Numeric ID anchor table | `*TITLE.DIC` streams expose headwords; HONMON IDs resolve to encrypted Windows renderer table `t_contents`. |
| `IWKOKUG8_ANDROID` | Numeric ID anchor table | Raw core matches iOS/Windows; HONMON IDs resolve to Android `IWKOKUG8(Html)` rows by `rowid * 5`. |

For these, the `entries` command skips body extraction by default and reports a
warning in `summary.json`. Try `fulldb` when `DictList.plist` declares
`DictFULLDB`; try `rendererdb` when a Windows package has `EXINFO.INI` and an
encrypted renderer SQLite sidecar such as `vlpljblb`, or when an Android package
has the observed `DICTID(Html)` app body table.

This is why the toolkit keeps the dense raw layer in scope. Even when a
database is required for final body text, raw `HONMON.DIC` and `*INDEX.DIC`
still define the dictionary's body anchors, lookup pointers, title linkage, and
the exact subset/order of IDs that belong to the packaged dictionary.

### HONMON/IDX Corpus Audit

The local `LOGOVISTA_ALL` corpus was audited with raw SSED expansion, raw
index-derived body boundaries, body-slice sampling, 32-byte HONMON record
probing, and title-component probing. SQLite and `DictFULLDB` body text were
not used to decide whether raw HONMON/IDX produced readable body entries.

Valid SSED dictionaries with `HONMON.DIC` fell into these practical groups:

| Group | Dictionaries |
| --- | --- |
| Raw HONMON/IDX gives readable body entries | `Dconci98`, `GENIUS53`, `GENIUSEB`, `HAESPJPN`, `HAIKSAIJ`, `HKKIGAK6`, `IBIO5`, `IPHYCHE5`, `KANJIGN5`, `KENCOLLO`, `KQCOLEXP`, `KQEBHOU`, `KQJCOLLO`, `KQLATINO`, `KQNEWEJ6`, `KQNEWJE5`, `KenE7J5`, `LMEDEJ12`, `MEIKYOU2`, `NIHONSHI`, `NKGORIN2`, `OUKOKU11`, `RDRSP2`, `ROYALEGR`, `Readers3`, `SINMEI7`, `Saitoje`, `ZYAKUKOG` |
| Raw HONMON/IDX exposes IDs, tokens, titles, or search keys, but sampled HONMON bodies are not definitions | `HABGESPA`, `HAFRAN`, `HOUGAKU5`, `IWKOKUG8`, `JSSAURU2`, `KENROWA`, `KOJIEN7`, `NANMED20` |
| Opaque `.dbc` products skipped by default | `KQCMPROS`, `OXFPEU4` |

Several products in the first group still declare SQL or `DictFULLDB` files.
That declaration alone is not enough to classify a dictionary as database-body
only. The raw audit must check the expanded HONMON stream and the raw indexes.
Conversely, the second group proves that some dictionaries need a database or
other payload dereference for final body text, but that does not make HONMON or
IDX irrelevant: they still carry the raw anchor layer.

The body sampler deliberately filters section-only spans, decimal/hex-only ID
records, and short opaque base64-like tokens. Without that filter, dense tables
can appear to contain entries such as `<section:0001>` or `K0NVOzjh`; those are
not coherent dictionary bodies.

### Non-iOS Body Streams

OUKOKU11 is useful because it is an Android-only package, not part of
LogoVista's iOS pipeline. It has no `Gaiji.plist`, `GaijiS.plist`,
`resourcesCopy.plist`, or `gaijiicon.plist`, but the raw `.IDX` / `.DIC`
structure is still compatible with the toolkit.

Observed OUKOKU11 layout:

```text
OUKOKU11.IDX
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
KWTITLE.DIC
KWINDEX.DIC
COLSCR.DIC
GA16FULL
GA16HALF
OUKOKU11.UNI
OUKOKU11.db
OUKOKU11_indexinfo.db
resource/kmkimges/
appendix/img/
manual/contents/img/
```

Important findings:

- Uppercase `.UNI` is enough for primary Unicode gaiji mapping. OUKOKU11 has
  568 usable Unicode mappings and no plist mappings.
- `GA16FULL` and `GA16HALF` are normal bitmap resources. The observed counts
  are 771 full-width glyphs from `B121` and 38 half-width glyphs from `A121`.
- `HONMON.DIC` is a real body stream, not a dense ID table. Expanded size is
  18,020,352 bytes.
- Entry starts are index-defined, not marker-defined. Raw marker count is
  64,453, but index-derived body boundaries produce 82,220 coherent entries.
- The app cache table has 70,375 `Block`/`Offset` rows. It is useful for
  validation, but it is not needed to extract body text.
- `OUKOKU11_indexinfo.db` is metadata, not dictionary body text.

A full raw extraction command:

```bash
logovista-tools entries /path/to/OUKOKU11 --dict OUKOKU11 \
  --section-markers --image-gaiji --html --out-dir out/oukoku
```

Expected high-level summary from the local test copy:

```text
entries_emitted:        82,220
index_entry_boundaries: 82,220
entry_markers:          64,453
image_resource_entries: 167
image_gaiji_entries:    56
unknown_controls:       0
```

### Windows Packages

Windows packages checked directly now include SINMEI7, HAESPJPN, IWKOKUG8,
EJJE200, and DAIJIRN4. They share the same SSED/EPWING-like core as the mobile
packages where matching copies are available, but add Windows app sidecars
around it.

#### SINMEI7 Windows vs iOS

SINMEI7 Windows and SINMEI7 iOS both use the same nine core `SSEDINFO`
components:

```text
HONMON.DIC
MENU.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

Both `HONMON.DIC` files are plain `SSEDDATA`, and raw extraction works without
SQLite. The Windows copy expands to 47,515,648 bytes, has 75,532 entry markers,
and the raw index scan produces 75,529 body boundaries. `MENU.DIC` expands to
8,988,672 bytes and resolves 75,939 menu/body destinations.

Observed platform differences:

- Windows keeps renderer assets in `Templates/`, `HTMLs/`, `HANREI.chm`, and a
  product-specific `HC0135.dll`.
- iOS keeps converted assets in top-level `img/`, `html/`, `OTHER/`, plist
  manifests, app SQL, and `bin/` payloads.
- Windows `EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC0135.dll`, `PCMP3=1`,
  `IDXCOUNT=1`, `IDXINFO0=00000135.idx`, and `IDXTITLE=付録`.
- `00000135.idx` is not `SSEDINFO`; it is CP932 tab-separated appendix metadata.
  Rows contain hex block, hex offset, optional empty/category fields, and a
  display title. The block/offset values point to raw HONMON addresses and to
  decimal-named files in `HTMLs/`, for example `00005a95 00000312` maps to
  `HTMLs/23189-786.html`.
- Windows `Templates/` resources are package images just like iOS `img/`
  resources. The local SINMEI7 Windows copy exposes 203 image/BMP resources and
  29 code-shaped gaiji-image keys after scanning `Templates/`.
- The two `.uni` files have no conflicting values for shared codes. Windows has
  351 usable mappings, iOS has 331. The Windows file contributes extra rare CJK
  and compatibility mappings; the iOS file contributes three radical mappings
  not present in Windows.
- `GA16FULL` and `GA16HALF` are byte-identical across the two copies. `GA16FULL`
  starts at `B221` and has 375 glyph slots; `GA16HALF` has zero glyphs.
- `PCMDATA.DIC` remains parseable on Windows and contains MP3 records referenced
  by raw HONMON controls.

#### HAESPJPN Windows vs iOS

HAESPJPN Windows and HAESPJPN iOS use byte-identical raw dictionary components:

```text
HAESPJPN.IDX
HONMON.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
COLSCR.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

The raw `HONMON.DIC` body stream is therefore fully compatible across the two
copies. Both expand to 27,979,776 bytes, expose 71,913 common entry markers,
and the raw index pass finds 79,904 body boundaries. The `entries` command
extracts coherent bodies from the Windows copy without SQLite; `rendererdb`
correctly ignores `HAESPJPN.db` because it is a conjugation/search cache with no
`t_contents` body table.

The differences are packaging and fallback assets:

- Windows has `EXINFO.INI`, `HC013A.DLL`, `Templates/`, `Panel/`, `Panels.xml`,
  `SPINDEX.DIC`, `HANREI.chm`, HTML help files, and `HAESPJPN.db`.
- Windows `EXINFO.INI` uses the legacy singleton form `IDXINFO=0000013A.idx`
  / `IDXTITLE=インデックス`, rather than `IDXCOUNT` / `IDXINFO0`.
- `0000013A.idx` is a CP932 text tree with virtual selector pointers:
  `10000000/ffff` = `西和ABC順`, `30000000/ffff` = `和西50音順`, and
  `60000000/ffff` = `動詞活用表`.
- Windows image resources live in `Templates/` and include `exam.png`,
  `sound.png`, and 122 code-shaped gaiji image keys.
- iOS image resources live in `img/`; its extra `Gaiji.plist` /
  `GaijiS.plist` fallback mappings raise the observed gaiji map from the
  Windows `.UNI` count of 60 to 97 combined mappings.

This is the cleanest current example of high raw-core compatibility with
platform-specific resource wrappers.

#### IWKOKUG8 iOS vs Android vs Windows

IWKOKUG8 has been checked across iOS, Android, and Windows. The raw core is
byte-identical across all three copies:

```text
IWKOKUG8.IDX
HONMON.DIC
FKTITLE.DIC / FKINDEX.DIC
FHTITLE.DIC / FHINDEX.DIC
BKTITLE.DIC / BKINDEX.DIC
BHTITLE.DIC / BHINDEX.DIC
GA16FULL / GA16HALF
IWKOKUG8.uni / IWKOKUG8.UNI
```

The shared `HONMON.DIC` is not a body stream. It expands to 10,477,568 bytes
and contains 65,480 numeric ID records in the dense 32-byte anchor-table
layout. Raw title/index extraction still works: the `*TITLE.DIC` streams expose
lookup titles, and the index parser finds 65,468 body/index boundary rows.

The platform body payload differs:

| Platform | Body payload | Raw ID relationship |
| --- | --- | --- |
| iOS | `DictFULLDB` SQLite `IWKOKUG8.sql`, table `t_contents` | `f_DataId` matches raw HONMON IDs |
| Android | Plain `IWKOKUG8.db`, table `IWKOKUG8(Html)` | `data_id = rowid * 5` |
| Windows | Encrypted `vlpljblh`, decrypted SQLite table `t_contents` | `f_DataId` matches raw HONMON IDs |

Observed extraction counts:

```text
Raw HONMON ID records:       65,480
iOS t_contents rows:         65,480
Windows t_contents rows:     65,480
Android Html rows:           65,468
Android raw IDs missing:         12
```

Those 12 missing Android rows correspond to the 12 `f_Type=5` rows present in
the Windows/iOS `t_contents` payload. The normal dictionary bodies line up by
raw ID.

The resource wrappers differ:

- iOS uses `img/` and an iOS `DictList.plist` declaration for `DictFULLDB`.
- Android uses `resource/conf.ini`, `resource/kmkimges/`, `manual/`,
  `innerdata/`, and a plain app DB. Its `media` table uses
  `id/name/type/main` columns and stores 345 SVG blobs plus one additional
  media row.
- Windows uses `EXINFO.INI`, `HC02D0.dll`, `Templates/`, `HANREI/`,
  encrypted `vlpljblh`, and two font sidecars: `vlpljblB` is `Noto Sans JP`
  Regular OpenType/CFF, and `vlpljblN` is `Noto Serif JP` Regular OpenType/CFF.
  These font files are not encrypted SQLite sidecars.

The `rendererdb` command handles both body-cache shapes while still starting
from raw HONMON IDs: `t_contents` for Windows renderer DBs and `rowid * 5` for
the Android `Html` table shape.

#### EJJE200 Windows Encryption

EJJE200 is the first observed Windows package with encrypted primary body data.
Its `EXINFO.INI` declares:

```ini
HTML=1
HTMLDLL=HC014F.dll
KWIT=1
IDXINFO0=select.html
ROSQLNAME=EJJE200.db
ENCRYHON=1
```

`HONMON.DIC` does not start with `SSEDDATA` on disk. Static analysis of the
shipped `vlpljbl.bin` shows it is a Crypto++ decryptor using AES-128-CBC
(`Rijndael`, `CBC_Decryption`, `StreamTransformationFilter`). The passphrase is
the obfuscated literal `LogoFontCipher`; each byte is stored XOR `0xff` in the
program. The key schedule is:

```text
digest = SHA256("LogoFontCipher")
AES-128-CBC key = digest[0:16]
AES-CBC IV      = digest[16:32]
key             = a3c48d86dabe8b0c91fb33d9fdf2941b
iv              = 80f2f3736bcec2e51665d02b640edbb0
```

Decrypting `HONMON.DIC` with that key reveals normal `SSEDDATA`:

```text
chunks=4087 start=0x2 end=0xff63 kind=0x0 storage=logofont_cipher
expanded_bytes=133,894,144
entry_markers=1,864,040
index_entry_boundaries=1,864,040
```

Raw entries are coherent without SQLite after decryption:

```text
(mobile)number portability
番号ポータビリティ[情報]

.NET
.NET[情報]
```

`HC014F.dll` is the product HTML renderer. It imports the normal `SSDicLib.dll`
entry/body/gaiji/picture APIs and contains strings for `epwing2HtmlBodydata` and
`pluginFunction`. It also contains the sidecar names `vlpljbl.bin`, `DIC014F`,
and `vlpljblF`, which matches the encrypted sidecar behavior.

`vlpljblF` decrypts with the same LogoFontCipher key to a SQLite database. It is
not the primary body stream. It contains 17 tables named `t_Search_1` through
`t_Search_17`, matching the 17 category checkboxes in `Templates/select.html`
for KWIT partial-match search (`情報`, `電気`, `物理`, ..., `環境`). The table
schema is:

```sql
CREATE TABLE t_Search_N (
  f_type TEXT,
  f_midasi TEXT,
  f_midasi_jis TEXT,
  f_block TEXT,
  f_offset TEXT
);
```

#### DAIJIRN4 Windows Renderer Database

DAIJIRN4 is the first observed Windows package where plain `HONMON.DIC` is not
a definition stream and the full formatted body is in a Windows renderer
database. The core `SSEDINFO` table contains 11 components:

```text
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
GA16FULL
GA16HALF
```

There is no `MENU.DIC`, `PCMDATA.DIC`, or `COLSCR.DIC`. `HONMON.DIC` is plain
`SSEDDATA` and expands to 43,106,304 bytes, but the expanded stream is a dense
run of 32-byte anchor records:

```text
1f0a 1f09 0001 1f41 0160 1f04 [8 JIS cells] 1f05 1f61
```

Every fifth record carries an eight-cell full-width decimal ID, for example
`00000025`; the other four records in that group are blank anchors. The marker
start used by raw indexes is two bytes after the 32-byte record start. Example:
data id `25` is at record offset `768`, block `2`, offset `768`, with the
marker target at block `2`, offset `770`.

The count relationship is exact:

```text
HONMON entry markers:       1,347,035
HONMON ID records:            269,407
t_contents rows:              269,386
DB rows matching raw IDs:      269,386
raw IDs missing in DB:             21  terminal trailer anchors only
```

This is not a failed HONMON parse. It is a raw anchor table. The body payload is
the encrypted sibling `vlpljblb`, and the bundled `vlpljbl.bin` is byte-identical
to the EJJE200 decryptor. Decrypting `vlpljblb` with the LogoFontCipher key
produces a 610,735,616-byte SQLite database with three tables:

```sql
CREATE TABLE t_contents (
  f_DataId INTEGER PRIMARY KEY,
  f_Type INTEGER,
  f_DataGroupId INTEGER,
  f_Anchor TEXT,
  f_Title TEXT,
  f_Title_SS TEXT,
  f_Html TEXT,
  f_Keyword TEXT,
  f_Plane TEXT
);

CREATE TABLE t_bunya (
  f_DataId INTEGER NOT NULL PRIMARY KEY,
  f_GenreKey TEXT,
  f_Title TEXT,
  f_TitleSS TEXT
);

CREATE TABLE media (
  No INTEGER NOT NULL PRIMARY KEY,
  f_name TEXT,
  f_type INTEGER,
  f_main BLOB
);
```

Observed row counts and content types:

```text
t_contents: 269,386 rows
t_bunya:     47,375 rows
media:        2,830 rows

f_Type=1  parent entries                 179,350
f_Type=2  child/sub entries               78,144
f_Type=3  idiom/phrase entries             8,328
f_Type=4  kanji entries                    3,283
f_Type=5  late appendix/search rows          273
f_Type=6  terminal/special rows                8

media f_type=2  PNG appendix images           86
media f_type=3  GIF entry figures          2,744
```

`f_Html` is complete renderer HTML. It contains links such as
`lved.dataid:01346760` and inline figure tags such as
`<img src="3djr_0002.gif" class="media">`; those image names resolve to the
`media.f_name` BLOB table. `f_Plane` is the flattened plain/search body. The
`rendererdb` command emits the HTML/plain rows and can write the `media` BLOBs
as portable image files.

`HC015E.dll` confirms this interpretation. Its strings include
`pluginFunction2nd`, `epwing2HtmlBodydata`, the sidecar name `vlpljblb`, and SQL
queries such as `SELECT f_Html FROM t_contents WHERE f_DataId = ?` and
`SELECT f_name, f_main FROM media WHERE f_name = ?`.

`EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC015E.dll`, `IDXCOUNT=3`,
`IDXINFO0=0000015E.IDX`, `IDXINFO1=select.html`, `IDXINFO2=select2.html`,
`ROSQLNAME=DAIJIRN4.db`, `BUBUNDB=1`, `ZENBUNDB=1`, and `VERTICAL=1`.
`0000015E.IDX` is a CP932 tab-tree with 284 rows. Its first two columns are
hex block/offset pointers into the raw HONMON anchor layer; tab depth defines
labels such as `大辞林 第四版 / 分野別索引 / 季語 / 春`.

DAIJIRN4 gaiji/resources are otherwise normal:

```text
DAIJIRN4.uni: simple12, 92 half records, 1,191 full records, 1,243 mappings
GA16HALF:     8x16, start A121, 92 glyphs
GA16FULL:    16x16, start B121, 1,191 glyphs
Templates:  255 portable resources after PNG/GIF/BMP/SVG discovery
```

#### SPINDEX.DIC

`SPINDEX.DIC` is a standalone Windows auxiliary file, not a component declared
inside the product `.IDX`. The local corpus currently contains four copies:
EJJE200, HAESPJPN Windows, SINMEI7 Windows, and the official Windows browser's
EJJE200 install copy. All four are byte-identical:

```text
sha256 aabd6d909fb7bed5d446192fbbf757d18367ca28fb6d72ad69984a842b1a85b9
size   14349 bytes
```

The file is still an SSED container. Its header starts with `SSEDDATA`, has the
submagic bytes `SPDATA`, reports kind byte `0x54`, and declares 116 compressed
chunks over logical blocks `0xd9c8..0xe101`:

```text
declared logical blocks:     1,850
expected expanded bytes:     3,788,800
physical chunks present:     2 / 116
expanded bytes present:      38,208
complete expanded pages:     18
partial expanded pages:      1
```

The physical file is therefore only a prefix of the declared SSED stream. The
chunk table points to later chunk offsets up to roughly `0x206921`, but the
file ends at `0x380d`. The second physical chunk is incomplete. Generic SSED
expansion should not assume that every offset in this file is backed by bytes.

The bytes that are present decode cleanly as index branch pages using the same
page machinery as `FKINDEX.DIC` internal pages:

```text
root page  logical block 0xd9c8  header word 0x601e  rows 33  slot 34
page 2     logical block 0xd9c9  header word 0x4020  rows 56  slot 36
page 3+    logical block 0xd9ca  header word 0x0020  rows 56  slot 36
```

No leaf/result pages are present in the observed physical file. The 19 parsed
pages contain 1,022 internal rows. Eighteen child links point to pages present
in the physical prefix and 1,004 child links point to missing pages.

The keys are stored backward:

```text
CITEROHPAID       -> DIAPHORETIC
DEZIRECREM        -> MERCERIZED
EPATGNITALUSNI    -> INSULATINGTAPE
GNIFRUSOGE        -> EGOSURFING
TEEHSNOITANIMAXE  -> EXAMINATIONSHEET
```

This strongly indicates suffix/backward-search support. The official
`SSDicLib.dll` also exposes `SDicSupportHore` and `epwing2HtmlSupportHore`
strings, which is consistent with 後方 search support. The rows visible in
`SPINDEX.DIC` are separator/fence keys for internal B-tree pages, not
dictionary hits. Because the observed file has no leaf pages and is identical
across unrelated dictionaries, it should be treated as common auxiliary
LogoVista/SSDicLib suffix-search metadata or a bundled search skeleton. It is
not a product-specific dictionary index and cannot produce body entries by
itself.

### Menu Components

`MENU.DIC` (`0x01`) is an EPWING-style menu/body stream, not an index page
tree. Some products keep it as a one-block stub containing only `1f03` or
`1f02 1f03`; KOJIEN7, OUKOKU11, KANJIGN5, HAFRAN, and KQCOLEXP show this
minimal form in the local corpus.

Other products store readable menu trees. GENIUSEB, HAIKSAIJ, IBIO5, and
NKGORIN2 all contain menu headings, section markers, and destination links.
The common menu link form is:

```text
1f43              menu-link start
...               visible JIS/gaiji label text
1f63              menu-link end
00 00 00 02 0002  packed-BCD block 2, packed-BCD offset 2
```

The destination is carried after the closing control, so a text decoder must
consume those six bytes. If it does not, pointer bytes are mis-decoded as
garbage characters appended to labels. Section markers use the normal
`1f09 xxxx` form; preserving them gives menu levels such as `0001`, `0002`,
and `0003`.

The destination payload is six bytes: four packed-BCD decimal bytes for the
logical block and two packed-BCD decimal bytes for the offset. In GENIUSEB, the
first menu item has payload `00 02 56 78 00 02`, which resolves to block
`25678`, offset `2`; that resolves to `HONMON.DIC` at component-relative
offset `2`. Other GENIUSEB menu items point back into `MENU.DIC` itself.

Some menu streams use the older `1f42 ... 1f62` wrapper instead. In HAIKSAIJ,
many of those labels include a no-op `1f00` immediately after `1f42`; the
parser treats it as wrapper padding and still extracts the label and packed-BCD
destination.

The `menus` command writes:

```text
raw_menus.jsonl   flat menu records with path, links, and destinations
menu_tree.json    nested menu records grouped by inferred section depth
menus_summary.json component-level counts and parser statistics
```

Representative target resolution from the local corpus:

```text
GENIUSEB  destinations=79     resolved=79     target kinds: body=67, menu=12
HAIKSAIJ  destinations=2,667  resolved=2,667  target kinds: body=2,617, menu=50
IBIO5     destinations=65,015 resolved=65,015 target kinds: body=61,082, menu=3,933
NKGORIN2  destinations=10     resolved=10     target kinds: body=9, menu=1
```

### Title Components

`*TITLE.DIC` components frequently contain readable headword/title lines after
SSED expansion. Examples include:

```text
FKTITLE.DIC
FHTITLE.DIC
BKTITLE.DIC
BHTITLE.DIC
CRTITLE.DIC
KWTITLE.DIC
```

The paired title/index roles observed so far are:

```text
0x04 FKTITLE.DIC  title stream for FKINDEX forward tagged lookup rows
0x05 FHTITLE.DIC  title stream for FHINDEX forward simple lookup rows
0x06 BKTITLE.DIC  title stream for BKINDEX backward tagged lookup rows
0x07 BHTITLE.DIC  title stream for BHINDEX backward simple lookup rows
0x03 KWTITLE.DIC  title stream for KWINDEX keyword groups/direct rows
0x0a CRTITLE.DIC  title stream for CRINDEX cross-reference groups/direct rows
```

These are not full definitions, but they are important for search/index
reconstruction. `KWTITLE.DIC` is a normal readable stream; OUKOKU11 has keyword
titles such as `いち【一】`, and NANMED20 has pipe-delimited keyword triples.
In large dictionaries where HONMON is an ID table, title streams can still
contain hundreds of thousands or millions of raw headword/title lines.

### Index Components

`*INDEX.DIC` components are binary search trees over 2048-byte pages. They
contain lookup keys, branch pages, leaf pages, and pointers into body/title
components. Some bytes decode as text if treated naively, but the useful data
comes from parsing the page records.

Common index components:

```text
FKINDEX.DIC
FHINDEX.DIC
BKINDEX.DIC
BHINDEX.DIC
CRINDEX.DIC
KWINDEX.DIC
```

Component types observed in `SSEDINFO`:

```text
0x90  FKINDEX.DIC  forward tagged index
0x91  FHINDEX.DIC  forward simple headword index
0x70  BKINDEX.DIC  backward tagged index
0x71  BHINDEX.DIC  backward simple headword index
0x80  KWINDEX.DIC  keyword index
0x81  CRINDEX.DIC  cross-reference index
```

The toolkit parses the common `FK/FH/BK/BH` page formats, direct and grouped
`KWINDEX` rows, and direct and grouped `CRINDEX` rows. The layouts below were
validated against Japanese, English, Spanish, French, science, medical, and
collocation dictionaries, including HAESPJPN, GENIUSEB, HAFRAN, NANMED20,
OUKOKU11, IPHYCHE5, KENCOLLO, KQJCOLLO, and KOJIEN7.

Representative parser coverage from the local corpus:

```text
HAESPJPN  FK/BK tagged + FH/BH simple indexes   unknown leaf subrecords: 0
GENIUSEB  FH/BH simple indexes                  unknown leaf subrecords: 0
NANMED20  FH/BH simple + KWINDEX grouped        unknown leaf subrecords: 0
OUKOKU11  FK/FH/BK/BH + KWINDEX grouped         unknown leaf subrecords: 0
IPHYCHE5  FK/FH/BK/BH + KWINDEX direct/grouped  unknown leaf subrecords: 0
KENCOLLO  FH/BH + large mixed KWINDEX           unknown leaf subrecords: 0
KQJCOLLO  FK/FH/BK/BH + CRINDEX grouped         unknown leaf subrecords: 0
KOJIEN7   FK/FH/BK/BH + CRINDEX grouped         unknown leaf subrecords: 0
```

#### Index Page Header

Every expanded index page begins with:

```text
offset  size  meaning
0x00    2     page flags / slot-size word, big endian
0x02    2     row or subrecord count, big endian
0x04    ...   page records
```

Pages whose first word has bit `0x8000` clear are branch pages. Pages whose
first word has bit `0x8000` set are leaf pages.

Branch page words observed include:

```text
601c 601e 6020
401e 4020
201e 2020
001e 0020
```

The low bits encode the branch slot size:

```text
slot_size = (page_word & 0x3f) + 4
```

Each branch slot is:

```text
offset  size             meaning
0x00    slot_size - 4    padded JIS key boundary
...     4                child logical block number, big endian
```

The child is a 32-bit logical block number. In small dictionaries the high two
bytes are usually zero, which can make the field look like a 16-bit pointer.
Large dictionaries such as KOJIEN7 require the full 32 bits.

#### Simple Leaf Pages

`FHINDEX.DIC` (`0x91`) and `BHINDEX.DIC` (`0x71`) usually use simple leaf
records:

```text
offset  size     meaning
0x00    1        key byte length
0x01    n        JIS/gaiji key bytes
...     4        body logical block, big endian
...     2        body offset in block, big endian
...     4        title logical block, big endian
...     2        title offset in block, big endian
```

Examples:

```text
HAFRAN FHINDEX  ACCENT -> body 4:1570, title 4:1570
GENIUSEB FHINDEX read-ish keys -> body HONMON blocks, title FHTITLE blocks
KOJIEN7 FHINDEX ?ASHURA' -> body HONMON ID-table anchor, title FHTITLE row
```

If a dictionary has no `*TITLE.DIC`, the title pointer can equal the body
pointer.

#### Tagged Leaf Pages

`FKINDEX.DIC` (`0x90`) and `BKINDEX.DIC` (`0x70`) usually use tagged leaf
subrecords. A search-key group starts with:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    2     target count hint, big endian
0x04    n     JIS/gaiji search key bytes
```

Each following target row starts with:

```text
offset  size  meaning
0x00    1     tag 0xc0
0x01    1     target/display key byte length
0x02    n     JIS/gaiji target key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     title logical block, big endian
...     2     title offset in block, big endian
```

The same search key can have multiple target rows. Page boundaries can occur
inside a group, so the parser carries the current `0x80` search key across
leaf pages when a page begins with a `0xc0` target row.

#### Cross-Reference Leaf Pages

`CRINDEX.DIC` (`0x81`) is used with `CRTITLE.DIC` (`0x0a`). It has two leaf
row forms.

Direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     CRTITLE logical block, big endian
...     2     CRTITLE offset in block, big endian
```

Grouped rows:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji cross-reference key bytes
...     4     CRTITLE logical block, big endian
...     2     CRTITLE offset in block, big endian
```

Following target rows are compact body pointers:

```text
offset  size  meaning
0x00    1     tag 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
```

Page boundaries can occur inside a group, so the parser carries the current
group key, count hint, and `CRTITLE` pointer across leaf pages. KOJIEN7 and
KQJCOLLO both parse with no unknown leaf bytes under this model.

#### Keyword Leaf Pages

`KWINDEX.DIC` (`0x80`) is used with `KWTITLE.DIC` (`0x03`). It has direct rows,
grouped rows, and continuation target pages.

Direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji keyword bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     KWTITLE logical block, big endian
...     2     KWTITLE offset in block, big endian
```

Grouped rows:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji keyword bytes
...     4     KWTITLE logical block, big endian
...     2     KWTITLE offset in block, big endian
```

Following target rows are seven bytes:

```text
offset  size  meaning
0x00    1     tag 0xb0 or 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
```

The grouped target rows do not carry their own title pointer; the surrounding
group's `KWTITLE` pointer applies to each target. IPHYCHE5 uses many direct
keyword rows, OUKOKU11 and NANMED20 use grouped keyword rows, and KENCOLLO uses
a mix of both.

Direct index parsing is useful for:

- deriving all exact lookup keys without SQLite;
- pairing title lines with body addresses;
- reconstructing aliases and subentries;
- resolving dense-HONMON ID dictionaries cleanly where possible.

Raw-only probes confirm that indexes can expose useful lookup strings even
when no `*TITLE.DIC` component is present. For example, `HABGESPA` exposes
Spanish keys in `FHINDEX.DIC` / `BHINDEX.DIC`, and `HAFRAN` exposes French
keys in the same forward/backward index components. Those decoded strings are
not full body entries; they are search keys and pointers into the body/title
layer.

### Gaiji

Gaiji are dictionary-specific characters and formatting markers. A gaiji code
is not globally meaningful. The same code can map to different Unicode text in
different dictionaries:

| Code | `HAESPJPN` | `GENIUSEB` | `KOJIEN7` | `HAFRAN` | `OUKOKU11` |
| --- | --- | --- | --- | --- | --- |
| `A126` | `é` | `ɑ̃` | `Ö` | `é` | `ä` |
| `A138` | `ñ` | `ō` | `ñ` | `⑥` | `ŋ` |

The extractor therefore builds a gaiji profile per dictionary. Do not use a
global replacement table such as `A126 = é`.

Observed resources include:

```text
GA16HALF
GA16FULL
GAI16H*
GAI16F*
*.uni
*.UNI
image/icon folders
iOS Gaiji.plist / GaijiS.plist fallback files
```

The current extractor:

- loads primary Unicode mappings from dictionary-local `.uni` / `.UNI` files;
- uses `Gaiji.plist` and `GaijiS.plist` only as fallbacks for codes missing
  from `.uni`;
- parses `GA16HALF` and `GA16FULL` headers, slices individual bitmap glyph
  records, and renders them to transparent PNG files;
- discovers PNG-backed gaiji from package image folders;
- emits unresolved half-width gaiji as `<hXXXX>` by default;
- can emit all unresolved gaiji as placeholders with `--gaiji placeholder`;
- can drop unresolved gaiji with `--gaiji drop`.

For conversion work, keeping unresolved placeholders is usually better than
dropping. It lets a later gaiji pass replace `<hXXXX>` / `<zXXXX>` with images
if the character has no Unicode equivalent.

#### Image Resources

Some dictionaries ship ready-made PNG resources outside `HONMON.DIC`. The iOS
HAESPJPN package has a top-level `img` directory with files such as:

```text
img/b13d_n.png
img/b13d_w.png
img/b13e_n.png
img/b13e_w.png
img/exam.png
```

The `_n` and `_w` suffixes are theme variants of the same asset. In HAESPJPN,
`b13d_n.png` is the black-theme image for the `B13D` full-width gaiji, and
`b13d_w.png` is the corresponding white-theme image. The body stream can refer
to that asset by the gaiji bytes `b1 3d`; the extractor can preserve it as
`<img:b13d>` or emit inline HTML:

```html
<img src="img/b13d_n.png" alt="b13d" class="lv-gaiji lv-gaiji-b13d">
```

Package metadata helps classify these resources:

```text
resourcesCopy.plist  complete-ish list of PNG resources to copy into the app package
gaijiicon.plist      keys that the app treats as gaiji/icon resources
```

OUKOKU11 is an Android-only layout with no plist manifests. Its image assets are
still local and usable:

```text
resource/kmkimges/b167_1.png
resource/kmkimges/b167_3.png
appendix/img/10_00.gif
manual/contents/img/rei.png
```

The `_1` and `_3` suffixes behave like theme variants, with `_1` used as the
normal/dark-on-transparent asset and `_3` used as the white/light-on-transparent
asset. Because no plist identifies gaiji icons, the toolkit classifies
code-shaped filenames such as `b167` as image-backed gaiji and keeps named
resources such as `rei` or `waka` as ordinary package images.

Windows dictionaries can also keep renderer assets in `Templates/`. In SINMEI7
Windows this directory contains named images such as `exam.png`, numbered
appendix images, BMP panels, and code-shaped gaiji images such as `B222.png`.
The resource scanner treats those as package resources and reports BMP files as
well as PNG/GIF/JPEG files.

Named images such as `exam.png` are not necessarily referenced by filename in
`HONMON.DIC`. They may be style resources used by the app for semantic regions
such as examples. The toolkit reports these resources, while the exporter or a
dictionary-specific style layer decides when to insert them. The `entries`
command supports explicit section-image rules such as `--section-image
0011=exam`; this preserves the raw section marker and inserts the named image
in `body_html`.

#### `COLSCR.DIC` Media Resources

`COLSCR.DIC` is a compressed SSED component, usually listed as component type
`0xd2` in `SSEDINFO`. It stores larger inline media used by the body stream.
The body does not name these images by filename. Instead, `HONMON.DIC` contains
`1f 4d` media controls with an 18-byte binary payload.

The useful pointer is encoded in the final six bytes of that payload:

```text
payload bytes 12..15  target logical block, packed BCD decimal
payload bytes 16..17  target offset in block, packed BCD decimal
```

For example:

```text
000000000000000000000000000176490030
```

decodes to logical block `17649`, offset `30`. In OUKOKU11, that is exactly
inside the `COLSCR.DIC` block range.

The pointed record has a simple wrapper:

```text
offset  size  meaning
0x00    4     ASCII magic: data
0x04    4     image payload size, little endian
0x08    n     image payload
```

The payload is not one fixed format. Verified examples include:

| Dictionary | Media refs | Valid records | Payload formats |
| --- | ---: | ---: | --- |
| `GENIUSEB` | 1,081 | 1,081 | BMP, 8 bpp; includes RLE8-compressed BMPs |
| `HAESPJPN` | 274 | 274 | BMP, 1 bpp |
| `IBIO5` | 1,324 | 1,324 | JPEG/JFIF |
| `IPHYCHE5` | 4,189 | 4,189 | BMP, 8 bpp |
| `KANJIGN5` | 952 | 952 | BMP, 8 bpp |
| `KenE7J5` | 96 | 96 | BMP, 8 bpp |
| `NKGORIN2` | 28,841 | 28,841 | BMP, mixed 8/24/1 bpp |
| `OUKOKU11` | 2,579 | 2,579 | BMP, 24 bpp |

For OUKOKU11 specifically, all raw media references resolve to strict
`data`-wrapped BMP records. Section code `0200` corresponds to `:筆順`
stroke-order images and section code `0201` corresponds to `:図版` figure
images. A strict scan of expanded `COLSCR.DIC` finds the same 2,579 records as
the `HONMON.DIC` media controls, with no unreferenced records under the current
parser.

#### `PCMDATA.DIC` Audio/Media Resources

`PCMDATA.DIC` is a compressed SSED component, usually listed as component type
`0xd8`. It is a sequential media store used mainly for audio. The first
expanded 2048-byte block is a small directory/header area. In all currently
tested dictionaries it starts with:

```text
0108 0000 ... 0000
```

and then contains 16-byte directory-looking rows such as:

```text
0000 0e00 0000 0002 0000 0000 0000 0000
0001 0e00 0000 0002 0000 0000 0000 0000
```

The exact purpose of this first block is still not classified, but actual
media records begin at expanded offset `2048`.

Raw `HONMON.DIC` references use `1f 4a` with a 16-byte payload. The same
control may render visible text such as `→音声1` until the closing `1f 6a`.
The useful pointer fields are:

```text
payload bytes 0..1    kind, observed 0001
payload bytes 2..3    flags, dictionary dependent
payload bytes 4..7    start logical block, packed BCD decimal
payload bytes 8..9    start offset in block, packed BCD decimal
payload bytes 10..13  end logical block, packed BCD decimal
payload bytes 14..15  end offset in block, packed BCD decimal
```

For example:

```text
00010000000231930000000231991579
```

decodes to start block `23193`, offset `0`, end block `23199`, offset `1579`.
In HAESPJPN this points at the first audio record and the visible label is
`→音声1`.

The pointed record formats observed so far are:

```text
fmt  + data
fmt  + fact + data
ID3/MP3 payload
```

The `fmt `/`data` records are RIFF/WAVE chunks without the outer `RIFF` and
`WAVE` wrapper. They are followed by a 12-byte zero trailer. For portable
export, the toolkit wraps PCM chunks into a standard WAVE file. If the WAVE
format tag is `0x0055` (`MPEG Layer III`), the toolkit writes the `data` chunk
as `.mp3` instead. Native `ID3`/MP3 records are written directly as `.mp3`.

Verified examples:

| Dictionary | HONMON refs | Unique refs | Unreferenced records | Payload formats |
| --- | ---: | ---: | ---: | --- |
| `HAESPJPN` | 9,996 | 9,996 | 10 | PCM WAVE chunks, mono 16 kHz, 16-bit |
| `KenE7J5` | 14,811 | 14,776 | 525 | PCM WAVE chunks, mono 11.025 kHz, 8-bit |
| `GENIUS53` | 105,805 | 100,835 | 2 | Native ID3/MP3 records |
| `RDRSP2` | 14,050 | 13,995 | 0 | MPEG Layer III stored inside WAVE chunks |
| `Readers3` | 14,050 | 13,995 | 0 | MPEG Layer III stored inside WAVE chunks |
| `ROYALEGR` | 295 | 295 | 0 | PCM WAVE chunks |
| `SINMEI7` | 84,372 | 68,437 | 0 | Native ID3/MP3 records |

The unreferenced count matters: `PCMDATA.DIC` is not merely a lookup table for
HONMON references. It is a sequential media store, and some records can exist
between referenced ranges. The `pcmdata` command therefore reports both
HONMON-referenced records and unreferenced records found in nonzero gaps.

#### `.uni` Files

`.uni` files are dictionary-specific gaiji mapping tables. They are not a
universal character map, and they are not all the same container layout.

Two layouts are currently supported.

`Ver2` layout:

```text
offset  size  meaning
0x00    6     ASCII magic: "Ver2  "
0x06    4     half-width gaiji record count, big endian
0x0a    ...   half-width records, 16 bytes each
...     4     full-width gaiji record count, big endian
...     ...   full-width records, 16 bytes each
```

Simple 12-byte layout:

```text
offset  size  meaning
0x00    4     half-width gaiji record count, big endian
0x04    ...   half-width records, 12 bytes each
...     4     full-width gaiji record count, big endian
...     ...   full-width records, 12 bytes each
```

The simple layout appears in at least `IWKOKUG8` and `KENROWA`. `IWKOKUG8`
uses a zero half count followed by full-width records.

Each `Ver2` 16-byte record is eight big-endian 16-bit fields:

```text
field  meaning
0      gaiji code, for example A126 or B121
1      metadata/flags or glyph metadata, not fully classified
2..3   display Unicode sequence
4..5   fallback/search Unicode sequence
6..7   legacy/alternate fields, not reliable as display text
```

Each simple 12-byte record is six big-endian 16-bit fields:

```text
field  meaning
0      gaiji code
1      metadata/flags or glyph metadata, not fully classified
2..3   display Unicode sequence
4..5   fallback/search Unicode sequence
```

The display sequence can be:

- one BMP codepoint, for example `00E9` -> `é`;
- a base character plus combining mark, for example `0075 032F` -> `u̯`;
- a UTF-16 surrogate pair, for example `D834 DD10` -> `U+1D110`.

The toolkit now combines valid surrogate pairs and ignores lone surrogate
code units. Older builds skipped all surrogate code units, which lost
supplementary-plane gaiji such as musical symbols and rare CJK characters.

Fields `4..5` behave like search/fallback text in dictionaries where they are
populated. For example, GENIUSEB maps `ɑ́` to fallback `a`, and KenE7J5 maps
`Á` to fallback `A`. These are useful for lookup normalization, but should not
replace the display sequence.

Fields `6..7` in `Ver2` are not a second display mapping. They often contain
legacy glyph codes or alternate values. Some look superficially useful
(`é` records often carry `É`), while others decode to control characters,
radical forms, or unrelated symbols. Treat them as diagnostic metadata until a
specific dictionary proves otherwise.

Some `Ver2` files contain duplicate gaiji codes across the half and full
sections. `GENIUSEB.uni`, for example, has both half and full records for
`A121`, with different display text. The current flattened map preserves the
previous toolkit behavior: later records override earlier records. The richer
record parser keeps section information so exporters can make a more precise
choice later.

Corpus summary from the local test collection:

```text
Ver2 files:    GENIUSEB, HAESPJPN, HAFRAN, KOJIEN7, and most others
simple12 files: IWKOKUG8, KENROWA
```

Representative inspector output:

```text
logovista-tools uni GENIUSEB.uni --limit 2
format: ver2
records: 854 half=384 full=470 mapped=430 fallback=190 legacy=311 metadata=542
half A121 meta=0000 display='á' fallback='a' legacy='Â'

logovista-tools uni KENROWA.uni --limit 2
format: simple12
records: 376 half=97 full=279 mapped=199 fallback=0 legacy=0 metadata=49

logovista-tools uni OUKOKU11.UNI --limit 2
format: ver2
records: 894 half=48 full=846 mapped=568 fallback=0 legacy=537 metadata=479
```

#### `GA16HALF` / `GA16FULL`

`GA16HALF` and `GA16FULL` contain bitmap glyphs. The observed header is:

```text
offset  size  meaning
0x08    1     glyph width
0x09    1     glyph height
0x0a    2     first gaiji code, big endian
0x0c    2     glyph count, big endian
0x800   ...   bitmap data
```

Typical values:

```text
GA16HALF  width=8   height=16  glyph bytes=16
GA16FULL  width=16  height=16  glyph bytes=32
```

Bitmap data is a dense run of 1bpp glyphs:

```text
glyph_offset = 0x800 + (code - first_code) * glyph_bytes
row_bytes    = ceil(width / 8)
glyph_bytes  = row_bytes * height
```

Rows are stored top-to-bottom. Bits inside each row are MSB-first, and set bits
are ink pixels. In HAESPJPN, this renders `A126` as `é` and `A138` as `ñ`,
matching EBWin and the `.uni` text mapping.

Some resources are valid headers with zero glyphs. For example, an empty
`GA16HALF` may declare width, height, and start code but have count `0`. The
toolkit reports these cleanly and emits no PNGs.

The `ga16` command converts bitmap glyphs to transparent RGBA PNGs. With
single-variant output, names use the resource kind as a prefix:

```text
hA126.png  half-width resource glyph A126
zB121.png  full-width resource glyph B121
```

With `--variants`, the command writes LogoVista-style theme pairs:

```text
hA126_n.png  black ink on transparent background
hA126_w.png  white ink on transparent background
```

These images can be copied into Yomitan, MDict, or HTML output packages and
referenced with normal inline `<img>` tags for gaiji that have no usable
Unicode mapping.

#### SQL/DictFULLDB Validation

SQL and `DictFULLDB` payloads are useful for validating gaiji mappings, but
they are evidence, not the primary source. The toolkit uses them in two ways.

First, it scans readable SQLite text columns and counts occurrences of
informative mapped display strings from `.uni` / plist resources. Single ASCII
characters are skipped for validation because a match on `a`, `/`, or `-` is
too ambiguous to prove anything.

Second, when a cache table has `Block` and `Offset` columns, it builds an
aligned row index. Raw `HONMON.DIC` entry slices are matched back to cache rows
by block/offset, with a small byte tolerance because some app caches point just
inside or just before the visible entry wrapper. If a raw entry contains gaiji
`A126`, `.uni` maps `A126` to `é`, and the aligned cache row contains `é`, the
code receives an aligned validation hit.

Aligned validation uses the same index-derived body boundaries as `entries`.
This matters for OUKOKU11: marker-only slicing sees 64,453 `1f09 0001`
boundaries, but parsed indexes expose 82,220 body boundaries. With index
boundaries enabled, a 10,000-entry validation pass produced 7,996 aligned
gaiji hits and only 8 mapped misses; marker-only slicing missed many legitimate
non-`0001` entries.

The report status values are:

```text
validated_aligned                strongest evidence; raw pointer and SQL text agree
db_text_evidence                 mapped display appears in SQL, but not via aligned row
mapped_no_db_evidence            raw/mapped code exists, SQL did not confirm it
mapped_unvalidated_uninformative mapped text is too ambiguous for SQL validation
mapped_unused_in_raw_scan        mapping exists, but raw HONMON/TITLE scans did not see it
image_asset_only                 no Unicode map, but package PNG gaiji exists
bitmap_asset_only                no Unicode map, but GA16 bitmap exists
unresolved                       raw code has no Unicode, image, or bitmap coverage
```

This distinction matters. HAESPJPN produces strong aligned evidence for accent
gaiji such as `A126` -> `é` and `A138` -> `ñ`. GENIUSEB, however, shows that
some SQLite cache text is normalized: many IPA/symbol mappings validate, while
some accent display forms from `.uni` do not appear in the cache. The correct
reaction is to flag that discrepancy, not to overwrite `.uni`.

Some sibling SQLite files are not useful validation sources. Android
`android_metadata` tables and standalone `*_indexinfo.db` metadata tables are
skipped so locale/index metadata does not pollute text evidence.

### DictFULLDB Payloads

Many LogoVista products include `.db` or `.sql` files. Some are app search
caches; some are explicitly declared by `DictList.plist` as `DictFULLDB`.
KOJIEN7 is in the second category, and its declared body payload happens to be
SQLite.

Practical interpretation:

- Database files may be app/mobile search caches.
- `DictFULLDB` is the declared full body payload for some products.
- It may flatten or normalize formatting.
- It may also contain formatted HTML with `div`, `sub`, `object`, `img`, and
  `lved.dataid:` links.
- It is useful for validation, fallback, pointer discovery, and full-body
  extraction when raw HONMON stores IDs instead of definitions.

The `entries` and `titles` commands do not read SQLite. The `fulldb` command
does, but only after decoding body IDs from raw HONMON records. The
`gaiji-report` command reads SQLite as an auxiliary validation source and keeps
that evidence separate from the raw `.DIC`/`.IDX` extraction path.

Windows renderer DBs and the observed Android body DB shape are related but not
the same declaration mechanism. They are not `DictFULLDB` entries in
`DictList.plist`; they are platform body/render caches. The toolkit still treats
them as raw-ID-assisted body sources, not as replacements for raw parsing:
`rendererdb` first decodes dense HONMON IDs and then accepts only DB rows that
match those raw IDs.

### Outliers

`OXFPEU4` declares `DictFtsDB` rather than `DictFULLDB`. Its SSED side contains
only tiny stub data, and `OXFPEU4.dbc` is an opaque 2048-byte-block payload.
Observed properties:

- size is exactly `7782 * 2048` bytes;
- entropy is effectively maximum at `7.999987` bits per byte;
- no SQLite, SSED, ZIP, gzip, zlib, HTML, or EPWING marker is present;
- no repeated 16-byte blocks were observed;
- fixed XOR and short-period mask probes did not reveal plaintext.

Treat this class as encrypted or otherwise cryptographically packed until a
reader implementation or documented key schedule is available.

## Roadmap

Near-term:

- Preserve a richer structured AST instead of emitting only plain body text.
- Dereference resolved `MENU.DIC` targets into preview snippets where practical.
- Add higher-level semantic labels for dictionary-specific section codes and
  named images.
- Link title streams to body IDs for dense-HONMON dictionaries.

Exporters:

- Yomitan structured v3.
- HTML/Markdown debug output.
- Lossless JSON IR with spans, gaiji tokens, links, and media references.

Longer-term:

- Dictionary-specific compatibility profiles.
- Better subentry handling for English dictionaries.
- Outlier payload research for `.dbc` products.

## Legal Notes

This repository contains code only. Do not commit proprietary dictionary data,
expanded book images, generated JSONL extractions, or gaiji image assets.

You are responsible for ensuring that any dictionary extraction or conversion
you perform complies with the licenses and laws that apply to your dictionary
files.
