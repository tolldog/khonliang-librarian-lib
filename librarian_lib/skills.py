"""CatalogSkills — the catalog read surface, shaped for bus registration.

A plain class with typed, docstringed methods. The owner agent wires these
to bus skills itself (using khonliang-bus-lib in the agent repo); this
library deliberately has no transport dependency. See the README wiring
example.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Optional, Union

from .catalog import SelfCatalog


class CatalogSkills:
    """Bus-skill-shaped wrappers over a :class:`SelfCatalog`.

    Register each method as a bus skill handler on the owner agent, e.g.
    ``catalog_query`` / ``catalog_search`` / ``catalog_stats`` /
    ``list_since``. All methods return plain dicts safe to serialize.
    """

    def __init__(self, catalog: SelfCatalog) -> None:
        self.catalog = catalog

    def catalog_query(
        self,
        project: str,
        filters: Optional[dict[str, Any]] = None,
        jmespath_expr: Optional[str] = None,
        fields: Optional[list[str]] = None,
        limit: int = 100,
        scope: str = "project",
    ) -> dict[str, Any]:
        """Structured query over this owner's index cards.

        Args:
            project: project scope (mandatory; isolation is load-bearing).
            filters: column filters (kind, record_id, schema_version,
                embedding_status), date bounds (updated_after /
                updated_before), or facet key matches.
            jmespath_expr: optional jmespath expression over the row dicts.
            fields: optional projection of row keys.
            limit: max rows (default 100).
            scope: 'project' (default) or 'all_projects' (explicit opt-in).

        Returns:
            {"count": int, "rows": [...]}
        """
        return self.catalog.query(
            project,
            filters=filters,
            jmespath_expr=jmespath_expr,
            fields=fields,
            limit=limit,
            scope=scope,
        )

    def catalog_search(
        self,
        project: str,
        query_text: Optional[str],
        limit: int = 20,
        query_vector: Optional[Union[bytes, str]] = None,
    ) -> dict[str, Any]:
        """Similarity search over this owner's index-card text.

        Vector search requires ALL of: sqlite-vec importable, a
        ``query_vector`` supplied (embedding the query text is the
        caller's / dispatcher's job), and embedded rows existing for the
        project — otherwise text LIKE fallback. The response reports
        which via "mode".

        Args:
            query_vector: raw float32 vector bytes, or the same
                base64-encoded (bus transports carry JSON, not bytes).
                A str that is not valid base64 raises ValueError loudly.

        Returns:
            {"mode": "vector"|"text_fallback", "count": int, "rows": [...]}
        """
        vec: Optional[bytes]
        if isinstance(query_vector, str):
            try:
                vec = base64.b64decode(query_vector, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    "query_vector string must be base64-encoded vector bytes"
                ) from exc
        else:
            vec = query_vector
        return self.catalog.search(project, query_text, limit=limit, query_vector=vec)

    def catalog_stats(self, project: Optional[str] = None) -> dict[str, Any]:
        """Catalog health counters (by kind/status, spec versions, backlog).

        Returns:
            {"total", "by_kind", "by_status", "by_spec_version",
             "pending_embeddings", "needs_curation", ...}
        """
        return self.catalog.stats(project=project)

    def list_since(
        self, project: str, since_ts: float, limit: int = 100
    ) -> dict[str, Any]:
        """Records updated after ``since_ts`` (oldest first) — librarian resync.

        Returns:
            {"count": int, "rows": [...]}
        """
        return self.catalog.list_since(project, since_ts, limit=limit)

    def catalog_list_expiring(
        self, project: str, before: float, limit: int = 100
    ) -> dict[str, Any]:
        """This source's records due for refresh, most overdue first.

        Generic per-record scheduler surface: any owner agent stamps an
        ``IndexRecord`` with ``expires_at`` (absolute epoch seconds) and
        an optional ``refresh_operation``/``refresh_args`` naming a skill
        on itself for the librarian to call back. This library never
        interprets what "refresh" means — pure storage + query.

        Rows with ``expires_at`` set but ``refresh_operation`` left
        ``None`` are included (a valid flag-only case, never dropped).

        FIXED CONTRACT: name, args (project, before, limit), and response
        shape ({"count", "rows"}) are relied on by the librarian agent's
        ``run_expiry_sweep`` (a sibling FR built in parallel) — do not
        rename or reshape.

        Args:
            project: project scope (mandatory; isolation is load-bearing).
            before: absolute epoch seconds cutoff; rows with
                ``expires_at <= before`` are due.
            limit: max rows (default 100).

        Returns:
            {"count": int, "rows": [...]} ordered by expires_at ascending.
        """
        return self.catalog.list_expiring(project, before, limit=limit)

    def catalog_mark_stale(self, project: str, spec: dict[str, Any]) -> dict[str, Any]:
        """Bulk-flag this source's records pending re-embedding after a spec bump.

        Call from the owner agent's own bus reactor when it observes the
        ecosystem's catalog-spec publish event — this library does not
        subscribe to that event itself (no transport dependency).

        Args:
            spec: the *full* new ``CatalogSpec`` as a dict (e.g. from
                ``SpecClient(...).get_spec(refresh=True).model_dump()``),
                not just its version number — stamping the version alone
                would decouple it from the spec content it names.

        Returns:
            {"updated": int}
        """
        return {"updated": self.catalog.mark_stale_for_spec_bump(project, spec)}
