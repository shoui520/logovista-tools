# logovista-tools

Raw-first tools for inspecting and extracting data from LogoVista/SystemSoft
SSED dictionary packages.

This project exists because many LogoVista dictionaries are only *EPWING-like*.
They often ship a friendly SQLite cache for app search, but the real packaged
dictionary assets are the proprietary-looking `.IDX` / `.DIC` files. These
tools work from those raw files first.

Current status: alpha, useful for research and bulk extraction. It can already
decompress SSED data, compose EPWING-like book images, extract readable
`HONMON.DIC` body entries for many dictionaries, extract raw title/headword
streams from `*TITLE.DIC`, and follow raw HONMON numeric ID records into
LogoVista `DictFULLDB` body payloads for products such as KOJIEN7 and other
dense-HONMON dictionaries. It also parses the common `*INDEX.DIC` search-tree
formats, emits raw lookup keys with body/title pointers, and discovers
dictionary-specific image resources used for image-backed gaiji and inline
badges. It can also decode `COLSCR.DIC` media pointers and extract referenced
BMP/JPEG records used by inline figures and stroke-order panels. For
body-stream dictionaries, it can also use raw index body pointers
as additional entry boundaries, which is required for packages whose real
entries do not all start with the common `1f09 0001` marker.

No dictionary data is included in this repository.

## Install

Use Python 3.10 or newer.

```bash
git clone https://github.com/shoui520/logovista-tools.git
cd logovista-tools
python -m pip install -e .
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

Compose an EPWING-like book image from an `.IDX` and its sibling `.DIC` files:

```bash
logovista-tools compose /path/to/DICT/DICT.IDX expanded-book.bin
```

Extract readable `HONMON.DIC` bodies as JSONL:

```bash
logovista-tools entries /path/to/LogoVista --out-dir out/bodies
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

Extract raw title/headword streams:

```bash
logovista-tools titles /path/to/LogoVista --out-dir out/titles
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
filenames. For `.DIC`, this prints the SSED chunk count and declared logical
block range.

### `expand`

Decompress one `SSEDDATA` file:

```bash
logovista-tools expand HONMON.DIC honmon.expanded
```

The output is the expanded EPWING/JIS-style byte stream for that component.

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

### `resources`

Discover package image resources.

```bash
logovista-tools resources /path/to/LogoVista --dict HAESPJPN
logovista-tools resources /path/to/LogoVista --dict HAESPJPN --json
```

LogoVista packages often include a top-level `img` directory plus
`resourcesCopy.plist` and `gaijiicon.plist`. Android/Windows-only packages may
omit those plist manifests and put images in `resource/kmkimges`,
`appendix/img`, or `manual/contents/img`. The resource scanner checks all of
those locations, groups theme variants such as `b13d_n.png` / `b13d_w.png` and
Android-style `b167_1.png` / `b167_3.png`, and reports code-like resources such
as `b13d` or `b167` as image-backed gaiji. Named images such as `exam.png`,
`esp.png`, or `jpn.png` are reported as package resources for format exporters
to use when reconstructing dictionary-specific styling.

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

### `gaiji-report`

Write per-dictionary gaiji audit reports.

```bash
logovista-tools gaiji-report /path/to/LogoVista --dict HAESPJPN --out-dir gaiji-report
logovista-tools gaiji-report /path/to/LogoVista --dict KOJIEN7 --no-sql-cache --max-sql-rows 1000
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
- aligned validation for cache tables that expose `Block` and `Offset`.

Useful options:

```bash
--dict NAME                         inspect only matching dictionary ids
--no-sql-cache                      use declared DictFULLDB only
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
- EPWING-like component composition.
- JIS X 0208 text decoding.
- Common `0x1f` stream controls for line breaks, headword spans, links,
  emphasis-ish spans, superscript/subscript, and media/link wrappers.
- Dictionary-specific `.uni` gaiji mapping using primary Unicode sequences,
  including UTF-16 surrogate-pair sequences and older 12-byte `.uni` files.
- Plist gaiji fallback mapping when `Gaiji.plist` or `GaijiS.plist` is
  present.
- `GA16HALF` / `GA16FULL` bitmap resource header parsing, glyph slicing, and
  PNG rendering.
- Package image discovery from top-level `img`, Android `resource/kmkimges`,
  `appendix/img`, and `manual/contents/img`, including `_n` / `_w` and
  `_1` / `_3` theme variants.
- Image-backed gaiji preservation as placeholders or inline HTML `<img>` tags.
- `COLSCR.DIC` media pointer decoding and extraction of referenced BMP/JPEG
  image records from raw `HONMON.DIC` media controls.
- SQL/`DictFULLDB`-assisted gaiji validation reports, including aligned
  `Block`/`Offset` checks where cache tables expose those columns.
- Common `*INDEX.DIC` branch-page and leaf-row parsing, including type `0x80`
  keyword indexes observed in OUKOKU11.
- Index-derived HONMON body boundaries for entries whose first section is not
  `0001`.
- Placeholder preservation for unresolved gaiji, for example `<hA126>`.
- Full-width ASCII normalization to half-width ASCII.
- Dense HONMON ID-table detection.
- Raw HONMON numeric ID decoding for `DictFULLDB` extraction.

Known limitations:

- Not all dictionaries store definitions in `HONMON.DIC`.
- `KWINDEX.DIC` type `0x80` leaf targets are parsed as body pointers, but the
  higher-level keyword semantics are not fully classified yet.
- `CRINDEX.DIC` primary rows are parsed, but auxiliary `0xc0` subrows are
  skipped because their payload is not the same body/title pointer format.
- Named UI/style images such as `exam.png` are discovered, but mapping them to
  semantic entry regions is still dictionary-specific.
- Output is JSONL, not a final Yomitan/MDict exporter.
- Some control opcodes are recognized only enough to avoid corrupt text.
- `DictFtsDB` `.dbc` payloads such as `OXFPEU4.dbc` are opaque; the observed
  file has no recoverable SSED, SQLite, HTML, or fixed-XOR structure.

## Format Deep Dive

This section documents the current reverse-engineered understanding of the
LogoVista/SystemSoft files handled by this project.

### Big Picture

A typical dictionary directory looks like:

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
Gaiji.plist
GaijiS.plist
DICT.db or DICT.sql
```

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
1f 4a ...         jump/link start with a 16-byte payload
1f 4d ...         media/reference start with an 18-byte payload
1f e0 xx xx       bold-ish start
1f e1             bold-ish end
1f e2 xx xx       color/style start
1f e3             color/style end
```

The current extractor does not claim full semantic knowledge of every control.
It uses enough structure to preserve line breaks and avoid mixing payload bytes
into visible text.

`1f 4a` link starts are followed by 16 bytes of binary target metadata before
visible link text resumes. In HAESPJPN, treating this as a 15-byte payload
leaks one binary byte into the text stream and produces mojibake before labels
such as `→音声1`. `1f 4d` media starts have an 18-byte payload in the same
dictionary family.

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

### Dense HONMON ID Tables

Some products have a large expanded `HONMON.DIC`, but it is not a definition
body stream. Instead it is a dense run of 32-byte records that look like:

```text
1f09 0001 1f41 .... 1f04 [blank JIS cells] 1f05 1f61 1f0a
```

Blank slots contain repeated JIS blank cells (`2121`). Populated slots contain
body IDs in the same span:

```text
1f0a 1f09 0001 1f41 0160 1f04 3330 3330 ... 1f05 1f61
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
- the full body is recovered by following raw HONMON IDs into that payload.

Observed dense-HONMON dictionaries include:

| Dictionary | Raw HONMON ID slots observed | Lookup/title text available without `DictFULLDB` |
| --- | ---: | --- |
| `HABGESPA` | 109,753 | No title components; Spanish keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `HAFRAN` | 7,892 | No title components; French keys are visible in `FHINDEX.DIC` / `BHINDEX.DIC`. |
| `IWKOKUG8` | 65,480 | `*TITLE.DIC` streams expose Japanese lookup titles such as `ああ【嗚呼】`. |
| `KENROWA` | 160,616 | `*TITLE.DIC` streams expose Russian/Japanese lookup titles. |
| `KOJIEN7` | 300,000 | `*TITLE.DIC` streams expose Japanese lookup titles; HONMON IDs resolve to `DictFULLDB`. |
| `NANMED20` | 38,976 | `*TITLE.DIC` streams expose alias triples such as `見出し|読み|表示見出し`. |

For these, the `entries` command skips body extraction by default and reports a
warning in `summary.json`. Try `fulldb` when `DictList.plist` declares
`DictFULLDB`.

### Android/Windows Body Streams

OUKOKU11 is useful because it was not packaged for LogoVista's iOS pipeline.
It has no `Gaiji.plist`, `GaijiS.plist`, `resourcesCopy.plist`, or
`gaijiicon.plist`, but the raw `.IDX` / `.DIC` structure is still compatible
with the toolkit.

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

These are not full definitions, but they are important for search/index
reconstruction. In large dictionaries where HONMON is an ID table,
title streams can still contain hundreds of thousands or millions of raw
headword/title lines.

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

The toolkit parses the common `FK/FH/BK/BH` page formats, the OUKOKU-style
`KWINDEX` page format, and the primary `CRINDEX` rows.

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

`CRINDEX.DIC` (`0x81`) uses a related but different format. Primary rows are:

```text
offset  size  meaning
0x00    2     key byte length, big endian
0x02    n     JIS/gaiji key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     title logical block, big endian
...     2     title offset in block, big endian
```

KOJIEN7 also has auxiliary `0x80` / `0xc0` CR subrecords. The `0x80` rows look
like grouped keys, but the following `0xc0` payloads do not behave like normal
6-byte body pointers and can exceed the book's logical block range if decoded
that way. The current parser counts these groups and skips the auxiliary
targets until their payload is understood.

#### Keyword Leaf Pages

`KWINDEX.DIC` (`0x80`) is present in OUKOKU11 and uses a compact grouped leaf
format. A group starts with:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji keyword bytes
```

Following target rows are seven bytes:

```text
offset  size  meaning
0x00    1     tag 0xb0 or 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
```

Unlike `FKINDEX`/`FHINDEX`, these rows do not carry a separate title pointer.
The toolkit emits the body pointer as both `body` and `title` in JSONL so the
row shape stays compatible with other index output. OUKOKU11's `KWINDEX.DIC`
has 405 pages, 57 keyword groups, and 2,211 target rows with no unknown leaf
bytes under this parser.

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
Gaiji.plist
GaijiS.plist
GA16HALF
GA16FULL
GAI16H*
GAI16F*
*.uni
*.UNI
image/icon folders
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

Some dictionaries ship ready-made PNG resources outside `HONMON.DIC`. HAESPJPN
has a top-level `img` directory with files such as:

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

OUKOKU11 is an Android/Windows-only layout with no plist manifests. Its image
assets are still local and usable:

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

The local collection has `IBIO5`; `IBIOS5` was not found. If another package
uses the `IBIOS5` id, it should be tested separately.

For OUKOKU11 specifically, all raw media references resolve to strict
`data`-wrapped BMP records. Section code `0200` corresponds to `:筆順`
stroke-order images and section code `0201` corresponds to `:図版` figure
images. A strict scan of expanded `COLSCR.DIC` finds the same 2,579 records as
the `HONMON.DIC` media controls, with no unreferenced records under the current
parser.

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
- Classify the skipped auxiliary `CRINDEX.DIC` subrecords.
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
- Image/media extraction.
- Outlier payload research for `.dbc` products.

## Legal Notes

This repository contains code only. Do not commit proprietary dictionary data,
expanded book images, generated JSONL extractions, or gaiji image assets.

You are responsible for ensuring that any dictionary extraction or conversion
you perform complies with the licenses and laws that apply to your dictionary
files.
