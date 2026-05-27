# LVED_SQLITE3 Corpus Findings

`LVED_SQLITE3` is the preferred reader-facing name for LogoVista's modern
SQLCipher-backed dictionary family. The toolkit command remains `lved` because
existing reports and recovered viewer names use that shorthand, but the useful
format distinction is the decrypted SQLite3 data model.

This page summarizes local corpus inspections of the modern SQLCipher payload
set. Private schema, feature, retained-resource, loose-resource, and render
reports live under `reports/lved-sqlite3-*`. No decrypted database, key string,
or dictionary row text is committed.

## Corpus Shape

The inspected corpus contains 73 `main.data` / `*.dbc` payloads.

All 73:

- were classified as SQLCipher candidates;
- had a local `.key` file detected beside the payload;
- validated with that local key;
- decrypted to SQLite3 successfully;
- exposed the same core reader tables: `list`, `content`, `media`, `info`, and
  `search` plus FTS shadow tables.
- now produce scan titles through the lightweight page-level title probe, with
  no full-payload decrypt required during normal `scan`.

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

For package discovery, the toolkit extracts a dictionary title from `info`
metadata rather than accepting the first readable `info` body. Rows that look
like copyright/about/index/HANREI pages are down-ranked, ruby annotations are
flattened for title text, and the result is reported with a `title_source`
showing whether it came from a plaintext database, a bounded SQLCipher page
probe, or a slow full-decrypt fallback.

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

Real search probes now confirm the base join:

```sql
search.rowid -> list.id -> list.refid -> content.id
```

The toolkit maps the reader modes as follows:

| Mode | Query strategy |
|---|---|
| 完全一致 / `exact` | `filter` boundary tokens such as `∥query∥` when present; `forward = ?` fallback when not. |
| 前方一致 / `forward` | FTS `forward:<query>*`. |
| 後方一致 / `backward` | Reverse the normalized query and use FTS `back:<reversed-query>*`. |
| 逐次一致 / `partial` | Character-token FTS over `part`. |
| 全文検索 / `fulltext` | FTS over `fts`; non-Latin/CJK-only queries are split into character terms. |
| `advanced`, `advanced1`, `advanced2`, `advanced3` | Product-specific FTS dimensions. Observed values include phrase/example/filter panes; there is no single universal semantic. |
| `filter` | Raw substring probe of the `filter` column for diagnostics. |

The current implementation is deliberately conservative. It proves the native
storage/query surface and returns real dictionary rows, but official ranking
and product-specific search-dialog behavior still need dedicated UI parity
tests.

The native `sqlcipher` command-line client can open the validated payloads much
faster than the pure-Python page decryptor used by the toolkit. That is useful
for research probes, but toolkit support still validates/decrypts internally
unless a future fast path is added explicitly. Full-corpus `--inspect-reader`
passes should be treated as expensive with the current Python decryptor.

## Body And Link Signals

The sampled body/info/title fields contain renderer-facing HTML and internal
LogoVista links. Sampled signals include:

| Signal | Where observed |
|---|---|
| HTML image tags | `content.body`, `info.body`, `list.title`, `list.titlesub` |
| HTML ruby tags | `content.body`, `info.body`, `list.title`, `list.titlesub` |
| HTML tables | `content.body`, `info.body` |
| `lved.dataid:` | `content.body`, `content.bodysub`, `info.body` |
| `lved.dataid.result:` | Viewer search-result detail-pane callback generated by templates; route as a detail target, not as a raw browser URL. |
| `lved.dataid.dict.<code>:` | Cross-dictionary entry navigation, observed in RPLUSREV and STEDABBR. |
| `lved.dataid<id>` | Malformed/no-colon content-id link, observed in KQEBHOU. Treat as `lved.dataid:<id>` when the numeric id is valid. |
| `lved.contentlink:<code>.<id>` | Cross-dictionary content link, observed between BUREI/HINKAKU/MEIKYOU3. |
| `lved.image:` / `lved.imag:` | `content.body` |
| `lved.media.sound:` | `content.body`, especially sound-heavy bilingual dictionaries |
| `lved.binran:` | `info.body` and `binran.body` in HBHYAKKA |
| `lved.bookmark:` | `content.body`, `info.body` |
| `lved.pdf:` | `content.body` / `info.body` in HBGEOLOG and OUKOKU12; targets media rows that may be real PDF or image-page blobs. |
| `lved.dict.<code>:pictlink...` | GENIUSE6 picture-dictionary links; targets `info` picture pages. |
| `lved.addr...` | Retained SSED-like block/offset links in EIBEIHOU and IBOI5; IBOI5 mostly bridges to nearby HTML anchors, while EIBEIHOU remains unresolved. |
| `lved.plugin:` | Rare viewer/plugin hook; observed in an info page |
| `lved.sql:` / `lved.findnum:` / `lved.select:` / `lved.group.*` / `lved.browser.*` | Viewer/template hooks. Preserve as inert router metadata unless a frontend implements the matching host behavior. |
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

The toolkit now classifies these tables by role during `--inspect-sqlite` and
emits `optional_provider_readiness` during `--inspect-reader`.
Currently supported reader-facing handling is:

- `list`, `content`, `media`, `info`, and `search` are active reader providers.
- `mediasub` and `sound` are recognized as secondary media/audio stores and
  are searched by the generic renderer alongside `media`.
- `listsub` plus `searchsub` form a secondary native search/list provider. In
  READERS3 and RPLUSREV it backs the observed `advanced3` phrase/translation
  search surface, while the primary `search` table carries the usual
  forward/back/part/fts modes plus `advanced1`/`advanced2`.
- `binran` is an active named HTML-body provider for HBHYAKKA reference/table
  pages. `lved.binran:<name>` links rewrite to generated local `binran_*`
  pages; observed duplicate suffix targets such as `kinen.html.html` collapse
  to the stored `kinen.html` row.
- `bunya`, `genre`, `exam`, `kanji`, `kanji_all`, `kwit`, `joken`, `sakuin`,
  `item`, `t_contents`, and `t_convert` are classified feature tables. Their
  exact product UI semantics are not all implemented yet and should remain
  named providers/gaps, not hidden columns.
- `optional_provider_readiness` records row counts, relevant non-empty body or
  keyword counts, protocol/body signal summaries, media type distributions, and
  target-column correlation where a provider column points back to SQLite
  `content` / `list` rows.

Targeted row inspection tightened several table roles:

- `exam` is an example-sentence lookup. KOJIEN7 stores `title_exam` and
  `key_exam`; NKGORIN2 stores classical example text, authority text, and
  anchors back into content rows.
- `genre` and `bunya` are category/filter metadata tables over entry refids.
  They should feed browse/filter surfaces rather than entry body rendering.
- `kwit`, `joken`, and `sakuin` in NANMED20/NMEDEJ12 are keyword/condition/
  index lookup surfaces. They point back to dictionary refids/data ids and
  should be exposed as native lookup providers when their UI is implemented.
- `kanji` and `kanji_all` are kanji metadata providers. Some title cells carry
  rich HTML or image references, so they need the same title/resource handling
  as ordinary `list` rows.
- `t_contents` in HAFRAN is a legacy HTML content store with
  `f_DataId`, `f_Title`, `f_Html`, `f_Plane`, and `f_Keyword`. It mirrors the
  sidecar-body pattern seen in older SSED dense dictionaries, but inside the
  SQLCipher payload.
- `t_convert` in SAITOJE maps old SSED-like `block`/`offset` positions and old
  titles to modern SQLite ids/titles. Treat it as an address compatibility
  bridge, not a body store.

## Loose Resources And Retained SSED-Like Files

49 payload directories contain a `res/` resource directory. The strict
loose-resource parser inspected 4,481 non-payload, non-key, non-retained
resource files across 40 payloads. Observed decoded kinds include:

| Kind | Files | Notes |
|---|---:|---|
| PNG | 2,343 | Mostly glyph/image resources; 2,220 are 150x150 and 33 are 75x150. |
| GIF | 1,726 | Mostly 48x48 button/icon resources. |
| JPG | 206 | Photo/figure resources with normal JPEG dimensions. |
| WebP | 76 | Figure resources in modern biology/science payloads. |
| SVG | 103 | Vector icons/glyphs; counted as loose resources by signature. |
| Fonts | 25 | OTF/TTF resources. |
| Web/runtime text | 2 | Loose runtime text resources; contents are not emitted. |

The private loose-resource summary is written under
`reports/lved-sqlite3-retained-v2/`.

25 SSED-like or index-like files are present beside SQLCipher payloads. The
dedicated retained-resource pass decodes all 25:

| Decoded kind | Files | Role |
|---|---:|---|
| UTF-8 text tree | 22 | SQLite content navigation tree. First field is a decimal `content.id`, sometimes with a query suffix such as `?key=...`; negative values are non-entry sentinel/category rows. |
| CP932 auxiliary hex tree | 1 | GENIUS53 rank/filter auxiliary tree. Its two hexadecimal fields do not map to SQLite content ids. |
| SSEDATA index stream | 2 | GENIUS53 retained compressed SSED indexes with normal block/offset leaf targets. |

The 22 decoded text trees all parse with zero unparsed rows and correlate
primarily to SQLite `content.id` and `list.refid`, so they are not opaque blobs.
They are navigation/category trees over the SQLite content store. The parser
does not emit decoded labels in normal reports; it reports row counts, query
parameter keys, depth counts, target forms, and target correlation.

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

The toolkit generic render path now materializes retained text trees as
`tree_*.html` pages. Positive targets link to generated `entry_<id>.html` pages
when those pages are already rendered or can be created within the configured
linked-entry budget. Other valid entry targets are marked with
`data-lv-deferred-entry` rather than generating dead links. Query suffixes such
as `?key=...` are preserved as metadata (`data-lv-query`) for future frontend
filter/search behavior.

The private retained decode/render reports are written under
`reports/lved-sqlite3-semantics-v2/`.

## Reader Probe And Generic Rendering

A focused reader probe was run on KOJIEN7, DAIJIRN4, GENIUS53, NANMED20, and
NKGORIN2 under `reports/lved-sqlite3-reader-probe-v3/`. It exercised real
queries across `exact`, `forward`, `backward`, `partial`, `fulltext`, and
`advanced1`, generated static HTML pages from real `content` and `info` rows,
and validated the generated pages.

A follow-up semantics probe under
`reports/lved-sqlite3-semantics-v1/` focused on the remaining optional-table
gaps. It confirmed secondary `searchsub/listsub` search in READERS3/RPLUSREV,
audio BLOB resolution from `sound`, HBHYAKKA `binran` reference-page
navigation, HAFRAN `t_contents`, SAITOJE `t_convert`, and several product
metadata tables. A real HBHYAKKA generic render produced 112 entry pages,
24 info pages, 14 binran pages, and 244 linked-entry stubs with zero missing
local references after link rewriting.

The v2 retained-tree probe generated a NIHONSHI tree showcase from
`res/nihonshi.idx`: 15,126 decoded rows, 15,055 query rows, zero tree-parser
failures, 1,971 immediately clickable links, and deferred metadata for the
remaining target rows outside the linked-entry budget. The remaining unresolved
references in that render were external `../../_images/UpperCase-Letters/...`
help-page assets, not retained-tree links.

A v3 protocol probe counted unresolved or previously deferred protocol families
across all 73 payloads:

| Protocol family | Row hits | Current toolkit handling |
|---|---:|---|
| `lved.dataid:` | 1,404,279 | Active entry navigation to `content.id`, preserving anchors. |
| `lved.dataid.dict.<code>:` | 2,407 links | Cross-dictionary entry navigation. Generic static renders preserve this as `data-lv-hook="dataid-dict"` with target dictionary/code metadata. |
| `lved.contentlink:` | 2,094 links | Cross-dictionary content links between BUREI/HINKAKU/MEIKYOU3. Generic static renders preserve this as `data-lv-hook="contentlink"` with target dictionary/code metadata. |
| malformed `lved.dataid<id>` | 1 link | No-colon content-id link observed in KQEBHOU; generic rendering treats it as a normal same-book content-id target when the numeric id parses. |
| `lved.info:` | 6,798 | Active info/HANREI/about page navigation. |
| `lved.media.sound:` | 446,019 | Active media/audio asset resolution through `media`, `mediasub`, or `sound`. |
| `lved.bookmark:` | 259,078 | Suppressed as viewer/user-state metadata. |
| `lved.imag:` / `lved.image:` | 5,841 | Active image asset resolution. |
| `lved.pdf:` | 312 | Active asset resolution; HBGEOLOG stores true PDF blobs, while OUKOKU12 uses the same protocol for media rows whose bytes identify as WebP image pages. |
| `lved.dict.` | 132 | Active rewrite for GENIUSE6 `pictlink` targets to generated `info_*` picture-dictionary pages. |
| `lved.addr` | 51 rows / 197 links | IBOI5 links mostly resolve to the first following same-block HTML anchor; unresolved targets are deferred with `data-lv-original-href`. |
| `lved.binran:` | 2 | Active named `binran` page navigation. |
| `lved.plugin:` | 1 | Suppressed as a viewer/plugin hook. |

No occurrences were found for `lved.browser.*`, `lved.findnum:`,
`lved.select:`, or `lved.group.*` in the inspected SQLCipher corpus text
columns. Those remain viewer-code strings rather than observed corpus content
links.

The generated HTML showcase is intentionally generic. It is useful for
verifying link/media/resource mechanics, not for claiming exact LVEDVIEWER
visual parity. Validation results from the v3 run:

- 28,609 HTML pages generated across the five selected packages.
- 0 missing local `href` / `src` targets after rewriting.
- `lved.dataid:` links, including `#anchor` suffixes, rewrite to local entry
  pages.
- `lved.dataid.result:` rewrites to the same local entry target while marking
  the source hook as a search-result/detail-pane callback.
- Cross-dictionary `lved.contentlink:` and `lved.dataid.dict.<code>:` hooks are
  not fake-resolved in single-book static renders. They are converted to inert
  links with `data-lv-dict`, `data-lv-content-id`, optional `data-lv-anchor`,
  and `data-lv-original-href` so a future frontend can route them through the
  library-wide dictionary registry.
- The malformed KQEBHOU `lved.dataid<id>#anchor` shape rewrites as a same-book
  entry link with `data-lv-hook="dataid-no-colon"`.
- `lved.info:` links rewrite to local info/HANREI pages.
- `lved.binran:` links rewrite to local named reference pages where a `binran`
  row exists.
- `lved.pdf:` links resolve to copied assets through the same media lookup
  path. Blob signatures decide the output extension.
- GENIUSE6 `lved.dict.<code>:pictlink.<name>.html` links rewrite to generated
  `info_<name>.html` picture-dictionary pages.
- `lved.addr...` links use an anchor bridge when proven: choose the first
  following HTML anchor in the same eight-hex block within `0x100` bytes and
  rewrite to that entry page/anchor. In IBOI5 this resolves 174 of 183 distinct
  targets and sample labels match the target midashi. EIBEIHOU has 5 distinct
  targets but no HTML anchors/conversion table, so those links remain explicit
  deferred metadata.
- Bare numeric `href` values inside entry HTML are content-id links and rewrite
  to generated entry pages.
- Relative appendix hooks such as `050000/0000` are observed in IBOI5. They
  likely target classification-table appendix surfaces, but the bridge is not
  proven yet, so generic rendering preserves them as deferred metadata.
- `lved.imag...` address-style image links without the usual colon form remain
  explicit deferred metadata; their resource/address bridge is not proven.
- `lved.image:` / `lved.imag:` resources resolve through the SQLite `media`
  table or loose resources.
- `lved.media.sound:` links resolve to copied audio assets through `media`,
  `mediasub`, or `sound` rows when matching media names exist.
- SQLite `media` / `mediasub` / `sound` payloads are decoded from BLOB values
  or text-storage values without leaking Python `b'...'` byte reprs into
  generated output.
- `lved.bookmark:`, `lved.plugin:`, `lved.sql:`, `lved.findnum:`,
  `lved.select:`, `lved.group.*`, `lved.browser.*`, and unknown future
  `lved.*` hooks are preserved as suppressed/deferred viewer hooks with their
  original target recorded in `data-lv-original-href` and a normalized
  `data-lv-hook` value.
- Headless Chromium loaded representative generated index pages.
- One KOJIEN7 info fragment contains extra closing table row/cell tags in the
  source body. The toolkit preserves it rather than silently rewriting vendor
  body HTML.

The probe also confirms that `info` rows are not disposable metadata. They
hold book home/help/HANREI/copyright-like surfaces, often as HTML fragments.
Many dictionaries need those pages to feel like a complete electronic book.
`info.name` is the stable named surface key; at least one observed payload has
nullable `info.id` values, so reader APIs should not require a numeric info id
for home/HANREI navigation.

A later full-readiness pass under
`reports/lved-sqlite3-readiness-v1/full-readiness-after-fixes.json` re-ran all
73 payloads with SQLite schema, retained-resource, loose-resource, and
reader-feature inspection enabled. All 73 decrypted and inspected cleanly. It
decoded 22 retained SQLite navigation trees, one auxiliary hex tree, two
retained SSED index streams, and 4,481 loose resources without unknown retained
or loose-resource rows. The pass also confirmed 70 `index.html` home surfaces,
85 HANREI-like surfaces, and 70 copyright-like surfaces across the corpus.

## Renderer Readiness

Most `content.body` and `info.body` values are HTML fragments, not full HTML
documents with complete CSS/JS. The generic renderer wraps those fragments with
minimal CSS, rewrites internal protocols, and copies resources. That is enough
for structural testing and rough browsing, but not enough for official visual
parity.

The official viewer supplies significant product-specific rendering material:
embedded per-product templates such as `Html_Template_Detail.html`,
`Html_Template_Index.html`, `Html_Template_DictSearch.html`, product CSS, and
viewer JavaScript/resources. Exact LVED_SQLITE3 renderer parity therefore
requires either extracting and applying those templates or reimplementing their
semantics. The database payload alone is not enough to guarantee exact visual
output.

A dedicated extraction pass of `LVEDVIEWER.exe` found 612 managed resources,
including 393 viewer-facing HTML/CSS/JS/text assets. The product inventory has
88 product/plugin codes plus global viewer resources, with specialized
templates for examples, kanji pages, tree navigation, HANREI/info pages,
advanced search, condition search, keyword-in-text search, sakuin/index pages,
and PDF/image-page views. See [LVEDVIEWER Embedded Assets](lved-viewer-assets.md).

## Reader Architecture Implications

`LVED_SQLITE3` should be a first-class package driver with shared providers:

- `ListProvider`: browse/search result display rows from `list` and optional
  `listsub`.
- `ContentProvider`: resolve `list.refid` / `list.anchor` to `content` rows.
- `SearchProvider`: expose native forward/back/part/fts/advanced search columns,
  including secondary `searchsub/listsub` providers when a mode exists only
  there.
- `MediaProvider`: resolve `media`, `mediasub`, `sound`, and loose `res/`
  resources.
- `InfoProvider`: expose `info` rows as home/help/about/HANREI-like surfaces.
  Do not treat them as hidden metadata by default.
- `NamedBodyProvider`: expose named HTML body tables such as HBHYAKKA `binran`
  as navigable reference surfaces.
- `OptionalMetadataProviders`: `exam`, `genre`, `bunya`, `kanji`, `kwit`,
  `joken`, `sakuin`, `item`, and conversion tables.
- `RetainedSsedProvider`: parse retained `.idx` / `*INDEX.DIC` / tree files
  when present, without treating the whole package as SSED. For decoded text
  trees, route tree targets to SQLite `content.id` / `list.refid`; for retained
  SSEDATA indexes, keep block/offset targets explicit until a product-specific
  bridge is proven.

The frontend should see package capabilities, not package-family restrictions.
For example, a SQLCipher dictionary with `BHINDEX.DIC` should be allowed to
offer that index provider.

## Current Unknowns

The corpus pass identifies schema and feature surfaces, but not complete
end-user behavior. Still open:

- exact official search ranking and query rewriting;
- numeric meaning of all `type` values in `list`, `content`, `media`, and
  `info`;
- official UI labels and ranking for `advanced*`, `filter`, and
  dictionary-specific metadata tables;
- exact UI placement and interaction model for decoded text trees in each
  product; structural page generation is implemented, but official widget
  placement remains unverified;
- whether GENIUS53 retained SSEDATA index block/offset targets have a bridge to
  modern SQLite rows or are only legacy/search compatibility data;
- unresolved external help/image assets referenced by some `info` pages, for
  example the observed upper-case-letter help images;
- target bridge for EIBEIHOU's 5 unresolved `lved.addr...` targets;
- resource/address bridge for IBOI5 `lved.imag...` address-style image links;
- bridge for IBOI5 relative appendix hooks such as `050000/0000`;
- complete visual rendering rules for every HTML/class/link pattern;
- product-specific LVEDVIEWER template/CSS/JS application and reimplementation
  of the product hooks summarized in
  [LVEDVIEWER Embedded Assets](lved-viewer-assets.md);
- complete product semantics for optional providers such as `exam`, `genre`,
  `bunya`, `kanji`, `kwit`, `joken`, `sakuin`, `item`, `t_contents`, and
  `t_convert` beyond the structural roles described above.

These are the next SQLCipher-specific targets before claiming a usable
`LVED_SQLITE3` reader path.
