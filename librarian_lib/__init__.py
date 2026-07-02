"""khonliang-librarian-lib — knowledge-owner contract for the librarian.

Owner agents embed a :class:`SelfCatalog` (sqlite sidecar of project-scoped
index cards) and call upsert/delete inside their own write paths; the
central librarian federates search/query across per-agent catalogs.
Classification/taxonomy is librarian-side — never in this library.
"""

from .catalog import SelfCatalog
from .mentions import extract_mentions
from .models import (
    CORE_FACETS,
    DEFAULT_ID_PATTERNS,
    DEFAULT_REL_VOCABULARY,
    DEFAULT_SPEC,
    CatalogSpec,
    IndexRecord,
    KnowledgeSourceDescriptor,
    Link,
    validate_links,
)
from .skills import CatalogSkills
from .spec_client import SpecClient

__version__ = "0.1.0"

#: Version of the owner<->librarian contract this library implements.
CONTRACT_VERSION = "0.1"

__all__ = [
    "CONTRACT_VERSION",
    "CORE_FACETS",
    "CatalogSkills",
    "CatalogSpec",
    "DEFAULT_ID_PATTERNS",
    "DEFAULT_REL_VOCABULARY",
    "DEFAULT_SPEC",
    "IndexRecord",
    "KnowledgeSourceDescriptor",
    "Link",
    "SelfCatalog",
    "SpecClient",
    "__version__",
    "extract_mentions",
    "validate_links",
]
