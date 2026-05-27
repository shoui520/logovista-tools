# Hourei Corpus Findings

LogoVista電子法令 Professional is its own observed package family. It is a
plain-SQLite law-browser package and a precursor to LV Law MultiView, but it is
not packaged like LVLMultiView and has no SSEDINFO facade.

## Observed Layout

```text
_DataBase/     core metadata, search DBs, law body shard DBs, HTMLs/, image/
_Kanpou/       official-gazette SQLite DBs
_KanpouImg/    loose gazette images
_Manual/       manual/help HTML and image resources
_Programs/     viewer executable, CSS, USER.db
```

Full deep scan:

```text
DB files:          8,603
body rows scanned: 538,031
body HTML bytes:   656,889,793
scan errors:       0
```

Core database counts:

```text
_DataBase/hore_base.db       tables=11 rows=11,836
_DataBase/hore_search_a.db   tables=1  rows=8,597
_DataBase/horejo_base.db     tables=4  rows=743,270
_Kanpou/hore_kanpou.db       tables=4  rows=3,739
_Kanpou/hore_aramashi.db     tables=3  rows=17,564
_Programs/USER.db            tables=9  rows=2
```

Top table occurrence counts across all DB files:

```text
t_page       8,598
t_hore       8,502
T_LINK_HORE  8,408
```

## Body HTML And Links

The body rows already contain rendered HTML fragments in `t_page.f_text`.
Common classes include `aname`, `box`, `amark`, `hon_gou`, `ahref`,
`hon_jyo`, `hon_kou`, `fusoku`, `fu_jyo`, and `fu_kou`.

The full body scan observed:

```text
1,011,028 lved_mark links
   25,632 local lved_ref&& links
  488,990 mode-1 law references
      658 mode-4 future/revision hooks
      734 lved_unsafe hooks
       74 GIF image references
```

All observed body image references resolve against the package resource
directories.

`_DataBase/HTMLs/I/` and `_DataBase/HTMLs/P/` contain paired cached index and
preview HTML for 8,725 laws. `_DataBase/HTMLs/H/` contains cached full-body
HTML for a 281-law subset. `_Programs/index_panel.html` is a CP932 kana index
panel that emits `lved_ref:<kana>` search links.

Native law search needs explicit ranking. Exact law-name/abbreviation matches
must sort before prefix and containing hits; otherwise short queries such as
`民法` can be buried behind unrelated containing titles.

## Toolkit Commands

```bash
logovista-tools hourei /path/to/hourei-package-or-corpus --out-dir out/hourei
logovista-tools hourei /path/to/hourei-package-or-corpus --deep --out-dir out/hourei-deep
logovista-tools hourei /path/to/hourei-package-or-corpus --write-showcase --query 民法 --query 会社法
```

The `hourei --write-showcase` path rewrites known `lved_*` navigation to local
pages/fragments where possible, makes mode-4 future/revision hooks inert with
structured metadata, renders cached index/preview pages, decodes the kana
panel, and copies resolved `_DataBase/image/` assets. The readiness run at the
time of this update produced zero missing local hrefs, zero missing image refs,
and no leaked live `lved_*` hrefs in the generated showcase.

The raw format notes live in [`../spec/hourei.md`](../spec/hourei.md).
