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
  `stats`, `list_since`, `list_expiring` (the generic per-record refresh
  scheduler), the embedding backlog (`pending_embeddings` /
  `store_embedding`), and `mark_stale_for_spec_bump` — the bulk "spec bump
  -> re-embed" primitive (`catalog.py`).
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

### Reacting to a `CatalogSpec` version bump

This library has no bus dependency, so it does not subscribe to the
ecosystem's catalog-spec publish event itself. When the owner agent's own
bus reactor observes that event, it should refetch the full spec and call
`mark_stale_for_spec_bump` (exposed as the `catalog_mark_stale` skill) to
bulk-flag that project's existing rows `embedding_status='pending'` under
the new spec version, so the dispatcher picks them up for re-embedding.
It takes the **full** new `CatalogSpec` (or its dict form), not just the
version number — stamping the version alone would decouple it from the
spec content it names, so `upsert()` could start writing rows tagged with
the new version while still validating against old rel vocabulary/facet
rules. It also bumps a dedicated `embedding_token` counter (not
`updated_at`) on every touched row, so an embedding worker already
holding a stale token from `pending_embeddings` can't win
`store_embedding`'s concurrency guard and write back a vector computed
under the old spec — `updated_at` itself is left untouched since it's
also `list_since`'s incremental-resync cursor, and rewriting it would
turn every spec bump into a synthetic content change for every row. It
also replaces `catalog.spec` in place (before the bulk update runs) when
the new version is newer, so a record re-saved right after the bump is
stamped and validated under the new spec instead of the stale cached one:

```python
def on_catalog_spec_published(event):
    new_spec = spec_client.get_spec(refresh=True)
    catalog.mark_stale_for_spec_bump(event["project"], new_spec)
```

### Generic per-record refresh scheduling

Any owner agent can flag one of its own `IndexRecord`s as due for
attention by stamping three optional fields at `upsert` time:

- `expires_at` — **absolute** epoch seconds (not a relative
  `ttl_seconds`). Deliberately absolute: the owner computes the instant
  once, at write time, so a later change to some global default TTL can
  never reinterpret an already-written row. `None` (the default) means
  never flagged.
- `refresh_operation` — the bus skill name on the record's own
  `owner_agent` that the librarian should call to refresh it. Opaque to
  this library — never validated against a real skill registry here;
  interpretation is entirely the librarian's and the owner's problem.
- `refresh_args` — JSON-serializable args to pass to that call.

A record may set `expires_at` while leaving `refresh_operation` unset —
that is a valid "flag only, no auto-refresh" case (e.g. a human-curation
nudge with no automated callback), not an error, and it is never dropped
from `list_expiring` / `catalog_list_expiring` results.

```python
catalog.upsert(IndexRecord(
    project="khonliang",
    source="developer",
    record_id=fr.id,
    schema_version=1,
    kind="fr",
    text=f"{fr.title}\n{fr.description}",
    ref={"skill": "get_fr", "args": {"fr_id": fr.id}},
    expires_at=time.time() + 86400 * 30,      # re-check in 30 days
    refresh_operation="get_fr",                # re-fetch via this skill
    refresh_args={"fr_id": fr.id},
))
```

The librarian polls each owner's `catalog_list_expiring(project, before,
limit=100)` skill (bus wrapper for `SelfCatalog.list_expiring`) to find
rows whose `expires_at <= before`, ordered most-overdue-first, and drives
whatever refresh policy it wants (calling `refresh_operation` on the
owner, or just surfacing the flag). This library never interprets what
"refresh" means, never triggers anything on a schedule, and keeps no
sweep/cooldown bookkeeping — that lives in the librarian's own registry.
`catalog_list_expiring`'s name, args, and `{"count", "rows"}` response
shape are a fixed contract with the librarian agent's own expiry-sweep
skill; do not reshape it in a way that breaks that caller.

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
