from librarian_lib import CatalogSpec, extract_mentions


class TestEcosystemIds:
    def test_fr_with_middle_segment(self):
        assert extract_mentions("see fr_developer_6ecd0c01 for details") == [
            "fr_developer_6ecd0c01"
        ]

    def test_fr_without_middle_segment(self):
        assert extract_mentions("fr_ff923ebf said deepseek clean") == ["fr_ff923ebf"]

    def test_multi_segment_middle(self):
        assert extract_mentions("retracted fr_prod_agents_systemd_units_9c587259") == [
            "fr_prod_agents_systemd_units_9c587259"
        ]

    def test_hyphenated_middle(self):
        assert extract_mentions("fr_khonliang-bus-lib_42555320 lands help") == [
            "fr_khonliang-bus-lib_42555320"
        ]

    def test_all_prefixes(self):
        text = (
            "bug_deadbeef then ms_developer_ef3469de then dog_a29646ac "
            "then art_0123abcd"
        )
        assert extract_mentions(text) == [
            "bug_deadbeef",
            "ms_developer_ef3469de",
            "dog_a29646ac",
            "art_0123abcd",
        ]

    def test_unknown_prefix_ignored(self):
        assert extract_mentions("zap_deadbeef is not ours") == []


class TestOtherPatterns:
    def test_corpus_hash(self):
        assert extract_mentions("corpus [8d3bcd64f6e12c25] backs this") == [
            "8d3bcd64f6e12c25"
        ]

    def test_short_hex_not_matched_as_hash(self):
        assert extract_mentions("cafef00d15 is 10 chars") == []

    def test_pr_number_forms(self):
        assert extract_mentions("merged in PR #34") == ["PR #34"]
        assert extract_mentions("merged in PR#34") == ["PR#34"]

    def test_repo_issue_form(self):
        assert extract_mentions("blocked on khonliang-bus#22 landing") == [
            "khonliang-bus#22"
        ]


class TestBehavior:
    def test_dedupe_preserves_order(self):
        text = "fr_aabbccdd then 8d3bcd64f6e12c25 then fr_aabbccdd again"
        assert extract_mentions(text) == ["fr_aabbccdd", "8d3bcd64f6e12c25"]

    def test_order_follows_text_position_across_pattern_types(self):
        text = "hash 8d3bcd64f6e12c25 before fr_aabbccdd and PR #7"
        assert extract_mentions(text) == [
            "8d3bcd64f6e12c25",
            "fr_aabbccdd",
            "PR #7",
        ]

    def test_empty_and_garbage_input_safe(self):
        assert extract_mentions("") == []
        assert extract_mentions(None) == []  # type: ignore[arg-type]
        assert extract_mentions(12345) == []  # type: ignore[arg-type]
        assert extract_mentions("\x00\xff ((( unbalanced [brackets") == []

    def test_malformed_spec_pattern_never_raises(self):
        spec = CatalogSpec(
            id_patterns={"bad": "([unclosed", "ok": r"\bfr_[0-9a-f]{8}\b"}
        )
        assert extract_mentions("fr_deadbeef plus junk", spec) == ["fr_deadbeef"]

    def test_spec_patterns_override_defaults(self):
        spec = CatalogSpec(id_patterns={"ticket": r"\bTKT-\d+\b"})
        assert extract_mentions("TKT-99 not fr_deadbeef", spec) == ["TKT-99"]
