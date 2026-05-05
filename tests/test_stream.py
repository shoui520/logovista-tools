from logovista_tools.entries import decode_tokens, normalize_fullwidth_ascii, tokens_to_text


def test_normalize_fullwidth_ascii() -> None:
    assert normalize_fullwidth_ascii("ＡＢＣ１２３　ｘｙｚ") == "ABC123 xyz"


def test_decode_jis_pair_and_line_break() -> None:
    # 2422 is JIS X 0208 "あ"; 1f0a is a LogoVista/EPWING line break.
    tokens, stats = decode_tokens(bytes.fromhex("24221f0a2424"))
    assert tokens_to_text(tokens) == "あ\nい"
    assert stats["jis_pairs"] == 2
    assert stats["controls"] == 1


def test_gaiji_placeholder_modes() -> None:
    tokens, stats = decode_tokens(bytes.fromhex("a126"), gaiji="h-placeholder")
    assert tokens_to_text(tokens) == "<hA126>"
    assert stats["gaiji"] == 1

    tokens, _ = decode_tokens(bytes.fromhex("b126"), gaiji="h-placeholder")
    assert tokens_to_text(tokens) == ""

    tokens, _ = decode_tokens(bytes.fromhex("b126"), gaiji="placeholder")
    assert tokens_to_text(tokens) == "<zB126>"


def test_gaiji_map_wins_over_placeholder() -> None:
    tokens, _ = decode_tokens(bytes.fromhex("a126"), gaiji="h-placeholder", gaiji_map={"a126": "é"})
    assert tokens_to_text(tokens) == "é"

