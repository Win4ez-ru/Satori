"""Read-only retention planning; a reviewable plan is never proof of deletion."""

from dataclasses import dataclass
from enum import StrEnum

from satori.domain.source_policy import CanonicalRootRef
from satori.domain.validation import non_blank, sha256_hex

ERASURE_POLICY_VERSION = "canonical_erasure_preview_v1"
MAX_ERASURE_ASSESSMENT_WORK = 1_000_000


class ErasureDisposition(StrEnum):
    REDACT_CONTENT = "owner_redact_content"
    REMOVE_INDEX = "owner_remove_derived_index"
    RETAIN_METADATA = "retain_structural_metadata"
    RETAIN_NUMERIC = "retain_numeric_history"
    REVIEW_UNCERTAIN = "owner_review_uncertain_content"


@dataclass(frozen=True, slots=True)
class ErasureRoute:
    """Counts and column names, never row keys, quotes or generated wording."""

    owner: str
    table: str
    disposition: ErasureDisposition
    rows: int
    content_columns: tuple[str, ...] = ()
    uncertain_rows: int = 0

    def __post_init__(self) -> None:
        for name in ("owner", "table"):
            object.__setattr__(self, name, non_blank(getattr(self, name), name, maximum=128))
        if not isinstance(self.disposition, ErasureDisposition):
            raise ValueError("invalid_erasure_disposition")
        if type(self.rows) is not int or self.rows < 0:
            raise ValueError("invalid_erasure_row_count")
        if type(self.uncertain_rows) is not int or not 0 <= self.uncertain_rows <= self.rows:
            raise ValueError("invalid_erasure_uncertain_count")
        columns = tuple(non_blank(item, "column", maximum=128) for item in self.content_columns)
        if len(set(columns)) != len(columns):
            raise ValueError("duplicate_erasure_column")
        object.__setattr__(self, "content_columns", columns)


@dataclass(frozen=True, slots=True)
class ErasureInventory:
    """One consistent main-DB assessment, assembled by infrastructure behind a port."""

    root: CanonicalRootRef
    schema_revision: str
    schema_digest: str
    store_digest: str
    routing_digest: str
    routes: tuple[ErasureRoute, ...]
    scanned_rows: int
    affected_payload_versions: int
    uncertain_payload_versions: int
    blockers: tuple[str, ...] = ()
    retention_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.root, CanonicalRootRef):
            raise ValueError("invalid_erasure_root")
        sha256_hex(self.schema_digest, "schema_digest")
        sha256_hex(self.store_digest, "store_digest")
        sha256_hex(self.routing_digest, "routing_digest")
        if not self.retention_digest:
            object.__setattr__(self, "retention_digest", self.store_digest)
        sha256_hex(self.retention_digest, "retention_digest")
        non_blank(self.schema_revision, "schema_revision", maximum=128)
        for name in ("scanned_rows", "affected_payload_versions", "uncertain_payload_versions"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError("invalid_erasure_inventory_count")
        routes = tuple(self.routes)
        if any(not isinstance(route, ErasureRoute) for route in routes):
            raise ValueError("invalid_erasure_route")
        keys = {(route.owner, route.table, route.disposition) for route in routes}
        if len(keys) != len(routes):
            raise ValueError("duplicate_erasure_route")
        object.__setattr__(self, "routes", routes)
        object.__setattr__(
            self,
            "blockers",
            tuple(non_blank(item, "blocker", maximum=128) for item in self.blockers),
        )
