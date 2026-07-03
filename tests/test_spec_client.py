from librarian_lib import DEFAULT_SPEC, CatalogSpec, SpecClient


class TestSpecClient:
    def test_no_fetch_fn_degrades_to_default(self):
        client = SpecClient(fetch_fn=None)
        spec = client.get_spec()
        assert spec == DEFAULT_SPEC
        assert client.degraded is True

    def test_failing_fetch_fn_degrades_to_default(self):
        def boom():
            raise ConnectionError("bus unreachable")

        client = SpecClient(fetch_fn=boom)
        spec = client.get_spec()
        assert spec == DEFAULT_SPEC
        assert client.degraded is True

    def test_invalid_payload_degrades(self):
        client = SpecClient(fetch_fn=lambda: {"version": "not-an-int-at-all", "core_facets": 7})
        assert client.get_spec() == DEFAULT_SPEC
        assert client.degraded is True

    def test_successful_fetch_from_dict(self):
        payload = {
            "version": 3,
            "rel_vocabulary": ["depends_on", "riffs_on"],
            "embedding_model": "nomic-embed-text",
            "embedding_dims": 768,
        }
        client = SpecClient(fetch_fn=lambda: payload)
        spec = client.get_spec()
        assert client.degraded is False
        assert spec.version == 3
        assert spec.embedding_dims == 768
        assert "riffs_on" in spec.rel_vocabulary

    def test_successful_fetch_of_spec_instance(self):
        client = SpecClient(fetch_fn=lambda: CatalogSpec(version=9))
        assert client.get_spec().version == 9
        assert client.degraded is False

    def test_caching_and_refresh(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"version": len(calls)}

        client = SpecClient(fetch_fn=fetch)
        assert client.get_spec().version == 1
        assert client.get_spec().version == 1  # cached
        assert client.get_spec(refresh=True).version == 2

    def test_recovery_after_degraded(self):
        state = {"up": False}

        def fetch():
            if not state["up"]:
                raise ConnectionError("down")
            return {"version": 5}

        client = SpecClient(fetch_fn=fetch)
        assert client.get_spec() == DEFAULT_SPEC
        assert client.degraded is True
        state["up"] = True
        assert client.get_spec(refresh=True).version == 5
        assert client.degraded is False


def test_fetch_fn_cleared_keeps_last_known_good():
    client = SpecClient(fetch_fn=lambda: CatalogSpec(version=9))
    assert client.get_spec().version == 9
    client.fetch_fn = None
    spec = client.get_spec(refresh=True)
    assert spec.version == 9  # last known good kept
    assert client.degraded is True
