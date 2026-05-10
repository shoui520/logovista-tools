# LVED SQLCipher Packages

Modern LVED/WebView2 packages are a separate dictionary family from the
SSED/HONMON dictionaries documented elsewhere in this repository.

Observed examples:

| Dictionary | Windows payload | Mobile payload | Status |
|---|---|---|---|
| OXFPEU4 | `main.data` | `OXFPEU4.dbc` | Validated SQLCipher database |
| KQCMPROS | `main.data` | `KQCMPROS.dbc` | Validated SQLCipher database |

For OXFPEU4, the Windows `main.data` and iOS `.dbc` observed in the local
corpus are byte-identical. KQCMPROS uses the same package family, but the
observed Windows and iOS payloads differ by product/version.

## Classification

The payload is not `SSEDINFO`, not `SSEDDATA`, and not an expanded HONMON body
stream. The observed files have these traits:

- filename `main.data` in Windows packages or `*.dbc` in mobile packages;
- size is a multiple of 4096 bytes;
- first bytes do not contain the plaintext `SQLite format 3` header;
- entropy is close to random data;
- decrypts as SQLCipher 4 with 4096-byte pages.

SSED/HONMON-oriented readers cannot recover body text from this package family
because there is no SSED/HONMON body stream to compose.

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

Observed decrypted databases contain ordinary SQLite tables:

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

Important query shapes recovered from the viewer include:

```sql
SELECT id, title FROM list WHERE refid = ? AND anchor = ?
SELECT type, body, media FROM content WHERE id = ?
SELECT main FROM media WHERE type = ? AND name = ?
SELECT body, media FROM info WHERE name == ?
SELECT id, type, refid, anchor, title
  FROM list INNER JOIN search ON list.id = search.docid
  WHERE search MATCH ?
```

`list` is the search/listing layer. `content` stores body HTML or body payloads.
`media` stores referenced binary assets. `info` stores auxiliary pages. `search`
is an FTS table backing lookup.

## Toolkit Support

The `lved` command classifies payloads, validates derived or explicit local
keys, optionally tests key candidates recovered from a local memory dump, and
can write a plaintext SQLite copy for local analysis:

```bash
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/OXFPEU4 --memory-dump LVEDVIEWER.dmp --json
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 \
  --write-decrypted /tmp/oxfpeu4.sqlite
```

Reports deliberately do not print recovered, derived, or explicit keys.
Memory-dump candidates are counted and can be used for validation, but key
strings are not emitted.

## Confidence

| Claim | Confidence |
|---|---|
| OXFPEU4 Windows `main.data` and observed iOS `.dbc` are identical | Proven for local files |
| OXFPEU4/KQCMPROS are SQLCipher 4 page databases | Proven for local files |
| The observed viewer derives DB keys from dictionary id/code metadata | Proven from viewer IL and validation |
| Product serial is not part of the SQLCipher database key path | Strong for observed viewer path |
| This package family is separate from SSED/HONMON | Proven for observed OXFPEU4/KQCMPROS packages |

Do not commit decrypted databases, memory dumps, serials, or proprietary viewer
files to this repository.
