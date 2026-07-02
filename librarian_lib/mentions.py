"""Deterministic mention extraction from record text.

Populates ``IndexRecord.mentions`` so the librarian can build a cross-source
reference graph without any LLM in the loop. Patterns come from
``CatalogSpec.id_patterns`` (falling back to the defaults baked into
``DEFAULT_SPEC``). Pure regex — deterministic, cheap, and safe on garbage
input.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import DEFAULT_SPEC, CatalogSpec


def extract_mentions(text: str, spec: Optional[CatalogSpec] = None) -> list[str]:
    """Extract ecosystem ids mentioned in ``text``.

    Recognizes (with the default spec):

    - ``fr_*/bug_*/ms_*/dog_*/art_*`` ids ending in an 8-hex suffix
      (``fr_developer_6ecd0c01``, ``fr_ff923ebf``, ``dog_a29646ac``)
    - 16-char hex corpus hashes (``8d3bcd64f6e12c25``)
    - ``PR #N`` / ``PR#N`` and ``repo#N`` forms

    Deduplicates while preserving first-seen order. Never raises: non-string
    input yields ``[]`` and malformed patterns in the spec are skipped.
    """
    if not isinstance(text, str) or not text:
        return []
    patterns = (spec or DEFAULT_SPEC).id_patterns or {}

    # Collect (position, match) across all patterns so ordering follows
    # appearance in the text, not pattern-dict order.
    hits: list[tuple[int, str]] = []
    for pattern in patterns.values():
        try:
            for m in re.finditer(pattern, text):
                hits.append((m.start(), m.group(0)))
        except (re.error, TypeError):
            # Malformed pattern in a spec must not break owner write paths.
            continue

    hits.sort(key=lambda h: h[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, value in hits:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
