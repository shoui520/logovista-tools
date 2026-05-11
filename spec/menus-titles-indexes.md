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
Payload `00 00 00 00 00 00` is a null/sentinel destination. It is not an
unresolved component pointer. Some selector-style menu/title components,
especially `MUL*.DIC` / `MULTI*.DIC` families, can contain null destinations
by design.

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
GENIUSEB  destinations=79     resolved=79     null=0     unresolved=0
HAIKSAIJ  destinations=2,667  resolved=945    null=1,722 unresolved=0
IBIO5     destinations=65,015 resolved=65,015 null=0     unresolved=0
HKKIGAK6  destinations=5,163  resolved=5,101  null=62    unresolved=0
KQEJMED2  destinations=99,157 resolved=99,130 null=27    unresolved=0
NKGORIN2  destinations=10     resolved=10     null=0     unresolved=0
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
0x09 BATITLE.DIC  alternate title stream observed in IGOSHO
0x0d MUL*.DIC     title stream paired with 0xa1 MULTI selector indexes
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
0x30  KINDEX.DIC   body-only tagged index
0x60  HINDEX.DIC   body-only simple index
0x90  FKINDEX.DIC  forward tagged index
0x91  FHINDEX.DIC  forward simple headword index
0x92  FAINDEX.DIC  forward alternate/simple index
0x70  BKINDEX.DIC  backward tagged index
0x71  BHINDEX.DIC  backward simple headword index
0x72  BAINDEX.DIC  backward alternate/simple index
0x80  KWINDEX.DIC  keyword index
0x81  CRINDEX.DIC  cross-reference index
0xa1  MUL*.DIC     MULTI selector index
```

Observed text-like sidecars and index-navigation helpers are not B-tree index
components:

```text
0x02  RIGHT.DIC     rights/copyright text stream
0x20  TOC.DIC       table-of-contents text stream
0x28  IDXJUMP.DIC   index-jump text stream
```

KQSYNONM also declares a type-`0x27` `INDEX.DIC` outlier that expands to a
text-like stream. lvcore classifies this as a text-like resource component,
not as a structured index page tree. KCOMPEJ2 has a loose `RIGHT.DIC`
`SSEDDATA` file that is not listed in its main `SSEDINFO` catalog.

The toolkit and the clean lvcore reader parser parse the common `FK/FH/BK/BH`
page formats, direct and grouped `KWINDEX` rows, direct and grouped `CRINDEX`
rows, the body-only `0x30`/`0x60` variants, simple alternate `0x72`/`0x92`
pages, and the `0xa1` MULTI selector index variant. Known tagged families
carry group context across continuation leaf pages; unsupported or malformed
index rows should be reported as diagnostics instead of silently returning
empty parse results. The layouts below were
validated against Japanese, English, Spanish, French, science, medical, and
collocation dictionaries, including HAESPJPN, GENIUSEB, HAFRAN, NANMED20,
OUKOKU11, IPHYCHE5, KENCOLLO, KQJCOLLO, KOJIEN7, 45KAGAKU, and KQSYNONM.

An earlier 169-package Windows SSED component-forensics pass reported zero
unknown index leaf subrecords. The only index residual is `NANDOKU2`
`FHINDEX.DIC`, which ends with a 5-byte partial physical page tail after all
full 2048-byte pages are consumed. Three of those tail bytes are nonzero; the
valid rows before the tail parse normally.

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
6068
401e 4020
201e 2020
001e 0020
```

The low byte encodes the branch slot size:

```text
slot_size = (page_word & 0xff) + 4
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

Branch keys are child upper-bound keys, not child lower-bound keys. During
lookup, readers descend through the first branch row whose padded key is greater
than or equal to the encoded search key. This is visible in ordinary LogoVista
indexes: for example, HAESPJPN `FKINDEX.DIC` root row `えんぶれむ` points to a
lower branch page whose final row is also `えんぶれむ`. The last branch row of
the final sibling page normally uses an all-`ff` key as an open-ended sentinel;
decoded as text this appears as an empty key.

Observed large Japanese indexes use depth-dependent branch key widths:

```text
nearest parent of leaves      32 key bytes  page words 4020/0020/2020
one branch level above        30 key bytes  page words 401e/001e/201e
root above that               28 key bytes  page word  601c
```

Two-level trees commonly use a `601e` root and `4020` / `2020` lower branch
pages. Latin-only indexes can shrink below these caps; GENIUSEB uses 14-byte
branch keys (`600e`, `400e`, `000e`, `200e`) because its upper-bound keys fit.
The experimental writer follows this upper-bound/sentinel model.

The upper byte/bits of the page word are page flags. Earlier probes treated
only the low six bits as the slot-size field, but corpus-wide branch-page
parsing found words such as `6068`; the full low byte is required. Valid
observed branch slots include the compact 6-byte form: two key bytes plus a
four-byte child block.

### Simple Leaf Pages

`FHINDEX.DIC` (`0x91`), `FAINDEX.DIC` (`0x92`), `BHINDEX.DIC` (`0x71`), and
`BAINDEX.DIC` (`0x72`) usually use simple leaf records:

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

Observed ASCII Latin lookup keys are stored as uppercase row-3 JIS cells. For
example, GENIUSEB `ALPHA` uses `23 41 23 4c 23 50 23 48 23 41`, and HAESPJPN
`PONER` uses `23 50 23 4f 23 4e 23 45 23 52`. This is an index-key encoding
rule; title and body streams can still preserve lowercase display text through
ordinary `1f04` / `1f05` halfwidth spans.

Searchable dictionaries also emit lookup aliases that are not identical to the
visible headword. Japanese headword separators, spaces, punctuation, and
hyphen-like marks are commonly absent from the lookup key, and katakana reading
keys can be folded to hiragana. KOUJIEN-style display text such as
`あん‐き 【安危】` therefore needs at least a reading lookup alias like `あんき`
and usually a bracketed spelling alias like `安危`. This alias policy lives in
the index rows, not in the title/body renderer. Writer-v0 preserves display
strings while emitting raw and normalized lookup aliases; dictionary-local
gaiji are still allocated when a lookup key cannot be represented in native
JIS/CP932 cells.

If a dictionary has no `*TITLE.DIC`, the title pointer can equal the body
pointer.

Some simple leaves contain no keys at all and instead store a compact pointer
table. The observed row size is 13 bytes:

```text
offset  size  meaning
0x00    4     body logical block, big endian
0x04    2     body offset in block, big endian
0x06    1     row flag / marker, not yet semantically named
0x07    4     title logical block, big endian
0x0b    2     title offset in block, big endian
```

This shape appears in 45KAGAKU `MUL2_1_2.DIC` / `MUL2_2_2.DIC`. The pointer
rows are structurally complete; the one-byte flag is preserved as an observed
field until renderer behavior gives it a stronger name.

### MULTI Descriptor Components

`MULTI*.DIC` components have type `0xff`. They are one-block selector
descriptors for additional search modes, not text streams and not B-tree pages.
They point at the associated `MUL*` title/index/menu components by type,
logical start block, and block count.

The descriptor header is:

```text
offset  size  meaning
0x00    2     selector record count, big endian
0x02    14    reserved/zero in observed files
0x10    ...   selector records
```

Each selector record is variable length:

```text
offset  size  meaning
0x00    1     component-reference count
0x01    1     record subtype/reserved, observed 0x00
0x02    30    null-padded JIS/gaiji selector label
0x20    16*n  component references
```

Each 16-byte component reference is:

```text
offset  size  meaning
0x00    1     component type
0x01    1     subtype/reserved, observed 0x00
0x02    4     component logical start block, big endian
0x06    4     component block count, big endian
0x0a    6     flags / selector metadata, preserved as raw hex
```

Across the observed 19 `MULTI*.DIC` files, all references match a declared
`SSEDINFO` element by component type, start block, and block count, and all
trailing bytes after the declared selector records are zero. Examples:

```text
GENIUSEB MULTI1  labels 単語1(成句)..単語4(成句)
                 refs 0x0d MUL1_1_1.DIC + 0xa1 MUL1_1_2.DIC
KQNEWEJ6 MULTI2  labels 成句1..成句4
                 refs 0xa1 MUL2_1_1.DIC + 0x0d MUL2_1_2.DIC
EJJE100  MULTI1  label 分類
                 refs 0x01 MUL1_1_1.DIC + 0x05 MUL1_1_2.DIC + 0x91 MUL1_1_3.DIC
```

The same descriptor can point multiple selector labels at the same component
pair. That appears to model multi-field search UI slots, for example
`単語1(成句)` through `単語4(成句)`.

### MULTI Selector Index Pages

Type `0xa1` `MUL*.DIC` files are B-tree indexes used by `MULTI*.DIC`
selectors. Branch pages use the normal index branch slot grammar. Leaf pages
use tagged rows that differ from `0x90`/`0x70`: target rows do not carry target
key text, only body/title pointer pairs.

Direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     title logical block, big endian
...     2     title offset in block, big endian
```

Grouped rows:

```text
offset  size  meaning
0x00    1     tag 0x80
0x01    1     key byte length
0x02    4     target count hint, big endian
0x06    n     JIS/gaiji search key bytes
```

Target rows:

```text
offset  size  meaning
0x00    1     tag 0xc0
0x01    4     body logical block, big endian
0x05    2     body offset in block, big endian
0x07    4     title logical block, big endian
0x0b    2     title offset in block, big endian
```

Pure target-continuation pages often start with page word `0x9000`; mixed
group/direct pages often start with `0xd000`. The parser carries the current
group key across pages, just like the tagged and keyword families.

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
leaf pages when a page begins with a `0xc0` target row. Official simple indexes
also split duplicate keys across adjacent leaves. The branch tree remains
searchable because branch rows are upper-bound keys; a lookup lands on the
first duplicate-key leaf and then scans forward through following matches.

Tagged pages can also contain direct rows:

```text
offset  size  meaning
0x00    1     tag 0x00
0x01    1     key byte length
0x02    n     JIS/gaiji key bytes
...     4     body logical block, big endian
...     2     body offset in block, big endian
...     4     title logical block, big endian
...     2     title offset in block, big endian
```

This direct-row form is not a separate component type; it can appear mixed into
otherwise tagged `0x70`/`0x90` pages.

### Body-Only Tagged Leaf Pages

`KINDEX.DIC` (`0x30`) uses the same grouped structure as tagged
`FKINDEX.DIC`/`BKINDEX.DIC`, but target rows carry only a body pointer:

```text
0x80 group row:  tag, key length, 2-byte target count, key bytes
0xc0 target row: tag, target key length, target key bytes, 6-byte body pointer
0x00 direct row: tag, key length, key bytes, 6-byte body pointer
```

The title address is therefore the body address. This is an index grammar
variant, not evidence that title data is missing from the package globally.

### Body-Only Simple Leaf Pages

`HINDEX.DIC` (`0x60`) is the simple equivalent of `0x30`:

```text
offset  size     meaning
0x00    1        key byte length
0x01    n        JIS/gaiji key bytes
...     4        body logical block, big endian
...     2        body offset in block, big endian
```

As with `0x30`, the title pointer is taken to be the body pointer because the
row carries no separate title address.

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
