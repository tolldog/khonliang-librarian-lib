import base64
import struct

import pytest

import librarian_lib
from librarian_lib import CatalogSkills, IndexRecord, SelfCatalog


def make_catalog(tmp_path):
    catalog = SelfCatalog(
        db_path=tmp_path / "skills.db", source="store", owner_agent="store-primary"
    )
    catalog.upsert(
        IndexRecord(
            project="khonliang",
            source="store",
            record_id="art_00112233",
            schema_version=1,
            kind="artifact",
            facets={"status": "ready"},
            text="benchmark scoresheet artifact",
            ref={"skill": "artifact_get", "args": {"artifact_id": "art_00112233"}},
            updated_at=100.0,
        )
    )
    return catalog


class TestCatalogSkills:
    def test_catalog_query(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        res = skills.catalog_query("khonliang", filters={"kind": "artifact"})
        assert res["count"] == 1

    def test_catalog_search(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        res = skills.catalog_search("khonliang", "scoresheet")
        assert res["mode"] in ("vector", "text_fallback")
        assert res["count"] == 1

    def test_catalog_stats(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        assert skills.catalog_stats("khonliang")["total"] == 1

    def test_list_since(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        assert skills.list_since("khonliang", 50.0)["count"] == 1
        assert skills.list_since("khonliang", 150.0)["count"] == 0

    def test_catalog_search_passes_query_vector_bytes(self, tmp_path):
        pytest.importorskip("sqlite_vec")
        catalog = make_catalog(tmp_path)
        vec = struct.pack("3f", 1.0, 0.0, 0.0)
        catalog.store_embedding("khonliang", "art_00112233", vec, model="test-embed")
        skills = CatalogSkills(catalog)
        res = skills.catalog_search("khonliang", "scoresheet", query_vector=vec)
        assert res["mode"] == "vector"
        assert res["count"] == 1
        assert "distance" in res["rows"][0]

    def test_catalog_search_accepts_base64_query_vector(self, tmp_path):
        pytest.importorskip("sqlite_vec")
        catalog = make_catalog(tmp_path)
        vec = struct.pack("3f", 1.0, 0.0, 0.0)
        catalog.store_embedding("khonliang", "art_00112233", vec, model="test-embed")
        skills = CatalogSkills(catalog)
        res = skills.catalog_search(
            "khonliang", "scoresheet", query_vector=base64.b64encode(vec).decode()
        )
        assert res["mode"] == "vector"

    def test_catalog_search_rejects_bad_base64_loudly(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        with pytest.raises(ValueError, match="base64"):
            skills.catalog_search("khonliang", "scoresheet", query_vector="not/valid!!")

    def test_skill_methods_have_docstrings(self, tmp_path):
        skills = CatalogSkills(make_catalog(tmp_path))
        for name in ("catalog_query", "catalog_search", "catalog_stats", "list_since"):
            assert getattr(skills, name).__doc__


class TestPublicSurface:
    def test_version_and_contract(self):
        assert librarian_lib.__version__ == "0.1.0"
        assert librarian_lib.CONTRACT_VERSION == "0.1"

    def test_all_exports_resolve(self):
        for name in librarian_lib.__all__:
            assert hasattr(librarian_lib, name), name
