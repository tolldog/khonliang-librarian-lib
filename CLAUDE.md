# khonliang-librarian-lib Agent Notes

This repository is the shared importable knowledge-owner contract: how owner
agents (developer, store, researcher) expose index cards to the central
librarian. Keep it small, transport-free, and free of librarian-side policy.

When working here:

- Records are project-scoped; `project` is mandatory and query isolation by
  project is load-bearing. Never weaken the default project scoping.
- `schema_version >= 1` is mandatory on every record.
- The catalog stores index cards, never full payloads — `ref` points back to
  the owner.
- Classification / taxonomy is librarian-side only. Owners never self-shelve;
  do not add shelving logic here.
- No hard dependency on khonliang-bus-lib or any transport. `SpecClient`
  takes an injected `fetch_fn`; `CatalogSkills` is a plain class the owner
  agent wires to bus skills itself.
- `sqlite-vec` stays an optional extra (`.[vec]`) behind a try/except import.
  Embedding generation is out of scope (stub-then-swap): this library only
  tracks `pending` rows and stores vectors handed to it.
- SQLite connections are per-operation and context-managed — always closed.
  The test suite escalates `ResourceWarning` to an error; keep it that way.

## Validation

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall librarian_lib
```
