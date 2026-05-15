# LVLMultiView Packages

`LVLMultiView` packages are a separate observed LogoVista package family. They
are not classic SSED/HONMON dictionaries and they are not LVED/WebView2
SQLCipher packages.

They still expose an `.IDX` file whose magic is `SSEDINFO`, but that index is a
facade. It declares familiar component names while the readable body, index,
and metadata payloads live in LogoFontCipher-encrypted SQLite files.

Observed subfamilies:

| Subfamily | Examples | Viewer | Main package traits |
|---|---|---|---|
| Law packages | MOROKU21-26, YROPPO02/03/04/5/06/07/08 | shared viewer-resource layout | `_DCT_*`, `*.IDX`, `menuData.xml`, `blvbat`, `hlvbat`, index/metadata payloads |
| Content/search package | ESPRANT2 | dedicated viewer-resource layout | `_DCT_ESPRANT2`, `ESPRANT2.IDX`, `menuData.xml`, `blvdat`, `HANREI/` static HTML |

## Classification

The `.IDX` facade declares normal-looking component records. Law packages
declare:

```text
HONMON.DIC
FKINDEX.DIC
FHINDEX.DIC
BKINDEX.DIC
BHINDEX.DIC
GA16FULL
GA16HALF
```

ESPRANT2 declares the same search/body component names but omits `GA16FULL`.
In all observed packages, the declared component files are physically absent.
The body data is not recoverable by composing `SSEDDATA` components because
there are no component files to expand.

The facade records are still structured and useful as package evidence:

```text
law component count: 7
ESPRANT2 count:      6
component types:     00, 90, 91, 70, 71, f1, f2 where present
index ranges:        blocks 3..10 for the four declared search indexes
GA16 ranges:         zero start/end, no physical GA16 file present
tail bytes:          632 bytes after the component record table
```

Observed facade layouts:

| Package group | Component count byte | Record start | Component count |
|---|---:|---:|---:|
| YROPPO02/03/04/5/06/07/08 | `0x4d` | `0x80` | 7 |
| MOROKU21-26 | `0x4c` | `0x7f` | 7 |
| ESPRANT2 | `0x4d` | `0x80` | 6 |

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

Observed package resources and runtime cache naming evidence identify these
payload roles:

```text
blvbat   -> hore_body.db
hlvbat   -> hanrei_youshi.db
nlvbat   -> yroppo.db
nlvdat   -> mo6.db
ilvbat   -> index.db
ilvdat   -> index.db
jlvbat   -> jiko_sakuin.db
```

The ESPRANT2 payload exposes product-specific `blvdat`, `t_contents`, and
`t_search` structures. Schema classification is more reliable than filename or
string matching alone.

Observed schema roles:

| Payload | Role | Typical schema |
|---|---|---|
| `blvdat` | content/search body | `t_contents(f_ID, f_Title, f_Body)`, `t_search(f_ID, f_Anchor, f_KeyWord, f_TitleMain, f_All, ...)` |
| `blvbat` | law body table store | hundreds of `t_<law-code>` tables with `f_hore_code`, `f_rec_id`, `f_anchor`, `f_text`, `f_text_plane` |
| `hlvbat` | case digest/body store | `t_page` body rows; YROPPO also has `t_base`, category, era, subcategory tables |
| `ilvbat` / `ilvdat` | HTML index store | `t_index(f_hore_code, f_title_no, f_title_sub, f_text)` |
| `jlvbat` | subject index | `t_page(f_name, f_name_key, f_name_kana, f_anchor, ...)` |
| `nlvbat` / `nlvdat` | law metadata | `t_hore`, `t_category`, `t_era`, optional `t_subcategory` |

Corpus-level payload counts:

| Payload | Packages | Role |
|---|---:|---|
| `blvdat` | 1 | content/search body |
| `blvbat` | 13 | law body tables |
| `hlvbat` | 13 | case digest/body |
| `ilvbat` | 7 | HTML index |
| `ilvdat` | 6 | HTML index |
| `jlvbat` | 7 | subject index |
| `nlvbat` | 7 | law metadata |
| `nlvdat` | 6 | law metadata |

Body rows already contain rendered HTML fragments and plain/search text:

```text
f_text        rendered HTML fragment
f_text_plane  plain text/search text
f_anchor      anchor used by menuData.xml and internal links
```

ESPRANT2 instead stores rendered content in `t_contents.f_Body`, titles in
`t_contents.f_Title`, and search metadata in `t_search`.

## Menu Data

`menuData.xml` is UTF-8 XML. It is the package navigation tree.

Observed law structure:

```xml
<list>
  <group type="hourei">
    <item label="..." href="111S21K1_HON-sy1" genre="1" index="1" />
  </group>
</list>
```

MOROKU uses only `list/item`; YROPPO uses `list/group/item`. ESPRANT2 uses
numeric `href` values:

```xml
<list>
  <item label="..." href="000001" />
</list>
```

Menu `href` values resolve as:

```text
content_id     six-digit href equals t_contents.f_ID after zero-padding
anchor_exact   href equals an SQLite f_anchor
hore_code      href equals a law code in f_hore_code
index_row      href uses index:<code> and <code> exists in t_index.f_hore_code
viewer_special built-in viewer pages such as 50on, about, hanrei, index
```

Observed corpus-wide resolution across 14 packages:

```text
85,472 anchor_exact
17,997 index_row
 5,750 hore_code
    24 viewer_special
    18 content_id
     0 unresolved
```

## Resources, Static HTML, and Viewers

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

MOROKU law packages ship extensionless `Resources/*` files. Most observed
entries decrypt with LogoFontCipher to PDF documents. MOROKU23 has three
resource files that remain plain/unknown under the current classifier and are
reported as opaque.

ESPRANT2 ships a `HANREI/` static HTML directory with CSS/JS and 15 HTML files.
The primary menu targets resolve to `t_contents`, not directly to those static
HTML filenames.

Viewer files are useful evidence but should not be treated as authoritative by
filename alone. ESPRANT2's dedicated viewer contains both ESPRANT2-specific
`blvdat`/`t_contents` strings and leftover shared law-viewer strings.

## Toolkit Support

Use:

```bash
logovista-tools multiview /path/to/LOGOVISTA_LVLMULTI_DICTS_WINDOWS --jobs 0 --out-dir out/multiview
```

The command writes one `multiview.json` report per package and a corpus
`summary.json`. With `--write-decrypted`, it keeps local plaintext SQLite
copies under the output directory. With `--write-decrypted-resources`, it also
writes decrypted `Resources/*` assets such as PDFs where classification allows.

Reports are redacted by default: they include schemas, hashes, row counts,
menu-resolution counts, static HTML directory summaries, viewer file hashes,
and file classifications, but not full body exports.
