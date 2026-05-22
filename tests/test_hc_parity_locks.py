from logovista_tools.hc_parity import CONFIRMED_RENDER_PARITY_DICT_IDS, confirmed_render_parity_lock
from logovista_tools.hcprofiles import build_hc_behavior_profile


def test_confirmed_render_parity_locks_match_reviewed_dictionary_set() -> None:
    assert CONFIRMED_RENDER_PARITY_DICT_IDS == {
        "ARCHSIC4",
        "BMANNAR",
        "DAIJIRN4",
        "Dconci87",
        "EJJE200",
        "GENKANA5",
        "GKKANAN3",
        "GKKANYOK",
        "GKKEIGO",
        "GKKOJIKT",
        "GKTISIKI",
        "HABGESPA",
        "HKBYOIN4",
        "HOUGAKU5",
        "IBIO4",
        "IBIO5",
        "IPHYCHE5",
        "JSSAURU2",
        "KCOMPEJ2",
        "KENROWA",
        "KOJIEN7",
        "KQBIZEJ",
        "KQDENTAL",
        "KQEJMED2",
        "KQNEWJE5",
        "KQSYNONM",
        "MEIKYOU",
        "MEIKYOU2",
        "NANMED19",
        "NANMED20",
        "NGFINANC",
        "PROYAL43",
        "PROYAL53",
        "SAIYOREI",
        "SPEECH",
        "TEGAMI",
        "YHOUGO4",
        "YHOUGO5",
        "YUECONO5",
    }


def test_confirmed_render_parity_lookup_preserves_corpus_package_spelling() -> None:
    lock = confirmed_render_parity_lock("gkbyoin4")
    assert lock is None

    lock = confirmed_render_parity_lock("HKBYOIN4")
    assert lock is not None
    assert lock.package_id == "_DCT_HKBYOIN4"
    assert "GKBYOIN4" in lock.notes


def test_behavior_profile_records_reference_render_parity_without_claiming_exact_hc_parity() -> None:
    profile = build_hc_behavior_profile(None, renderer_code_override="014F", dict_id="EJJE200")
    data = profile.as_dict()

    assert data["reference_render_parity_confirmed"] is True
    assert data["reference_render_parity_source"] == "v5 visual review"
    assert data["exact_hc_parity"] is False
    assert "visual_parity_unverified" not in data["named_gaps"]


def test_behavior_profile_keeps_visual_gap_for_unconfirmed_branch_subset() -> None:
    profile = build_hc_behavior_profile(None, renderer_code_override="014F", dict_id="UNCONFIRMED")
    data = profile.as_dict()

    assert data["reference_render_parity_confirmed"] is False
    assert "visual_parity_unverified" in data["named_gaps"]
