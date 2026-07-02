"""SpecClient — fetch the ecosystem CatalogSpec without a bus dependency.

The transport is injected: the owner agent passes a ``fetch_fn`` callable
(typically a closure over its bus client) that returns CatalogSpec JSON.
This library never imports khonliang-bus-lib. When the fetch is absent or
fails, the client degrades to :data:`DEFAULT_SPEC` and sets
``degraded=True`` so callers can surface it — bootstrap must never block
on the librarian being up.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .models import DEFAULT_SPEC, CatalogSpec


class SpecClient:
    """Resolve the active :class:`CatalogSpec`.

    Args:
        fetch_fn: zero-arg callable returning CatalogSpec content as a
            dict, JSON-shaped mapping, or ``CatalogSpec`` instance —
            usually a bus request wrapper. ``None`` means "no librarian
            available": bootstrap on the default spec.
    """

    def __init__(self, fetch_fn: Optional[Callable[[], Any]] = None) -> None:
        self.fetch_fn = fetch_fn
        self.degraded: bool = False
        self._cached: Optional[CatalogSpec] = None

    def get_spec(self, refresh: bool = False) -> CatalogSpec:
        """Return the active spec, degrading to ``DEFAULT_SPEC`` on failure.

        The result is cached; pass ``refresh=True`` to re-fetch (e.g. after
        the librarian comes back up). ``degraded`` reflects the most recent
        resolution attempt.
        """
        if self._cached is not None and not refresh:
            return self._cached
        if self.fetch_fn is None:
            self.degraded = True
            self._cached = DEFAULT_SPEC
            return self._cached
        try:
            payload = self.fetch_fn()
            if isinstance(payload, CatalogSpec):
                spec = payload
            else:
                spec = CatalogSpec.model_validate(payload)
            self.degraded = False
            self._cached = spec
        except Exception:
            # Any transport or validation failure → degraded bootstrap.
            self.degraded = True
            self._cached = DEFAULT_SPEC
        return self._cached
