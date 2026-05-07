# Confidence Levels

The project distinguishes observed facts from inferred behavior. This matters
because LogoVista is a family of related package shapes, not one cleanly
versioned public format.

| Level | Meaning |
|---|---|
| Proven | Directly verified from bytes and covered by parser/tests or repeated corpus evidence. |
| Strongly inferred | Fits multiple dictionaries and has a clear structural explanation, but still lacks full renderer parity or exhaustive samples. |
| Corpus-inferred | True for currently observed products; should not be treated as universal. |
| Dictionary-specific | Known to be tied to one product, product family, or platform wrapper. |
| Unknown / opaque | Classified enough to avoid corrupting nearby data, but the semantics or dereference path is not understood. |

Recommended documentation style:

- state the evidence source;
- avoid global language for corpus-inferred behavior;
- keep raw bytes, block/offsets, and file names in reports;
- prefer neutral names for unknown controls or payloads;
- move semantic labels into dictionary profiles when they are product-specific.
