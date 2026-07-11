"""SelfCatalog — the sqlite sidecar an owner agent embeds.

Owners call :meth:`SelfCatalog.upsert` / :meth:`SelfCatalog.delete` inside
their own write paths; the librarian federates reads via ``query`` /
``search`` / ``list_since``. The catalog stores index cards only — never
full payloads.

Connection hygiene: every operation opens a connection via the
``_connect()`` context manager and always closes it (SQLite connection
leaks were a recent fleet-wide bug class; the test suite escalates
``ResourceWarning`` to an error to keep this honest).

Vector search is optional: ``sqlite-vec`` is imported behind a try/except
and used only when a query vector is supplied and embedded rows exist.
Embedding *generation* is out of scope (stub-then-swap) — this class only
tracks ``embedding_status='pending'`` rows and stores vectors handed to
:meth:`store_embedding`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import jmespath

from .mentions import extract_mentions
from .models import DEFAULT_SPEC, CatalogSpec, IndexRecord, Link, validate_links

try:  # optional dependency — install via the 'vec' extra
    import sqlite_vec  # type: ignore

    _HAS_SQLITE_VEC = True
except ImportError:  # pragma: no cover - depends on environment
    sqlite_vec = None  # type: ignore
    _HAS_SQLITE_VEC = False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    project          TEXT NOT NULL,
    source           TEXT NOT NULL,
    record_id        TEXT NOT NULL,
    schema_version   INTEGER NOT NULL,
    kind             TEXT NOT NULL,
    facets_json      TEXT NOT NULL DEFAULT '{}',
    text             TEXT NOT NULL DEFAULT '',
    links_json       TEXT NOT NULL DEFAULT '[]',
    mentions_json    TEXT NOT NULL DEFAULT '[]',
    ref_json         TEXT NOT NULL DEFAULT '{}',
    updated_at       REAL NOT NULL,
    spec_version     INTEGER NOT NULL,
    embedding_status TEXT NOT NULL DEFAULT 'pending',
    embedding        BLOB,
    embedding_model  TEXT,
    PRIMARY KEY (project, source, record_id)
);
CREATE INDEX IF NOT EXISTS idx_records_project_kind ON records(project, kind);
CREATE INDEX IF NOT EXISTS idx_records_project_updated ON records(project, updated_at);
"""

#: Filter keys that map to real columns rather than facets.
_COLUMN_FILTERS = {"kind", "record_id", "schema_version", "embedding_status"}

_ROW_FIELDS = [
    "project",
    "source",
    "record_id",
    "schema_version",
    "kind",
    "facets",
    "text",
    "links",
    "mentions",
    "ref",
    "updated_at",
    "spec_version",
    "embedding_status",
]


class SelfCatalog:
    """Per-owner sqlite catalog of project-scoped index cards.

    Args:
        db_path: sqlite file path (parent dirs created; ``":memory:"`` is
            not supported because connections are per-operation).
        source: this owner's source id — stamped on every record.
        owner_agent: bus agent id of the owner (descriptor metadata).
        spec: active :class:`CatalogSpec`; defaults to ``DEFAULT_SPEC``.
    """

    def __init__(
        self,
        db_path: str | Path,
        source: str,
        owner_agent: str,
        spec: Optional[CatalogSpec] = None,
    ) -> None:
        self.db_path = str(db_path)
        # Only the literal :memory: path is in-memory here: connections are
        # opened without uri=True, so "file:...mode=memory" strings are
        # treated by sqlite as ordinary filenames, not URIs.
        if self.db_path == ":memory:":
            raise ValueError(
                "in-memory sqlite is unsupported: connections are per-operation, "
                "so each call would see a fresh empty database — use a file path "
                "(e.g. a tmp_path in tests)"
            )
        self.source = source
        self.owner_agent = owner_agent
        self.spec = spec or DEFAULT_SPEC
        parent = Path(self.db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ------------------------------------------------------------------
    # connection hygiene
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection that is always closed, even on error."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # write path (called inside the owner's own write paths)
    # ------------------------------------------------------------------

    def upsert(self, record: IndexRecord) -> dict[str, Any]:
        """Insert or replace one index card.

        - Auto-populates ``mentions`` via :func:`extract_mentions` when the
          record arrives with an empty mentions list.
        - Flags links whose ``rel`` is outside the spec vocabulary
          (``unknown_rel=True``) rather than rejecting them.
        - Stamps the active ``spec.version`` and resets
          ``embedding_status`` to ``'pending'`` (the text may have changed;
          re-embedding is the dispatcher's job).
        """
        if record.source != self.source:
            raise ValueError(
                f"record.source {record.source!r} does not match catalog source "
                f"{self.source!r} — a SelfCatalog holds one owner's records only"
            )
        mentions = record.mentions or extract_mentions(record.text, self.spec)
        links = validate_links(record.links, self.spec)
        updated_at = record.updated_at or time.time()
        try:
            facets_json = json.dumps(record.facets, allow_nan=False)
            ref_json = json.dumps(record.ref, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"IndexRecord {record.record_id!r}: facets and ref must be "
                f"JSON-serializable ({exc})"
            ) from exc
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO records (
                    project, source, record_id, schema_version, kind,
                    facets_json, text, links_json, mentions_json, ref_json,
                    updated_at, spec_version, embedding_status, embedding,
                    embedding_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL)
                ON CONFLICT(project, source, record_id) DO UPDATE SET
                    schema_version   = excluded.schema_version,
                    kind             = excluded.kind,
                    facets_json      = excluded.facets_json,
                    text             = excluded.text,
                    links_json       = excluded.links_json,
                    mentions_json    = excluded.mentions_json,
                    ref_json         = excluded.ref_json,
                    updated_at       = excluded.updated_at,
                    spec_version     = excluded.spec_version,
                    embedding_status = 'pending',
                    embedding        = NULL,
                    embedding_model  = NULL
                """,
                (
                    record.project,
                    record.source,
                    record.record_id,
                    record.schema_version,
                    record.kind,
                    facets_json,
                    record.text,
                    json.dumps([link.model_dump() for link in links]),
                    json.dumps(mentions),
                    ref_json,
                    updated_at,
                    self.spec.version,
                ),
            )
            conn.commit()
        return {
            "project": record.project,
            "record_id": record.record_id,
            "mentions": mentions,
            "unknown_rels": [link.rel for link in links if link.unknown_rel],
        }

    def delete(self, project: str, record_id: str) -> bool:
        """Delete one record. Returns True when a row was removed."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM records WHERE project = ? AND source = ? AND record_id = ?",
                (project, self.source, record_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get(self, project: str, record_id: str) -> Optional[IndexRecord]:
        """Fetch one record as an :class:`IndexRecord`, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM records WHERE project = ? AND source = ? AND record_id = ?",
                (project, self.source, record_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def mark_stale_for_spec_bump(
        self, project: str, spec: Union[CatalogSpec, dict[str, Any]]
    ) -> int:
        """Bulk-flag this source's records as pending re-embedding.

        Takes the *full* new :class:`CatalogSpec` (or its dict form), not
        just a version number — this is deliberate: stamping only the
        version integer would let ``upsert()`` start writing
        ``spec_version=<new>`` while still validating/extracting mentions
        against the old cached spec content (rel vocabulary, facet rules,
        id patterns), permanently mislabeling those rows as current since
        this method only ever revisits rows with ``spec_version <
        spec.version``. Passing the full spec makes the version bump and
        the content swap atomic.

        Sets every row of this source (in ``project``) still stamped with
        an older ``spec_version`` to ``embedding_status='pending'`` and
        clears its stored embedding, then stamps ``spec_version`` to
        ``spec.version``. Also replaces ``self.spec`` with the new spec
        (when its version is newer), so a subsequent ``upsert()`` stamps
        the new version and validates against the new content instead of
        regressing to the stale cached spec.

        Also bumps ``updated_at`` on every touched row. Without this, an
        embedding worker already holding an old ``expected_updated_at``
        from :meth:`pending_embeddings` could still win the
        :meth:`store_embedding` concurrency guard after the bump and write
        back a vector computed under the pre-bump spec/model — flipping the
        row to 'ready' with a stale embedding and defeating the trigger.

        This is the primitive the "spec bump -> re-embed" trigger needs;
        the reaction itself — subscribing to the ecosystem's catalog-spec
        publish event — is the owner agent's job in its own repo, since
        this library has no transport dependency (see ``spec_client.py``).
        Call this from that reactor with the new spec once it is observed,
        e.g. via ``SpecClient(...).get_spec(refresh=True)``.

        Returns:
            Number of rows updated.
        """
        if not isinstance(spec, CatalogSpec):
            spec = CatalogSpec.model_validate(spec)
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE records
                SET spec_version = ?, embedding_status = 'pending',
                    embedding = NULL, embedding_model = NULL, updated_at = ?
                WHERE project = ? AND source = ? AND spec_version < ?
                """,
                (spec.version, time.time(), project, self.source, spec.version),
            )
            conn.commit()
            updated = cur.rowcount
        if spec.version > self.spec.version:
            self.spec = spec
        return updated

    # ------------------------------------------------------------------
    # read path (federated by the librarian)
    # ------------------------------------------------------------------

    def query(
        self,
        project: str,
        filters: Optional[dict[str, Any]] = None,
        jmespath_expr: Optional[str] = None,
        fields: Optional[list[str]] = None,
        limit: int = 100,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Structured query over index cards.

        Project-scoped by default; pass ``scope="all_projects"`` to opt in
        to a cross-project read (multi-project isolation is load-bearing —
        it is never the default).

        Args:
            filters: ``kind`` / ``record_id`` / ``schema_version`` /
                ``embedding_status`` match columns; ``updated_after`` /
                ``updated_before`` bound ``updated_at``; any other key is
                matched against the record's facets.
            jmespath_expr: optional jmespath expression applied to the list
                of row dicts (e.g. ``"[?facets.status=='open']"``).
            fields: optional projection — keep only these keys per row.
            limit: max rows fetched from sqlite (before jmespath).
            scope: ``"project"`` (default) or ``"all_projects"``.

        Returns:
            ``{"count": int, "rows": [...]}``.
        """
        if scope not in ("project", "all_projects"):
            raise ValueError("scope must be 'project' or 'all_projects'")
        where = ["source = ?"]
        params: list[Any] = [self.source]
        if scope == "project":
            where.append("project = ?")
            params.append(project)
        for key, value in (filters or {}).items():
            if key in _COLUMN_FILTERS:
                where.append(f"{key} = ?")
                params.append(value)
            elif key == "updated_after":
                where.append("updated_at > ?")
                params.append(float(value))
            elif key == "updated_before":
                where.append("updated_at < ?")
                params.append(float(value))
            else:
                # Facet filter. json_extract needs a quoted key path for
                # keys with odd characters; embedded double-quotes would
                # produce an invalid path, so reject them loudly.
                if '"' in key:
                    raise ValueError(
                        f"facet filter key {key!r} contains a double-quote — "
                        "unsupported in json_extract paths"
                    )
                where.append('json_extract(facets_json, ?) = ?')
                params.append(f'$."{key}"')
                params.append(int(value) if isinstance(value, bool) else value)
        sql = (
            f"SELECT * FROM records WHERE {' AND '.join(where)} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(int(limit))
        with self._connect() as conn:
            rows = [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        if jmespath_expr:
            result = jmespath.search(jmespath_expr, rows)
            if result is None:
                rows = []
            elif isinstance(result, list):
                rows = result
            else:
                rows = [result]
        if fields:
            rows = [
                {k: r[k] for k in fields if isinstance(r, dict) and k in r}
                if isinstance(r, dict)
                else r
                for r in rows
            ]
        return {"count": len(rows), "rows": rows}

    def search(
        self,
        project: str,
        query_text: Optional[str],
        limit: int = 20,
        query_vector: Optional[bytes] = None,
    ) -> dict[str, Any]:
        """Similarity search over record text.

        Runs vector search when all of the following hold: ``sqlite-vec``
        is importable, a ``query_vector`` is supplied (embedding the query
        text is the caller's / dispatcher's job — stub-then-swap), and
        embedded rows exist. Otherwise degrades to a case-insensitive LIKE
        scan over ``text``.

        Returns:
            ``{"mode": "vector" | "text_fallback", "count": int, "rows": [...]}``
            with a ``distance`` key per row in vector mode. When a vector
            attempt fails and falls back to text, the response also
            carries ``vector_error`` (the exception, stringified).
        """
        if _HAS_SQLITE_VEC and query_vector is not None:
            try:
                # _embedded_count is INSIDE the try too: a failing count
                # query (sqlite error, I/O issue) must degrade like any
                # other vector-path failure, never raise past this method.
                if self._embedded_count(project) > 0:
                    return self._vector_search(project, query_vector, limit)
            except Exception as exc:  # noqa: BLE001 — fallback path: any
                # vector failure (sqlite errors, extension load OSError,
                # sqlite_vec API changes) must degrade, never raise. The
                # reason is surfaced via vector_error for observability.
                out = self._text_search(project, query_text, limit)
                out["vector_error"] = str(exc)
                return out
        return self._text_search(project, query_text, limit)

    def _embedded_count(self, project: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM records "
                "WHERE project = ? AND source = ? AND embedding_status = 'ready' "
                "AND embedding IS NOT NULL",
                (project, self.source),
            ).fetchone()
        return int(row["n"])

    def _vector_search(
        self, project: str, query_vector: bytes, limit: int
    ) -> dict[str, Any]:
        with self._connect() as conn:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            rows = conn.execute(
                "SELECT *, vec_distance_cosine(embedding, ?) AS distance "
                "FROM records "
                "WHERE project = ? AND source = ? AND embedding_status = 'ready' "
                "AND embedding IS NOT NULL "
                "ORDER BY distance ASC LIMIT ?",
                (query_vector, project, self.source, int(limit)),
            ).fetchall()
        out = []
        for row in rows:
            d = _row_to_dict(row)
            d["distance"] = row["distance"]
            out.append(d)
        return {"mode": "vector", "count": len(out), "rows": out}

    def _text_search(
        self, project: str, query_text: Optional[str], limit: int
    ) -> dict[str, Any]:
        terms = [t for t in (query_text or "").split() if t]
        if not terms:
            # An empty query matches nothing — search stays search, never
            # a listing endpoint (use query()/list_since() for listings).
            return {"mode": "text_fallback", "count": 0, "rows": []}
        where = ["project = ?", "source = ?"]
        params: list[Any] = [project, self.source]
        for term in terms:
            where.append("text LIKE ? COLLATE NOCASE")
            params.append(f"%{term}%")
        sql = (
            f"SELECT * FROM records WHERE {' AND '.join(where)} "
            f"ORDER BY updated_at DESC LIMIT ?"
        )
        params.append(int(limit))
        with self._connect() as conn:
            rows = [_row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
        return {"mode": "text_fallback", "count": len(rows), "rows": rows}

    def stats(self, project: Optional[str] = None) -> dict[str, Any]:
        """Catalog health counters, optionally scoped to one project."""
        where = "WHERE source = ?"
        params: list[Any] = [self.source]
        if project is not None:
            where += " AND project = ?"
            params.append(project)
        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS n FROM records {where}", params
            ).fetchone()["n"]
            by_kind = {
                r["kind"]: r["n"]
                for r in conn.execute(
                    f"SELECT kind, COUNT(*) AS n FROM records {where} GROUP BY kind",
                    params,
                )
            }
            by_status = {
                (r["s"] if r["s"] is not None else "unset"): r["n"]
                for r in conn.execute(
                    f"SELECT json_extract(facets_json, '$.status') AS s, COUNT(*) AS n "
                    f"FROM records {where} GROUP BY s",
                    params,
                )
            }
            by_spec_version = {
                str(r["spec_version"]): r["n"]
                for r in conn.execute(
                    f"SELECT spec_version, COUNT(*) AS n FROM records {where} "
                    f"GROUP BY spec_version",
                    params,
                )
            }
            pending = conn.execute(
                f"SELECT COUNT(*) AS n FROM records {where} "
                f"AND embedding_status = 'pending'",
                params,
            ).fetchone()["n"]
            needs_curation = conn.execute(
                f"SELECT COUNT(*) AS n FROM records {where} "
                f"AND json_extract(facets_json, '$.needs_curation') IN (1, 'true')",
                params,
            ).fetchone()["n"]
        return {
            "source": self.source,
            "project": project,
            "total": total,
            "by_kind": by_kind,
            "by_status": by_status,
            "by_spec_version": by_spec_version,
            "pending_embeddings": pending,
            "needs_curation": needs_curation,
        }

    def list_since(
        self, project: str, since_ts: float, limit: int = 100
    ) -> dict[str, Any]:
        """Records updated after ``since_ts``, oldest first — librarian resync."""
        with self._connect() as conn:
            rows = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT * FROM records "
                    "WHERE project = ? AND source = ? AND updated_at > ? "
                    "ORDER BY updated_at ASC LIMIT ?",
                    (project, self.source, float(since_ts), int(limit)),
                ).fetchall()
            ]
        return {"count": len(rows), "rows": rows}

    # ------------------------------------------------------------------
    # embedding backlog (stub-then-swap: generation happens elsewhere)
    # ------------------------------------------------------------------

    def pending_embeddings(self, limit: int = 100) -> list[dict[str, Any]]:
        """Rows awaiting embedding, across all projects (worker-facing).

        Each row carries ``updated_at`` — pass it back to
        :meth:`store_embedding` as ``expected_updated_at`` so a vector
        computed from stale text is never marked ready.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT project, record_id, text, updated_at FROM records "
                "WHERE source = ? AND embedding_status = 'pending' "
                "ORDER BY updated_at ASC LIMIT ?",
                (self.source, int(limit)),
            ).fetchall()
        return [
            {
                "project": r["project"],
                "record_id": r["record_id"],
                "text": r["text"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    def store_embedding(
        self,
        project: str,
        record_id: str,
        vector: bytes,
        model: str,
        expected_updated_at: Optional[float] = None,
    ) -> bool:
        """Attach a vector produced elsewhere; marks the row 'ready'.

        Pass ``expected_updated_at`` (from :meth:`pending_embeddings`) to
        guard against a concurrent upsert: if the record's text changed
        since the worker read it, the write is skipped (returns False) and
        the row stays 'pending' for re-embedding. Omitting the guard writes
        unconditionally — only safe when the caller serializes with the
        owner's write path.
        """
        sql = (
            "UPDATE records SET embedding = ?, embedding_model = ?, "
            "embedding_status = 'ready' "
            "WHERE project = ? AND source = ? AND record_id = ?"
        )
        params: list[Any] = [vector, model, project, self.source, record_id]
        if expected_updated_at is not None:
            sql += " AND updated_at = ?"
            params.append(float(expected_updated_at))
        with self._connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0


# ----------------------------------------------------------------------
# row helpers
# ----------------------------------------------------------------------


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "project": row["project"],
        "source": row["source"],
        "record_id": row["record_id"],
        "schema_version": row["schema_version"],
        "kind": row["kind"],
        "facets": json.loads(row["facets_json"]),
        "text": row["text"],
        "links": json.loads(row["links_json"]),
        "mentions": json.loads(row["mentions_json"]),
        "ref": json.loads(row["ref_json"]),
        "updated_at": row["updated_at"],
        "spec_version": row["spec_version"],
        "embedding_status": row["embedding_status"],
    }


def _row_to_record(row: sqlite3.Row) -> IndexRecord:
    d = _row_to_dict(row)
    return IndexRecord(
        project=d["project"],
        source=d["source"],
        record_id=d["record_id"],
        schema_version=d["schema_version"],
        kind=d["kind"],
        updated_at=d["updated_at"],
        facets=d["facets"],
        text=d["text"],
        links=[Link(**link) for link in d["links"]],
        mentions=d["mentions"],
        ref=d["ref"],
    )
