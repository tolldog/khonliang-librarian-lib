"""CatalogSkills — the catalog read surface, shaped for bus registration.

A plain class with typed, docstringed methods. The owner agent wires these
to bus skills itself (using khonliang-bus-lib in the agent repo); this
library deliberately has no transport dependency. See the README wiring
example.
"""

from __future__ import annotations

from typing import Any, Optional

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
        self, project: str, query_text: str, limit: int = 20
    ) -> dict[str, Any]:
        """Similarity search over this owner's index-card text.

        Vector search when embeddings + sqlite-vec are available, text
        LIKE fallback otherwise; the response reports which via "mode".

        Returns:
            {"mode": "vector"|"text_fallback", "count": int, "rows": [...]}
        """
        return self.catalog.search(project, query_text, limit=limit)

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
