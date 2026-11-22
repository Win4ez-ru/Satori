"""Closed references and complete audit metadata/content/UNKNOWN partitions."""

from dataclasses import replace

import pytest

from satori.domain.audit_metadata import (
    AUDIT_METADATA_FIELDS_BY_FAMILY,
    AuditBindingRef,
    AuditMetadataBinding,
    AuditMetadataClassification,
    AuditMetadataCoverage,
    AuditMetadataCoverageStatus,
    AuditMetadataFamily,
    AuditMetadataUnknownReason,
)


@pytest.mark.parametrize("family", list(AUDIT_METADATA_FIELDS_BY_FAMILY))
def test_exact_closed_binding_reference(family: AuditMetadataFamily) -> None:
    record_id = "identity" if family is AuditMetadataFamily.ACTIVATION else "owner-receipt"
    value = AuditMetadataBinding("event", "identity", AuditBindingRef(family, record_id), "a" * 64)
    assert value.contract_version == 1
    assert value.reference.family is family
    assert value.source_input_id is None


def test_appraisal_has_separate_proof_and_cannot_receive_sidecar() -> None:
    with pytest.raises(ValueError, match="unsupported_audit_binding_family"):
        AuditBindingRef(AuditMetadataFamily.APPRAISAL_TRANSITION, "transition")
    with pytest.raises(ValueError, match="audit_activation_identity_mismatch"):
        AuditMetadataBinding(
            "event", "identity", AuditBindingRef(AuditMetadataFamily.ACTIVATION, "other"), "a" * 64
        )


@pytest.mark.parametrize("version", [True, 1.0, 0, 2])
def test_contract_version_never_coerces_or_guesses(version: int | float) -> None:
    value = AuditMetadataBinding(
        "event", "identity", AuditBindingRef(AuditMetadataFamily.ACTIVATION, "identity"), "a" * 64
    )
    with pytest.raises(ValueError, match="unsupported_audit_metadata_contract"):
        replace(value, contract_version=version)  # type: ignore[arg-type]


def test_proved_available_appraisal_content_is_distinct_from_unknown() -> None:
    certified = AuditMetadataClassification(
        "metadata", AuditMetadataCoverageStatus.CERTIFIED_METADATA, AuditMetadataFamily.ACTIVATION
    )
    content = AuditMetadataClassification(
        "appraisal",
        AuditMetadataCoverageStatus.OWNED_CONTENT,
        AuditMetadataFamily.APPRAISAL_TRANSITION,
        AuditMetadataUnknownReason.CONTENT_PRESENT,
    )
    unknown = AuditMetadataClassification(
        "legacy",
        AuditMetadataCoverageStatus.UNKNOWN,
        reason=AuditMetadataUnknownReason.MISSING_BINDING,
    )
    assert (certified.status, content.status, unknown.status) == tuple(AuditMetadataCoverageStatus)
    assert AuditMetadataCoverage(3, 1, 1, 1).total_events == 3
    with pytest.raises(ValueError, match="incomplete_audit_metadata_coverage"):
        AuditMetadataCoverage(3, 2, 1, 1)
    with pytest.raises(ValueError, match="invalid_owned_audit_content"):
        replace(content, family=AuditMetadataFamily.ACTIVATION)
    with pytest.raises(ValueError, match="missing_unknown_audit_reason"):
        replace(unknown, reason=None)


@pytest.mark.parametrize("count", [-1, True, 1.0])
def test_coverage_counts_are_strict_nonnegative_integers(count: int | float) -> None:
    with pytest.raises(ValueError, match="invalid_audit_metadata_count"):
        AuditMetadataCoverage(count, 0, 0, 0)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw_key", ["", "x" * 129])
def test_invalid_raw_legacy_key_is_preserved_only_as_private_unknown(raw_key: str) -> None:
    item = AuditMetadataClassification(
        raw_key,
        AuditMetadataCoverageStatus.UNKNOWN,
        reason=AuditMetadataUnknownReason.INVALID_SHAPE,
    )
    assert item.event_id == raw_key
    with pytest.raises(ValueError, match="event_id"):
        AuditMetadataClassification(
            raw_key, AuditMetadataCoverageStatus.CERTIFIED_METADATA, AuditMetadataFamily.ACTIVATION
        )
