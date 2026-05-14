# lvcore experimental

`lvcore` is a clean Python reimplementation of the LogoVista reader/parser core.
It intentionally does not import `logovista_tools`.

`lvcore` is reader-only. The experimental writer in the main toolkit is useful
as reverse-engineering evidence, but writer-generated packages are not the
compatibility target for this library. The compatibility target is the real
LogoVista SSED corpus.

Current scope:

- detect SSED / LVED SQLCipher / LVLMultiView SQLite package families;
- parse SSEDINFO catalogs;
- load plain and LogoFontCipher-encrypted SSEDDATA components;
- expand SSED chunks and read component slices;
- parse dictionary-local `.uni` / `.UNI` gaiji mappings, plist fallback
  metadata, GA16/GAI16 bitmap resources, and image-backed gaiji assets;
- decode SSED text streams into model-like spans;
- classify observed SSED `0x1f` controls through a local behavior atlas;
- parse title streams and native index rows, including simple, tagged,
  body-only, keyword, cross-reference, and MULTI selector index families;
- classify SSED body sources separately from package families;
- slice readable direct `HONMON.DIC` body-stream entries;
- detect dense HONMON anchor tables and avoid rendering anchor records as
  friendly dictionary bodies;
- resolve structurally understood dense-anchor SQLite sidecars such as
  `t_contents`, `HONBUN`, and dict-code-named `main` payloads;
- classify supplemental SQLite sidecars by role and attach structurally clear
  examples/idioms, link-reference, sidecar-search metadata, and sidecar BLOB
  media resources without transforming their contents;
- expose a small CLI for inspection and lookup experiments;
- expose reader-facing `SearchResults` / `SearchHit` objects instead of
  requiring callers to consume raw index rows;
- perform native exact, forward-prefix, backward-suffix, and default native
  index search over parsed LogoVista index rows;
- dereference search hits through body/title pointers into entries;
- build `EntryDocument` trees from decoded spans;
- render friendly/semantic/LogoVista-like/debug HTML and plain text;
- collect recoverable diagnostics instead of leaking raw failures into
  friendly output;
- keep raw inspection/debug output explicit.

LVED and LVLMultiView are only detected for now. SSED is the active
implementation target.

Run directly from the repo:

```bash
PYTHONPATH=src/lvcore-experimental python3 -m lvcore info /path/to/_DCT_DICT
PYTHONPATH=src/lvcore-experimental python3 -m lvcore info /path/to/_DCT_DICT --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore body-source /path/to/_DCT_DICT --json
PYTHONPATH=src/lvcore-experimental python3 -m lvcore entries /path/to/_DCT_DICT --limit 5
PYTHONPATH=src/lvcore-experimental python3 -m lvcore search /path/to/_DCT_DICT term --search-profile forward --json
PYTHONPATH=src/lvcore-experimental python3 -m lvcore search /path/to/_DCT_DICT term --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --search-profile native --format html --profile friendly
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --search-profile native --format html --profile semantic
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --search-profile native --format html --profile logovista-like
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --search-profile native --format html --profile debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore resources /path/to/_DCT_DICT term --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore resources /path/to/_DCT_DICT term --json --debug --include-sidecar
PYTHONPATH=src/lvcore-experimental python3 -m lvcore resources /path/to/_DCT_DICT term --json --debug --include-gaiji
PYTHONPATH=src/lvcore-experimental python3 -m lvcore gaiji /path/to/_DCT_DICT --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore sidecars /path/to/_DCT_DICT --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore resource-info /path/to/_DCT_DICT term media-1 --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore resource-bytes /path/to/_DCT_DICT term media-1 --output /private/output/media.bin
```

Audit and scorecard commands live in the sibling `lvcore-audit` package:

```bash
PYTHONPATH=src/lvcore-experimental:src/lvcore-audit \
  python3 -m lvcore_audit package /path/to/_DCT_DICT --sample-search-hits 5

PYTHONPATH=src/lvcore-experimental:src/lvcore-audit \
  python3 -m lvcore_audit corpus /path/to/corpus --full --jobs 0 --progress \
  --output-dir /private/reports/lvcore-corpus
```

The lvcore CLI writes progress/status messages to stderr and keeps JSON/HTML/text
payloads on stdout. Use `--verbose` before or after a subcommand for extra CLI
progress detail and tracebacks while debugging unexpected errors.

Reader-facing commands are lazy by default: `info`, `search`, `render`,
`entries`, and resource listing avoid full index/body-source/sidecar/gaiji
audits unless debug or explicit resource enumeration asks for them. Use
`--debug` for forensic body-source evidence and raw structural details.
Package-level search/render/entry helpers are convenience delegates; the
Rust-shaped API surface is `package.dictionaries()[0].search(...)` followed by
`dictionary.entry_for_hit(...)`.

Small app-facing examples are available under `src/lvcore-experimental/examples`:

```bash
PYTHONPATH=src/lvcore-experimental \
  python3 src/lvcore-experimental/examples/friendly_reader.py /path/to/_DCT_DICT term

PYTHONPATH=src/lvcore-experimental \
  python3 src/lvcore-experimental/examples/debug_inspection.py /path/to/_DCT_DICT term
```

The normal dictionary-app path does not need spans, opcodes, index pages, or
component offsets:

```python
from lvcore import SearchProfile, detect_family, open_package

path = "/path/to/_DCT_DICT"
family = detect_family(path)
package = open_package(path)
body_source = package.body_source()
results = package.search("term", profile=SearchProfile.NATIVE, limit=5)

for hit in results.hits:
    heading = hit.heading
    title_status = hit.title_status
    entry = package.entry_for_hit(hit)
    html = entry.html()
    text = entry.plain_text()
    diagnostics = entry.diagnostics()
```

Raw inspection is a separate, explicit path:

```python
hit_debug = results.hits[0].inspect()
entry = package.entry_for_hit(results.hits[0])
entry_debug = entry.inspect()
document_debug = entry.document().to_dict(debug=True)
```

Inspection output is bounded by default. It exposes useful fields such as
component names, body/title pointers, index page/row numbers, opcode IDs,
diagnostics, body-source details, and span summaries. It does not emit large raw
body-byte dumps unless a future explicit low-level API is added for that
purpose.

Search profiles are native reader profiles:

- `exact`: exact match against decoded row keys and target keys, with
  conservative lookup normalization;
- `forward`: prefix lookup over forward-compatible index components;
- `backward`: suffix lookup over backward-compatible index components;
- `native`: default reader lookup that combines exact, forward, and backward
  paths while deduplicating hits that target the same body/title pointer.

Friendly search JSON exposes the reader-facing heading, heading source, and
title status. It hides raw page/pointer internals by default. Add `--debug`
when inspecting component names, page/row positions, body/title pointers,
title-resolution details, and raw parsed rows.

Known SSED index component families are either parsed or explicitly diagnosed.
The current parser handles `0x30`, `0x60`, `0x70`, `0x71`, `0x72`, `0x80`,
`0x81`, `0x90`, `0x91`, `0x92`, and `0xa1` index pages. Grouped tagged,
keyword, cross-reference, and MULTI selector rows carry group context across
leaf page boundaries where continuation target pages occur. Validation reports
index component type counts, rows by component type, malformed leaf rows,
partial physical page tails, text-like `INDEX.DIC` outliers, unsupported
component types, and continuation counts for audit use.

Title pointers are status-bearing. Some native index rows store the body
pointer in the title-pointer slot, even when other index families in the same
package have title streams. lvcore treats those rows as a clean heading
fallback from the display key, not as a title dereference failure. Real title
dereference failures remain diagnostics with reason codes.

The lvcore control model uses behavior-level names derived from observed corpus
behavior. Friendly output hides private directives and raw control bytes,
literal/preformatted spans remain readable, URL and pointer-bearing reference
spans become semantic link nodes where currently understood, and layout/media
controls are preserved as diagnostics or resource hints until a richer resolver
is available.

Gaiji, media, and links are app-facing document concepts:

- Unicode gaiji mappings are preferred by default. Missing-Unicode gaiji are
  classified by display readiness: bitmap-backed, image-backed,
  formatting-helper, renderer-entry-backed, or true unresolved display
  failure. Bitmap/resource gaiji output is an explicit render policy, and only
  true unresolved display gaiji render as missing placeholders with diagnostics.
- Media, image, and audio controls become `ResourceRef` nodes with stable
  `lvcore-resource://...` placeholder URLs unless a caller provides its own
  mapper. Media descriptor metadata is preserved for debug/developer use.
  Package-local resource APIs resolve GA16/GAI16 glyph bytes by JIS-grid and
  `.uni` record-order addressing, plist/image-backed gaiji assets,
  `COLSCR.DIC` `data`-wrapped image/media payloads, and `PCMDATA.DIC`
  addressed audio/media ranges where exact extents are known.
  `resource_bytes()` returns original untouched payload bytes only on explicit
  request; normal render/JSON output never embeds media bytes.
- URL and internal reference spans become semantic `LinkTarget` nodes when the
  target can be recovered. Unresolved link targets keep visible label text and
  record diagnostics; friendly output does not expose raw pointer bytes.

HTML rendering has four explicit profiles:

- `friendly`: default reader-facing HTML. It favors readable, escaped,
  resolved output and hides raw opcodes, offsets, dense-anchor bytes, pointer
  payloads, and private renderer directives.
- `semantic`: app-neutral HTML. It uses stable `lv-block-*`, `lv-inline-*`,
  and `data-block-kind` / `data-kind` attributes so reader applications can
  style the document without knowing LogoVista internals.
- `logovista-like`: conservative visual-intent profile. It preserves currently
  understood LogoVista-like structure with `lv-lvlike-*` classes, but it is not
  a pixel-perfect renderer contract.
- `debug`: inspection HTML. It may expose control IDs, raw span metadata,
  body-source diagnostics, gaiji codes, media IDs, link payloads, and diagnostic
  details. Use this profile for reverse-engineering and tests, not normal
  reader output.

Plain text rendering is separate from HTML profiles. It keeps readable entry
text, uses Unicode gaiji where available, and does not expose raw controls or
offsets.

SSED body-source kinds are distinct from package families:

- `body_stream`: body pointers resolve directly into readable `HONMON.DIC`;
- `dense_anchor_table` / `dense_marker_table`: `HONMON.DIC` holds anchor
  records, not final body text;
- `dense_anchor_with_sidecar`, `renderer_sqlite_sidecar`, `honbun_sidecar`, and
  related sidecar kinds: `HONMON.DIC` anchors can be resolved through a sibling
  body database when the schema is understood;
- `sidecar_unknown`: dense anchors and one or more SQLite-like sidecars were
  detected, but no supported body table schema was identified;
- `missing_body_component`: the catalog declares no readable `HONMON.DIC`
  body component in the local package copy, so validation reports a local
  package/component integrity residual rather than an unknown body format. Full
  corpus closure scorecards can ignore this class when the local package copy
  is known broken;
- LVED SQLCipher and LVLMultiView are separate package families and remain
  deferred. They are not SSED body-source failures.

Friendly rendering never displays raw dense-anchor bytes. If a dense body source
cannot be resolved, lvcore returns a clean placeholder entry plus diagnostics.
Debug output may expose anchor IDs, attempted sidecar query values, pointers,
table/column names, and sidecar mapping details.

Observed dense-anchor SQLite schemas currently recognized by lvcore include
`t_contents` rows keyed by `f_DataId`, `f_contents_id`, or `f_order_id`,
`HONBUN` rows keyed by `ID` / `f_DataId`, and extensionless dict-code-named
`main` tables keyed by `ID`. These are treated as SSED sidecar body sources,
not LVED.

Reader-side validation includes sidecar-resolution counters for sampled search
hits: resolved rows, missing anchor IDs, missing sidecar rows, and unsupported
body-source placeholders. It also reports gaiji display-readiness buckets,
reason/source/status counts, resource-byte availability, media, link, and
title-dereference counters plus body decode telemetry for unknown controls and
bytes so private corpus audits can separate safe fallback behavior from real
compatibility gaps. The corpus summary includes a closure scorecard for hard
SSED failures, compatibility-significant sidecars, native sampled search
misses, and true display-unresolved gaiji. Sidecar reports also classify sibling
SQLite and non-SQLite files by observed role, such as body-critical,
media/resource, examples/idioms, search, kanji-support, ancillary, or unknown.
For sidecars with visible block/offset columns, validation records whether
sampled entry addresses are referenced by supplemental tables. Those
supplemental sidecar rows are audit evidence only; the reader no longer attaches
them to `EntryDocument`. Structurally clear sidecar BLOB media tables are
exposed as package-level `ResourceRef` records with explicit `resource_bytes()`
access to the untouched BLOB payload. Remaining ambiguous schemas stay
diagnosed and counted.

`lvcore_audit corpus` is the private full-corpus audit entry point. Its JSON summary
uses the `lvcore.audit.corpus.v1` schema and reports package-family counts,
SSED body-source counts, SSED render-support counts, diagnostic counts by
severity/area/code, top blockers, sample limits, and search-hit
dereference/render totals. LVED and LVLMultiView are counted as deferred package
families, separately from deferred or unsupported SSED body sources.

Useful audit options:

- `--jobs 0`: use all available CPUs, or pass an explicit worker count for a
  bounded run;
- `--progress`: write package-level progress to stderr without contaminating
  JSON stdout;
- `--sample-entries N` and `--sample-search-hits N`: make sample limits
  explicit in the summary;
- `--max-bytes-per-scan N`: exercise deterministic partial-scan behavior and
  report `scan_truncated` diagnostics when a sampled operation hits the budget;
- `--output-dir DIR`: write `summary.json`, `targets.jsonl`,
  `failures.jsonl`, and `diagnostics.jsonl`;
- `--failures-jsonl PATH` and `--diagnostics-jsonl PATH`: write those streams
  to explicit private report paths.

Normal corpus validation output avoids entry text. Debug and private report
paths are intended for local compatibility audits only.

See `ARCHITECTURE.md` for the current document/rendering model. See
`FUTURE_RUST.md` for the intended future Rust architecture and C ABI shape this
proof of concept is preserving. See `REVIEW_CHECKLIST.md` for the scope,
friendly/debug separation, corpus-validation, and public/private-boundary checks
expected for lvcore changes.
