# LVLMultiView Corpus Findings

LVLMultiView packages are not classic SSED/HONMON packages and are not
`LVED_SQLITE3` packages. They ship `menuData.xml`, a small SSEDINFO-magic
`.IDX` facade, and LogoFontCipher-wrapped SQLite payloads.

The observed corpus has 14 packages: ESPRANT2, MOROKU21 through MOROKU26, and
YROPPO02/03/04/5/06/07/08.

## SSEDINFO Facade

The law subfamily declares seven familiar component records:

```text
HONMON.DIC, FKINDEX.DIC, FHINDEX.DIC, BKINDEX.DIC, BHINDEX.DIC,
GA16FULL, GA16HALF
```

ESPRANT2 declares six records and omits `GA16FULL`. In every observed package,
the declared component files are absent; readable data comes from encrypted
SQLite, not from `SSEDDATA` components.

Observed facade layouts:

```text
7 YROPPO packages:  count byte 0x4d, record start 0x80, 7 components
6 MOROKU packages:  count byte 0x4c, record start 0x7f, 7 components
1 ESPRANT2 package: count byte 0x4d, record start 0x80, 6 components
```

## Payload Roles

Observed payload classification across the 14 packages:

| Payload | Count | Storage | Role |
|---|---:|---|---|
| `blvdat` | 1 | LogoFontCipher SQLite | content/search body |
| `blvbat` | 13 | LogoFontCipher SQLite | law body tables |
| `hlvbat` | 13 | LogoFontCipher SQLite | case digest/body |
| `ilvbat` | 7 | LogoFontCipher SQLite | HTML index |
| `ilvdat` | 6 | LogoFontCipher SQLite | HTML index |
| `jlvbat` | 7 | LogoFontCipher SQLite | subject index |
| `nlvbat` | 7 | LogoFontCipher SQLite | law metadata |
| `nlvdat` | 6 | LogoFontCipher SQLite | law metadata |

ESPRANT2 is the non-law variant observed so far. It has one `blvdat` payload
with `t_contents(f_ID, f_Title, f_Body)` and `t_search(...)` tables, plus
`t_dummy`. The decrypted database has 18,203 content rows and 44,477 search
rows. Its `menuData.xml` hrefs are six-digit numeric content IDs such as
`000001`, all resolving to `t_contents.f_ID`.

ESPRANT2 is packaged with a dedicated viewer-resource layout rather than the
shared law-package layout. Private runtime/resource evidence and the decrypted
payload schema identify `blvdat`, `t_contents`, and `t_search` as the relevant
content/search layer. ESPRANT2 also ships a `HANREI/` static HTML directory
with 15 HTML files; this appears to be a browsable example/help-style appendix
rather than the primary body store.

Law viewer binaries contain UTF-16 strings naming decrypted cache targets:
`hore_body.db`, `hanrei_youshi.db`, `index.db`, `jiko_sakuin.db`, `yroppo.db`,
and `mo6.db`.

## Menu Resolution

Across all 14 packages, `menuData.xml` is a real navigation tree. Current menu
resolution totals are:

```text
85,472 anchor_exact
17,997 index_row
 5,750 hore_code
    24 viewer_special
    18 content_id
     0 unresolved
```

ESPRANT2 renders numeric `menuData.xml` `t_contents` targets first, rather than
arbitrary first rows. Law packages render `hore_code`, exact `f_anchor`, and
`index:<code>` menu targets through decoded SQLite payloads. Viewer-special
targets (`about`, `hanrei`, `index`, `50on`) are represented as explicit
reader-interface surfaces instead of unresolved menu failures.

## Loose Resources

The MOROKU law-package `Resources/*` entries named `inshizei`, `minji`,
`zenkoku`, and `zeihou` are encrypted PDF assets. Across MOROKU21-26 there are
20 such resources. Seventeen decrypt with the Windows LogoFontCipher variant;
the three MOROKU23 files decrypt with the Mac OS X LogoFontCipher variant. In
both cases the decrypted payload is a normal PDF and should be written as
`.pdf` without rewriting the document bytes.

YROPPO body fragments reference `kagi.png`; the showcase resolves this from the
package `Templates/` or `Help/image/` directories.

## Toolkit Commands

```bash
logovista-tools multiview /path/to/multiview-corpus --jobs 12 --out-dir out/multiview
logovista-tools multiview /path/to/multiview-corpus \
  --write-showcase --showcase-limit 80 --jobs 12 --out-dir out/multiview
```

The latest readiness pass rendered bounded reader-oriented HTML samples for all
14 packages and produced:

```text
menu unresolved:          0
showcase deferred:        0
missing hrefs:            0
missing srcs:             0
leaked lved_* protocols:  0
table/cell balance gaps:  0
```

The raw format notes live in [`../spec/multiview.md`](../spec/multiview.md).
