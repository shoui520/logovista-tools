# Corpus Findings

Observed dictionary behavior. These notes are evidence from the local corpus, not universal claims about every LogoVista product ever shipped.

## Current Corpus Harnesses

There are now two authoritative corpus harnesses, with different purposes:

- `logovista-tools dump-package-models` is the research/model harness. It keeps
  SSED, LVED SQLCipher, and LVLMultiView packages in one family-aware model
  directory while preserving package paths with deterministic hashes.
- `lvcore_audit corpus` is the reader-compatibility harness for the clean
  reader core. It validates open/search/dereference/render behavior and reports
  package-family counts, SSED body-source support, diagnostics, and blockers.

Writer-generated packages are useful sanity checks for the writer proof of
concept. They are not the compatibility proof for lvcore; lvcore is judged
against real LogoVista packages.

### Decoded Model Corpus Pass

Latest local pass:

```bash
logovista-tools dump-package-models /path/to/LogoVista \
  --out-dir /path/to/reports/model-v0 \
  --jobs 0 \
  --resume \
  --progress \
  --gaiji-readiness \
  --chunked \
  --no-raw \
  --parse-mode forensic \
  --allow-failures
```

The run started with all detected CPU cores, then resumed with lower worker
counts after memory pressure from large SSED workers. The final model directory
contains 261 chunked package bundles and zero failures:

```text
total package targets:    261
SSED:                     202
LVED SQLCipher:            45
LVLMultiView SQLite:       14
decoded model failures:     0
```

The corresponding model-derived capability matrix is generated with:

```bash
logovista-tools capability-matrix \
  --model-dir /path/to/reports/model-v0 \
  --out-dir /path/to/reports/capability-from-model
```

The matrix records capability fields for raw HONMON body availability, index
parsing, title parsing, gaiji display readiness, media resolution, and menu
destination resolution. It also splits planning status into four profiles:
`read_existing`, `export_existing`, `author_core_ssed_v0`, and
`lossless_repack_existing`.

Interpretation rules:

- Dense-HONMON packages are valid SSED packages, but their raw HONMON is an
  anchor/dereference layer rather than a self-contained body stream. The model
  classifies them and emits dereference records; external export/repack remain
  stricter because they must consume or reproduce the sidecar body provider.
- `missing_declared_components` is a local package-integrity blocker. It means
  the gathered package declares files that are absent from the local copy. Do
  not treat this as a format fact. For example, the Windows SSED `Genius53`
  package in this corpus is missing `HONMON.DIC` and `PCMDATA.DIC`, while the
  older iOS `GENIUS53` package has an intact readable `HONMON.DIC` and is
  green in the model matrix.
- `NGYOKTUK` has no direct raw gaiji resources, but renderer sidecar evidence
  resolves entry-level display through row-aligned `HONBUN` HTML.
- `ARCHSIC3`'s `PCMDATA.DIC` is a shared WAVE store: HONMON pointers address
  slices inside one component-level `data` chunk.
- Menu destinations no longer produce current blockers. Packed
  `000000000000` menu payloads are null/sentinel destinations, not unresolved
  pointers, and `MUL*.DIC` selector streams can legitimately contain only null
  destinations.

Regenerate the full model matrix before quoting aggregate readiness counts
after any focused parser/body-source/gaiji/media fix.

### lvcore Reader Corpus Validation

The lvcore reader-core audit has a separate command shape:

```bash
PYTHONPATH=src/lvcore-experimental:src/lvcore-audit python3 -m lvcore_audit corpus \
  /path/to/LogoVista \
  --jobs 0 \
  --progress \
  --output-dir /private/reports/lvcore-corpus
```

The `lvcore.audit.corpus.v1` summary is designed for private compatibility
audits. It distinguishes:

- SSED packages whose body source is direct `HONMON.DIC`;
- SSED dense-anchor or sidecar-backed body sources;
- LVED SQLCipher package families detected but not implemented as lvcore
  reader paths;
- LVLMultiView package families detected but not implemented as lvcore reader
  paths;
- unknown or unsupported package families.

It also aggregates diagnostics by severity, area, and code. Normal output avoids
entry text; debug/private report paths are for local inspection only. Current
reader reports include title status and heading-source counters so native rows
that intentionally reuse the body pointer as the title pointer are counted as
safe heading fallback rather than title failures. Sidecar summaries also
separate body-critical stores from media/resource, examples/idioms, search,
kanji-support, ancillary, non-SQLite, and unknown roles. Supplemental sidecars
with visible block/offset relationships are tracked as audit-side sidecar
references rather than attached to entry documents, so example/idiom and
navigation-like schemas remain visible compatibility evidence without expanding
the reader's friendly document model. Sidecar BLOB media tables with clear
name/blob columns are exposed as package-level resources with explicit untouched
byte access. Index summaries count
component types, rows by component type, malformed leaf rows, partial physical
page tails, text-like index outliers, unsupported component types, and
grouped-continuation rows; body decode telemetry records unknown control and
byte totals from sampled entries without exposing entry text.

The latest committed lvcore audit baseline reports gaiji display-readiness
buckets instead of treating every non-Unicode code as unresolved. The full
baseline covers 161 detected SSED packages and counts 67,372 gaiji
occurrences: 12,035 Unicode-mapped, 20,394 bitmap-backed, 33,912 image-backed,
1,031 formatting-helper, and 0 true display-unresolved. Resource-byte
availability is counted separately; bitmap and image-backed gaiji remain
original package bytes and are available only via explicit resource APIs. The
same baseline reports 145 renderable and 16 partially renderable SSED packages.
Compatibility-significant unsupported sidecars, sampled native search misses,
unresolved media, and true display-unresolved gaiji are all zero. Three sampled
unresolved link-target diagnostics remain as nonblocking diagnostics.

## Windows SSED Corpus Profile

An earlier Windows SSED corpus pass profiled 169 packages with raw SSED expansion,
raw `*INDEX.DIC` scanning, sampled lossless HONMON span decoding, and no
SQLite body text. The command shape was:

```bash
logovista-tools profile /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-slices 25 --max-issue-samples 10 --no-hash \
  --out-dir /tmp/lv-profile-corpus
```

Aggregate result:

```text
profiles:                 169
package status:           136 ok, 33 incomplete
HONMON shapes:            110 body_stream_indexed
                           33 body_stream_marker_sliced
                           18 dense_marker_table
                            8 none/missing
body source hints:        143 honmon
                           18 honmon_anchor_dereference
                            8 none
expanded HONMON bytes:    3,497,793,539
entry markers counted:    26,656,375
index body boundaries:    12,370,613
unknown text controls:    0
unknown text bytes:       0
unknown index leaf bytes: 0
strict span failures:     0
```

This pass produced two concrete text-stream findings:

- `1f0b` / `1f0c` are zero-argument literal/preformatted spans. ROYALEGR uses
  them around box-drawing table rows; NKGORIN2 uses them around ASCII numeric
  character references such as `&#x4E05;`.
- `1f3b` / `1f5b` are zero-argument URL spans. GEN2001 uses them around URL
  display blocks.

The `unknown index leaf bytes: 0` result is important. It means the current
branch/leaf parser consumed all observed forward, backward, keyword,
cross-reference, and extra index leaf structures in this corpus. This is still
an empirical result, not a claim that every LogoVista product ever shipped uses
only these layouts.

## Full HONMON Byte Scan

The stronger corpus pass scans every expanded `HONMON.DIC` byte, not sampled
entry slices:

```bash
logovista-tools honmon-bytes /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-issue-samples 20 \
  --out-dir /tmp/lv-honmon-bytes-corpus
```

Aggregate result with the current decoder:

```text
targets:                    169
status:                     161 ok, 8 missing_honmon_file
HONMON byte shapes:         142 marker_rich_text_stream
                            18 dense_marker_table
                             1 text_stream_without_entry_markers
                             8 none/missing
storage modes:              135 plain
                            26 logofont_cipher
                             8 none/missing
expanded HONMON bytes: 3,497,793,539
bytes covered:         3,497,793,539
uncovered bytes:                   0
entry markers:            26,656,375
controls:                460,913,534
known controls:          460,913,534
unknown controls:                  0
unknown bytes:                     0
invalid JIS cells:                 0
truncated controls:                1
truncated gaiji:                   0
```

The full scan produced several corrections to the text-stream model:

- `1f1a` is a nonprinting tab/column position control used by table-like
  display runs, and `1f1c` is a nonprinting media-layout control observed
  before `1f4d` media references. Both have fixed two-byte payloads.
- `1f44` / `1f64` are an extended link pair with 10-byte and 6-byte payloads.
- JIS cell decoding needs CP932 and Shift_JIS-2004 fallback after ISO-2022-JP.
  This accounts for extension symbols such as `①`, `㎏`, `❾`, and `◦`.
- A bare `0x0a` can occur as a legacy line break byte.

The sole remaining forensic issue is `NANDOKU3`: the expanded stream ends with
a lone final `0x1f` byte after decoded text. It is covered and reported as a
truncated control; no opcode is inferred.

## Focused All-In Corpus Pass

After the model-consolidation work, a broader focused pass was run against
priority Windows packages, medium-priority Windows packages, all available
Android/iOS SSED packages, and the remaining lower-priority Windows SSED set.
Scratch outputs were written outside the repository.

The pass used process-level parallelism (`--jobs 0`) and progress-emitting
batch scripts. It deliberately kept SQL/LVED dictionaries out of the SSED pass.

Aggregate across these staged package targets:

```text
package targets:             182
expanded HONMON bytes: 3,687,534,595
bytes covered:         3,687,534,595
unknown HONMON controls:             0
unknown HONMON bytes:                0
invalid JIS cells:                   0
truncated controls:                  1
```

The one truncated control is still the known `NANDOKU3` final lone `0x1f`.

Component-forensics across the same staged targets produced:

```text
component reports ok:      1,521
missing declared files:       42
text/index/media byte residuals:
  unknown title bytes:         1   ITALIAN FHTITLE.DIC standalone 0x11
  known vendor title defect:   1   25IGAKU FHTITLE.DIC singleton 1f1f
  index tail bytes:            5   NANDOKU2 FHINDEX.DIC partial physical tail
  PCMDATA shared slices:     235   ARCHSIC3 shared WAVE data-slice records
```

The staged pass also confirmed several practical compatibility points:

- `KANJIGN5` requires the sibling `_DCT_KANJIGN5_GAIJI` directory for its
  image-backed gaiji. The resource scanner now discovers sibling
  `*_GAIJI` companion directories instead of treating them as unrelated
  packages.
- Several iOS packages carry Windows-looking metadata such as `EXINFO.INI`.
  Platform classification now treats iOS plist evidence as a stronger wrapper
  marker than `EXINFO.INI`; markers are evidence, not mutually exclusive
  package identities.
- `HABGESPA.uni` introduced a third `.uni` layout: a single-section simple12
  file with a 32-bit count followed immediately by 12-byte records and no
  second full-width count. It maps Spanish punctuation/diacritic gaiji such as
  `A121 -> ¡`, `A123 -> ¿`, `A124 -> Á`.
- `KQJEXPRS` is a readable text-stream HONMON without normal entry markers.
  It is classified separately as `text_stream_without_entry_markers`.
- With renderer sidecars enabled, `NGYOKTUK` is display-ready through
  contextual row-aligned `HONBUN` HTML. Without renderer sidecars, it remains
  the named raw-resource gaiji exception.

## Previously Excluded Britannica/Genius Pass

A follow-up pass covered 17 Britannica/Genius-family SSED packages that had
been deliberately skipped in earlier priority runs: older Britannica releases,
older/later `GEN20xx` packages, `GENIUS43`, and `BRINEN15`. Media companion
folders were not treated as dictionary roots.

The pass used the same scratch-outside-repo rule, `--jobs 0`, and
progress/log teeing as the focused all-in pass.

HONMON byte accounting:

```text
package targets:             17
status:                      17 ok
expanded HONMON bytes: 1,101,215,744
bytes covered:         1,101,215,744
HONMON shapes:              16 marker_rich_text_stream
                             1 dense_marker_table
storage modes:              10 plain
                             7 logofont_cipher
entry markers:          777,883
controls:              120,793,035
known controls:        120,793,035
unknown controls:                0
unknown bytes:                   0
invalid JIS cells:               0
truncated controls:              0
truncated gaiji:                 0
gaiji occurrences:       1,897,256
media refs:                  7,119
links:                   2,127,869
```

Component forensics found no new residual byte classes:

```text
component reports ok:             133
missing declared files:            12
index residual bytes:               0
title/menu unknown controls:        0
title/menu unknown bytes:           0
GA16 trailing/header residuals:     0
.uni trailer bytes:                 0
COLSCR unparsed bytes:              0
PCMDATA unparsed bytes:             0
```

The missing files are package-completeness issues rather than new binary
layouts: `BRINEN15` declares missing title/index/GA16 components, and
`GEN2001` lacks `MENU.DIC` and `KWTITLE.DIC`. The parsed index rows total
14,677,657 with zero unknown leaf bytes. Title extraction emits 9,079,801 rows,
mostly from the Britannica small-entry encyclopedias and `GEN2001`; later
`GEN20xx` packages rely on HONMON/index/menu structures rather than title
streams.

Gaiji readiness:

```text
display-ready packages:       16
no raw gaiji occurrences:      1
display-unresolved packages:   0
bitmap-backed occurrences:     1,609,808
unicode-mapped occurrences:      274,715
image-backed occurrences:         19,492
formatting-helper occurrences:    18,564
```

Some older Genius packages still lack Unicode search fallbacks for bitmap-only
gaiji, but display is backed by raw resources. This is a search/export quality
issue, not a byte-parsing issue.

`BRINEN15` (`2015 ブリタニカ国際年鑑`) exposed a new renderer-body variant and
is now supported:

- `HONMON.DIC` is a 135,168-byte dense 32-byte anchor table, not definition
  text.
- Unlike earlier dense-anchor rows whose `1f09` section marker appears after a
  leading `1f0a`, its records start with `1f09` at byte 0.
- The visible head span is an eight-digit JIS numeric ID such as `10100000`.
- `vlpljblF` decrypts with LogoFontCipher to SQLite.
- `t_contents` has `f_array_no`, `f_data_id`, `f_midashi`, `f_contents`, and
  `f_media`.
- `t_media` has only `f_name` and `f_blob`; the blobs are JPEG images.

After adding the marker-at-byte-0 anchor parser and renderer DB column aliases,
the dereference result is exact:

```text
raw HONMON ID records:       713
t_contents rows:             713
entries matched:             713
db rows without raw anchor:     0
raw anchors missing in DB:      0
rows with f_media:           154
t_media rows:                226
sample media export:         JPEG
```

Representative extracted row:

```text
data_id=10100000
title=国際百科年表　１月６日
body source=t_contents.f_contents HTML
```

This confirms `BRINEN15` as another SSED package where HONMON remains the raw
anchor authority even though final display bodies are supplied by a Windows
renderer sidecar.

The HC renderer pass over the same 17 packages found 17 `HC????.dll` files and
16 unique hashes. `GEN2001` and `Gen2010` share `HC009B.dll`. The Britannica
small-entry packages use panel-style HC DLLs without numeric `00000xxx.idx`
sidecars; later `GEN20xx` packages use vertical renderers with numeric sidecar
indexes. `BRINEN15` uses `HC0C80.dll`, which imports only font/path bridge APIs
and uses SQL/plugin/user-data hooks plus the `vlpljblF` sidecar.

## Corpus 0x1f Opcode Atlas

The dedicated opcode atlas pass scans expanded SSED text-stream components:
`HONMON.DIC`, `MENU.DIC`, `*TITLE.DIC`, and text-like TOC/RIGHT/IDXJUMP/INDEX
components. Binary B-tree index pages are not scanned as text.

```bash
logovista-tools opcode-atlas /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --out-dir /tmp/lv-opcode-atlas-corpus
```

Aggregate result:

```text
text components scanned:   547
expanded bytes scanned:    7,026,978,819
controls observed:         713,941,069
distinct 0x1f opcodes:      40
vendor singleton anomalies:   1 occurrence
```

During this run, several LVLMultiView packages were still present in the local
SSED folder. They contributed zero scanned text-stream components, so the
component/byte/control totals above are the relevant opcode evidence.

Every observed payload length matched the current structural table except one
singleton title-stream vendor anomaly:

```text
25IGAKU / FHTITLE.DIC / offset 4980735 / raw 1f1f
```

The surrounding title stream contains two ordinary title lines with the
singleton control between them:

```text
<title line>
<1f1f>
<title line>
```

The `1f1f` sequence is only observed once. It is between title line breaks, has
no usable payload, and appears in a title component rather than HONMON. The
toolkit keeps it reportable as a known vendor data defect and does not infer a
global zero-argument opcode from it.

Most frequent and structurally important controls from the atlas:

```text
1f04 / 1f05   halfwidth conversion span pair
1f09          section/entry marker with 2-byte payload
1f0a          line break
1f41 / 1f61   headword/title span pair
1f1a          tab/column position control with 2-byte payload
1f1c          media block layout control with 2-byte payload
1f42 / 1f62   body/cross-reference link pair; 1f62 has 6-byte pointer payload
1f43 / 1f63   menu/navigation link pair; 1f63 has 6-byte pointer payload
1f4a / 1f6a   jump/audio range pair; 1f4a has 16-byte payload
1f4d / 1f6d   inline media/reference pair; 1f4d has 18-byte payload
1fe0 / 1fe1   bold-ish span pair; 1fe0 has 2-byte payload
1fe2 / 1fe3   private renderer directive span pair; 1fe2 has 2-byte payload
```

The `1fe2` / `1fe3` pair is common and should not be rendered as ordinary
visible body text. Its payload text carries renderer directives such as `IMG:`,
`RUB:`, `SMC:`, `IDX:`, `HTM:`, `SQL:`, `GTH:`, `BOX:`, and
`<PlaySound>...`. Earlier `color/style` wording was too weak: corpus evidence
shows this pair is the wrapper for hidden renderer/private metadata, with
directive-specific semantics still requiring separate resolvers.

## Full Component Forensics Pass

The companion pass accounts for non-HONMON SSED components and adjacent gaiji
mapping files across the same Windows SSED corpus:

```bash
logovista-tools component-forensics /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --max-issue-samples 20 \
  --out-dir /tmp/lv-component-forensics-corpus
```

Aggregate component inventory:

```text
packages scanned:        169
component reports:     1,231 ok, 82 missing_file
MENU.DIC:                84
*TITLE.DIC:             307
structured *INDEX.DIC:  536
text-like INDEX.DIC:      1
GA16 resources:         314
COLSCR.DIC:              59
PCMDATA.DIC:             12
.uni/.UNI files:         90
```

The `missing_file` count reflects incomplete local gathered packages whose
`SSEDINFO` tables name components that are not physically present. It is not a
parser failure mode.

Byte-coverage result:

```text
text stream uncovered bytes:          0
text stream unknown controls:         1
text stream unknown bytes:            1
text stream invalid JIS pairs:        0
structured index nonzero residual:    3
GA16 missing glyph bytes:             0
GA16 nonzero trailing bytes:          0
GA16 unknown header nonzero bytes:    0
COLSCR nonzero unparsed bytes:        0
COLSCR invalid referenced records:    0
PCMDATA nonzero unparsed bytes:       0
PCMDATA shared WAVE slices:         235
.uni trailing bytes:                 72
.uni nonzero trailing bytes:         14
```

The pass added several concrete format details:

- `0x30` `KINDEX.DIC` is a body-only tagged index: grouped rows match the
  `0x70`/`0x90` tagged grammar, but target rows carry a single 6-byte body
  pointer instead of a body/title pair.
- `0x60` `HINDEX.DIC` is a body-only simple index: each key row carries a
  single 6-byte body pointer and uses that same address as the title address.
- `0x72` `BAINDEX.DIC` and `0x92` `FAINDEX.DIC` use the same simple row grammar
  as `0x71`/`0x91`.
- Tagged index pages can contain direct `00 len` rows in addition to grouped
  `80`/`c0` rows.
- Some simple leaf pages are keyless 13-byte pointer tables: 6-byte body
  pointer, one flag byte, and 6-byte title pointer.
- `0xa1` `MUL*.DIC` files are MULTI selector indexes. Branch pages use the
  normal index branch grammar; leaf pages use `00` direct rows, `80` grouped
  rows with a 4-byte target count, and compact `c0` target rows carrying only
  body/title pointer pairs. All seven observed `0xa1` files parse with zero
  nonzero residual bytes.
- `0xff` `MULTI*.DIC` files are selector descriptor tables. The corpus has 19
  observed files and 58 selector records; every component reference matched a
  declared `SSEDINFO` element by type, start block, and block count, and every
  descriptor had zero nonzero trailing bytes.
- `0x09` `BATITLE.DIC` and `0x0d` `MUL*.DIC` are additional title stream
  component types. They decode as the same JIS/control text-stream family as
  the common `0x03`..`0x0a` title streams.
- `0x02` `RIGHT.DIC`, `0x20` `TOC.DIC`, and `0x28` `IDXJUMP.DIC` are text-like
  sidecar streams. KCOMPEJ2 also has a loose `RIGHT.DIC` that is valid
  one-block `SSEDDATA` but is not declared in the main SSEDINFO catalog.
- IBIO4VRS `TOC.DIC` uses a `1f49` / `1f69` internal-link pair. `1f49` carries
  10 bytes: four outline/path bytes followed by a six-byte body pointer.
- KQNEWEJ6 `0x0d` multi-title streams use a bare `11 03` nonprinting separator
  between title rows. The exact two-byte sequence is now accounted for as a
  legacy title separator; lone `0x11` bytes remain reportable anomalies.
- Branch-page slot size uses the full low byte:
  `slot_size = (page_word & 0xff) + 4`. The upper byte/bits are page flags, and
  valid observed slots include 6-byte rows.
- Branch-page keys are child upper bounds, not child lower bounds. HAESPJPN,
  MEIKYOU2, DAIJIRN4, KOJIEN7, and GENIUSEB all show parent rows matching the
  final key of the child branch/page. Final sibling branch rows use all-`ff`
  sentinel keys, which decode as empty keys in text reports. Large Japanese
  indexes use 32-byte upper-bound keys nearest leaves, 30 bytes one branch
  level above, and 28 bytes above that; Latin-only indexes can shrink to the
  actual key width.
- One `INDEX.DIC` outlier is text-like rather than a B-tree page component:
  `KQSYNONM` component type `0x27` is handled as a text stream.
- `COLSCR.DIC` records can wrap PNG payloads with the same `data` + little
  endian size header used by BMP/JPEG records.
- `PCMDATA.DIC` pointer ranges can address self-contained audio records or
  slices inside a shared component-level WAVE `data` chunk.

The remaining component anomalies are intentionally small and named:

- `NANDOKU2` `FHINDEX.DIC` has a 5-byte partial physical page tail after all
  full 2048-byte index pages are parsed. Three tail bytes are nonzero; valid
  rows before the tail are preserved.
- `25IGAKU` `FHTITLE.DIC` has one malformed singleton `1f1f` title-stream
  sequence. It is treated as a vendor data defect, not a model gap.
- `ITALIAN` `FHTITLE.DIC` has one standalone `0x11` byte. It is covered as an
  unknown byte span.
- `HKDKSR14`, `HKDKSR30`, and `YHOUGO4` have small nonzero `.uni` trailers
  after all declared records are parsed.
- `ARCHSIC3` has 235 in-range `PCMDATA.DIC` references. They are contiguous
  slices inside one shared WAVE `data` payload and are resolved as
  `wave_data_slice` records.

## Corpus Gaiji Readiness

The gaiji readiness pass separates display failures from Unicode-mapped gaiji,
bitmap/image-backed gaiji, probable formatting helpers, and missing
search/fallback text:

```bash
logovista-tools gaiji-readiness /path/to/LOGOVISTA_SSED_DICTS_WINDOWS \
  --jobs 0 --out-dir /tmp/lv-gaiji-readiness-corpus
```

Aggregate result:

```text
dictionaries:              169
readiness status:          143 yes
                             25 n/a
                              1 no
raw gaiji occurrences:  34,854,621
unicode-mapped occ.:   15,135,023
bitmap-backed occ.:     8,725,094
image-backed occ.:     10,280,015
formatting-helper occ.:   681,325
display-unresolved occ.:   33,164
search-fallback-missing occ.: 7,989,069
```

The key correction from this pass is that GA16 header ranges advance in JIS
row/cell order, not as flat 16-bit integers. A resource beginning at `A121`
continues through `A17E`, then `A221`. This resolves the apparent missing
bitmap gaiji in `ARCHSIC3`, `LMEDEJ12`, `MEIKYOU`, `NANDOKU3`, `NANDOKU4`,
`Dconci87`, `Bri2019P`, `KQBIZEJ`, `IBIO4`, and `IBIO4VRS`.

Several Windows packages still require the second addressing view: GA16/GAI16
glyph slot `n` aligns with `.uni` half/full record `n`; the `.uni` record's
code field is the raw body code. This resolves sparse codes in GENIUSEB,
RDRSP2, Readers3, RPLUSREV, KENE7J5, KQNEWEJ6, KQNEWJE5, and related packages.

Display-unresolved dictionary under the default raw-resource-only policy:

```text
NGYOKTUK   146 codes, 33,164 occurrences
```

`NGYOKTUK` has raw gaiji but no `.uni`, plist, GA16, or package image evidence
in the SSED package. It ships a LogoFontCipher renderer sidecar, and with
`gaiji-readiness --renderer-sidecars` it is display-ready through entry-level
`HONBUN` HTML. It is not reducible to one dictionary-global gaiji map: at least
one raw code is context-dependent, so the renderer row is the correct display
source for lossless conversion.

## Capability Matrix

The current preferred capability matrix is the model-derived matrix described
near the top of this document. The older matrix path combined three redacted
report families. With `gaiji-readiness`, the gaiji status was refined from raw
unresolved-span counts into display readiness:

```bash
logovista-tools capability-matrix \
  --profile-dir /tmp/lv-profile-corpus4 \
  --honmon-bytes-dir /tmp/lv-honmon-bytes-corpus-v3 \
  --component-forensics-dir /tmp/lv-component-forensics-corpus-v4 \
  --gaiji-readiness-dir /tmp/lv-gaiji-readiness-corpus-grid-v1 \
  --out-dir /tmp/lv-capability-matrix-corpus-grid-v1
```

This legacy command path does not inspect dictionary payloads directly. It
classifies each dictionary from existing raw-first reports. The older matrix
covers 169 SSED targets.

Capability counts:

```text
raw HONMON body:       yes 143, no 26
indexes fully parsed: yes 123, partial 1, no 11, n/a 34
titles fully parsed:  yes 79, partial 2, no 4, n/a 84
gaiji fully resolved: yes 143, no 1, n/a 25
media refs resolved:  yes 62, partial 1, no 1, n/a 105
menu pointers:        yes 63, partial 12, no 9, n/a 85
```

Older alias planning status from that 169-target legacy matrix:

```text
legacy writer v0:   green 116, yellow 21, red 32
lossless repacker:  green 106, red 63
combined worst:     green 106, red 63
```

The green count changed because most dictionaries previously marked
`gaiji_not_fully_resolved` were actually display-ready once bitmap/image-backed
gaiji, JIS-grid GA16 glyphs, `.uni` record-order GA16 glyphs, and
formatting-helper candidates were separated from true display failures.

Top blocker counts:

```text
missing_declared_components:        33
body_requires_sidecar_or_is_missing: 26
menu_not_fully_resolved:            21
raw_body_not_self_contained:        18
indexes_not_fully_parsed:           12
titles_not_fully_parsed:             6
unknown_or_structural_text_issues:   3
media_not_fully_resolved:            2
gaiji_not_fully_resolved:            1
```

The matrix makes the next reverse-engineering priorities more concrete:

- include renderer sidecar evidence for `NGYOKTUK` model/gaiji readiness and
  preserve its `HONBUN` renderer rows as contextual display evidence;
- treat the legacy `menu_not_fully_resolved` counts above as superseded by the
  current model-derived matrix: null/sentinel menu destinations are now
  classified separately and no current menu pointer blockers remain;
- keep missing declared components visible as package-integrity blockers while
  avoiding format conclusions from locally incomplete gathered packages;
- keep the remaining vendor/corpus anomalies (`25IGAKU`, `ITALIAN`,
  `NANDOKU3`) explicitly named and measurable;
- preserve `ARCHSIC3`'s `wave_data_slice` PCMDATA references as shared-WAVE
  slices rather than self-contained records.

## HONMON/IDX Corpus Audit

An older mixed mobile SSED corpus was audited with raw SSED expansion, raw
index-derived body boundaries, body-slice sampling, 32-byte HONMON record
probing, and title-component probing. SQLite and `DictFULLDB` body text were
not used to decide whether raw HONMON/IDX produced readable body entries.

Valid SSED dictionaries with `HONMON.DIC` fell into these practical groups:

| Group | Dictionaries |
| --- | --- |
| Raw HONMON/IDX gives readable body entries | `Dconci98`, `GENIUS53`, `GENIUSEB`, `HAESPJPN`, `HAIKSAIJ`, `HKKIGAK6`, `IBIO5`, `IPHYCHE5`, `KANJIGN5`, `KENCOLLO`, `KQCOLEXP`, `KQEBHOU`, `KQJCOLLO`, `KQLATINO`, `KQNEWEJ6`, `KQNEWJE5`, `KenE7J5`, `LMEDEJ12`, `MEIKYOU2`, `NIHONSHI`, `NKGORIN2`, `OUKOKU11`, `RDRSP2`, `ROYALEGR`, `Readers3`, `SINMEI7`, `Saitoje`, `ZYAKUKOG` |
| Raw HONMON/IDX exposes IDs, tokens, titles, or search keys, but sampled HONMON bodies are not definitions | `HABGESPA`, `HAFRAN`, `HOUGAKU5`, `IWKOKUG8`, `JSSAURU2`, `KENROWA`, `KOJIEN7`, `NANMED20` |

Several products in the first group still declare SQL or `DictFULLDB` files.
That declaration alone is not enough to classify a dictionary as database-body
only. The raw audit must check the expanded HONMON stream and the raw indexes.
Conversely, the second group proves that some dictionaries need a database or
other payload dereference for final body text, but that does not make HONMON or
IDX irrelevant: they still carry the raw anchor layer.

`NANMED20` adds one renderer-sidecar wrinkle: its Windows `vlpljblF`
`t_contents` table contains normal decimal `f_DataId` rows that match raw
dense-HONMON IDs, plus non-decimal rows such as `99A00001`. The latter are not
raw HONMON anchors. They are sidecar-only rows and should be measured rather
than forced through the decimal ID matcher.

LVED/WebView2 SQLCipher products such as OXFPEU4 and KQCMPROS are covered as a
separate package family below. They are not failed raw-HONMON body streams.

The body sampler deliberately filters section-only spans, decimal/hex-only ID
records, and short opaque base64-like tokens. Without that filter, dense tables
can appear to contain entries such as `<section:0001>` or `K0NVOzjh`; those are
not coherent dictionary bodies.

### KENROWA Focused Pass

KENROWA is a useful stress case for the decoded model because the SSED layer is
complete but the readable body is renderer-sidecar backed.

Observed package shape:

```text
dict title:       研究社露和・和露辞典
platform:         Windows SSED
HONMON shape:     dense_marker_table
body source:      honmon_anchor_dereference
main sidecar:     vlpljblF, LogoFontCipher SQLite
renderer DLL:     HC015B.dll
auxiliary index:  0000015B.IDX
menu component:   none
media components: none
static HTML:      select.html, select2.html, HANREI/
```

Raw byte coverage:

```text
expanded HONMON bytes:       51,398,656
HONMON entry markers:         1,606,160
bytes covered:               51,398,656
unknown controls/bytes:               0
invalid JIS cells:                    0
component-forensics issues:            0
```

The raw `HONMON.DIC` is not definition text. It is a dense anchor table. Each
32-byte row contains the usual head-span scaffolding around blank JIS cells.
Real renderer body IDs point to the marker two bytes into the row:

```text
body pointer -> HONMON relative offset
anchor id    -> floor(relative_offset / 32) + 1
renderer ids -> multiples of 10
```

The decrypted `vlpljblF` SQLite sidecar contains the final entry bodies:

```text
t_contents rows:                160,616
t_contents f_Type values:       2 only
matched raw HONMON IDs:         160,616 / 160,616
db rows without HONMON anchor:  0
HONMON anchors without db row:  0
t_seikuyourei rows:             230,561
```

`t_contents` carries `f_Title`, `f_Title_SS`, `f_Keyword`, `f_sakuin`,
`f_Html`, and `f_Plane`. The HC renderer contains SQL snippets that query
`t_contents` by `f_DataId` and query `t_seikuyourei` for example/idiom search.
This strongly confirms the raw-HONMON anchor plus renderer-SQLite model for
this product.

Raw title and index components are still meaningful and parse completely:

```text
title rows emitted:  799,004
index leaf rows:     799,004
index internal rows:  14,577
unknown leaf bytes:       0
```

Every index body pointer observed in the full KENROWA index dump points to
`relative_offset % 32 == 2`, i.e. to the marker inside a dense HONMON row. Every
derived anchor ID is `anchor_id % 10 == 0`, matching the renderer DB's
`f_DataID` convention.

Gaiji handling is display-complete:

```text
.UNI format:               simple12
.UNI records:              376
mapped records:            162
GA16FULL glyphs:           265
GA16HALF glyphs:            97
image resources:           115
raw gaiji occurrences: 571,720
display unresolved:          0
```

Most raw gaiji occurrences in the title streams are image-backed dictionary
symbols. Two mapped symbols (`b17d` -> `°`, `b17b` -> `©`) currently report
missing search fallbacks, which is an exporter/search-normalization concern,
not a display blocker.

The package also contains static presentation resources that are not SSED
components: root helper pages `select.html` and `select2.html`, a `HANREI/`
help/front-matter tree, and many `Templates/*.png` / `Templates/*.svg` images.
Converters should preserve these as package resources/navigation material
instead of treating them as dictionary body entries.

## LVED SQLCipher Packages

OXFPEU4 and KQCMPROS are not failed SSED/HONMON dictionaries. They are a
separate LVED/WebView2 package family. The body/search/media data lives in
SQLCipher payloads named `main.data` on Windows and `*.dbc` in mobile-style
packages.

Observed OXFPEU4 facts:

```text
Windows main.data size:    15,937,536 bytes
4096-byte pages:            3,891
Windows/iOS payload match:  byte-identical in the local corpus
decrypted schema:           list, content, media, info, search, FTS backing tables
list rows:                  2,802
content rows:                  70
search rows:                2,802
```

Observed KQCMPROS facts:

```text
Windows main.data size:   197,382,144 bytes
4096-byte pages:           48,189
decrypted schema:          list, content, media, info, search, FTS backing tables
list rows:                135,317
content rows:              64,517
search rows:              135,317
```

Observed LVED packages ship WebView2 and SQLCipher runtime components. Private
runtime evidence confirms a metadata-derived `sqlite3_key` path, and independent
key validation shows the database key path uses dictionary id/code metadata, not
the product serial. Memory dumps and recovered keys are private local evidence,
not repository artifacts.

The toolkit therefore treats LVED payloads as a distinct package layer:

```bash
logovista-tools lved /path/to/OXFPEU4 --dict-id 750 --dict-code OXFPEU4 --json
logovista-tools lved /path/to/KQCMPROS --dict-id 751 --json
```

Reports validate the payload without emitting derived, explicit, or recovered
keys.

## LVLMultiView Packages

The current LVLMultiView corpus has 14 packages: ESPRANT2, MOROKU21 through
MOROKU26, and YROPPO02/03/04/5/06/07/08. These are not classic SSED/HONMON
packages and are not LVED SQLCipher packages. They ship `menuData.xml`, a small
SSEDINFO-magic `.IDX` facade, and LogoFontCipher SQLite payloads.

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
with 15 HTML files; this appears to be a browsable
example/help-style appendix rather than the primary body store.

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

Law viewer binaries contain UTF-16 strings naming decrypted cache targets:
`hore_body.db`, `hanrei_youshi.db`, `index.db`, `jiko_sakuin.db`, `yroppo.db`,
and `mo6.db`.

The MOROKU law-package `Resources/*` entries named `inshizei`, `minji`,
`zenkoku`, and `zeihou` are encrypted PDF assets. Across MOROKU21-26 there
are 20 such resources. Seventeen decrypt with the Windows LogoFontCipher
variant; the three MOROKU23 files decrypt with the Mac OS X LogoFontCipher
variant. In both cases the decrypted payload is a normal PDF and should be
written as `.pdf` without rewriting the document bytes.

Toolkit command:

```bash
logovista-tools multiview /path/to/LOGOVISTA_LVLMULTI_DICTS_WINDOWS --jobs 0 --out-dir out/multiview
```

## Non-iOS Body Streams

OUKOKU11 is useful because it is an Android-only package, not part of
LogoVista's iOS pipeline. It has no `Gaiji.plist`, `GaijiS.plist`,
`resourcesCopy.plist`, or `gaijiicon.plist`, but the raw `.IDX` / `.DIC`
structure is still compatible with the toolkit.

Observed OUKOKU11 layout:

```text
OUKOKU11.IDX
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
KWTITLE.DIC
KWINDEX.DIC
COLSCR.DIC
GA16FULL
GA16HALF
OUKOKU11.UNI
OUKOKU11.db
OUKOKU11_indexinfo.db
resource/kmkimges/
appendix/img/
manual/contents/img/
```

Important findings:

- Uppercase `.UNI` is enough for primary Unicode gaiji mapping. OUKOKU11 has
  568 usable Unicode mappings and no plist mappings.
- `GA16FULL` and `GA16HALF` are normal bitmap resources. The observed counts
  are 771 full-width glyphs from `B121` and 38 half-width glyphs from `A121`.
- `HONMON.DIC` is a real body stream, not a dense ID table. Expanded size is
  18,020,352 bytes.
- Entry starts are index-defined, not marker-defined. Raw marker count is
  64,453, but index-derived body boundaries produce 82,220 coherent entries.
- The app cache table has 70,375 `Block`/`Offset` rows. It is useful for
  validation, but it is not needed to extract body text.
- `OUKOKU11_indexinfo.db` is metadata, not dictionary body text.

A full raw extraction command:

```bash
logovista-tools entries /path/to/OUKOKU11 --dict OUKOKU11 \
  --section-markers --image-gaiji --html --out-dir out/oukoku
```

Expected high-level summary from the local test copy:

```text
entries_emitted:        82,220
index_entry_boundaries: 82,220
entry_markers:          64,453
image_resource_entries: 167
image_gaiji_entries:    56
unknown_controls:       0
```

## Windows Packages

Windows packages share the same SSED/EPWING-like core as the mobile packages
where matching copies are available, but add Windows app sidecars around it.
The observed Windows SSED corpus is broad enough to separate general wrapper
rules from one-off product behavior.

### Windows `HC????.dll` HTML Renderer Plugins

Windows SSED packages usually set `HTML=1` and declare a product renderer in
`EXINFO.INI` with `HTMLDLL=HC????.dll`. The `HC` suffix is a four-hex product or
plugin code. Windows installs can also carry replacement renderers under
`fix/<eight-hex-id>/HC????.dll`; those replacement files are renderer
alternatives, not dictionary components.

The combined Windows SSED corpus plus recovered GEN-family package pass found:

```text
HC files:                         158
unique SHA-256 binaries:          109
EXINFO HTMLDLL exact declarations: 158
PE architecture:                  all PE32 / Intel i386 DLLs
imports shared by every HC DLL:   dictionary bridge, MSVCP60.dll, MSVCRT.dll, KERNEL32.dll
exports shared by every HC DLL:   epwing2HtmlBodydata
```

The follow-up code-level pass decompiled one representative for each of the
109 exact SHA-256 binary families. The report therefore tracks behavior by
binary identity, not by the four-hex HC code alone. Several HC codes have more
than one exact binary, and exact binaries are shared across package sets.

One recovered GEN2010 package copy contains an unrelated `SESGRASS.IDX`.
That IDX is package contamination, not evidence that HC renderers support
cross-dictionary SSEDINFO catalogs. `EXINFO.INI` `HTMLDLL` remains the
authoritative renderer link.

The HC DLL is not the raw dictionary container. It is a product-specific HTML
renderer plugin loaded by the Windows browser through dynamic library calls and
product bridge hooks. The imported service families cover body, picture, gaiji,
menu/search, SQL, and plugin operations.

Observed HC plugin feature counts:

```text
html_body_renderer             158   epwing2HtmlBodydata
vertical_renderer              127   epwing2HtmlBodydataVertical
uses_gaiji_unicode_api         137   SDicGetCustomCharacterUincode
uses_gaiji_bitmap_api          133   SDicGetCustomCharacterBitmap
uses_body_api                  109   SDicGetBodyData
uses_picture_api               100   SDicGetPictureData
headword_modifier               75   modifyHeadword / modifyHeadwordEx / modifyHeadwordAddr
custom_gaiji_dib                72   getCustomCharacterDIB
sql_hooks                       45   initializeSQL/finalizeSQL or SQL search API imports
dictionary_original_search      30   execDicOrgSearch / execDicOrgSearchEx
plugin_hooks                    22   pluginFunction* exports
user_data_hooks                 22   openUserData/closeUserData
fulltext_search                 16   execDicZenbunSearch
panel_hooks                     13   initializePanel/finalizePanel
uses_menu_api                    2   SDicGetMenuData
zip_media_export                 1   createMediaFileFromZip, observed in PROYAL53
lvelib_renderer                  2   epwing2HtmlBodydataLVELib
```

Shared binaries are real. The largest duplicate groups are:

| Count | HC DLL | Meaning |
|---:|---|---|
| 30 | `HC0190.dll` | Shared by the `SIZK0101` through `SIZK0605` package set. |
| 10 | `HC009B.dll` | Shared by `GEN2001` through `GEN2010`. |
| 2 | `HC00A0.dll` | Shared by `GKBUSINE` and `GKTRAVEL`. |
| 2 | `HC0048.dll` | Shared by `SPEECH` and `TEGAMI`. |

The most common export signatures are:

| Count | Export signature |
|---:|---|
| 60 | `epwing2HtmlBodydata`; `epwing2HtmlBodydataVertical` |
| 20 | `epwing2HtmlBodydata`; `epwing2HtmlBodydataVertical`; `getCustomCharacterDIB`; `modifyHeadword` |
| 17 | `epwing2HtmlBodydata` |
| 5 | SQL/search/plugin renderer signature with `closeUserData`, `execDicOrgSearchEx`, `execDicZenbunSearch`, `initializeSQL`, and `pluginFunction2nd` |

The decompiled renderer loops confirm that the standard HC body renderer is a
JIS/control-byte to HTML transducer around raw SSED body bytes. Common behavior:

- `epwing2HtmlBodydata` calls the dictionary bridge to fetch body bytes and
  emits HTML directly; `epwing2HtmlBodydataVertical` is the vertical-writing
  variant when present.
- Gaiji rendering asks for Unicode first when the Unicode bridge import is
  present, then falls back to bitmap/DIB glyph data where available.
- `1f42`/`1f62` and `1f43`/`1f63` produce internal address links.
- `1f3c`, `1f4d`, and picture-capable `1f44` paths invoke picture extraction
  and produce image links/placeholders. This is a renderer effect; the wire
  grammar still treats `1f44` as the 10-byte start of the `1f44`/`1f64` pair.
- `1f4a`/`1f6a` produces sound links for addressed PCMDATA-style ranges.
  Renderers commonly emit an `img_mark2` sound icon when a package-local
  `sound` image asset exists.
- `1f36`, `1f37`, `1f48`, `1f49`, `1f4b`, `1f4c`, `1f4e`, `1f4f`, and
  `1fe0`..`1fe6` are renderer-private/layout controls. They carry structured
  payloads or state changes and should not be emitted as literal body text.

This is enough for toolkit parsers to consume the correct byte lengths and
preserve renderer intent. It is not a claim of pixel-perfect reproduction of
every product-specific HC HTML template or SQL/search hook.

The `hc-render` command now turns those common semantics into executable HTML
rendering for raw body slices. It emits safe internal-link anchors,
COLSCR/PCMDATA resource placeholders, gaiji Unicode/image fallbacks, suppressed
private renderer directive metadata, and optional rendererdb/media/ziptomedia
comparison output. Product-specific hooks remain named gaps unless their exact
data path is understood.

Current exact-binary-family status:

```text
decoded branch subsets:             21   HC013A, HC0065, HC009D, HC00C6, HC012D, HC012E, HC013D, HC0141, HC0144, HC0145, HC0190, HC009C, HC02C5, HC0151, HC03E8, HC02BC, HC02BE, HC02C2, HC0146, HC0157, HC0158
common semantics plus named gaps:    91
full product visual parity:           0
```

Named unresolved hook families in the exact-SHA pass include custom gaiji DIB
hooks, `modifyHeadword*` callbacks, Panel lifecycle hooks, and SQL/search
helper hooks. These are tracked as explicit gaps unless the relevant branch
table and data path are understood.

The first product-specific HC rendering branch subset implemented is
`HC013A.dll` for
HAESPJPN-style entries. The renderer decodes `1f09` section payloads as packed
decimal section numbers. Section `0011` enters an example block and emits the
`exam.png` template; sections `0010`, `0011`, and `0012` keep that block active,
so the badge is emitted once per contiguous examples region rather than before
every example/translation line.

The second branch-subset proof case is `HC0065.dll`. Its body loop opens
entries with a `midashi` block and treats `1f41` as the transition into
`contents_body`, rather than as a visible headword span. It also uses the
product `lLink` class for internal links, renders A174 and A430-A433 as
B/c/u/S/D grammar labels, and keeps A251/A253 as `img_gaiji` template-backed
images. Example/collocation boxes, SQL original-search hooks, custom DIB
generation, and `modifyHeadwordEx` remain named gaps, so exact HC0065 visual
parity is not claimed.

The GKCEREMO branch-subset proof case is `HC009D.dll`. Its body loop maps
`1f09` section payloads to `lineinfoN` blocks. Section `0008` additionally
looks ahead at B14x renderer markers: B142/B144/B146/B148/B14A and
B150/B152/B154/B156/B158 open product kakomi wrappers such as `columnKakomi`,
`komattaKakomi`, `tokuKakomi`, and `simpleKakomi`, with `img_kakomi` icons
where the DLL template branch names one; the corresponding odd markers close
the wrapper. The same branch subset renders B121 as a pointing-hand literal,
B125 as a checkbox marker, and B130/B131/B138-B13D as explicit breaks. In the
first 20-entry GKCEREMO sample, generic `lv-hc-heading` wrappers dropped from
20 to 0, `lineinfo` blocks rose from 0 to 498, `lineLink` anchors from 0 to
114, kakomi wrappers from 0 to 12, and generic gaiji placeholders from 104 to
11. Remaining custom DIB gaiji, exact table header/body lifecycle, loose HTMLs
fallback, and broader visual parity remain named gaps, so exact HC009D visual
parity is not claimed.

The third branch-subset proof case is `HC00C6.dll`. Its vertical body loop
maps `1f09` section payloads to product block classes used by
`Templates/000000c6.css`: `0001` is a headword block, `0002`/`0003` are
subheadword blocks, `0006` is the translated-definition block, and
`0007`/`0008` form example/source and example-translation blocks with the
`exam.png` badge emitted once per contiguous examples region. The same branch
table handles A23C/A23D and A24C/A24D as `partwaku` labels, A244/A245 as
`supAB` A/B labels, B126 as a rule line, and template-backed gaiji markers as
classed images. Unresolved literal `DAT_*` branches and custom DIB generation
remain named gaps, so exact HC00C6 visual parity is not claimed.

The fourth branch-subset proof case is `HC02BE.dll`. Its vertical body loop
maps section payloads to the `ind_####` classes defined in
`Templates/000002BE.css`. It also treats a large subset of gaiji-plane values
as phonetic renderer commands: half-width and full-width base characters are
combined with `aigu.png`, `grave.png`, `tilde.png`, `tilde_aigu.png`,
`tilde_grave.png`, or `macron.png`; B928/B929 wrap pronunciation text in
`hatsuon`; B92C/B92D wrap reading text in `yomigana`; B926/B927 emit the
corresponding parentheses; and B924/B925 are consumed as renderer selectors.
Panel lifecycle, SQL/search, `modifyHeadword`, and custom DIB paths remain
named gaps, so exact HC02BE visual parity is not claimed.

The fifth branch-subset proof case is `HC02BC.dll`. Its vertical body loop
maps `1f09` section payloads to STEDMAN6 block shapes such as `midashi`,
`komidashi`, `honbun`, and `contents`, and section `0002` emits
`fukumidashi.png` when the package asset is present. The same branch table
uses B121-B125 for blue text, B132/B133 for `sc` small-cap text,
B134-B139 for color/bold spans, B13C-B13E for break/indent structure, and
A145/A146, A147/A148, A159/A15E/A160, and B126-B131 for inline medical
chemistry/phonetic composites. Custom DIB generation, `modifyHeadwordEx`, and
unverified vertical-navigation wrapper scaffolding remain named gaps, so exact
HC02BC visual parity is not claimed.


The sixth branch-subset proof case is `HC02C2.dll`. Its vertical body loop
maps `1f09` section payloads to KQCOLEXP `midashi` and `honbun` blocks.
Sections `0007`, `0008`, `0009`, and `000A` emit `1.png`, `2.png`, `3.png`,
and `4.png` `img_icon` markers respectively; section `0007` also opens the
`moji-down` paragraph shape. The same decoded branch subset keeps B13E-B15D
as `img_gaiji` template-backed image gaiji, consumes `1f41`/`1f4c` as renderer
state controls, and emits internal links with the recovered `lineLink` class.
Custom DIB generation, `modifyHeadwordEx`, Panel lifecycle hooks, and broader
visual parity remain named gaps, so exact HC02C2 visual parity is not claimed.

The seventh branch-subset proof case is `HC012E.dll`. Its vertical body loop
uses package-local `Gaijitemp/` image resources for most kanji-form glyphs.
The implemented subset maps common `1f09` section payloads to `honbun`,
`bushu`, `kaku_midashi`, `exam`, `Oyaji`, and `Itaiji_2` block/table shapes;
section `0027` is intentionally kept as normal `honbun` in the toolkit because
the real corpus uses it for common kun labels that should not inherit the large
`.Itaiji` glyph style. B238/B239/B241/B242 map to black/red/sizedown spans;
B136-B139 map to Gaijitemp `hatsuon` images; A149 maps to spacing; and `1f6d`
is consumed as a renderer marker. Custom DIB generation, `modifyHeadword`,
original-search SQL hooks, exact `0027` large-glyph context, and full
stroke-order table lifecycle parity remain named gaps, so exact HC012E visual
parity is not claimed.

The eighth branch-subset proof case is `HC012D.dll`. Its vertical body loop
maps `1f09` section payloads to MEIKYOU2 block classes such as `honbun_start`,
`honbun`, `yorei`, `yindex_*`, `hinshi`, `kaisetsu_*`, and `ruigo_*`.
The `1f41`/`1f61` path produces the product `midashi` transition rather than
the generic heading wrapper, internal links use the recovered `lineLink` class,
and the decoded inline-image branches map `217E`, `2221`, `222A` before a link,
and `224E` to `kaisetsu_s`, `kaisetsu_m`, `link_k`, and `link_t` template
images. A134/A137 are spacing markers and B87C/B87D are consumed as layout
markers. Custom DIB generation, `modifyHeadword`, SQL/original-search hooks,
exact yindex/ruigo script lifecycle, and broader representative coverage remain
named gaps, so exact HC012D visual parity is not claimed.

The RDRSP2 branch-subset proof case is `HC0145.dll`. Its vertical body loop
decodes `1f09` payloads as decimal section states and maps them to RDRSP2
`midashi`, `komidashi`, `honbun`, and `contents` blocks. The `1f41` path is a
renderer state marker, not a visible generic heading wrapper. Internal links
use `lineLink`; B924/B925 wrap bold-italic spans; A921-A924 and
B92A/B92B/B934/B936 emit the recovered bracket/superscript/parenthesis/spacing
literals; and known selector gaiji such as B92C/B92D/B931/B932 are consumed
instead of displayed. In the first 20-entry RDRSP2 sample, generic
`lv-hc-heading` wrappers dropped from 20 to 0, product `midashi` blocks rose
from 0 to 20, `honbun` blocks from 0 to 39, `contents` blocks from 0 to 13,
`lineLink` anchors from 0 to 4, and generic gaiji placeholders from 108 to 21.
Custom DIB generation, `modifyHeadwordEx`, SQL original-search plus
`D_Example`/`D_Idiom` hooks, and exact table/navigation wrapper lifecycle remain
named gaps, so exact HC0145 visual parity is not claimed.

The RPLUSREV branch-subset proof case is `HC0144.dll`. Its vertical body loop
uses the same section family shape as HC0145 for the common English dictionary
entry stream: `1f09` sections map to `midashi`, `komidashi`, `honbun`, and
`contents` blocks; `1f41` is consumed as renderer state; internal links use
`lineLink`; B924/B925 wrap bold-italic spans; A921-A924 and B92A/B92B/B934/B936
emit recovered literal markers; and B921/B926-B929/B92C-B92F/B931-B933/B935/B937
are consumed as renderer selectors/no-output markers. In the first 100-entry
RPLUSREV sample, generic `lv-hc-heading` wrappers dropped from 100 to 0,
product `midashi` blocks rose from 0 to 101, `honbun` blocks from 0 to 138,
`lineLink` anchors from 0 to 23, and generic gaiji placeholders from 181 to 32.
Custom DIB generation, `modifyHeadwordEx`, SQL example/idiom helpers, exact
`HTMLs`/`fix` fallback lifecycle, custom-character image suffix selection, and
broader visual parity remain named gaps, so exact HC0144 visual parity is not
claimed.

The GENKANA5 branch-subset proof case is `HC03E8.dll`. Its vertical body loop
contains the same visible `midashi`, `honbun`, `contents`, and `lineLink`
strings used by the product stylesheet, plus a marker branch table for
B924/B925 bold-italic spans, A921-A924 and B92A/B92B/B934/B936 literals, and
B921/B939/B926-B929/B92C-B92F/B931-B933/B935/B937 no-output selector markers.
The implemented subset maps the common sampled `1f09` sections to product
`midashi`/`honbun` blocks, consumes `1f41` as renderer state, emits `lineLink`
for internal links, and preserves the HC03E8-specific B936 `]&nbsp;` literal.
In the first 100-entry GENKANA5 sample, generic `lv-hc-heading` wrappers
dropped from 100 to 0, product `midashi` blocks rose from 0 to 100, and
`honbun` blocks rose from 0 to 100. Custom DIB generation, `modifyHeadwordEx`,
SQL full-text/zenbun search hooks, exact `HTMLs`/`fix` fallback lifecycle,
custom-character image suffix selection, dense-sidecar body behavior, and
broader visual parity remain named gaps, so exact HC03E8 visual parity is not
claimed.

The Readers3 branch-subset proof case is `HC0141.dll`. Its vertical body loop
maps `1f09` section payloads to the product `midashi`, `komidashi`, `honbun`,
and `contents` block family. The same branch table handles B924/B925
bold-italic spans, A921-A924 and B92A/B92B/B934/B936 literal markers, and
B926-B929/B92C-B92F/B931-B933/B935 as no-output selector markers. Internal
links use `lineLink`, image-backed gaiji use the product `img_gaiji` class,
and `1f41` is consumed as renderer state. In the first 100-entry Readers3
sample, generic `lv-hc-heading` wrappers dropped from 100 to 0, product
`midashi` blocks rose from 0 to 108, `honbun` blocks from 0 to 215,
`contents` blocks from 0 to 131, `lineLink` anchors from 0 to 82, and generic
gaiji placeholders from 470 to 87. Custom DIB generation, `modifyHeadword`,
dictionary-original SQL search, D_Example/D_Idiom helper integration, exact
body-file/fix fallback lifecycle, custom-character image suffix selection, and
broader visual parity remain named gaps, so exact HC0141 visual parity is not
claimed.

The SIZK read-aloud branch-subset proof case is `HC0190.dll`. Its body loop
does not render the raw body stream directly as a normal entry. Instead,
B121-B124 select package HTML templates under `HTMLs/`, `1f09` sections are
captured into numeric buckets, and the renderer substitutes those buckets into
`<!--&IND####;-->` placeholders in the selected template. The implemented
subset decodes that template path for the representative SIZK0101 package and
uses the product HTML table/image layout rather than showing B121-B124 as
missing gaiji. In the SIZK0101 sample, generic `lv-hc-heading` wrappers and
B121-B124 gaiji placeholders dropped from 4 to 0; package template images such
as `roudoku.png` and `haikei.png` appear through the selected HTML; and all
`IND` placeholders are either filled or intentionally emptied. Exact
JavaScript audio-player lifecycle, runtime `fix/` override behavior, original
viewer temp-file output, and visual coverage across all read-aloud set volumes
remain named gaps, so exact HC0190 visual parity is not claimed.

The SESGRASS image-index branch-subset proof case is `HC009C.dll`. Its body
loop maps `1f09` section payloads to product `midashi` and margin-adjusted
`honbun` blocks, consumes `1f41` as renderer state, and renders internal links
with the recovered `lineLink` class. The same branch table treats B122 as a
direct `img_mark2` image marker, B128/B129 as `ko-midashi` wrappers, B13A as
`page_comment`, B12A-B137 as product table wrappers, B148-B14B as season
images, and B139/B140-B147/B14C/B14D as selector/no-output markers. Private
`IMG:I########.PNG` directives resolve to full image links with thumbnail
preview images from the package `images_thumb` tree. In the first 50-entry
SESGRASS sample, generic `lv-hc-heading` and `lv-hc-section` wrappers dropped
to 0, product `honbun` blocks rose to 4,470, `midashi` blocks to 67,
`lineLink` anchors to 4,090, thumbnail-backed image buttons to 4,032, and
generic gaiji placeholders fell from 4,201 to 24. Custom gaiji bitmap
generation, `modifyHeadword` hooks, Panel/plugin/user-data hooks, SQL/search
helpers, and broader visual parity remain named gaps, so exact HC009C visual
parity is not claimed.

The GENIUS53 branch-subset proof case is `HC02C5.dll`. Its body loop maps
`1f41` to product `midashi` or `CB_Title` heading blocks, uses `lLink` for
internal links, consumes `1f5c`/`1f6d` as renderer anchor-close state, maps
clear `1f09` section values to recovered product wrappers such as `contents`,
`Seiku`, `indent11`, `indent12`, `indent58`, and margin blocks, and treats
B146-B150/B373-B37B/B443-B44D as bold numeric labels, B353-B358/B37C-B423/
B44E-B455 as small letter labels, and B273/B347/B348/B372 as `img_hin`
image markers. In a 30-entry GENIUS53 sample, generic `lv-hc-heading` and
`lv-hc-section` wrappers dropped to 0, `midashi` blocks rose to 1,802,
`contents` blocks to 899, `lLink` anchors to 2,768, `dummy.GIF` audio images
to 2,322, `img_hin` images to 446, strong labels to 307, small labels to 112,
and raw `unknown_control_1f6d` gaps dropped to 0. Exact select-menu lifecycle,
full `gohou`/`gohou2` lookahead branches, custom character DIB generation,
`modifyHeadword`, Panel hooks, SQL/search helper hooks, and broader visual
parity remain named gaps, so exact HC02C5 visual parity is not claimed.

The IBIO5 branch-subset proof case is `HC0151.dll`. Its body loop maps `1f41`
to product `midashi`, `1f61` to product `contents`, uses `Link` for `1f42`
internal links and `lineLink` for `1f43` links, consumes `1f6d` as renderer
state, maps clear `1f09` payloads to `indent##` blocks or table row/cell
wrappers, and treats B156/B157 as small-text delimiters plus B159 as a table
cell transition in table sections. In a 50-entry IBIO5 sample, generic
`lv-hc-heading` and `lv-hc-section` wrappers dropped to 0, `midashi` blocks
rose to 50, `contents` blocks to 50, `indent23` blocks to 97, product `Link`
anchors to 105, image-backed gaiji gained the product `img_gaiji` class, and
raw `unknown_control_1f6d` gaps dropped to 0. Exact previous/next navigation,
HTMLs/fix fallback lifecycle, custom DIB character generation,
`modifyHeadwordEx`, Panel hooks, SQL/search helper hooks, and broader visual
parity remain named gaps, so exact HC0151 visual parity is not claimed.

The HKDKSR13 branch-subset proof case is `HC013D.dll`. Its vertical body loop
maps `1f09` section payloads to product drug-layout classes such as `title3`,
`medblk`, `med`, `medprice`, `medimage`, `mednamelist*`, and `indent##`.
The `1f41` path opens the recovered `midashi` block, internal links use
`lineLink`, `1f6d` is consumed as renderer state, image-backed gaiji use the
product `img_gaiji` class, and recovered JIS-pair lookahead branches emit the
`syohatsu`, `midashi*`, `title*`, litre-unit, and entity templates. In the
first 200-entry HKDKSR13 sample, generic `lv-hc-heading` wrappers dropped from
200 to 0, `lineLink` anchors rose from 0 to 2,222, product `medimage` blocks
rose from 0 to 407, and `unknown_control_1f6d` gaps dropped to 0. Custom DIB
generation, `modifyHeadword`, exact contents/table/click-menu lifecycle, full
picture extraction into final HTML, and broader representative visual parity
remain named gaps, so exact HC013D visual parity is not claimed.

The ARCHSIC4 branch-subset proof case is `HC0158.dll`. Its body loop treats a
subset of `.uni`-empty B3xx gaiji codes as inline HTML/CSS commands rather than
glyphs. Implemented mappings include rank/star spans, part-of-speech and
conjugation spans, boxed labels, red emphasis, and the conditional boxed
translation-label form. The same renderer still resolves ordinary numbered and
conjugation gaiji through `Templates/*.svg`, and renders `PCMDATA.DIC` sound
ranges with the discovered `sound.png` template. The focused ARCHSIC4 reference
entry has strong visual coverage, but this is still not a claim of exact
`HC0158.dll` parity across every entry and hook.

The DCONCI98 branch-subset proof case is `HC0157.dll`. Its vertical body
loop treats several gaiji-plane values as style/control markers tied to
`Templates/00000157.css`: A14D/A14E are accent spans; B156, B15A/B15B,
B15C/B15D, B160-B17D, B221-B226, B228/B229/B22A, B23C-B23F, and B240/B241
open or close named CSS spans; B157/B158 and B172/B173 both style the following
region and render their own gaiji/image value; and B22D-B23B render circled
number gaiji inside a red span. Its `1f4a`/`1f6a` path also uses the
`sound.png` / `img_mark2` template when available. This is a branch-table
implementation, not a string-only inference, and it remains visually incomplete
until the product wrapper, section/layout state, and remaining custom hooks are
validated.

The PROYAL43 branch-subset proof case is `HC0146.dll`. Its vertical body
loop maps B232/B233 to a `color_font` delimiter pair, B240 to the literal
abbreviation label `略：`, B157-B159 to `_M` image templates with
`img_mark4`, B25A-B351 to `gaiji_icon`, and B23B/B357-B424 to `gaiji_full`.
B236/B237/B241 and B44F-B451 are consumed as renderer template selectors rather
than displayed as glyphs. Several other HC0146 branches still route through
runtime-initialized template globals; those remain named gaps until the
concrete open tags, section/layout state, wrapper CSS, and state transitions are
recovered.

Some other renderer families are decoded but deliberately not implemented yet.
The toolkit keeps unresolved branches as named behavior gaps rather than
guessing from strings alone.

### Panel Subsystem

The Panel subsystem is now decoded as an optional SSED navigation/UI subsystem
rather than a generic file-family inventory item. The decoded samples cover
Windows package copies, recovered package copies, and mobile package copies;
Panel is not a Windows platform marker.

Complete Windows Panel packages have `Panels.dtd`, `Panels.xml`, `Panel.html`,
`Cell.html`, and external `.bin` payloads. The DTD schema is stable across the
decoded set: `panels` contains package information and `panel+`; each `panel`
contains a `title` and one or more `data` nodes; `data` may contain inline
`cell` nodes or reference an external file. `paneltype` separates menu and
contents panels. Menu panels use inline cells whose `ref` attributes point to
other Panel `index` values. Content panels usually use `data type="bin"` and a
`filename`; a few use `data type="html"`. Mac bundle resources use a
`Panels.plist` representation of the same panel/data model, while mobile
packages can use nested menu plists whose `path` values name `.bin` tables
under package `bin/` directories.

The common decoded binary payload grammar is:

```text
uint32le record_count
uint32le text_width
repeat record_count:
  uint32le target_block
  uint32le target_offset
  byte[text_width] label_text_stream
```

`label_text_stream` uses the same conservative LogoVista text decoding model as
body/title text: JIS pairs, gaiji pairs, NUL padding, and `0x1f` display
controls. This matters because many observed labels contain halfwidth and
superscript controls; treating them as raw byte pairs loses display semantics.

Additional decoded variants are now known: id-prefixed rows with an extra
`uint32le record_id`, declared-count mismatch tables where the physical row
count is smaller than the header count, empty/zero-width placeholder tables,
and one mobile headerless table whose records are `uint32be block`,
`uint32be offset`, and fixed-width NUL-padded UTF-8 labels.

The current full Panel decode pass covered every non-`vlpljbl.bin` `.bin`
candidate under the Windows SSED corpus, recovered packages, and the mobile
corpus:

```text
metadata files parsed:          12
external references parsed:  2,544
Panel BIN files decoded:     4,544
binary decode failures:         0
```

Rows mostly target `HONMON.DIC`; one decoded package also has a small set of
rows targeting `MENU.DIC`. This makes Panel content an optional navigation
surface: label -> raw SSED address. It is not an entry body store, media store,
compressed container, SQLite sidecar, or replacement for normal native index
search.

Observed packaging details:

- Some packages store Panel `.bin` payloads in sibling `_Panel` directories,
  package-local `Panel/`, package-local `bin/`, or the package root.
- XML filenames use Windows backslashes and require path normalization. Mobile
  plist references can omit the `.bin` suffix.
- Some packages carry Panel templates or DTD fragments without `Panels.xml`;
  those files are renderer/template residue, not a complete decodable Panel
  navigation surface by themselves.
- One Windows package copy has a missing Panel `.bin` referenced from XML; the
  corresponding iOS package copy supplies that same file, and it decodes with
  the same fixed-record grammar.
- Windows reader metadata uses `EXINFO.INI` `GENERAL/PANELXML` to advertise
  Panel XML, and renderer plug-ins can expose Panel lifecycle hooks. Those
  hooks wrap the generic Panel model; they do not change the observed BIN
  record grammar.

Reader impact: Panel support should be exposed as an explicit optional
navigation/sidebar API. Friendly entry rendering should not automatically merge
Panel rows into normal body text, but a reader can safely show Panel labels as
links to the decoded target addresses.

### CCALTSTR Alternate-String Tables

The observed Windows corpus contains four `CCALTSTR.HA` files in the targeted
English/Japanese-English packages, plus one `CCALTSTR.FU` sibling in the
French/Japanese-French package family. The files decode as fixed-record
custom-character alternate-string tables.

Observed table facts:

```text
magic values:      SDICALTH (.HA), SDICALTF (.FU)
header size:       16 bytes
record size:       62 bytes
value field:       60-byte NUL-terminated alternate string
code sequence:     JIS row/cell order
file size rule:    16 + record_count * 62
```

The `.HA` tables use half-width/custom-character ranges such as `A121...`; the
observed `.FU` table uses a full-width/custom-character range beginning at
`B121`. Nonempty rows overlap the package-local `.uni` custom-character code
set, but the values are short alternate strings rather than display text or
glyph data.

Reader impact: `CCALTSTR` is relevant to search/headword normalization for
custom characters. It is not an entry body store, Panel data, media, a gaiji
bitmap/image store, or a replacement for `.uni` display mappings.

### SIZK / NHK 文学のしずく Read-Aloud Packages

The corpus contains 30 SIZK packages, `SIZK0101` through `SIZK0605`. These are
not normal search dictionaries, but they are still classic SSED packages:

```text
SIZKxxxx.IDX
HONMON.DIC
GA16FULL
GA16HALF
EXINFO.INI
HC0190.dll
HTMLs/b121.html ... b124.html
Templates/honbun.html
shizuku.mp3
shizuku_honbun.txt
shizuku_time.txt
shizuku.uni
```

All 30 share `HC0190.dll`, and `EXINFO.INI` declares:

```ini
SRCINFO=NHK 文学のしずく
HTML=1
HTMLDLL=HC0190.dll
IDXTITLE=メニュー
INDEXURL=index.html
MP3NAME=shizuku.mp3
GAIJI=shizuku.uni
```

The `GAIJI=shizuku.uni` declaration matters. The file is not named after the
main `.IDX` stem, so stem-only gaiji discovery misses it. The file uses the
simple 12-byte `.uni` layout with zero half-width records and 15 full-width
records. The observed records include blank `B121`-`B124` template selectors
and visible mappings such as `B128=〜`, `B129=︱`, `B12A=鷗`, `B12B=—`, and
`B12C=蟬`.

`HONMON.DIC` is tiny: 29 packages expand to one 2048-byte page, and `SIZK0502`
uses two declared pages. Each expanded HONMON stream has four entry markers.
The first full-width gaiji in each entry selects one renderer template:

| Code | Template | Role |
|---|---|---|
| `B121` | `HTMLs/b121.html` | overview |
| `B122` | `HTMLs/b122.html` | author introduction |
| `B123` | `HTMLs/b123.html` | narrator introduction |
| `B124` | `HTMLs/b124.html` | read-aloud playback |

The same HONMON entries carry structured section data. Observed section roles:

```text
0004 work title            0014 author image
0005 work reading          0015 author image credit
0006 publication year      0018 author biography
0007 intro/excerpt line    0021 narrator name
0008 synopsis              0022 narrator reading
0011 author name           0023 narrator profile
0012 author reading        0024 narrator image
0013 author dates          0028 narrator credits
0031 audio filename        0032 time filename
0033 text filename
```

The read-aloud body is loose package data rather than `PCMDATA.DIC`:
`shizuku.mp3` plus UTF-16 `shizuku_time.txt` and `shizuku_honbun.txt`.
`Templates/honbun.html` contains matching vertical-text `<div class="honbun"
id="...">` rows whose IDs are millisecond timestamps. The `sizk` command
validated all 30 packages with four HONMON entries, four HTML templates,
synchronized playback rows, and zero issues.

### Numeric `00000xxx.idx` Sidecar Trees

The observed eight-hex-digit `*.idx` files are not `SSEDINFO` catalogs and are
not binary patch files. They are CP932 text sidecar trees used for appendices,
classification lists, and browser/search selector panels. Windows packages
usually reference them from `EXINFO.INI`, but they can also appear in mobile
packages and are not always declared there.

Observed row format:

```text
00000000<TAB>00000000<TAB>category label
00000002<TAB>00000002<TAB><TAB>leaf label
```

The first two columns are eight-digit hexadecimal block/offset values. Leading
tabs after the pointer columns define tree depth. Labels are CP932 text and may
contain HTML numeric entities such as `&#xe0;`, which decode to display text
such as `à`.

Pointer semantics:

- `00000000 00000000` is a non-clickable heading/category row.
- Normal pointers resolve against the raw component block table, almost always
  to `HONMON.DIC`.
- In body-stream dictionaries the pointer is a direct body destination.
- In dense-HONMON renderer dictionaries the pointer lands on a 32-byte HONMON
  ID anchor, then that raw ID resolves into `DictFULLDB`, renderer `t_contents`,
  or an Android body table.
- Values such as `10000000 0000ffff`, `30000000 0000ffff`, and
  `60000000 0000ffff` are virtual selector rows. The high nibble is the
  selector ID; in HAESPJPN these map to `西和ABC順`, `和西50音順`, and
  `動詞活用表`.

The filename often matches the product/plugin code used by the Windows renderer
DLL, but this is a convention rather than a requirement. In the combined
HC-bearing pass, 103 of 158 renderer rows have at least one sibling numeric
index and 89 have the exact unsharded expected name, computed as the HC code
padded to eight hex digits (`HC013A.dll` -> `0000013A.idx`). Some packages use
sharded same-code numeric trees such as `00000151_0.idx`; others carry numeric
indexes whose code differs from the HC DLL. `EXINFO.INI` `HTMLDLL`, not the
numeric index filename, is the authoritative HC renderer link.

Observed corpus examples:

| Dictionary | File | Rows | Meaning |
|---|---|---:|---|
| `HAESPJPN_WIN` | `0000013A.idx` | 4 | Three virtual selector rows for Spanish/Japanese browse modes and verb conjugation. |
| `SINMEI7_WIN` | `00000135.idx` | 4 | Appendix entries such as accent display and symbol tables. |
| `HAFRAN` iOS/Windows | `00000152.idx` | 1,084 | A-Z French grammar/topic tree; Windows and iOS files are byte-identical. |
| `IWKOKUG8_WIN` | `000002D0.IDX` | 14 | Appendix table for word-formation, word-class, and conjugation material. |
| `DAIJIRN4_WIN` | `0000015E.IDX` | 284 | Field/season-word appendix tree pointing into HONMON ID anchors. |
| `PROYAL53_WIN` | `0000015F.IDX` | 149 | Important-word and grammar-frame appendix tree. |
| `JSSAURU2` iOS | `0000015C.IDX` | 10,298 | Large thesaurus classification tree. |
| `KENROWA` iOS | `0000015B.IDX` | 233 | Abbreviation and topic appendix tree. |
| `MEIKYOU2` iOS | `0000012d.idx` | 443 | Appendix and column tree. |

For conversion work, these files should be preserved as structured navigation
metadata. They are not usually the main lookup index, but they can expose
appendix bodies and topic hierarchies that a plain headword conversion would
otherwise miss.

### SINMEI7 Windows vs iOS

SINMEI7 Windows and SINMEI7 iOS both use the same nine core `SSEDINFO`
components:

```text
HONMON.DIC
MENU.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

Both `HONMON.DIC` files are plain `SSEDDATA`, and raw extraction works without
SQLite. The Windows copy expands to 47,515,648 bytes, has 75,532 entry markers,
and the raw index scan produces 75,529 body boundaries. `MENU.DIC` expands to
8,988,672 bytes and resolves 75,939 menu/body destinations.

Observed platform differences:

- Windows keeps renderer assets in `Templates/`, `HTMLs/`, `HANREI.chm`, and a
  product-specific `HC0135.dll`.
- iOS keeps converted assets in top-level `img/`, `html/`, `OTHER/`, plist
  manifests, app SQL, and `bin/` payloads.
- Windows `EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC0135.dll`, `PCMP3=1`,
  `IDXCOUNT=1`, `IDXINFO0=00000135.idx`, and `IDXTITLE=付録`.
- `00000135.idx` is not `SSEDINFO`; it is CP932 tab-separated appendix metadata.
  Rows contain hex block, hex offset, optional empty/category fields, and a
  display title. The block/offset values point to raw HONMON addresses and to
  decimal-named files in `HTMLs/`, for example `00005a95 00000312` maps to
  `HTMLs/23189-786.html`.
- Windows `Templates/` resources are package images just like iOS `img/`
  resources. The local SINMEI7 Windows copy exposes 203 image/BMP resources and
  29 code-shaped gaiji-image keys after scanning `Templates/`.
- The two `.uni` files have no conflicting values for shared codes. Windows has
  351 usable mappings, iOS has 331. The Windows file contributes extra rare CJK
  and compatibility mappings; the iOS file contributes three radical mappings
  not present in Windows.
- `GA16FULL` and `GA16HALF` are byte-identical across the two copies. `GA16FULL`
  starts at `B221` and has 375 glyph slots; `GA16HALF` has zero glyphs.
- `PCMDATA.DIC` remains parseable on Windows and contains MP3 records referenced
  by raw HONMON controls.

### HAESPJPN Windows vs iOS

HAESPJPN Windows and HAESPJPN iOS use byte-identical raw dictionary components:

```text
HAESPJPN.IDX
HONMON.DIC
FKINDEX.DIC / FHINDEX.DIC
BKINDEX.DIC / BHINDEX.DIC
COLSCR.DIC
PCMDATA.DIC
GA16FULL / GA16HALF
```

The raw `HONMON.DIC` body stream is therefore fully compatible across the two
copies. Both expand to 27,979,776 bytes, expose 71,913 common entry markers,
and the raw index pass finds 79,904 body boundaries. The `entries` command
extracts coherent bodies from the Windows copy without SQLite; `rendererdb`
correctly ignores `HAESPJPN.db` because it is a conjugation/search cache with no
`t_contents` body table.

The differences are packaging and fallback assets:

- Windows has `EXINFO.INI`, `HC013A.DLL`, `Templates/`, `Panel/`, `Panels.xml`,
  `SPINDEX.DIC`, `HANREI.chm`, HTML help files, and `HAESPJPN.db`.
- Windows `EXINFO.INI` uses the legacy singleton form `IDXINFO=0000013A.idx`
  / `IDXTITLE=インデックス`, rather than `IDXCOUNT` / `IDXINFO0`.
- `0000013A.idx` is a CP932 text tree with virtual selector pointers:
  `10000000/ffff` = `西和ABC順`, `30000000/ffff` = `和西50音順`, and
  `60000000/ffff` = `動詞活用表`.
- Windows image resources live in `Templates/` and include `exam.png`,
  `sound.png`, and 122 code-shaped gaiji image keys.
- iOS image resources live in `img/`; its extra `Gaiji.plist` /
  `GaijiS.plist` fallback mappings raise the observed gaiji map from the
  Windows `.UNI` count of 60 to 97 combined mappings.

This is the cleanest current example of high raw-core compatibility with
package-specific resource layers.

### IWKOKUG8 iOS vs Android vs Windows

IWKOKUG8 has been checked across iOS, Android, and Windows. The raw core is
byte-identical across all three copies:

```text
IWKOKUG8.IDX
HONMON.DIC
FKTITLE.DIC / FKINDEX.DIC
FHTITLE.DIC / FHINDEX.DIC
BKTITLE.DIC / BKINDEX.DIC
BHTITLE.DIC / BHINDEX.DIC
GA16FULL / GA16HALF
IWKOKUG8.uni / IWKOKUG8.UNI
```

The shared `HONMON.DIC` is not a body stream. It expands to 10,477,568 bytes
and contains 65,480 numeric ID records in the dense 32-byte anchor-table
layout. Raw title/index extraction still works: the `*TITLE.DIC` streams expose
lookup titles, and the index parser finds 65,468 body/index boundary rows.

The platform body payload differs:

| Platform | Body payload | Raw ID relationship |
| --- | --- | --- |
| iOS | `DictFULLDB` SQLite `IWKOKUG8.sql`, table `t_contents` | `f_DataId` matches raw HONMON IDs |
| Android | Plain `IWKOKUG8.db`, table `IWKOKUG8(Html)` | `data_id = rowid * 5` |
| Windows | Encrypted `vlpljblh`, decrypted SQLite table `t_contents` | `f_DataId` matches raw HONMON IDs |

Observed extraction counts:

```text
Raw HONMON ID records:       65,480
iOS t_contents rows:         65,480
Windows t_contents rows:     65,480
Android Html rows:           65,468
Android raw IDs missing:         12
```

Those 12 missing Android rows correspond to the 12 `f_Type=5` rows present in
the Windows/iOS `t_contents` payload. The normal dictionary bodies line up by
raw ID.

The resource wrappers differ:

- iOS uses `img/` and an iOS `DictList.plist` declaration for `DictFULLDB`.
- Android uses `resource/conf.ini`, `resource/kmkimges/`, `manual/`,
  `innerdata/`, and a plain app DB. Its `media` table uses
  `id/name/type/main` columns and stores 345 SVG blobs plus one additional
  media row.
- Windows uses `EXINFO.INI`, `HC02D0.dll`, `Templates/`, `HANREI/`,
  encrypted `vlpljblh`, and two font sidecars: `vlpljblB` is `Noto Sans JP`
  Regular OpenType/CFF, and `vlpljblN` is `Noto Serif JP` Regular OpenType/CFF.
  These font files are not encrypted SQLite sidecars.

The `rendererdb` command handles both body-cache shapes while still starting
from raw HONMON IDs: Windows renderer `t_contents` rows and the Android
`Html` table shape using `rowid * 5`.

### Observed SQLite `.db` Sidecars

SQLite sidecars are not Windows-only. In the observed corpus they appear as
Windows root/template DBs, Android app DBs, no-platform supplemental DBs, and
iOS `DictFULLDB` SQL payloads. Two Windows-corpus `Thumbs.db` files are OLE
thumbnail caches and are not dictionary SQLite.

Observed role classes:

| Role | Observed examples | Reader impact |
|---|---|---|
| `sqlite_renderer_body*` | Android `IWKOKUG8.db`, Windows encrypted/renderer `t_contents` payloads | Body-critical when raw HONMON carries dense IDs. |
| `sqlite_block_offset_body` | Android `OUKOKU11.db` | Body rows carry `Block`/`Offset` plus title/body text. |
| `sqlite_android_index_metadata` | Android `OUKOKU11_indexinfo.db` | Android UI/index metadata, not body text. |
| `sqlite_examples_idioms` | `D_Example`, `D_Idiom` DBs | Supplemental examples/idioms keyed by body address. |
| `sqlite_supplemental` | `D_Goyo`, `D_Keigo`, `D_Kininaru` | Supplemental usage panes keyed by body address. |
| `sqlite_link_reference` | `GENIUSEB.db`, category-style block/offset/title tables | Navigation/reference metadata. |
| `sqlite_kanji_support` | `t_all`, `t_bushu`, `t_jukugo`, `t_yomi`, `t_exam` | Kanji lookup/support tables. |
| `sqlite_search_or_conjugation` | `HAESPJPN.db` | Search/conjugation helper cache; not a body source. |
| `sqlite_template_navigation` | `GKBusine.db`, `GKTravel.db` | Template-driven phrase/navigation helper DBs. |
| `sqlite_category_search_index` | `kyz_filter.db` | Category/filter search helper DB. |
| `sqlite_ancillary` | `D_InternationalChronology`, tiny `t_data(index,data)` DBs | Ancillary app/helper data. |

The toolkit classifies these by schema capabilities first, then by stable
table-name evidence only where the schema is otherwise ambiguous. Body
extraction remains conservative: only body-capable SQLite roles are accepted as
`rendererdb` body sidecars. Supplemental/search/kanji/ancillary DBs are
reported and can be copied by `extract`, but they are not treated as replacement
entry bodies.

### Corpus-Wide `vlpljbl*` Audit

The audited Windows SSED corpus contains 98 `vlpljbl*` files. Every observed
file is now classified by raw magic, LogoFontCipher-decrypted magic when
needed, SQLite schema when applicable, and inferred role.

Suffix counts:

```text
.bin      49
.exe      12
<none>     4
B          3
F         14
M          3
N          3
S          5
b          4
h          1
```

Role counts:

```text
logofont_decryptor_binary                 61
font                                      12
sqlite_renderer_body                       7
sqlite_renderer_body_with_media            8
sqlite_row_ordered_honbun_renderer_body    1
sqlite_honbun_data_id_body                 1
sqlite_block_offset_body                   2
sqlite_media_store                         3
sqlite_search_index                        1
sqlite_category_search_index               1
sqlite_block_offset_title_index            1
```

The suffixes are meaningful but not globally one-to-one:

- `.bin` / `.exe` are PE Crypto++ decryptor programs. The two filename
  extensions carry product-generation/package differences, not data role.
- `B` is plain OpenType/CFF `Noto Sans JP` in the observed packages.
- `N` is plain OpenType/CFF `Noto Serif JP` in the observed packages.
- `M` is plain SQLite media-only storage.
- lowercase `b` is LogoFontCipher SQLite renderer body storage with media in
  DAIJIRN4, SINJIGEN, YHOUGO5, and YUPSYCHO.
- lowercase `h` is the IWKOKUG8 LogoFontCipher SQLite renderer body/media
  store.
- uppercase `F` is always LogoFontCipher SQLite in the observed corpus, but
  its role varies: normal renderer body, body with media, row-ordered `HONBUN`,
  data-id `HONBUN`, block/offset body rows, or KWIT category search.
- `S` is overloaded: it can be a font, a plain SQLite search/index DB, or an
  encrypted font depending on dictionary generation.
- no suffix is also overloaded: observed files include fonts and encrypted
  block/offset body SQLite.

The rule is content-first classification. The toolkit must not infer
body/media/font behavior from suffix alone.

### Dense-Anchor Sidecar Dereference Update

The five previously deferred dense-anchor packages inspected in the lvcore
body-source pass are sidecar-backed SQLite cases, not non-SQLite opaque
LogoFontCipher payloads:

| package | sidecar payload | decrypted storage | schema role | anchor key |
|---|---|---|---|---|
| `CJJC160` | `CJJC160` | LogoFontCipher SQLite | extensionless `main` word-list table | `main.ID` |
| `KJJK100` | `KJJK100` | LogoFontCipher SQLite | extensionless `main` word-list table | `main.ID` |
| `PRMEDAB7` | `vlpljblF` | LogoFontCipher SQLite | renderer `t_contents` table plus keyword table | `t_contents.f_contents_id` |
| `YHOUGO5` | `vlpljblb` | LogoFontCipher SQLite | renderer `t_contents`, search, full-text, media tables | `t_contents.f_order_id` |
| `YUPSYCHO` | `vlpljblb` | LogoFontCipher SQLite | renderer `t_contents`, list, search, full-text, media tables | `t_contents.f_order_id` |

`HONMON.DIC` remains the SSED anchor layer. The dense records contain
zero-padded numeric IDs inside normal body-stream control wrappers; native
index body pointers in the three indexed packages resolve to those IDs, and
the ID values map directly to the SQLite body rows above. `CJJC160` and
`KJJK100` use the same numeric anchor table shape, but their local corpus
copies do not expose usable native SSED index files, so lvcore can classify and
inspect the body source but cannot yet provide normal native index search for
those packages from SSED index components alone.

### EJJE200 Windows Encryption

EJJE200 is the first observed Windows package with encrypted primary body data.
Its `EXINFO.INI` declares:

```ini
HTML=1
HTMLDLL=HC014F.dll
KWIT=1
IDXINFO0=select.html
ROSQLNAME=EJJE200.db
ENCRYHON=1
```

`HONMON.DIC` does not start with `SSEDDATA` on disk. Static analysis of the
shipped `vlpljbl.bin` shows it is a Crypto++ decryptor using AES-128-CBC
(`Rijndael`, `CBC_Decryption`, `StreamTransformationFilter`). The passphrase is
the obfuscated literal `LogoFontCipher`; each byte is stored XOR `0xff` in the
program. The key schedule is:

```text
digest = SHA256("LogoFontCipher")
AES-128-CBC key = digest[0:16]
AES-CBC IV      = digest[16:32]
key             = a3c48d86dabe8b0c91fb33d9fdf2941b
iv              = 80f2f3736bcec2e51665d02b640edbb0
```

Decrypting `HONMON.DIC` with that key reveals normal `SSEDDATA`:

```text
chunks=4087 start=0x2 end=0xff63 kind=0x0 storage=logofont_cipher
expanded_bytes=133,894,144
entry_markers=1,864,040
index_entry_boundaries=1,864,040
```

Raw entries are coherent without SQLite after decryption:

```text
(mobile)number portability
番号ポータビリティ[情報]

.NET
.NET[情報]
```

The package-specific Windows renderer sidecars also point at the same encrypted
sidecar family, matching the observed `vlpljbl.bin`, `DIC014F`, and
`vlpljblF` behavior. This is package-correlation evidence, not a dependency of
the open reader model.

`vlpljblF` decrypts with the same LogoFontCipher key to a SQLite database. It is
not the primary body stream. It contains 17 tables named `t_Search_1` through
`t_Search_17`, matching the 17 category checkboxes in `Templates/select.html`
for KWIT partial-match search (`情報`, `電気`, `物理`, ..., `環境`). The table
schema is:

```sql
CREATE TABLE t_Search_N (
  f_type TEXT,
  f_midasi TEXT,
  f_midasi_jis TEXT,
  f_block TEXT,
  f_offset TEXT
);
```

### NGYOKTUK Windows HONBUN Renderer Database

`NGYOKTUK` (`日外 外国人名よみ方・綴り方字典`) uses a dense 32-byte HONMON
entry table. The raw rows contain enough visible label text to align entries,
but the full formatted display body lives in a renderer sidecar. Its Latin
gaiji are not backed by `.uni`, GA16, plist, or package image resources.
`EXINFO.INI` declares `GAIJI=NGYOKTUK.UNI`, but that file is absent from the
package.

The encrypted sibling `vlpljblF` decrypts with the LogoFontCipher key to a
SQLite database with a single `HONBUN` table:

```sql
CREATE TABLE HONBUN (
  ID TEXT NOT NULL UNIQUE,
  Title_UTF8 TEXT,
  Title_SJIS TEXT,
  Contents_HTML_box TEXT,
  Contents_HTML_list TEXT,
  LEVEL1 TEXT,
  LEVEL2 TEXT,
  LEVEL3 TEXT,
  PRIMARY KEY(ID)
);
```

The row count is 278,705, exactly matching the number of raw HONMON entry
slices. Ordering by `ID` aligns rows to raw HONMON entries:

```text
raw entry 117: Abb<hA13C> / アベ * / アベー
HONBUN 00000117: Abbé / アベ ＊ / アベー
```

This sidecar is therefore valid entry-level display evidence. It is not a
simple gaiji map. Some raw gaiji codes are context-dependent; for example a
code observed as `A168` can render as different Latin letters with diacritics
in different name-entry contexts. A lossless exporter should preserve the raw
code provenance and use the matched `HONBUN` HTML for display when direct raw
gaiji resources are absent.

### DAIJIRN4 Windows Renderer Database

DAIJIRN4 is the first observed Windows package where plain `HONMON.DIC` is not
a definition stream and the full formatted body is in a Windows renderer
database. The core `SSEDINFO` table contains 11 components:

```text
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
GA16FULL
GA16HALF
```

There is no `MENU.DIC`, `PCMDATA.DIC`, or `COLSCR.DIC`. `HONMON.DIC` is plain
`SSEDDATA` and expands to 43,106,304 bytes, but the expanded stream is a dense
run of 32-byte anchor records:

```text
1f0a 1f09 0001 1f41 0160 1f04 [8 JIS cells] 1f05 1f61
```

Every fifth record carries an eight-cell full-width decimal ID, for example
`00000025`; the other four records in that group are blank anchors. The marker
start used by raw indexes is two bytes after the 32-byte record start. Example:
data id `25` is at record offset `768`, block `2`, offset `768`, with the
marker target at block `2`, offset `770`.

The count relationship is exact:

```text
HONMON entry markers:       1,347,035
HONMON ID records:            269,407
t_contents rows:              269,386
DB rows matching raw IDs:      269,386
raw IDs missing in DB:             21  terminal trailer anchors only
```

This is not a failed HONMON parse. It is a raw anchor table. The body payload is
the encrypted sibling `vlpljblb`, and the bundled `vlpljbl.bin` is byte-identical
to the EJJE200 decryptor. Decrypting `vlpljblb` with the LogoFontCipher key
produces a 610,735,616-byte SQLite database with three tables:

```sql
CREATE TABLE t_contents (
  f_DataId INTEGER PRIMARY KEY,
  f_Type INTEGER,
  f_DataGroupId INTEGER,
  f_Anchor TEXT,
  f_Title TEXT,
  f_Title_SS TEXT,
  f_Html TEXT,
  f_Keyword TEXT,
  f_Plane TEXT
);

CREATE TABLE t_bunya (
  f_DataId INTEGER NOT NULL PRIMARY KEY,
  f_GenreKey TEXT,
  f_Title TEXT,
  f_TitleSS TEXT
);

CREATE TABLE media (
  No INTEGER NOT NULL PRIMARY KEY,
  f_name TEXT,
  f_type INTEGER,
  f_main BLOB
);
```

Observed row counts and content types:

```text
t_contents: 269,386 rows
t_bunya:     47,375 rows
media:        2,830 rows

f_Type=1  parent entries                 179,350
f_Type=2  child/sub entries               78,144
f_Type=3  idiom/phrase entries             8,328
f_Type=4  kanji entries                    3,283
f_Type=5  late appendix/search rows          273
f_Type=6  terminal/special rows                8

media f_type=2  PNG appendix images           86
media f_type=3  GIF entry figures          2,744
```

`f_Html` is complete renderer HTML. It contains links such as
`lved.dataid:01346760` and inline figure tags such as
`<img src="3djr_0002.gif" class="media">`; those image names resolve to the
`media.f_name` BLOB table. `f_Plane` is the flattened plain/search body. The
`rendererdb` command emits the HTML/plain rows and can write the `media` BLOBs
as portable image files.

The package-specific Windows renderer sidecars corroborate this interpretation:
the observed sidecar names and SQL table/column names match the decrypted
`vlpljblb` schema. This evidence is used to name the structure; it is not a
runtime dependency.

`EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC015E.dll`, `IDXCOUNT=3`,
`IDXINFO0=0000015E.IDX`, `IDXINFO1=select.html`, `IDXINFO2=select2.html`,
`ROSQLNAME=DAIJIRN4.db`, `BUBUNDB=1`, `ZENBUNDB=1`, and `VERTICAL=1`.
`0000015E.IDX` is a CP932 tab-tree with 284 rows. Its first two columns are
hex block/offset pointers into the raw HONMON anchor layer; tab depth defines
labels such as `大辞林 第四版 / 分野別索引 / 季語 / 春`.

DAIJIRN4 gaiji/resources are otherwise normal:

```text
DAIJIRN4.uni: simple12, 92 half records, 1,191 full records, 1,243 mappings
GA16HALF:     8x16, start A121, 92 glyphs
GA16FULL:    16x16, start B121, 1,191 glyphs
Templates:  255 portable resources after PNG/GIF/BMP/SVG discovery
```

### PROYAL53 Windows Renderer Database and Ziptomedia

PROYAL53 (`旺文社「プチ・ロワイヤル 仏和（第5版）・和仏（第3版）辞典」`)
is another Windows renderer-database package. Its raw core has the same 11
component shape as DAIJIRN4:

```text
HONMON.DIC
FKTITLE/FKINDEX, FHTITLE/FHINDEX
BKTITLE/BKINDEX, BHTITLE/BHINDEX
GA16FULL
GA16HALF
```

`HONMON.DIC` is plain `SSEDDATA`, expands to 12,040,192 bytes, and is a dense
32-byte raw ID anchor table rather than definition text. Direct `entries`
extraction correctly emits no body entries. `audit-honmon` classifies the
package as `dense_honmon_id_table_rendererdb`.

`EXINFO.INI` declares `HTML=1`, `HTMLDLL=HC015F.dll`, `PCMP3=1`,
`IDXINFO0=0000015F.IDX`, `IDXINFO1=select.html`, `ROSQLNAME=PROYAL53.db`,
`BUBUNDB=1`, and `ZENBUNDB=1`. The auxiliary `0000015F.IDX` is a readable
CP932 tab tree with 149 rows and HONMON anchor destinations for sections such
as `仏和辞典-重要語 / ランク1`.

The encrypted sibling `vlpljblF` decrypts with the LogoFontCipher key to a
SQLite renderer database. Its schema is a lowercase variant of the same idea:

```sql
CREATE TABLE t_contents (
  f_dataid INTEGER PRIMARY KEY,
  f_datagroupid INTEGER,
  f_type INTEGER,
  f_genre INTEGER,
  f_title TEXT,
  f_title_ss TEXT,
  f_keyword TEXT,
  f_sakuin TEXT,
  f_rank INTEGER,
  f_html TEXT,
  f_plane TEXT
);

CREATE TABLE t_media (
  id INTEGER PRIMARY KEY,
  type INTEGER,
  name TEXT,
  main BLOB
);
```

Renderer extraction now normalizes `t_contents` column case and accepts both
`media` and `t_media` BLOB-table names. Observed counts:

```text
raw HONMON ID records:       75,243
t_contents rows:             75,225
rows matching raw IDs:        75,224
DB rows without raw ID:            1
raw IDs missing in DB:            19

f_type=1  main entries        44,538
f_type=2  sub/child entries   30,564
f_type=8..12 special rows        122

t_media rows:                   342
t_media type=1                  116
t_media type=2                  226
```

The raw search/title layer is parseable:

```text
FKINDEX/BKINDEX leaf rows:   362,519 each
FHINDEX/BHINDEX leaf rows:   361,903 each
FK/BK search groups:         314,922
unknown index leaf bytes:          0
title unknown controls:            0
```

`f_Html` is complete renderer HTML and contains enough structure for a
Yomitan/MDict-style export: headword spans, subentry anchors, grammatical
labels, examples, inline references, and media/image tags. `f_keyword` is also
very useful for exact lookup expansion because it contains alternate spellings
and inflected French forms separated by `∥`, for example `abandonné`,
`abandonnee`, plural forms, and conjugated forms. This is better lookup
evidence than trying to infer every exact Yomitan headword from display HTML.

Image resources come from both `Templates/` and `HANREI/img`. The renderer HTML
references assets such as `sound.png`, `b159_M.png`, and BLOB-backed names such
as `00002153-0082-000006ec.png`; `--write-media` preserves original renderer
filenames when possible so those HTML `src` values can be copied or rewritten
directly. PROYAL53 gaiji is otherwise normal:

```text
PROYAL53.uni: simple12, 60 half records, 350 full records, 120 mappings
GA16HALF:     8x16, start A121, 60 glyphs
GA16FULL:    16x16, start B121, 351 glyphs
raw unresolved gaiji codes after .uni/image/bitmap coverage: 0
```

PROYAL53 also introduces loose ziptomedia audio. Renderer HTML links look like:

```html
<a href="lved.ziptomedia:000010.wav"><img src="sound.png" class="gaiji_icon"></a>
```

The physical files live outside the dictionary core in the sibling directory
`_DCT_PROYAL53_Sound_Files/` and have no filename extension. Each observed file
is LogoFontCipher-wrapped audio; decrypting `000010` yields a normal
`RIFF/WAVE` file:

```text
000010.wav: WAVE audio, Microsoft PCM, 16 bit, mono 44100 Hz
```

The package references more audio than is physically present in the local copy:

```text
HTML ziptomedia references:       17,155
distinct referenced sound names:  17,124
loose sound files present:         2,506
unreferenced loose sound files:        0
referenced sound files missing:   14,618
```

That is not a body-decoding blocker. It means a full text/image conversion is
well covered, while complete audio export requires a package/install that
contains the missing ziptomedia files.

PROYAL53 also has ten extensionless files under `dat/`. These are not SQLite
tables or SSED components. Each file is LogoFontCipher-wrapped `RIFF/WAVE`
audio; decrypting the file gives the app-ready `.wav` payload directly. The
filenames are part of the resource identity and should be preserved with a
`.wav` suffix on export.

### Britannica Loose Media Text Files

Observed Britannica media companion packages use plaintext side files outside
the SSED catalog:

- `whatday/M-D.body` and `whatday/M-D.top` are CP932 HTML fragments keyed by
  month/day. The fragments contain table markup and `lved.addrXXXXXXXX:YYYY`
  links. The address fields are hexadecimal block/offset pairs into the
  associated dictionary body/search space.
- `top/top_art.dat`, `top/top_biography.dat`, and `top/top_nature.dat` are
  CP932 five-line record lists. Each record contains an item id, display title,
  short description, hexadecimal body address, and image filename. The image
  filename resolves against sibling `thumb/`, `mini/`, and `full/` media
  directories.

The current local files decode as:

```text
whatday .body fragments:         366
whatday .top fragments:          366
whatday lved.addr references:  7,555
top/*.dat files:                   6
top/*.dat records:               241
top records with image matches:  241
```

These are auxiliary media/navigation resources, not encrypted component
stores. The parser preserves the HTML/text payloads and separately exposes the
resolved address and image relationships.

### SPINDEX.DIC

`SPINDEX.DIC` is a standalone auxiliary SSED container, not a component declared
inside the product `.IDX` catalog and not a Windows-only marker. Mac OS X
package layouts can use SPINDEX-style auxiliary resources too. The local
Windows corpus currently contains four copies: EJJE200, HAESPJPN Windows,
SINMEI7 Windows, and the companion browser install copy. All four are
byte-identical:

```text
sha256 aabd6d909fb7bed5d446192fbbf757d18367ca28fb6d72ad69984a842b1a85b9
size   14349 bytes
```

The file is still an SSED container. Its header starts with `SSEDDATA`, has the
submagic bytes `SPDATA`, reports kind byte `0x54`, and declares 116 compressed
chunks over logical blocks `0xd9c8..0xe101`:

```text
declared logical blocks:     1,850
expected expanded bytes:     3,788,800
physical chunks present:     2 / 116
expanded bytes present:      38,208
complete expanded pages:     18
partial expanded pages:      1
```

The physical file is therefore only a prefix of the declared SSED stream. The
chunk table points to later chunk offsets up to roughly `0x206921`, but the
file ends at `0x380d`. The second physical chunk is incomplete. Generic SSED
expansion should not assume that every offset in this file is backed by bytes.

The bytes that are present decode cleanly as index branch pages using the same
page machinery as `FKINDEX.DIC` internal pages:

```text
root page  logical block 0xd9c8  header word 0x601e  rows 33  slot 34
page 2     logical block 0xd9c9  header word 0x4020  rows 56  slot 36
page 3+    logical block 0xd9ca  header word 0x0020  rows 56  slot 36
```

No leaf/result pages are present in the observed physical file. The 19 parsed
pages contain 1,022 internal rows. Eighteen child links point to pages present
in the physical prefix and 1,004 child links point to missing pages.

The keys are stored backward:

```text
CITEROHPAID       -> DIAPHORETIC
DEZIRECREM        -> MERCERIZED
EPATGNITALUSNI    -> INSULATINGTAPE
GNIFRUSOGE        -> EGOSURFING
TEEHSNOITANIMAXE  -> EXAMINATIONSHEET
```

This strongly indicates suffix/backward-search support. The rows visible in
`SPINDEX.DIC` are separator/fence keys for internal B-tree pages, not dictionary
hits. Because the observed file has no leaf pages and is identical across
unrelated dictionaries, it should be treated as common auxiliary LogoVista
suffix-search metadata or a bundled search skeleton. It is not a
product-specific dictionary index and cannot produce body entries by itself.
