# khonliang-librarian-lib

Knowledge-owner contract for the khonliang librarian agent.

Agents that own data (developer, store, researcher) embed a `SelfCatalog` —
a sqlite sidecar of index cards — and call `upsert`/`delete` inside their own
write paths. The central librarian agent federates search and query across
these per-agent catalogs. This library is what an owner agent imports; the
librarian-side federation, classification, and taxonomy live in the librarian
agent, not here.

## Design Rules

- **Records are project-scoped.** Every `IndexRecord` carries a mandatory,
  non-empty `project` field. Multi-project isolation is load-bearing: queries
  are project-scoped by default and cross-project reads require an explicit
  `scope="all_projects"` opt-in.
- **`schema_version` is mandatory** (integer, `>= 1`) on every record.
- **Index cards, never payloads.** The catalog stores embeddable text plus
  facets and links; the `ref` field is an owner callback pointer
  (`{"skill": ..., "args": ...}`) that the librarian uses to fetch the full
  payload from the owner. Do not put full documents in `text`.
- **Classification / taxonomy is NOT in this library.** Owners never
  self-shelve; shelving is a librarian-side concern.
- **Embedding is stub-then-swap.** The catalog tracks
  `embedding_status='pending'` rows and accepts vectors via
  `store_embedding`; actual embedding generation is the caller's / the
  dispatcher's job later.

## What Belongs Here

- `IndexRecord`, `Link`, `KnowledgeSourceDescriptor`, `CatalogSpec` — the
  pydantic contract models (`models.py`).
- `extract_mentions` — deterministic regex extraction of ecosystem ids
  (`fr_*/bug_*/ms_*/dog_*/art_*` ids, 16-char corpus hashes, `PR #N` /
  `repo#N`) from record text (`mentions.py`).
- `SelfCatalog` — the sqlite sidecar: upsert/delete/get, structured
  `query` (facet filters + optional jmespath + projection), `search`
  (sqlite-vec vector search when available, text LIKE fallback otherwise),
  `stats`, `list_since`, and the embedding backlog
  (`pending_embeddings` / `store_embedding`) (`catalog.py`).
- `SpecClient` — fetches the ecosystem `CatalogSpec` via an injected
  callable; degrades to `DEFAULT_SPEC` when the fetch is absent or fails
  (`spec_client.py`).
- `CatalogSkills` — plain methods shaped for registration as bus skills
  (`skills.py`).

## What Does Not Belong Here

- The librarian agent itself: federation, classification, taxonomy,
  neighborhood building.
- Bus registration or transport. There is no dependency on
  `khonliang-bus-lib`; wiring is the owner agent's job (see below).
- Embedding model invocation. This library stores vectors; it never
  generates them.

## Wiring Example (owner agent)

```python
from librarian_lib import (
    CatalogSkills,
    IndexRecord,
    SelfCatalog,
    SpecClient,
)

# 1. Fetch the ecosystem spec via the bus (injected — no bus import here).
spec_client = SpecClient(fetch_fn=lambda: bus.request("librarian", "get_catalog_spec"))
spec = spec_client.get_spec()          # DEFAULT_SPEC + .degraded=True on failure

# 2. Open the sidecar next to the agent's own database.
catalog = SelfCatalog(
    db_path="data/self_catalog.db",
    source="developer",
    owner_agent="developer-primary",
    spec=spec,
)

# 3. Call upsert/delete inside your own write paths.
def save_fr(fr):
    ...  # the agent's real write
    catalog.upsert(IndexRecord(
        project=fr.project,
        source="developer",
        record_id=fr.id,
        schema_version=1,
        kind="fr",
        facets={"status": fr.status, "priority": fr.priority},
        text=f"{fr.title}\n{fr.description}",
        ref={"skill": "get_fr", "args": {"fr_id": fr.id}},
    ))

# 4. Register the catalog surface as bus skills (using khonliang-bus-lib
#    in the agent repo — this library stays transport-free).
skills = CatalogSkills(catalog)
# e.g. Skill(name="catalog_query", ...) with handler skills.catalog_query
```

The librarian then calls `catalog_query` / `catalog_search` / `list_since`
on each owner to federate, and follows `ref` back to the owner for full
payloads.

## Optional vector search

`pip install -e ".[vec]"` pulls `sqlite-vec`. When it is importable, a query
vector is supplied, and embedded rows exist, `SelfCatalog.search` runs cosine
vector search; otherwise it falls back to a text LIKE scan. The response
reports which mode ran: `{"mode": "vector" | "text_fallback", ...}`.

## Running tests

```bash
pip install -e ".[test]"
pytest
```
