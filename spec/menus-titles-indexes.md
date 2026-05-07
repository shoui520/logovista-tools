# Menus, Titles, and Indexes

`MENU.DIC`, `*TITLE.DIC`, and `*INDEX.DIC` component structures.

## Menu Components

`MENU.DIC` (`0x01`) is an EPWING-style menu/body stream, not an index page
tree. Some products keep it as a one-block stub containing only `1f03` or
`1f02 1f03`; KOJIEN7, OUKOKU11, KANJIGN5, HAFRAN, and KQCOLEXP show this
minimal form in the local corpus.

Other products store readable menu trees. GENIUSEB, HAIKSAIJ, IBIO5, and
NKGORIN2 all contain menu headings, section markers, and destination links.
The common menu link form is:

```text
1f43              menu-link start
...               visible JIS/gaiji label text
1f63              menu-link end
00 00 00 02 0002  packed-BCD block 2, packed-BCD offset 2
```

The destination is carried after the closing control, so a text decoder must
consume those six bytes. If it does not, pointer bytes are mis-decoded as
garbage characters appended to labels. Section markers use the normal
`1f09 xxxx` form; preserving them gives menu levels such as `0001`, `0002`,
and `0003`.

The destination payload is six bytes: four packed-BCD decimal bytes for the
logical block and two packed-BCD decimal bytes for the offset. In GENIUSEB, the
first menu item has payload `00 02 56 78 00 02`, which resolves to block
`25678`, offset `2`; that resolves to `HONMON.DIC` at component-relative
offset `2`. Other GENIUSEB menu items point back into `MENU.DIC` itself.

Some menu streams use the older `1f42 ... 1f62` wrapper instead. In HAIKSAIJ,
many of those labels include a no-op `1f00` immediately after `1f42`; the
parser treats it as wrapper padding and still extracts the label and packed-BCD
destination.

The `menus` command writes:

```text
raw_menus.jsonl   flat menu records with path, links, and destinations
menu_tree.json    nested menu records grouped by inferred section depth
menus_summary.json component-level counts and parser statistics
```

Representative target resolution from the local corpus:

```text
GENIUSEB  destinations=79     resolved=79     target kinds: body=67, menu=12
HAIKSAIJ  destinations=2,667  resolved=2,667  target kinds: body=2,617, menu=50
IBIO5     destinations=65,015 resolved=65,015 target kinds: body=61,082, menu=3,933
NKGORIN2  destinations=10     resolved=10     target kinds: body=9, menu=1
```

## Title Components

`*TITLE.DIC` components frequently contain readable headword/title lines after
SSED expansion. Examples include:

```text
FKTITLE.DIC
FHTITLE.DIC
BKTITLE.DIC
BHTITLE.DIC
CRTITLE.DIC
KWTITLE.DIC
```

The paired title/index roles observed so far are:

```text
0x04 FKTITLE.DIC  title stream for FKINDEX forward tagged lookup rows
0x05 FHTITLE.DIC  title stream for FHINDEX forward simple lookup rows
0x06 BKTITLE.DIC  title stream for BKINDEX backward tagged lookup rows
0x07 BHTITLE.DIC  title stream for BHINDEX backward simple lookup rows
0x03 KWTITLE.DIC  title stream for KWINDEX keyword groups/direct rows
0x0a CRTITLE.DIC  title stream for CRINDEX cross-reference groups/direct rows
```

These are not full definitions, but they are important for search/index
reconstruction. `KWTITLE.DIC` is a normal readable stream; OUKOKU11 has keyword
titles such as `いち【一】`, and NANMED20 has pipe-delimited keyword triples.
In large dictionaries where HONMON is an ID table, title streams can still
contain hundreds of thousands or millions of raw headword/title lines.

## Index Components

`*INDEX.DIC` components are binary search trees over 2048-byte pages. They
contain lookup keys, branch pages, leaf pages, and pointers into body/title
components. Some bytes decode as text if treated naively, but the useful data
comes from parsing the page records.

Common index components:

```text
FKINDEX.DIC
FHINDEX.DIC
BKINDEX.DIC
BHINDEX.DIC
CRINDEX.DIC
KWINDEX.DIC
```

Component types observed in `SSEDINFO`:

```text
0x90  FKINDEX.DIC  forward tagged index
0x91  FHINDEX.DIC  forward simple headword index
0x70  BKINDEX.DIC  backward tagged index
0x71  BHINDEX.DIC  backward simple headword index
0x80  KWINDEX.DIC  keyword index
0x81  CRINDEX.DIC  cross-reference index
```

The toolkit parses the common `FK/FH/BK/BH` page formats, direct and grouped
`KWINDEX` rows, and direct and grouped `CRINDEX` rows. The layouts below were
validated against Japanese, English, Spanish, French, science, medical, and
collocation dictionaries, including HAESPJPN, GENIUSEB, HAFRAN, NANMED20,
OUKOKU11, IPHYCHE5, KENCOLLO, KQJCOLLO, and KOJIEN7.

Representative parser coverage from the local corpus:

```text
HAESPJPN  FK/BK tagged + FH/BH simple indexes   unknown leaf subrecords: 0
GENIUSEB  FH/BH simple indexes                  unknown leaf subrecords: 0
NANMED20  FH/BH simple + KWINDEX grouped        unknown leaf subrecords: 0
OUKOKU11  FK/FH/BK/BH + KWINDEX grouped         unknown leaf subrecords: 0
IPHYCHE5  FK/FH/BK/BH + KWINDEX direct/grouped  unknown leaf subrecords: 0
KENCOLLO  FH/BH + large mixed KWINDEX           unknown leaf subrecords: 0
KQJCOLLO  FK/FH/BK/BH + CRINDEX grouped         unknown leaf subrecords: 0
KOJIEN7   FK/FH/BK/BH + CRINDEX grouped         unknown leaf subrecords: 0
```

### Index Page Header

Every expanded index page begins with:

```text
offset  size  meaning
0x00    2     page flags / slot-size word, big endian
0x02    2     row or subrecord count, big endian
0x04    ...   page records
```

Pages whose first word has bit `0x8000` clear are branch pages. Pages whose
first word has bit `0x8000` set are leaf pages.

Branch page words observed include:

```text
601c 601e 6020
401e 4020
201e 2020
001e 0020
```

The low bits encode the branch slot size:

```text
slot_size = (page_word & 0x3f) + 4
```

Each branch slot is:

```text
offset  size             meaning
0x00    slot_size - 4    padded JIS key boundary
...     4                child logical block number, big endian
```

The child is a 32-bit logical block number. In small dictionaries the high two
bytes are usually zero, which can make the field look like a 16-bit pointer.
Large dictionaries such as KOJIEN7 require the full 32 bits.

### Simple Leaf Pages

`FHINDEX.DIC` (`0x91`) and `BHINDEX.DIC` (`0x71`) usually use simple leaf
records:

```text
offset  size     meaning
0x00    1        key byte length
0x01    n        JIS/gaiji key bytes
...     4        body logical block, big endian
...     2        body offset in block, big endian
...     4        title logical block, big endian
...     2        title offset in block, big endian
```

Examples:

```text
HAFRAN FHINDEX  ACCENT -> body 4:1570, title 4:1570
GENIUSEB FHINDEX read-ish keys -> body HONMON blocks, title FHTITLE blocks
KOJIEN7 FHINDEX ?ASHURA' -> body HONMON ID-table anchor, title FHTITLE row
```

If a dictionary has no `*TITLE.DIC`, the title pointer can equal the body
pointer.

### Tagged Leaf Pages

`FKINDEX.DIC` (`0x90`) and `BKINDEX.DIC` (`0x70`) usually use tagged leaf
subrecords. A search-key group starts with:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    2     target count hint, big endian
0x04    n     JIS/gaiji search key bytes
```

Each following target row starts with:

```text
offset  size  meaning
0x00    1     tag 0xc0
0x01    1     target/display key byte length
0x02    n     JIS/gaiji target key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     title logical block, big endian
...     2     title offset in block, big endian
```

The same search key can have multiple target rows. Page boundaries can occur
inside a group, so the parser carries the current `0x80` search key across
leaf pages when a page begins with a `0xc0` target row.

### Cross-Reference Leaf Pages

`CRINDEX.DIC` (`0x81`) is used with `CRTITLE.DIC` (`0x0a`). It has two leaf
row forms.

Direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     CRTITLE logical block, big endian
...     2     CRTITLE offset in block, big endian
```

Grouped rows:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji cross-reference key bytes
...     4     CRTITLE logical block, big endian
...     2     CRTITLE offset in block, big endian
```

Following target rows are compact body pointers:

```text
offset  size  meaning
0x00    1     tag 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
```

Page boundaries can occur inside a group, so the parser carries the current
group key, count hint, and `CRTITLE` pointer across leaf pages. KOJIEN7 and
KQJCOLLO both parse with no unknown leaf bytes under this model.

### Keyword Leaf Pages

`KWINDEX.DIC` (`0x80`) is used with `KWTITLE.DIC` (`0x03`). It has direct rows,
grouped rows, and continuation target pages.

Direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji keyword bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     KWTITLE logical block, big endian
...     2     KWTITLE offset in block, big endian
```

Grouped rows:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji keyword bytes
...     4     KWTITLE logical block, big endian
...     2     KWTITLE offset in block, big endian
```

Following target rows are seven bytes:

```text
offset  size  meaning
0x00    1     tag 0xb0 or 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
```

The grouped target rows do not carry their own title pointer; the surrounding
group's `KWTITLE` pointer applies to each target. IPHYCHE5 uses many direct
keyword rows, OUKOKU11 and NANMED20 use grouped keyword rows, and KENCOLLO uses
a mix of both.

Direct index parsing is useful for:

- deriving all exact lookup keys without SQLite;
- pairing title lines with body addresses;
- reconstructing aliases and subentries;
- resolving dense-HONMON ID dictionaries cleanly where possible.

Raw-only probes confirm that indexes can expose useful lookup strings even
when no `*TITLE.DIC` component is present. For example, `HABGESPA` exposes
Spanish keys in `FHINDEX.DIC` / `BHINDEX.DIC`, and `HAFRAN` exposes French
keys in the same forward/backward index components. Those decoded strings are
not full body entries; they are search keys and pointers into the body/title
layer.
