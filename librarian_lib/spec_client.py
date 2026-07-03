"""SpecClient — fetch the ecosystem CatalogSpec without a bus dependency.

The transport is injected: the owner agent passes a ``fetch_fn`` callable
(typically a closure over its bus client) that returns CatalogSpec JSON.
This library never imports khonliang-bus-lib. When the fetch is absent or
fails, the client sets ``degraded=True`` and keeps the last known-good
cached spec when one exists; only with no prior cache does it bootstrap
on :data:`DEFAULT_SPEC` — bootstrap must never block on the librarian
being up, and a transient outage must never regress a good spec.
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
        """Return the active spec, degrading gracefully on failure.

        On fetch/validation failure ``degraded`` is set True and the last
        known-good cached spec is kept; ``DEFAULT_SPEC`` is used only when
        there is no prior cache. The result is cached; pass
        ``refresh=True`` to re-fetch (e.g. after the librarian comes back
        up). ``degraded`` reflects the most recent resolution attempt.
        """
        if self._cached is not None and not refresh:
            return self._cached
        if self.fetch_fn is None:
            # Same keep-last-known-good semantics as the failure path below.
            self.degraded = True
            if self._cached is None:
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
            # Any transport or validation failure → degraded. Keep the last
            # known-good spec when we have one (a transient librarian outage
            # must not regress the fleet to DEFAULT_SPEC); only bootstrap on
            # the default when there is no prior cache.
            self.degraded = True
            if self._cached is None:
                self._cached = DEFAULT_SPEC
        return self._cached
