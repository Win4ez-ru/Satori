"""One exact row partition from a transaction-local proved retention context."""

import hashlib
import json
from collections import Counter

from satori.domain.erasure_preparation import ErasurePreparationOwner
from satori.domain.retention import (
    RetentionClass,
    RetentionFacet,
    RetentionReason,
    RetentionReasonCount,
    RetentionTableCounts,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.erasure_inventory import Store
from satori.infrastructure.persistence.erasure_routes import ROUTE_SPECS
from satori.infrastructure.persistence.retention_context import RetentionContext


def partition_store(
    store: Store,
    primary_keys: dict[str, tuple[str, ...]],
    context: RetentionContext,
) -> tuple[tuple[RetentionTableCounts, ...], tuple[RetentionReasonCount, ...], str]:
    digest = hashlib.sha256()
    tables: list[RetentionTableCounts] = []
    reasons: Counter[RetentionReason] = Counter()
    for table in sorted(store):
        spec = ROUTE_SPECS.get(table)
        keys = primary_keys.get(table)
        if spec is None or not keys:
            raise SourcePolicyError("retention_owner_registry_incomplete")
        counts: Counter[RetentionClass] = Counter()
        facets: Counter[RetentionFacet] = Counter()
        seen: set[tuple[object, ...]] = set()
        for row in store[table]:
            context.budget.charge()
            key = tuple(row[name] for name in keys)
            if key in seen:
                raise SourcePolicyError("retention_row_key_unproven")
            seen.add(key)
            decision = context.classify(table, row)
            counts[decision.category] += 1
            facets.update(decision.facets)
            reasons.update(decision.reasons)
            material = [
                table,
                key,
                decision.category,
                sorted(decision.facets),
                sorted(decision.reasons),
            ]
            encoded = json.dumps(
                material, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode()
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
        tables.append(
            RetentionTableCounts(
                owner=ErasurePreparationOwner(spec.owner),
                table=table,
                total_rows=len(store[table]),
                supported_retained_rows=counts[RetentionClass.SUPPORTED_RETAINED],
                owned_content_rows=counts[RetentionClass.OWNED_CONTENT],
                unknown_rows=counts[RetentionClass.UNKNOWN],
                structural_metadata_rows=facets[RetentionFacet.STRUCTURAL_METADATA],
                numeric_history_rows=facets[RetentionFacet.NUMERIC_HISTORY],
                terminal_body_rows=facets[RetentionFacet.TERMINAL_BODY],
            )
        )
    return (
        tuple(tables),
        tuple(
            RetentionReasonCount(reason=reason, rows=rows)
            for reason, rows in sorted(reasons.items())
        ),
        digest.hexdigest(),
    )
