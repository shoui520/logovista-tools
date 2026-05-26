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
`advanced2`, and `advanced3`. Treat these as dictionary-specific native search
dimensions until query behavior is tested in a reader.

Some packages retain SSED-style or index-style files beside the SQLCipher
payload, including `res/tree.idx`, `res/*.idx`, `BHINDEX.DIC`, and
`BKINDEX.DIC`. Reader architecture must share SSED/index parsers as feature
providers when present instead of assuming `LVED_SQLITE3` packages are pure
SQLite-only books.

## Toolkit Support

The `lved` command classifies payloads, validates local `.key` files beside
payloads, validates derived or explicit keys, optionally tests key candidates
recovered from a local memory dump, inspects decrypted SQLite schema/features,
and can write a plaintext SQLite copy for local analysis:

```bash
logovista-tools lved /path/to/SQLCIPHER_CORPUS --json
logovista-tools lved /path/to/SQLCIPHER_CORPUS --jobs 12 --inspect-sqlite --json
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
