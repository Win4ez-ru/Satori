"""SQLite JSON affinity cannot turn accepted numerical knowledge into unknown lineage."""

from pathlib import Path

import pytest
from sqlalchemy import select

from satori.core.semantic import SemanticClaimKind, SemanticClaimProposal, SemanticValueKind
from satori.domain.semantic_memory import normalize_semantic_value, semantic_claim_key
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure import ERASURE_KNOWLEDGE_SCHEMA_DIGEST, _load_store
from satori.infrastructure.persistence.erasure_inventory import ProvenOwners
from satori.infrastructure.persistence.models import SemanticClaimRow
from tests.erasure_knowledge_support import add_knowledge
from tests.erasure_support import create_case


@pytest.mark.parametrize("value", [37, 37.5])
def test_native_sqlite_numeric_json_preserves_exact_semantic_namespace(
    migrated_database: Database,
    tmp_path: Path,
    value: int | float,
) -> None:
    case = create_case(migrated_database, tmp_path / "numeric-floor", excluded=False)
    fixture = add_knowledge(case)
    with case.database.session_factory() as session:
        row = session.get(SemanticClaimRow, fixture.scalar_claim_ids[0])
        assert row is not None
        row.value = value
        row.normalized_value = normalize_semantic_value(SemanticValueKind.NUMBER, value)
        row.claim_key = semantic_claim_key(
            SemanticClaimProposal(
                subject="user",
                predicate="age",
                value_kind=SemanticValueKind.NUMBER,
                value=float(value),
                polarity=True,
                claim_kind=SemanticClaimKind.HYPOTHESIS,
                confidence=0.31,
                evidence_memory_ids=(case.episode_ids[0],),
            ),
            scope=case.scope,
        )
        session.commit()
    with case.database.engine.connect() as connection:
        store, *_ = _load_store(connection, ERASURE_KNOWLEDGE_SCHEMA_DIGEST)
        numeric = next(
            row
            for row in store["semantic_claims"]
            if row["claim_id"] == fixture.scalar_claim_ids[0]
        )
        assert type(numeric["value"]) is type(value)
        owners = ProvenOwners(store, case.scope.identity_id)
        assert owners.admits(("semantic_claim", fixture.scalar_claim_ids[0], 1))
        assert owners.admits(("semantic_claim", fixture.scalar_claim_ids[1], 1))
    with case.database.session_factory() as session:
        assert (
            session.scalar(
                select(SemanticClaimRow.value).where(
                    SemanticClaimRow.claim_id == fixture.scalar_claim_ids[1]
                )
            )
            is True
        )
