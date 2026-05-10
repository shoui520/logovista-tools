# lvcore experimental

`lvcore` is a clean Python reimplementation of the LogoVista reader/parser core.
It intentionally does not import `logovista_tools`.

Current scope:

- detect SSED / LVED SQLCipher / LVLMultiView SQLite package families;
- parse SSEDINFO catalogs;
- load plain and LogoFontCipher-encrypted SSEDDATA components;
- expand SSED chunks and read component slices;
- parse dictionary-local `.uni` gaiji mappings;
- decode SSED text streams into model-like spans;
- parse title/index rows;
- slice readable HONMON body-stream entries;
- expose a small CLI for inspection and lookup experiments.
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
PYTHONPATH=src/lvcore-experimental python3 -m lvcore entries /path/to/_DCT_DICT --limit 5
PYTHONPATH=src/lvcore-experimental python3 -m lvcore search /path/to/_DCT_DICT term
PYTHONPATH=src/lvcore-experimental python3 -m lvcore render /path/to/_DCT_DICT term --format html
PYTHONPATH=src/lvcore-experimental python3 -m lvcore validate /path/to/_DCT_DICT --json
```

See `ARCHITECTURE.md` for the document/rendering model and the future Rust/C
ABI constraints this proof of concept is preserving.
