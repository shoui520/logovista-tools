# LVLMultiView Law Packages

`LVLMultiView` law products are a separate observed LogoVista package family.
They are not classic SSED/HONMON dictionaries and they are not LVED/WebView2
SQLCipher packages.

Observed examples:

| Dictionary | Viewer | Main package traits |
|---|---|---|
| YROPPO08 | `LOGOVISTAMULTIVIEW/LVLMultiView.exe` | `_DCT_YROPPO08`, `YROPPO08.IDX`, `menuData.xml`, `*lvbat` payloads |
| MOROKU26 | `LOGOVISTAMULTIVIEW/LVLMultiView.exe` | `_DCT_MOROKU26`, `MOROKU26.IDX`, `menuData.xml`, `*lvbat`/`*lvdat` payloads |

## Classification

The package has an `.IDX` file whose magic is `SSEDINFO`, but it is a facade.
It declares familiar component names:

```text
HONMON.DIC
FKINDEX.DIC
FHINDEX.DIC
BKINDEX.DIC
BHINDEX.DIC
GA16FULL
GA16HALF
```

Those physical component files are absent in the observed packages. The body
data is not recoverable by composing `SSEDDATA` components because there are no
component files to expand. The readable body payloads decrypt directly to
SQLite.

The facade records are still structured and useful as package evidence:

```text
component count: 7
component types: 00, 90, 91, 70, 71, f1, f2
index ranges:    blocks 3..10 for the four declared search indexes
GA16 ranges:     zero start/end, no physical GA16 file present
tail bytes:      632 bytes after the seven component records
```

Two facade layouts have been observed:

| Product | Component count byte | Record start |
|---|---:|---:|
| YROPPO08 | `0x4d` | `0x80` |
| MOROKU26 | `0x4c` | `0x7f` |

The record body layout is otherwise the same as normal `SSEDINFO`: type at
record offset `0x03`, start block at `0x04`, end block at `0x08`, metadata at
`0x0c`, filename length at `0x10`, and filename bytes at `0x11`.

## Payload Encryption

The payload files use the same LogoFontCipher AES-CBC mechanism documented for
Windows sidecars:

```text
passphrase: SHA-256("LogoFontCipher")
key:        digest[0:16]
IV:         digest[16:32]
padding:    PKCS#7
```

Every observed `*lvbat` / `*lvdat` payload decrypts to `SQLite format 3`.
This is different from LVED `main.data` / `.dbc`, which uses SQLCipher page
encryption.

## Payload Roles

The viewer binary contains UTF-16 strings that name the decrypted cache files:

```text
blvbat   -> hore_body.db
hlvbat   -> hanrei_youshi.db
nlvbat   -> yroppo.db
nlvdat   -> mo6.db
ilvbat   -> index.db
ilvdat   -> index.db
jlvbat   -> jiko_sakuin.db
```

Observed schema roles:

| Payload | Role | Typical schema |
|---|---|---|
| `blvbat` | law body table store | hundreds of `t_<law-code>` tables with `f_hore_code`, `f_rec_id`, `f_anchor`, `f_text`, `f_text_plane` |
| `hlvbat` | case digest/body store | `t_page` body rows; YROPPO08 also has `t_base`, category, era, subcategory tables |
| `ilvbat` / `ilvdat` | HTML index store | `t_index(f_hore_code, f_title_no, f_title_sub, f_text)` |
| `jlvbat` | subject index | `t_page(f_name, f_name_key, f_name_kana, f_anchor, ...)` |
| `nlvbat` / `nlvdat` | law metadata | `t_hore`, `t_category`, `t_era`, optional `t_subcategory` |

Observed counts:

| Product | Payload | Tables | Rows | Role |
|---|---:|---:|---:|---|
| MOROKU26 | `blvbat` | 374 | 54,812 | law body |
| MOROKU26 | `hlvbat` | 1 | 24,424 | case digest/body |
| MOROKU26 | `ilvdat` | 1 | 3,040 | HTML index |
| MOROKU26 | `nlvdat` | 3 | 362 | law metadata |
| YROPPO08 | `blvbat` | 382 | 67,395 | law body |
| YROPPO08 | `hlvbat` | 5 | 43,202 | case digest/body |
| YROPPO08 | `ilvbat` | 1 | 8 | HTML index |
| YROPPO08 | `jlvbat` | 1 | 17,833 | subject index |
| YROPPO08 | `nlvbat` | 4 | 416 | law metadata |

Body rows already contain rendered HTML fragments and plain/search text:

```text
f_text        rendered HTML fragment
f_text_plane  plain text/search text
f_anchor      anchor used by menuData.xml and internal links
```

## Menu Data

`menuData.xml` is UTF-8 XML. It is the package navigation tree.

Observed structure:

```xml
<list>
  <group type="hourei">
    <item label="..." href="111S21K1_HON-sy1" genre="1" index="1" />
  </group>
</list>
```

MOROKU26 uses only `list/item`; YROPPO08 uses `list/group/item`.

Menu `href` values resolve as:

```text
anchor_exact   href equals an SQLite f_anchor
hore_code      href equals a law code in f_hore_code
index_row      href uses index:<code> and <code> exists in t_index.f_hore_code
viewer_special built-in viewer pages such as 50on, about, hanrei, index
```

Observed resolution:

| Product | Unique hrefs | Resolution |
|---|---:|---|
| MOROKU26 | 8,834 | 5,569 anchors, 2,559 index rows, 702 law codes, 4 viewer-special |
| YROPPO08 | 8,041 | 7,826 anchors, 207 law codes, 8 index rows |

No unresolved `menuData.xml` hrefs remain after applying these rules.

## Resources and Templates

`Templates/` contains CSS, PNG icons, and JavaScript used by the embedded
MSHTML renderer. CSS values include viewer placeholders such as:

```text
#[fontcolor]
#[backcolor]
#[linkcolor]
#[midashicolor]
[font]
[fontsize]
[lineheight]
```

MOROKU26 additionally has `Resources/inshizei`, `Resources/minji`, and
`Resources/zeihou`. These are LogoFontCipher-encrypted PDF files with no file
extension in the package. After decryption they start with `%PDF-1.6`.

## Toolkit Support

Use:

```bash
logovista-tools multiview /path/to/Unclassified_win --jobs 0 --out-dir out/multiview
```

The command writes one `multiview.json` report per package and a corpus
`summary.json`. With `--write-decrypted`, it keeps local plaintext SQLite
copies under the output directory. With `--write-decrypted-resources`, it also
writes decrypted `Resources/*` assets such as PDFs.

Reports are redacted by default: they include schemas, hashes, row counts,
menu-resolution counts, and file classifications, but not full body exports.
