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
dense-HONMON dictionaries.

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

Extract raw title/headword streams:

```bash
logovista-tools titles /path/to/LogoVista --out-dir out/titles
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
--no-skip-dense-marker-honmon       force extraction on placeholder HONMON
```

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
- Plist gaiji mapping when `Gaiji.plist` or `GaijiS.plist` is present.
- Placeholder preservation for unresolved gaiji, for example `<hA126>`.
- Full-width ASCII normalization to half-width ASCII.
- Dense HONMON ID-table detection.
- Raw HONMON numeric ID decoding for `DictFULLDB` extraction.

Known limitations:

- Not all dictionaries store definitions in `HONMON.DIC`.
- `*INDEX.DIC` binary search structures are not fully parsed yet.
- `.uni`, `GA16HALF`, `GA16FULL`, and bitmap gaiji are only partially handled.
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
FKTITLE.DIC
FKINDEX.DIC
FHTITLE.DIC
FHINDEX.DIC
BKTITLE.DIC
BKINDEX.DIC
GA16HALF
GA16FULL
Gaiji.plist
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
0x04  FKTITLE.DIC
0x05  FHTITLE.DIC
0x06  BKTITLE.DIC
0x07  BHTITLE.DIC
0x0a  CRTITLE.DIC
0x70  BKINDEX.DIC
0x71  BHINDEX.DIC
0x81  CRINDEX.DIC
0x90  FKINDEX.DIC
0x91  FHINDEX.DIC
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
1f 4a ...         jump/link start with payload
1f 4d ...         media/reference start with payload
1f e0 xx xx       bold-ish start
1f e1             bold-ish end
1f e2 xx xx       color/style start
1f e3             color/style end
```

The current extractor does not claim full semantic knowledge of every control.
It uses enough structure to preserve line breaks and avoid mixing payload bytes
into visible text.

### Entry Slicing

Many body streams use this marker near each entry boundary:

```text
1f 09 00 01
```

The current entry extractor finds every marker and slices from one marker to
the next. If the marker is immediately preceded by `1f 02`, the slice starts at
that wrapper start instead.

This works well for dictionaries where `HONMON.DIC` really is a body stream,
including dictionaries such as GENIUSEB and HAESPJPN.

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

`*INDEX.DIC` components are binary. They contain search keys, branch/page
structures, and pointers into other components. Some bytes decode as text if
treated naively, but most of the component is structured binary data.

Common index components:

```text
FKINDEX.DIC
FHINDEX.DIC
BKINDEX.DIC
BHINDEX.DIC
CRINDEX.DIC
KWINDEX.DIC
```

Parsing these directly is the next major milestone. It is required for:

- deriving all exact lookup keys without SQLite;
- pairing title lines with body addresses;
- reconstructing aliases and subentries;
- resolving dense-HONMON ID dictionaries cleanly where possible.

Raw-only probes confirm that indexes can expose useful lookup strings even
when no `*TITLE.DIC` component is present. For example, `HABGESPA` exposes
Spanish keys in `FHINDEX.DIC` / `BHINDEX.DIC`, and `HAFRAN` exposes French
keys in the same forward/backward index components. Those decoded strings are
not full body entries; they are evidence that the remaining direct raw work is
index-structure parsing.

### Gaiji

Gaiji are dictionary-specific characters and formatting markers. Observed
resources include:

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

- loads string mappings from `Gaiji.plist` and `GaijiS.plist`;
- emits unresolved half-width gaiji as `<hXXXX>` by default;
- can emit all unresolved gaiji as placeholders with `--gaiji placeholder`;
- can drop unresolved gaiji with `--gaiji drop`.

For conversion work, keeping placeholders is usually better than dropping.
It lets a later gaiji pass replace `<hA126>` with `é`, `<hA138>` with `ñ`, or
with images if the character has no Unicode equivalent.

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
does, but only after decoding body IDs from raw HONMON records.

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

- Parse `.uni`, `GA16HALF`, and `GA16FULL` gaiji resources.
- Preserve a richer structured AST instead of emitting only plain body text.
- Parse common `*INDEX.DIC` search structures.
- Link title streams to body IDs for dense-HONMON dictionaries.
- Add optional SQLite-assisted validation reports for non-`DictFULLDB` caches.

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
