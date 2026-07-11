import gc
import sqlite3
import warnings

import pytest

from librarian_lib import CatalogSpec, IndexRecord, Link, SelfCatalog


@pytest.fixture
def catalog(tmp_path):
    return SelfCatalog(
        db_path=tmp_path / "self_catalog.db",
        source="developer",
        owner_agent="developer-primary",
    )


def make_record(**overrides):
    base = dict(
        project="khonliang",
        source="developer",
        record_id="fr_deadbeef",
        schema_version=1,
        kind="fr",
        facets={"status": "open", "priority": "high"},
        text="A feature request about catalog federation.",
        ref={"skill": "get_fr", "args": {"fr_id": "fr_deadbeef"}},
    )
    base.update(overrides)
    return IndexRecord(**base)


class TestRoundtrip:
    def test_upsert_get_roundtrip(self, catalog):
        rec = make_record()
        catalog.upsert(rec)
        got = catalog.get("khonliang", "fr_deadbeef")
        assert got is not None
        assert got.project == "khonliang"
        assert got.kind == "fr"
        assert got.facets == {"status": "open", "priority": "high"}
        assert got.ref == {"skill": "get_fr", "args": {"fr_id": "fr_deadbeef"}}
        assert got.updated_at > 0  # stamped when absent

    def test_upsert_replaces(self, catalog):
        catalog.upsert(make_record())
        catalog.upsert(make_record(facets={"status": "closed"}, text="updated text"))
        got = catalog.get("khonliang", "fr_deadbeef")
        assert got.facets == {"status": "closed"}
        assert got.text == "updated text"
        assert catalog.stats("khonliang")["total"] == 1

    def test_delete(self, catalog):
        catalog.upsert(make_record())
        assert catalog.delete("khonliang", "fr_deadbeef") is True
        assert catalog.get("khonliang", "fr_deadbeef") is None
        assert catalog.delete("khonliang", "fr_deadbeef") is False

    def test_get_missing_returns_none(self, catalog):
        assert catalog.get("khonliang", "nope") is None

    def test_source_mismatch_rejected(self, catalog):
        with pytest.raises(ValueError, match="source"):
            catalog.upsert(make_record(source="researcher"))


class TestUpsertEnrichment:
    def test_auto_mentions_from_text(self, catalog):
        result = catalog.upsert(
            make_record(text="Fixes bug_a29646ac, follows fr_developer_6ecd0c01 and PR #34.")
        )
        assert result["mentions"] == ["bug_a29646ac", "fr_developer_6ecd0c01", "PR #34"]
        got = catalog.get("khonliang", "fr_deadbeef")
        assert got.mentions == ["bug_a29646ac", "fr_developer_6ecd0c01", "PR #34"]

    def test_explicit_mentions_not_overwritten(self, catalog):
        catalog.upsert(
            make_record(text="mentions fr_aabbccdd", mentions=["custom_mention"])
        )
        assert catalog.get("khonliang", "fr_deadbeef").mentions == ["custom_mention"]

    def test_unknown_link_rel_flagged(self, catalog):
        result = catalog.upsert(
            make_record(
                links=[
                    Link(rel="fixes", target_source="developer", target_id="bug_11112222"),
                    Link(rel="riffs_on", target_source="researcher", target_id="x"),
                ]
            )
        )
        assert result["unknown_rels"] == ["riffs_on"]
        got = catalog.get("khonliang", "fr_deadbeef")
        assert [link.unknown_rel for link in got.links] == [False, True]

    def test_upsert_resets_embedding_status(self, catalog):
        catalog.upsert(make_record())
        assert catalog.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")
        assert catalog.stats("khonliang")["pending_embeddings"] == 0
        catalog.upsert(make_record(text="changed — needs re-embedding"))
        assert catalog.stats("khonliang")["pending_embeddings"] == 1


def _embedding_status(catalog, project, record_id):
    rows = catalog.query(project, filters={"record_id": record_id})["rows"]
    return rows[0]["embedding_status"]


def make_spec(version, **overrides):
    return CatalogSpec(version=version, **overrides)


class TestMarkStaleForSpecBump:
    def test_marks_ready_rows_pending_and_stamps_version(self, catalog):
        catalog.upsert(make_record())
        catalog.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")
        assert catalog.stats("khonliang")["pending_embeddings"] == 0

        updated = catalog.mark_stale_for_spec_bump("khonliang", make_spec(2))

        assert updated == 1
        stats = catalog.stats("khonliang")
        assert stats["pending_embeddings"] == 1
        assert stats["by_spec_version"] == {"2": 1}
        assert _embedding_status(catalog, "khonliang", "fr_deadbeef") == "pending"

    def test_accepts_spec_as_plain_dict(self, catalog):
        catalog.upsert(make_record())
        updated = catalog.mark_stale_for_spec_bump("khonliang", {"version": 2})
        assert updated == 1
        assert catalog.spec.version == 2

    def test_noop_when_already_on_spec_version(self, catalog):
        catalog.upsert(make_record())
        catalog.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")
        assert catalog.mark_stale_for_spec_bump("khonliang", make_spec(0)) == 0
        assert catalog.stats("khonliang")["pending_embeddings"] == 0

    def test_does_not_downgrade_rows_already_on_a_newer_version(self, catalog):
        catalog.upsert(make_record())
        catalog.mark_stale_for_spec_bump("khonliang", make_spec(5))
        catalog.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")

        updated = catalog.mark_stale_for_spec_bump("khonliang", make_spec(2))

        assert updated == 0
        assert catalog.stats("khonliang")["pending_embeddings"] == 0
        assert catalog.stats("khonliang")["by_spec_version"] == {"5": 1}

    def test_advances_cached_spec_so_upsert_does_not_regress_version(self, catalog):
        assert catalog.spec.version == 0
        catalog.upsert(make_record())

        catalog.mark_stale_for_spec_bump("khonliang", make_spec(3))

        assert catalog.spec.version == 3
        catalog.upsert(make_record(text="re-saved after the bump"))
        assert catalog.stats("khonliang")["by_spec_version"] == {"3": 1}

    def test_new_spec_content_swaps_in_atomically_with_the_version(self, catalog):
        new_spec = make_spec(2, rel_vocabulary=["only_this_rel"])

        catalog.mark_stale_for_spec_bump("khonliang", new_spec)

        assert catalog.spec.version == 2
        assert catalog.spec.rel_vocabulary == ["only_this_rel"]

    def test_does_not_replace_cached_spec_when_version_is_not_newer(self, catalog):
        original_spec = catalog.spec
        catalog.mark_stale_for_spec_bump("khonliang", make_spec(0, rel_vocabulary=["other"]))
        assert catalog.spec is original_spec

    def test_rejects_in_flight_embedding_computed_before_the_bump(self, catalog):
        catalog.upsert(make_record())
        [pending] = catalog.pending_embeddings()
        stale_updated_at = pending["updated_at"]

        catalog.mark_stale_for_spec_bump("khonliang", make_spec(2))

        wrote = catalog.store_embedding(
            "khonliang",
            "fr_deadbeef",
            b"\x00" * 8,
            "old-model",
            expected_updated_at=stale_updated_at,
        )

        assert wrote is False
        assert catalog.stats("khonliang")["pending_embeddings"] == 1

    def test_project_scoped(self, catalog):
        catalog.upsert(make_record(project="khonliang", record_id="a"))
        catalog.upsert(make_record(project="other", record_id="b"))
        catalog.store_embedding("khonliang", "a", b"\x00" * 8, "m")
        catalog.store_embedding("other", "b", b"\x00" * 8, "m")

        updated = catalog.mark_stale_for_spec_bump("khonliang", make_spec(2))

        assert updated == 1
        assert _embedding_status(catalog, "khonliang", "a") == "pending"
        assert _embedding_status(catalog, "other", "b") == "ready"

    def test_source_scoped(self, tmp_path):
        dev = SelfCatalog(
            db_path=tmp_path / "shared.db", source="developer", owner_agent="developer-primary"
        )
        researcher = SelfCatalog(
            db_path=tmp_path / "shared.db", source="researcher", owner_agent="researcher-primary"
        )
        dev.upsert(make_record())
        researcher.upsert(make_record(source="researcher"))
        dev.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")
        researcher.store_embedding("khonliang", "fr_deadbeef", b"\x00" * 8, "m")

        updated = dev.mark_stale_for_spec_bump("khonliang", make_spec(2))

        assert updated == 1
        assert _embedding_status(dev, "khonliang", "fr_deadbeef") == "pending"
        assert _embedding_status(researcher, "khonliang", "fr_deadbeef") == "ready"


class TestProjectIsolation:
    def test_default_scope_isolates_projects(self, catalog):
        catalog.upsert(make_record(project="proj_a", record_id="r1"))
        catalog.upsert(make_record(project="proj_b", record_id="r2"))
        res = catalog.query("proj_a")
        assert res["count"] == 1
        assert res["rows"][0]["record_id"] == "r1"
        # a project-B record never leaks into a project-A read
        assert all(r["project"] == "proj_a" for r in res["rows"])

    def test_get_and_delete_are_project_scoped(self, catalog):
        catalog.upsert(make_record(project="proj_a", record_id="r1"))
        assert catalog.get("proj_b", "r1") is None
        assert catalog.delete("proj_b", "r1") is False
        assert catalog.get("proj_a", "r1") is not None

    def test_all_projects_opt_in(self, catalog):
        catalog.upsert(make_record(project="proj_a", record_id="r1"))
        catalog.upsert(make_record(project="proj_b", record_id="r2"))
        res = catalog.query("proj_a", scope="all_projects")
        assert res["count"] == 2

    def test_bad_scope_rejected(self, catalog):
        with pytest.raises(ValueError, match="scope"):
            catalog.query("proj_a", scope="everything")


class TestQuery:
    @pytest.fixture(autouse=True)
    def seed(self, catalog):
        catalog.upsert(
            make_record(record_id="fr_1", kind="fr",
                        facets={"status": "open", "priority": "high"}, updated_at=100.0)
        )
        catalog.upsert(
            make_record(record_id="fr_2", kind="fr",
                        facets={"status": "closed"}, updated_at=200.0)
        )
        catalog.upsert(
            make_record(record_id="bug_1", kind="bug",
                        facets={"status": "open", "custom": "x"}, updated_at=300.0)
        )

    def test_filter_by_kind(self, catalog):
        res = catalog.query("khonliang", filters={"kind": "bug"})
        assert res["count"] == 1
        assert res["rows"][0]["record_id"] == "bug_1"

    def test_filter_by_facet(self, catalog):
        res = catalog.query("khonliang", filters={"status": "open"})
        assert {r["record_id"] for r in res["rows"]} == {"fr_1", "bug_1"}

    def test_filter_by_extension_facet(self, catalog):
        res = catalog.query("khonliang", filters={"custom": "x"})
        assert res["count"] == 1

    def test_date_filters(self, catalog):
        res = catalog.query(
            "khonliang", filters={"updated_after": 150.0, "updated_before": 250.0}
        )
        assert [r["record_id"] for r in res["rows"]] == ["fr_2"]

    def test_jmespath_filter(self, catalog):
        res = catalog.query(
            "khonliang", jmespath_expr="[?facets.status=='open' && kind=='fr']"
        )
        assert res["count"] == 1
        assert res["rows"][0]["record_id"] == "fr_1"

    def test_jmespath_no_match_returns_empty(self, catalog):
        res = catalog.query("khonliang", jmespath_expr="[?kind=='nope']")
        assert res == {"count": 0, "rows": []}

    def test_projection(self, catalog):
        res = catalog.query(
            "khonliang", filters={"kind": "bug"}, fields=["record_id", "kind"]
        )
        assert res["rows"] == [{"record_id": "bug_1", "kind": "bug"}]

    def test_limit(self, catalog):
        res = catalog.query("khonliang", limit=2)
        assert res["count"] == 2
        # ordered newest-first
        assert [r["record_id"] for r in res["rows"]] == ["bug_1", "fr_2"]


class TestSearchAndSync:
    def test_text_fallback_mode_without_vector(self, catalog):
        catalog.upsert(make_record(record_id="r1", text="catalog federation design"))
        catalog.upsert(make_record(record_id="r2", text="unrelated inference tuning"))
        res = catalog.search("khonliang", "Catalog Federation")
        assert res["mode"] == "text_fallback"
        assert res["count"] == 1
        assert res["rows"][0]["record_id"] == "r1"

    def test_search_scoped_to_project(self, catalog):
        catalog.upsert(make_record(project="proj_a", record_id="r1", text="same words"))
        catalog.upsert(make_record(project="proj_b", record_id="r2", text="same words"))
        res = catalog.search("proj_a", "same words")
        assert [r["project"] for r in res["rows"]] == ["proj_a"]

    def test_list_since(self, catalog):
        catalog.upsert(make_record(record_id="r1", updated_at=100.0))
        catalog.upsert(make_record(record_id="r2", updated_at=200.0))
        catalog.upsert(make_record(record_id="r3", updated_at=300.0))
        res = catalog.list_since("khonliang", 150.0)
        assert [r["record_id"] for r in res["rows"]] == ["r2", "r3"]  # oldest first

    def test_stats(self, catalog):
        catalog.upsert(make_record(record_id="fr_1", kind="fr", facets={"status": "open"}))
        catalog.upsert(
            make_record(record_id="dog_1", kind="dogfood",
                        facets={"status": "open", "needs_curation": True})
        )
        stats = catalog.stats("khonliang")
        assert stats["total"] == 2
        assert stats["by_kind"] == {"fr": 1, "dogfood": 1}
        assert stats["by_status"] == {"open": 2}
        assert stats["pending_embeddings"] == 2
        assert stats["needs_curation"] == 1
        assert stats["by_spec_version"] == {"0": 2}

    def test_stats_all_projects(self, catalog):
        catalog.upsert(make_record(project="proj_a", record_id="r1"))
        catalog.upsert(make_record(project="proj_b", record_id="r2"))
        assert catalog.stats()["total"] == 2
        assert catalog.stats("proj_a")["total"] == 1


class TestEmbeddingBacklog:
    def test_pending_then_store(self, catalog):
        catalog.upsert(make_record(record_id="r1", text="embed me"))
        pending = catalog.pending_embeddings()
        assert len(pending) == 1
        assert pending[0]["project"] == "khonliang"
        assert pending[0]["record_id"] == "r1"
        assert pending[0]["text"] == "embed me"
        assert "updated_at" in pending[0]
        assert catalog.store_embedding("khonliang", "r1", b"\x01\x02", "test-model") is True
        assert catalog.pending_embeddings() == []
        assert catalog.stats("khonliang")["pending_embeddings"] == 0

    def test_store_embedding_missing_row(self, catalog):
        assert catalog.store_embedding("khonliang", "ghost", b"\x00", "m") is False


class TestConnectionHygiene:
    def test_no_resource_warning_across_operations(self, tmp_path):
        # ResourceWarning is escalated to an error by pytest.ini; this test
        # additionally forces collection so an unclosed connection surfaces
        # here rather than at interpreter shutdown.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            catalog = SelfCatalog(
                db_path=tmp_path / "hygiene.db",
                source="developer",
                owner_agent="developer-primary",
            )
            catalog.upsert(make_record())
            catalog.get("khonliang", "fr_deadbeef")
            catalog.query("khonliang", filters={"kind": "fr"})
            catalog.search("khonliang", "catalog")
            catalog.stats("khonliang")
            catalog.list_since("khonliang", 0.0)
            catalog.pending_embeddings()
            catalog.store_embedding("khonliang", "fr_deadbeef", b"\x00", "m")
            catalog.delete("khonliang", "fr_deadbeef")
            del catalog
            gc.collect()

    def test_connection_closed_even_on_error(self, tmp_path):
        catalog = SelfCatalog(
            db_path=tmp_path / "err.db", source="developer", owner_agent="dev"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with pytest.raises(sqlite3.Error):
                with catalog._connect() as conn:
                    conn.execute("SELECT * FROM no_such_table")
            gc.collect()


class TestMemoryPathRejection:
    def test_memory_path_rejected_up_front(self, tmp_path):
        import pytest

        from librarian_lib import SelfCatalog

        with pytest.raises(ValueError, match="in-memory"):
            SelfCatalog(db_path=":memory:", source="s", owner_agent="a")

    def test_mode_memory_substring_path_is_a_literal_file(self, tmp_path):
        # Without uri=True, sqlite treats "...mode=memory..." strings as
        # ordinary filenames — they must NOT be rejected (round-6 finding:
        # the earlier substring guard over-rejected legitimate paths).
        from librarian_lib import SelfCatalog

        cat = SelfCatalog(
            db_path=tmp_path / "odd mode=memory name.db",
            source="s",
            owner_agent="a",
        )
        assert cat.stats()["total"] == 0  # persists like any file db


class TestReviewRound2Fixes:
    def test_facet_key_with_quote_rejected(self, tmp_path):
        import pytest

        from librarian_lib import SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "q.db", source="s", owner_agent="a")
        with pytest.raises(ValueError, match="double-quote"):
            catalog.query("khonliang", filters={'bad"key': 1})

    def test_store_embedding_guard_skips_stale_row(self, tmp_path):
        from librarian_lib import IndexRecord, SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "g.db", source="s", owner_agent="a")
        rec = IndexRecord(
            project="khonliang", source="s", record_id="r1", schema_version=1,
            kind="fr", text="v1 text", updated_at=100.0,
        )
        catalog.upsert(rec)
        pending = catalog.pending_embeddings()
        assert pending[0]["updated_at"] == 100.0
        # concurrent upsert bumps updated_at before the worker writes back
        catalog.upsert(rec.model_copy(update={"text": "v2 text", "updated_at": 200.0}))
        ok = catalog.store_embedding(
            "khonliang", "r1", b"\x00\x00\x80?", model="m",
            expected_updated_at=pending[0]["updated_at"],
        )
        assert ok is False
        assert catalog.pending_embeddings()[0]["record_id"] == "r1"  # still pending

    def test_spec_client_keeps_last_known_good_on_refresh_failure(self):
        from librarian_lib import CatalogSpec
        from librarian_lib.spec_client import SpecClient

        good = CatalogSpec(version=7)
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            if calls["n"] == 1:
                return good
            raise RuntimeError("librarian down")

        client = SpecClient(fetch_fn=fetch)
        assert client.get_spec().version == 7
        spec = client.get_spec(refresh=True)
        assert spec.version == 7  # last known good kept, not DEFAULT_SPEC
        assert client.degraded is True


class TestReviewRound3Fixes:
    def test_empty_query_text_matches_nothing(self, tmp_path):
        from librarian_lib import IndexRecord, SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "e.db", source="s", owner_agent="a")
        catalog.upsert(IndexRecord(
            project="khonliang", source="s", record_id="r1", schema_version=1,
            kind="fr", text="some text", updated_at=1.0,
        ))
        for q in ("", "   ", None):
            res = catalog.search("khonliang", q)
            assert res["count"] == 0 and res["rows"] == []

    def test_non_json_serializable_facets_fail_fast(self, tmp_path):
        import pytest

        from librarian_lib import IndexRecord, SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "j.db", source="s", owner_agent="a")
        rec = IndexRecord(
            project="khonliang", source="s", record_id="r1", schema_version=1,
            kind="fr", text="t", updated_at=1.0, facets={"bad": {1, 2}},
        )
        with pytest.raises(ValueError, match="JSON-serializable"):
            catalog.upsert(rec)


class TestReviewRound5Fixes:
    def test_nan_facet_fails_fast(self, tmp_path):
        import pytest

        from librarian_lib import IndexRecord, SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "n.db", source="s", owner_agent="a")
        rec = IndexRecord(
            project="khonliang", source="s", record_id="r1", schema_version=1,
            kind="fr", text="t", updated_at=1.0, facets={"score": float("nan")},
        )
        with pytest.raises(ValueError, match="JSON-serializable"):
            catalog.upsert(rec)


class TestEmbeddedCountFailureDegrades:
    def test_embedded_count_failure_degrades_to_text_fallback(self, tmp_path, monkeypatch):
        pytest.importorskip("sqlite_vec")
        from librarian_lib import IndexRecord, SelfCatalog

        catalog = SelfCatalog(db_path=tmp_path / "e8.db", source="s", owner_agent="a")
        catalog.upsert(IndexRecord(
            project="khonliang", source="s", record_id="r1", schema_version=1,
            kind="fr", text="findable text", updated_at=1.0,
        ))

        def boom(self, project):
            raise RuntimeError("count query failed")

        monkeypatch.setattr(SelfCatalog, "_embedded_count", boom)
        res = catalog.search("khonliang", "findable", query_vector=b"\x00" * 12)
        assert res["mode"] == "text_fallback"
        assert res["count"] == 1
        assert "vector_error" in res
