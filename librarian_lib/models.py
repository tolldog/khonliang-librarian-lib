"""Contract models for the knowledge-owner <-> librarian catalog surface.

All models are pydantic v2. Design rules enforced here:

- ``project`` is mandatory and non-empty on every record (multi-project
  isolation is load-bearing).
- ``schema_version`` is mandatory and ``>= 1``.
- Records are index cards: ``text`` is embeddable summary text and ``ref``
  points back to the owner for the full payload.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Relationship vocabulary shipped with the default spec.
DEFAULT_REL_VOCABULARY = [
    "depends_on",
    "fixes",
    "implements",
    "bundles",
    "backed_by",
    "supersedes",
]

#: Core facet keys whose value types are validated when present.
CORE_FACETS = ["status", "target", "priority"]

#: Default mention-extraction patterns (name -> regex). See mentions.py.
DEFAULT_ID_PATTERNS = {
    # fr_developer_6ecd0c01, fr_ff923ebf, bug_..., ms_..., dog_..., art_...
    "ecosystem_id": r"\b(?:fr|bug|ms|dog|art)_(?:[a-z0-9][a-z0-9_-]*_)?[0-9a-f]{8}\b",
    # 16-char hex corpus hashes, e.g. 8d3bcd64f6e12c25
    "corpus_hash": r"\b[0-9a-f]{16}\b",
    # PR #42 / PR#42
    "pr_number": r"\bPR\s?#\d+\b",
    # repo#42 style cross-references (khonliang-bus#22)
    "repo_issue": r"\b[A-Za-z][A-Za-z0-9_.-]*#\d+\b",
}


class Link(BaseModel):
    """Typed relationship from one index record to another record.

    ``rel`` should come from the catalog spec's ``rel_vocabulary``. Links
    with rels outside the active spec's vocabulary are accepted but flagged
    with ``unknown_rel=True`` (set by :func:`validate_links` /
    ``SelfCatalog.upsert``), never rejected — the vocabulary can grow faster
    than owners redeploy.
    """

    rel: str
    target_source: str
    target_id: str
    unknown_rel: bool = False


class CatalogSpec(BaseModel):
    """Ecosystem-wide catalog contract, served by the librarian.

    Owners fetch this via :class:`librarian_lib.spec_client.SpecClient`;
    when unavailable they bootstrap on :data:`DEFAULT_SPEC`.
    """

    version: int = 0
    core_facets: list[str] = Field(default_factory=lambda: list(CORE_FACETS))
    rel_vocabulary: list[str] = Field(
        default_factory=lambda: list(DEFAULT_REL_VOCABULARY)
    )
    embedding_model: Optional[str] = None
    embedding_dims: Optional[int] = None
    #: name -> regex used by mentions.extract_mentions
    id_patterns: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_ID_PATTERNS)
    )


#: Spec-absent bootstrap spec (version 0, no embedding model configured).
DEFAULT_SPEC = CatalogSpec()


def validate_links(links: list[Link], spec: Optional[CatalogSpec]) -> list[Link]:
    """Return links with ``unknown_rel`` set for rels outside the spec vocabulary.

    With no spec, the default vocabulary is used. Never raises: unknown rels
    are flagged, not rejected.
    """
    vocab = set((spec or DEFAULT_SPEC).rel_vocabulary)
    out = []
    for link in links:
        out.append(link.model_copy(update={"unknown_rel": link.rel not in vocab}))
    return out


class IndexRecord(BaseModel):
    """One index card in an owner's SelfCatalog.

    Not the payload — ``text`` is embeddable summary text and ``ref`` is the
    owner callback pointer (``{"skill": ..., "args": ...}``) the librarian
    uses to fetch the real thing.
    """

    model_config = ConfigDict(validate_assignment=True)

    project: str
    source: str
    record_id: str
    schema_version: int = Field(ge=1)
    kind: str
    updated_at: float = 0.0
    facets: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    links: list[Link] = Field(default_factory=list)
    mentions: list[str] = Field(default_factory=list)
    ref: dict[str, Any] = Field(default_factory=dict)

    @field_validator("project")
    @classmethod
    def _project_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "project is required and must be non-empty — "
                "records are project-scoped (multi-project isolation)"
            )
        return v

    @field_validator("facets")
    @classmethod
    def _core_facet_types(cls, v: dict[str, Any]) -> dict[str, Any]:
        # Core keys are type-validated when present; arbitrary extension
        # keys pass through untouched.
        for key in ("status", "target"):
            if key in v and not isinstance(v[key], str):
                raise ValueError(f"core facet {key!r} must be a string when present")
        if "priority" in v and (
            isinstance(v["priority"], bool)
            or not isinstance(v["priority"], (str, int))
        ):
            # bool is a subclass of int — exclude it explicitly.
            raise ValueError("core facet 'priority' must be a string or int when present")
        return v


class KnowledgeSourceDescriptor(BaseModel):
    """Self-description an owner agent hands the librarian at registration."""

    source_id: str
    kind: str
    owner_agent: str
    projects: list[str] = Field(default_factory=list)
    contract_version: str
    facet_extensions: dict[str, Any] = Field(default_factory=dict)
    record_count: Optional[int] = None
    freshness: Optional[float] = None
