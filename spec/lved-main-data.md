# LVED_SQLITE3 / Modern LogoVista SQLCipher Packages

`LVED_SQLITE3` is the preferred reader-facing name for LogoVista's modern
SQLCipher-backed dictionary family. These packages are separate from the
SSED/HONMON dictionaries documented elsewhere in this repository. The toolkit
still uses `lved` as the command/module shorthand because recovered viewer
names and older reports use LVED-related names, but LogoVista also reused
`lved` internally for older SSED-era viewers. In this document, the useful
format claim is therefore the storage model: SQLCipher `main.data` / `*.dbc`
payloads that decrypt to ordinary SQLite3 databases.

Observed corpus shape:

| Payload count | Local `.key` validated | SQLite inspected | Core tables present |
|---:|---:|---:|---:|
| 73 | 73 | 73 | 73 |

The full-corpus schema/feature summary lives in
[LVED_SQLITE3 Corpus Findings](../docs/lved-sqlite3-corpus.md).

## Classification

The payload is not `SSEDINFO`, not `SSEDDATA`, and not an expanded HONMON body
stream. The observed files have these traits:

- filename `main.data` in Windows packages or `*.dbc` in mobile packages;
- size is a multiple of 4096 bytes;
- first bytes do not contain the plaintext `SQLite format 3` header;
- entropy is close to random data;
- decrypt as SQLCipher 4 with 4096-byte pages;
- expose ordinary SQLite tables after decryption.

SSED/HONMON-oriented readers cannot recover body text from this package family
because there is no SSED/HONMON body stream to compose.

Some packages in this family still carry retained SSED-style components or
indexes beside the SQLCipher payload. Reader architecture must treat those as
shared feature providers when present, not gate SSED component parsers away
from SQLCipher packages.

## Viewer Access Path

The Windows viewer is a .NET/WebView2 application that loads `sqlcipher.dll`
directly. The observed SQLCipher interop layer imports functions such as:

```text
sqlite3_open_v2
sqlite3_key
sqlite3_exec
sqlite3_prepare_v2
sqlite3_step
sqlite3_column_text
sqlite3_column_blob
```

The viewer opens the dictionary main data path, derives a SQLCipher key from
dictionary metadata, and passes that key to `sqlite3_key`.

The observed key construction is deterministic and is implemented by
`derive_lved_sqlcipher_key(dict_id, dict_code)`:

```python
key_code = (dict_code[0] + dict_code[-1]).lower()
key = (
    "jlasgoi"
    + "ahoiam"
    + "pvsjhosD"
    + "Hfopj"
    + key_code
    + str(dict_id * 19286)
)
```

This path uses dictionary id/code metadata. It does not use the product serial
number for the SQLCipher database key. Serial/license handling is a separate
application concern.

The repository documents the derivation rather than committing per-product
final key strings, serials, memory-dump material, or decrypted databases.

## SQLCipher Parameters

The validated page settings match SQLCipher 4 defaults used by the shipped
runtime:

```text
page size:       4096
reserve bytes:   80
KDF:             PBKDF2-HMAC-SHA512
iterations:      256000
cipher:          AES-256-CBC
salt:            first 16 bytes of page 1
page IV:         first 16 bytes of each page reserve area
```

Page 1 stores the salt where plaintext SQLite would normally store the database
header. After decryption, the first plaintext page is reconstructed as:

```text
"SQLite format 3\0" + decrypted page-1 payload + zeroed reserve bytes
```

Other pages decrypt directly from the encrypted usable area plus zeroed reserve
bytes.

## Decrypted Schema

Every inspected decrypted database contains these ordinary SQLite tables:

```text
list
content
media
info
search
search_content
search_docsize
search_segdir
search_segments
search_stat
```

The core table roles are:

| Table | Role |
|---|---|
| `list` | Entry/search-result list with display titles and anchor/refid mapping. |
| `content` | Entry body store with HTML/text body and media-reference fields. |
| `media` | Binary resource store. |
| `info` | Auxiliary/about/help/info page store. |
| `search` | Native FTS table for forward/back/part/full-text lookup. |
| `search_*` | FTS shadow tables. |

Important query shapes recovered from the viewer and confirmed by schema shape
include:

```sql
SELECT id, title FROM list WHERE refid = ? AND anchor = ?
SELECT type, body, media FROM content WHERE id = ?
SELECT main FROM media WHERE type = ? AND name = ?
SELECT body, media FROM info WHERE name == ?
SELECT id, type, refid, anchor, title
  FROM list INNER JOIN search ON list.id = search.docid
  WHERE search MATCH ?
```

Always-present search columns are:

```text
forward, back, part, fts
```

Optional search columns include `filter`, `advanced`, `advanced1`,
`advanced2`, and `advanced3`.

The reader search surface recovered so far is:

| Reader mode | Storage mapping |
|---|---|
| 完全一致 / exact | `filter` boundary match using `∥query∥`, falling back to `forward = ?` when `filter` is absent. |
| 前方一致 / forward | FTS `forward:<query>*`. |
| 後方一致 / backward | Reverse normalized query, then FTS `back:<reversed-query>*`. |
| 逐次一致 / partial | Character-token FTS over `part`. |
| 全文検索 / fulltext | FTS over `fts`; CJK/non-Latin-only probes use character terms. |
| advanced / advanced1/2/3 | Optional product-specific search dimensions. |

`advanced*` is not one universal feature. It is an extra native FTS column used
by products for phrases, examples, filters, or auxiliary search panes. Reader
frontends should expose it only when the package capability metadata identifies
what that product uses it for.

The `filter` column commonly stores exact-boundary tokens such as
`∥normalized query∥`. The toolkit's exact mode prefers that boundary form when
the column exists. `advanced*` columns must not be assigned global labels
without product evidence.

READERS3 and RPLUSREV also have a secondary search/list pair:

```text
listsub(id, refid, type, anchor, title, titlesub)
searchsub(advanced3)
```

For these packages, `advanced3` is not in the primary `search` table. It is a
separate native provider whose `searchsub.rowid` joins to `listsub.id`, and
`listsub.refid` then resolves to `content.id` just like the primary `list`
path. This is currently best modeled as a second `SearchProvider`, not as a
special-case post-filter over primary search results.

## Optional Feature Tables

Observed optional tables are feature providers layered on top of the core
SQLite book model. They are not separate package families.

| Table | Role |
|---|---|
| `exam` | Example-sentence lookup. KOJIEN7 and NKGORIN2 use different schemas, but both point back to content ids/anchors. |
| `genre` | KOJIEN7 genre/category metadata over refids. |
| `bunya` | DAIJIRN4 field/category metadata over refids. |
| `kanji` / `kanji_all` | Kanji metadata providers. Some cells contain rich HTML/image references. |
| `kwit` | NANMED/NMEDEJ keyword lookup rows. |
| `joken` | NANMED condition/filter lookup rows. |
| `sakuin` | NANMED index/search lookup rows. |
| `item` | SANKOKU8 grammatical/category metadata. |
| `mediasub` | Secondary media BLOB store with the same practical lookup shape as `media`. |
| `sound` | Separate audio BLOB store; ASJPUSCL uses it for `lved.media.sound:` targets. |
| `binran` | HBHYAKKA named reference-page store with `id`, `type`, `name`, `body`, and `media`. |
| `t_contents` | HAFRAN legacy HTML content table: `f_DataId`, `f_Type`, `f_Title`, `f_Html`, `f_Plane`, `f_Keyword`. |
| `t_convert` | SAITOJE old block/offset/title to modern id/title conversion bridge. |

`binran` rows are full named HTML fragments. Links use `lved.binran:<name>`.
Observed links may contain duplicated `.html` suffixes, for example
`kinen.html.html`; these normalize to the row named `kinen.html`.

Some packages retain SSED-style or index-style files beside the SQLCipher
payload, including `res/tree.idx`, `res/*.idx`, `BHINDEX.DIC`, and
`BKINDEX.DIC`. The observed text-tree `.idx` files are UTF-8/CP932 tabular
navigation trees with rows shaped as:

```text
target<TAB>depth<TAB>label
```

The `target` field can be a positive decimal SQLite content target, a negative
sentinel/category value, a zero root/sentinel value, an eight-hex auxiliary
target, or a target plus query suffix such as `12345?key=...`. Positive
decimal targets in the observed text trees correlate to SQLite `content.id` /
`list.refid`. Query suffixes are retained as target metadata; they should not
be discarded just because the entry id is sufficient for simple navigation.

GENIUS53 also retains compressed SSEDATA `BHINDEX.DIC` and `BKINDEX.DIC`
streams with normal SSED block/offset leaf targets. Their block/offset bridge
to modern SQLite rows is not proven. Reader architecture must share SSED/index
parsers as feature providers when present instead of assuming
`LVED_SQLITE3` packages are pure SQLite-only books.

## Reader Surfaces

The `info` table is a first-class reader surface provider. Observed row names
include `index.html`, `hanrei.html`, copyright/about/help pages, and
product-specific info pages. The row `body` values are usually fragments, not
complete documents. They should be wrapped by the reader and resolved through
the same resource/link path as entry bodies.

Retained text trees such as `res/tree.idx` are navigation trees over SQLite
content. Positive targets correlate to `content.id` / `list.refid`; negative
targets are category/sentinel rows. They should become browse/navigation
surfaces, not exporter-only diagnostics. Generic showcase rendering now
materializes decoded tree pages and marks out-of-budget valid entry targets as
deferred links rather than producing broken local hrefs.

The body/link protocols observed in the corpus include:

| Protocol | Current interpretation |
|---|---|
| `lved.dataid:<id>` | Jump to SQLite `content.id`. Preserve `#anchor` suffixes when present. |
| `lved.info:<name>` | Jump to an `info.name` page such as `hanrei.html`. |
| `lved.binran:<name>` | Jump to a `binran.name` reference page. Duplicate `.html` suffixes normalize to the stored row name. |
| `lved.image:` / `lved.imag:` | Resolve an image through SQLite `media` or loose resources. |
| `lved.media.sound:` | Resolve an audio media row through `media`, `mediasub`, or `sound`. |
| `lved.pdf:<name>` | Resolve through `media` by name/stem. HBGEOLOG stores true PDF blobs; OUKOKU12 uses the same protocol for media rows that identify as WebP image pages. |
| `lved.dict.<code>:pictlink.<page>.html` | GENIUSE6 picture-dictionary link to an `info.name` page. The `pictlink.` prefix is stripped before resolving the generated page. |
| `lved.addr<block>:<offset>` | Retained SSED-style block/offset link. When a package has same-block HTML anchors, resolve to the first following anchor within `0x100` bytes; otherwise preserve as deferred metadata. |
| `lved.bookmark:` | Viewer/user-state protocol; do not treat as content navigation. |
| `lved.plugin:` | Viewer/plugin callback hook; suppress or preserve as metadata unless the frontend implements that plugin. |

Bare numeric `href` values in entry HTML are observed as direct `content.id`
links and should be resolved through the same entry-navigation path as
`lved.dataid:`.

Relative address-like hooks such as `050000/0000` are observed in IBOI5
appendix/classification-table links. Their target bridge is not proven yet, so
generic renderers should preserve them as deferred metadata instead of emitting
broken local links.

Address-style image links such as `lved.imag<block>:<offset>:<length>` are
also observed. They are not the same as `lved.imag:<name>` media-name links,
and their resource bridge is not proven yet; generic rendering preserves them
as deferred metadata when they appear as hrefs.

Viewer-code strings not found as corpus content links in the current
SQLCipher text probe include `lved.browser.*`, `lved.findnum:`,
`lved.select:`, and group/settings hooks. They should remain explicit
unsupported hooks until a real package and viewer code path prove their
behavior.

## Rendering Contract

The SQLCipher database usually stores HTML fragments. It does not, by itself,
contain enough template/CSS/JS information for exact official visual parity.
The shipped viewer contains embedded per-product resources such as
`Html_Template_Detail.html`, `Html_Template_Index.html`,
`Html_Template_DictSearch.html`, product CSS, and viewer scripts.
The current private extraction summary is in
[LVEDVIEWER Embedded Assets](../docs/lved-viewer-assets.md).

Toolkit generic rendering therefore has this contract:

- produce navigable HTML useful for analysis and structural smoke testing;
- preserve source body HTML rather than flattening it;
- rewrite known `lved.*` protocols to local pages/assets where possible;
- copy BLOB/loose media assets needed by generated pages;
- decode SQLite media values safely when a package stores a resource payload in
  text storage rather than BLOB storage;
- report or preserve unknown product hooks instead of claiming exact parity.

Exact LVED_SQLITE3 reader parity is a separate renderer-template task.

## Toolkit Support

The `lved` command classifies payloads, validates local `.key` files beside
payloads, validates derived or explicit keys, optionally tests key candidates
recovered from a local memory dump, inspects decrypted SQLite schema/features,
and can write a plaintext SQLite copy for local analysis:

```bash
logovista-tools lved /path/to/SQLCIPHER_CORPUS --json
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-sqlite --json
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-sqlite --inspect-retained --json
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-loose-resources --json
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-reader --search 犬 \
  --search-mode exact --search-mode forward --search-mode backward \
  --search-mode partial --search-mode fulltext --json
logovista-tools lved /path/to/KOJIEN7 --search 右 --render-dir /tmp/koji-lved-pages
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/OXFPEU4 --memory-dump viewer-memory.dmp --json
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 \
  --write-decrypted /tmp/oxfpeu4.sqlite
```

When a payload has a sibling `DICTCODE.key`, `main.data.key`, payload-stem key,
or exactly one `.key` file in the payload directory, the command uses it
automatically for validation and `--write-decrypted`. Reports include only the
key file name/match rule, never the key text.

Reports deliberately do not print recovered, derived, or explicit keys.
Memory-dump candidates are counted and can be used for validation, but key
strings are not emitted. `--inspect-sqlite` records exact schema, row counts,
small value distributions, and bounded text-signal evidence; it does not emit
dictionary row text.

## Confidence

| Claim | Confidence |
|---|---|
| The inspected modern corpus payloads are SQLCipher 4 page databases | Proven for 73 local payloads |
| The inspected payloads decrypt to ordinary SQLite3 with stable core tables | Proven for 73 local payloads |
| The observed payload key path derives DB keys from dictionary id/code metadata or local `.key` files | Proven for local packages through independent key validation |
| Product serial is not part of the SQLCipher database key path | Strong for observed viewer path |
| `LVED_SQLITE3` is separate from SSED/HONMON while still being allowed to carry retained SSED-style feature files | Proven for the inspected corpus |

Do not commit decrypted databases, memory dumps, serials, or proprietary viewer
files to this repository.
