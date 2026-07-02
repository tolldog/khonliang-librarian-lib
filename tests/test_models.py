import pytest
from pydantic import ValidationError

from librarian_lib import (
    DEFAULT_SPEC,
    CatalogSpec,
    IndexRecord,
    KnowledgeSourceDescriptor,
    Link,
    validate_links,
)


def make_record(**overrides):
    base = dict(
        project="khonliang",
        source="developer",
        record_id="fr_deadbeef",
        schema_version=1,
        kind="fr",
        text="an index card",
    )
    base.update(overrides)
    return IndexRecord(**base)


class TestIndexRecord:
    def test_valid_record(self):
        rec = make_record()
        assert rec.project == "khonliang"
        assert rec.schema_version == 1
        assert rec.mentions == []
        assert rec.ref == {}

    def test_missing_project_rejected(self):
        with pytest.raises(ValidationError):
            IndexRecord(
                source="developer",
                record_id="x",
                schema_version=1,
                kind="fr",
            )

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_empty_project_rejected_loudly(self, bad):
        with pytest.raises(ValidationError, match="project"):
            make_record(project=bad)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_schema_version_ge_1(self, bad):
        with pytest.raises(ValidationError):
            make_record(schema_version=bad)

    def test_schema_version_required(self):
        with pytest.raises(ValidationError):
            IndexRecord(
                project="p", source="s", record_id="r", kind="fr"
            )

    def test_unknown_facet_keys_allowed(self):
        rec = make_record(facets={"status": "open", "custom_axis": {"deep": [1, 2]}})
        assert rec.facets["custom_axis"] == {"deep": [1, 2]}

    def test_core_facet_status_type_validated(self):
        with pytest.raises(ValidationError, match="status"):
            make_record(facets={"status": 5})

    def test_core_facet_target_type_validated(self):
        with pytest.raises(ValidationError, match="target"):
            make_record(facets={"target": ["not", "a", "string"]})

    def test_core_facet_priority_accepts_str_and_int(self):
        assert make_record(facets={"priority": "high"}).facets["priority"] == "high"
        assert make_record(facets={"priority": 2}).facets["priority"] == 2
        with pytest.raises(ValidationError, match="priority"):
            make_record(facets={"priority": 1.5})


class TestLink:
    def test_known_rels_unflagged(self):
        links = [Link(rel="fixes", target_source="developer", target_id="bug_12345678")]
        out = validate_links(links, DEFAULT_SPEC)
        assert out[0].unknown_rel is False

    def test_unknown_rel_flagged_not_rejected(self):
        links = [Link(rel="riffs_on", target_source="researcher", target_id="x")]
        out = validate_links(links, DEFAULT_SPEC)
        assert out[0].unknown_rel is True
        assert out[0].rel == "riffs_on"

    def test_validate_against_custom_spec_vocabulary(self):
        spec = CatalogSpec(rel_vocabulary=["riffs_on"])
        out = validate_links(
            [
                Link(rel="riffs_on", target_source="a", target_id="b"),
                Link(rel="fixes", target_source="a", target_id="c"),
            ],
            spec,
        )
        assert out[0].unknown_rel is False
        assert out[1].unknown_rel is True

    def test_no_spec_uses_default_vocab(self):
        out = validate_links([Link(rel="supersedes", target_source="a", target_id="b")], None)
        assert out[0].unknown_rel is False


class TestSpecAndDescriptor:
    def test_default_spec_bootstrap_shape(self):
        assert DEFAULT_SPEC.version == 0
        assert DEFAULT_SPEC.embedding_model is None
        assert DEFAULT_SPEC.embedding_dims is None
        assert "depends_on" in DEFAULT_SPEC.rel_vocabulary
        assert set(DEFAULT_SPEC.core_facets) == {"status", "target", "priority"}
        assert DEFAULT_SPEC.id_patterns  # non-empty defaults

    def test_descriptor(self):
        d = KnowledgeSourceDescriptor(
            source_id="developer",
            kind="frs",
            owner_agent="developer-primary",
            projects=["khonliang"],
            contract_version="0.1",
        )
        assert d.record_count is None
        assert d.freshness is None
