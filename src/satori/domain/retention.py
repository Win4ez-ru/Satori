"""Disjoint logical-row retention coverage, separate from physical erasure."""

from dataclasses import dataclass
from enum import StrEnum

from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.validation import non_blank, sha256_hex

RETENTION_POLICY_VERSION = "whole_store_retention_v1"


class RetentionClass(StrEnum):
    SUPPORTED_RETAINED = "supported_retained"
    OWNED_CONTENT = "owned_content"
    UNKNOWN = "unknown"


class RetentionFacet(StrEnum):
    STRUCTURAL_METADATA = "structural_metadata"
    NUMERIC_HISTORY = "numeric_history"
    TERMINAL_BODY = "terminal_body"


class RetentionReason(StrEnum):
    ROW_SHAPE_UNPROVEN = "row_shape_unproven"
    ROW_VERSION_UNSUPPORTED = "row_version_unsupported"
    SCALAR_CODE_UNSUPPORTED = "scalar_code_unsupported"
    OWNER_SCOPE_UNPROVEN = "owner_scope_unproven"
    OWNER_REFERENCE_UNPROVEN = "owner_reference_unproven"
    INPUT_PROVENANCE_UNPROVEN = "input_provenance_unproven"
    STRUCTURED_VALUE_UNPROVEN = "structured_value_unproven"
    TERMINAL_MATERIAL_UNPROVEN = "terminal_material_unproven"
    AUDIT_CONTRACT_UNPROVEN = "audit_contract_unproven"
    STORE_FOREIGN_KEY_UNPROVEN = "store_foreign_key_unproven"


@dataclass(frozen=True, slots=True)
class RetentionTableCounts:
    owner: ErasurePreparationOwner
    table: str
    total_rows: int
    supported_retained_rows: int
    owned_content_rows: int
    unknown_rows: int
    structural_metadata_rows: int = 0
    numeric_history_rows: int = 0
    terminal_body_rows: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.owner, ErasurePreparationOwner):
            raise ValueError("invalid_retention_owner")
        non_blank(self.table, "table", maximum=128)
        for name in (
            "total_rows",
            "supported_retained_rows",
            "owned_content_rows",
            "unknown_rows",
            "structural_metadata_rows",
            "numeric_history_rows",
            "terminal_body_rows",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= self.total_rows:
                raise ValueError("invalid_retention_count")
        if (
            self.supported_retained_rows + self.owned_content_rows + self.unknown_rows
            != self.total_rows
        ):
            raise ValueError("retention_row_partition_incomplete")
        if self.terminal_body_rows > self.supported_retained_rows:
            raise ValueError("retention_terminal_rows_not_proved")


@dataclass(frozen=True, slots=True)
class RetentionReasonCount:
    reason: RetentionReason
    rows: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.reason, RetentionReason)
            or type(self.rows) is not int
            or self.rows < 1
        ):
            raise ValueError("invalid_retention_reason_count")


@dataclass(frozen=True, slots=True)
class RetentionInventory:
    identity_id: str
    lineage_id: str
    source_generation: int
    erasure_revision: int
    body_revision: int
    body_head_digest: str
    schema_revision: str
    schema_digest: str
    store_digest: str
    classification_digest: str
    table_counts: tuple[RetentionTableCounts, ...]
    reason_counts: tuple[RetentionReasonCount, ...]
    scanned_rows: int
    work_units: int
    policy_version: str = RETENTION_POLICY_VERSION

    def __post_init__(self) -> None:
        for name in ("identity_id", "lineage_id", "schema_revision"):
            non_blank(getattr(self, name), name, maximum=128)
        for name in ("schema_digest", "store_digest", "classification_digest"):
            sha256_hex(getattr(self, name), name)
        for name in (
            "source_generation",
            "erasure_revision",
            "body_revision",
            "scanned_rows",
            "work_units",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("invalid_retention_inventory_count")
        if self.policy_version != RETENTION_POLICY_VERSION:
            raise ValueError("unsupported_retention_policy")
        if self.body_revision:
            sha256_hex(self.body_head_digest, "body_head_digest")
        elif self.body_head_digest:
            raise ValueError("invalid_empty_retention_body_head")
        if len({item.table for item in self.table_counts}) != len(self.table_counts):
            raise ValueError("duplicate_retention_table")
        if {item.owner for item in self.table_counts} != set(ErasurePreparationOwner):
            raise ValueError("retention_owner_coverage_incomplete")
        if sum(item.total_rows for item in self.table_counts) != self.scanned_rows:
            raise ValueError("retention_inventory_partition_incomplete")
        if len({item.reason for item in self.reason_counts}) != len(self.reason_counts):
            raise ValueError("duplicate_retention_reason")
