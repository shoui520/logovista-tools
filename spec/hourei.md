# Hourei SQLite Packages

`Hourei SQLite` describes the observed LogoVista電子法令 Professional package.
It is treated as its own package family. It is not classic SSED, not
`LVED_SQLITE3`, and not the later LVLMultiView law layout, even though it shares
law-domain concepts and `lved_*` link prefixes with the newer viewers.

The observed package root contains:

```text
_DataBase/
  hore_base.db
  hore_search_a.db
  horejo_base.db
  H01/ ... H28/
  M05/ ... M45/
  S02/ ... S64/
  T01/ ... T15/
  HTMLs/
  image/
_Kanpou/
  hore_kanpou.db
  hore_aramashi.db
_KanpouImg/
_Manual/
_Programs/
  LVMultiView.exe
  LVLawBrowser_00.css
  USER.db
```

The databases are ordinary SQLite files. They are not SQLCipher payloads and do
not use the LVLMultiView LogoFontCipher wrapper.

## Database Roles

Corpus scan:

```text
DB files:          8,603
body rows scanned: 538,031
body HTML bytes:   656,889,793
scan errors:       0
```

Core database roles:

| Database | Role | Observed rows |
|---|---|---:|
| `_DataBase/hore_base.db` | law metadata, categories, eras, abolished/missing-law metadata | 11,836 |
| `_DataBase/hore_search_a.db` | top-level law search table | 8,597 |
| `_DataBase/horejo_base.db` | article/title search tables | 743,270 |
| `_Kanpou/hore_kanpou.db` | official gazette metadata/body fragments | 3,739 |
| `_Kanpou/hore_aramashi.db` | gazette summaries/check rows | 17,564 |
| `_Programs/USER.db` | user state such as bookmarks, markers, notes, history | 2 in the observed install |

Per-law body shards are addressed by `f_hore_id`. The first digit selects an
era directory and the next two digits select the year:

```text
1 -> M, 2 -> T, 3 -> S, 4 -> H
401200000000000042 -> _DataBase/H01/401200000000000042.db
105400000000000337 -> _DataBase/M05/105400000000000337.db
```

Most law shard databases contain `t_page` and `t_hore`; many also contain
`T_LINK_HORE`.

## Body HTML

Law body rows in `t_page.f_text` are rendered HTML fragments, not raw SSED text.
`f_text_plane` is the plain/search form where present. The official CSS is in
`_Programs/LVLawBrowser_00.css` and uses placeholders such as:

```text
$NORMAL_FONT_NAME$
$NORMAL_FONT_SIZE$
$ARTICLE_FONT_NAME$
$ARTICLE_FONT_SIZE$
```

Program-hook string signals from `_Programs/LVMultiView.exe` and shipped HTML
confirm the database and navigation layer names without requiring a private
decompile in the public spec:

```text
hore_base.db, hore_search_a.db, horejo_base.db
t_hore, t_horejo, t_kanpou, t_miseko
OpenHoreView, GetHoreInfos, LVLawKanpou
lved_ref, lved_mark
```

Common body classes from the full body scan include:

```text
aname, box, amark, hon_gou, ahref, hon_jyo, hon_kou, fusoku,
fu_jyo, fu_kou, hon_label, fu_label, hon_ind, hon_ind1, fu_gou,
header, update, maegaki, mat, betsuhyo, yoshiki
```

Observed link/resource prefixes:

```text
lved_mark&&<anchor>              local anchor marker
lved_ref&&<anchor>               same-law/local reference
lved_ref&1:<hore_id>             law-to-law reference
lved_ref&1:<hore_id>&<anchor>    law-to-law reference to an article anchor
lved_ref&4:<hore_id>:<id>        future/revision-law hook; deferred
lved_ref:<kana>                  kana/search-panel style reference
lved_unsafe                      unsafe/deferred viewer hook
```

Only a small number of image `src` references were observed in body rows during
the full scan. They resolve against `_DataBase/image/` in the current toolkit
showcase path.

## Search and Navigation Surfaces

The recovered viewer strings and schema identify these first-class surfaces:

- keyword law search through `_DataBase/hore_search_a.db`;
- article/title search through `_DataBase/horejo_base.db`;
- cached law navigation HTML under `_DataBase/HTMLs/I/` and
  `_DataBase/HTMLs/P/` for 8,725 laws;
- cached full law body HTML under `_DataBase/HTMLs/H/` for a 281-law subset;
- kana index-panel HTML in `_Programs/index_panel.html` encoded as CP932;
- category, institution, era, abolished-law, and missing-law metadata from
  `_DataBase/hore_base.db`;
- kana-style references through `lved_ref:<kana>`;
- official-gazette search/content through `_Kanpou/hore_kanpou.db` and
  `_Kanpou/hore_aramashi.db`;
- manual/help content under `_Manual/`;
- user-only state under `_Programs/USER.db`.

`USER.db` should not be treated as dictionary content.

Toolkit search probes rank exact law-name/abbreviation matches first, then
prefix matches, then containing matches. This matters because the raw search
tables are broad LIKE-friendly tables rather than pre-sorted reader result
lists.

## Toolkit Support

Use:

```bash
logovista-tools hourei /path/to/hourei-package-or-corpus --out-dir out/hourei
logovista-tools hourei /path/to/hourei-package-or-corpus --deep --out-dir out/hourei-deep
logovista-tools hourei /path/to/hourei-package-or-corpus --write-showcase --query 民法 --query 会社法
```

The `hourei` command writes one `hourei.json` report per package plus a corpus
`summary.json`. `--deep` scans every per-law body shard for HTML classes,
links, image references, and SQLite errors. `--write-showcase` writes bounded
reader-oriented HTML pages that preserve body fragments, rewrite known
`lved_*` links to local pages/fragments where possible, make mode-4
future/revision hooks inert with structured `data-lv-*` metadata, decode the
CP932 kana panel, render cached index/preview pages where available, and copy
resolved image assets under the private report directory. The showcase manifest
includes validation counters for missing local links/assets and leaked live
`lved_*` hrefs.

This is reader-oriented inspection, not export support. Exact official viewer
chrome, user-state behavior, and every search dialog are intentionally outside
the current scope.
