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
- parse dictionary-local `.uni` gaiji mappings;
- decode SSED text streams into model-like spans;
- classify observed SSED `0x1f` controls through a local behavior atlas;
- parse title/index rows;
- classify SSED body sources separately from package families;
- slice readable direct `HONMON.DIC` body-stream entries;
- detect dense HONMON anchor tables and avoid rendering anchor records as
  friendly dictionary bodies;
- resolve structurally understood dense-anchor SQLite sidecars such as
  `t_contents`, `HONBUN`, and dict-code-named `main` payloads;
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
PYTHONPATH=src/lvcore-experimental python3 -m lvcore body-source /path/to/_DCT_DICT --json
PYTHONPATH=src/lvcore-experimental python3 -m lvcore entries /path/to/_DCT_DICT --limit 5
PYTHONPATH=src/lvcore-experimental python3 -m lvcore search /path/to/_DCT_DICT term --search-profile forward --json
PYTHONPATH=src/lvcore-experimental python3 -m lvcore search /path/to/_DCT_DICT term --json --debug
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --search-profile native --format html
PYTHONPATH=src/lvcore-experimental python3 -m lvcore validate /path/to/_DCT_DICT --sample-search-hits 5 --json
PYTHONPATH=src/lvcore-experimental python3 -m lvcore corpus-validate /path/to/corpus --json --full --jobs 0
```

Search profiles are native reader profiles:

- `exact`: exact match against decoded row keys and target keys, with
  conservative lookup normalization;
- `forward`: prefix lookup over forward-compatible index components;
- `backward`: suffix lookup over backward-compatible index components;
- `native`: default reader lookup that combines exact, forward, and backward
  paths while deduplicating hits that target the same body/title pointer.

Friendly search JSON hides raw page/pointer internals by default. Add
`--debug` when inspecting component names, page/row positions, body/title
pointers, and raw parsed rows.

The lvcore control model uses behavior-level names derived from observed corpus
behavior. Friendly output hides private directives and raw control bytes,
literal/preformatted spans remain readable, URL and link spans become semantic
link nodes where currently understood, and layout/media controls are preserved
as diagnostics or resource hints until a richer resolver is available.

SSED body-source kinds are distinct from package families:

- `body_stream`: body pointers resolve directly into readable `HONMON.DIC`;
- `dense_anchor_table` / `dense_marker_table`: `HONMON.DIC` holds anchor
  records, not final body text;
- `dense_anchor_with_sidecar`, `renderer_sqlite_sidecar`, `honbun_sidecar`, and
  related sidecar kinds: `HONMON.DIC` anchors can be resolved through a sibling
  body database when the schema is understood;
- LVED SQLCipher and LVLMultiView are separate package families and remain
  deferred. They are not SSED body-source failures.

Friendly rendering never displays raw dense-anchor bytes. If a dense body source
cannot be resolved, lvcore returns a clean placeholder entry plus diagnostics.
Debug output may expose anchor IDs, pointers, and sidecar mapping details.

Observed dense-anchor SQLite schemas currently recognized by lvcore include
`t_contents` rows keyed by `f_DataId`, `f_contents_id`, or `f_order_id`,
`HONBUN` rows keyed by `ID` / `f_DataId`, and extensionless dict-code-named
`main` tables keyed by `ID`. These are treated as SSED sidecar body sources,
not LVED.

See `ARCHITECTURE.md` for the document/rendering model and the future Rust/C
ABI constraints this proof of concept is preserving.
