# LVED_SQLITE3 Corpus Findings

`LVED_SQLITE3` is the preferred reader-facing name for LogoVista's modern
SQLCipher-backed dictionary family. The toolkit command remains `lved` because
existing reports and recovered viewer names use that shorthand, but the useful
format distinction is the decrypted SQLite3 data model.

This page summarizes a full local corpus inspection of the modern SQLCipher
payload set. The private run wrote schema/feature reports under
`reports/lved-sqlite3-corpus-v1/`. No decrypted database, key string, or
dictionary row text is committed.

## Corpus Shape

The inspected corpus contains 73 `main.data` / `*.dbc` payloads.

All 73:

- were classified as SQLCipher candidates;
- had a local `.key` file detected beside the payload;
- validated with that local key;
- decrypted to SQLite3 successfully;
- exposed the same core reader tables: `list`, `content`, `media`, `info`, and
  `search` plus FTS shadow tables.

The inspection command was:

```bash
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-sqlite --json
```

`--inspect-sqlite` records exact schema, row counts, small numeric
distributions, and bounded text-signal counts. Text-signal counts are sampled
from up to 1024 non-null values per inspected column and are used only as
feature evidence; they are not complete occurrence counts.

## Core Database Model

Every inspected payload has these tables:

| Table | Count | Reader role |
|---|---:|---|
| `list` | 73 | Entry/search result list. Holds display titles and anchor/refid mapping. |
| `content` | 73 | Entry body store. Holds HTML/text body and per-entry media references. |
| `media` | 73 | Binary resource store. Holds image/audio/other BLOBs in `main`. |
| `info` | 73 | Auxiliary pages such as about/help/metadata body fragments. |
| `search` | 73 | Native FTS search virtual table. |
| `search_content` | 73 | FTS shadow content table. |
| `search_docsize` | 73 | FTS shadow docsize table. |
| `search_segdir` | 73 | FTS shadow segment directory. |
| `search_segments` | 73 | FTS shadow segment blocks. |
| `search_stat` | 73 | FTS shadow stat table. |

This strongly suggests the first-class reader model should treat
`LVED_SQLITE3` as a SQLite-backed book with native search, list browsing,
content rendering, media resolution, and info-page surfaces.

## Core Columns

`list` always has:

```text
id, refid, type, anchor, title
```

Observed optional `list` columns:

| Column | Payloads | Meaning |
|---|---:|---|
| `titlesub` | 58 | Secondary display title/subtitle. |
| `subtext` | 12 | Secondary display text, often equivalent to subtitle content. |
| `titleplain` | 3 | Plain title variant. |
| `example_title` | 1 | Example-title metadata. |
| `title_a` / `titlesub_a` | 1 | Alternate title/subtitle pair. |

`content` always has:

```text
id, type, body, media
```

Observed optional `content` columns:

| Column | Payloads | Meaning |
|---|---:|---|
| `bodysub` | 2 | Secondary body fragment. |
| `idsub` | 2 | Secondary body id. |

`media` always has:

```text
id, name, type, main
```

8 payloads also have an `os` column for platform-specific media rows.

`info` always has:

```text
id, type, name, body, media
```

## Search Model

Every payload has a native FTS table named `search`.

Always present:

```text
forward, back, part, fts
```

Observed optional search columns:

| Column | Payloads |
|---|---:|
| `filter` | 72 |
| `advanced1` | 29 |
| `advanced2` | 26 |
| `advanced` | 6 |
| `advanced3` | 3 |

This maps well to the reader search modes:

- `forward`: 前方一致;
- `back`: 後方一致;
- `part`: 逐次一致 / partial matching;
- `fts`: 全文検索;
- `filter` and `advanced*`: dictionary-specific filters or advanced search
  dimensions.

The exact query and ranking behavior still needs end-user visual/search tests.
Do not assume FTS `MATCH` alone reproduces official search ordering.

## Body And Link Signals

The sampled body/info/title fields contain renderer-facing HTML and internal
LogoVista links. Sampled signals include:

| Signal | Where observed |
|---|---|
| HTML image tags | `content.body`, `info.body`, `list.title`, `list.titlesub` |
| HTML ruby tags | `content.body`, `info.body`, `list.title`, `list.titlesub` |
| HTML tables | `content.body`, `info.body` |
| `lved.dataid:` | `content.body`, `content.bodysub`, `info.body` |
| `lved.image:` / `lved.imag:` | `content.body` |
| `lved.bookmark:` | `content.body`, `info.body` |
| HTTP/HTTPS links | `content.body`, `info.body`, occasionally title/subtitle fields |

Important implication: title/list labels need the same rich-text and gaiji/image
resolution policy as entry bodies. A frontend must not assume search/title rows
are plain strings.

## Additional Tables

The core model is stable, but several dictionaries add feature tables:

| Table | Payloads | Observed dictionaries / role |
|---|---:|---|
| `exam` | 2 | KOJIEN7 and NKGORIN2 example-sentence lookup variants. |
| `genre` | 1 | KOJIEN7 genre/category metadata. |
| `bunya` | 1 | DAIJIRN4 category/field metadata. |
| `kanji` | 2 | HBJITSU and SCROWNCJ kanji metadata variants. |
| `kanji_all` | 1 | NKGORIN2 kanji metadata. |
| `kwit` | 2 | NANMED20/NMEDEJ12 keyword metadata. |
| `joken` | 1 | NANMED20 condition/filter metadata. |
| `sakuin` | 1 | NANMED20 index/search metadata. |
| `listsub` plus `searchsub*` | 2 | READERS3/RPLUSREV secondary list/search surface. |
| `mediasub` | 5 | Secondary media store. |
| `sound` | 1 | ASJPUSCL separate sound BLOB store. |
| `binran` | 1 | HBHYAKKA auxiliary encyclopedia/table material. |
| `t_contents` | 1 | HAFRAN sidecar-style body table retained in SQLCipher corpus. |
| `t_convert` | 1 | SAITOJE block/offset/title conversion table. |
| `item` | 1 | SANKOKU8 grammatical/category metadata. |

These must be modeled as optional feature providers. They are not evidence for
separate package families.

## Loose Resources And Retained SSED-Like Files

49 payload directories contain a `res/` resource directory. Observed loose
resource extensions include PNG, GIF, JPG, SVG, WebP, OTF, and TTF.

25 SSED-like or index-like files are present beside SQLCipher payloads:

```text
DAIJIRN4     res/tree.idx
GEN2022     res/tree.idx
GEN2023     res/tree.idx
GEN2024     res/tree.idx
GEN2025     res/tree.idx
GENIUS53    000002C5.idx, BHINDEX.DIC, BKINDEX.DIC
GENMEM1     res/tree.idx
IBIO5       res/ibio5_2.idx, res/ibio5_3.idx
IBOI5       res/ibio5_2.idx, res/ibio5_3.idx
IPHYCHE5    res/iphyche5.idx, res/iphyche5_appendix.idx
IWKOKUG8    res/tree.idx
JSSAURU2    res/tree.idx
KENROWA     res/tree.idx
KENWARO     res/tree.idx
KOJIEN7     res/tree.idx
KQEBHOU     res/tree.idx
KQSYNONM    res/tree.idx
NIHONSHI    res/nihonshi.idx
PROYALF5    res/tree.idx
PROYALJ3    res/tree.idx
```

This confirms the reader architecture must not gate SSED/index parsers behind
the SSED package family. SQLCipher packages can expose retained SSED-style
indexes or trees as package-local feature providers.

## Reader Architecture Implications

`LVED_SQLITE3` should be a first-class package driver with shared providers:

- `ListProvider`: browse/search result display rows from `list` and optional
  `listsub`.
- `ContentProvider`: resolve `list.refid` / `list.anchor` to `content` rows.
- `SearchProvider`: expose native forward/back/part/fts/advanced search columns.
- `MediaProvider`: resolve `media`, `mediasub`, `sound`, and loose `res/`
  resources.
- `InfoProvider`: expose `info` rows as home/help/about/HANREI-like surfaces
  when appropriate.
- `OptionalMetadataProviders`: `exam`, `genre`, `bunya`, `kanji`, `kwit`,
  `joken`, `sakuin`, `item`, and conversion tables.
- `RetainedSsedProvider`: parse retained `.idx` / `*INDEX.DIC` / tree files
  when present, without treating the whole package as SSED.

The frontend should see package capabilities, not package-family restrictions.
For example, a SQLCipher dictionary with `BHINDEX.DIC` should be allowed to
offer that index provider.

## Current Unknowns

The corpus pass identifies schema and feature surfaces, but not complete
end-user behavior. Still open:

- exact official search ranking and query rewriting;
- numeric meaning of all `type` values in `list`, `content`, `media`, and
  `info`;
- exact semantics of `advanced*`, `filter`, and dictionary-specific metadata
  tables;
- how `tree.idx` and other retained `.idx` files map to the modern UI;
- complete rendering/resource rules for every HTML/link pattern;
- whether all `info` rows should be home surfaces, help/HANREI pages, hidden
  metadata, or product-specific UI panels.

These are the next SQLCipher-specific targets before claiming a usable
`LVED_SQLITE3` reader path.
