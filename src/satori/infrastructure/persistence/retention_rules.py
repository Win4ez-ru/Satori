"""Closed declared field roles and scalar-code vocabularies for retention V1."""

_METADATA = frozenset(
    [
        "alembic_version",
        "conversation_sessions",
        "conversation_interactions",
        "episode_formation_decisions",
        "memory_lifecycle_decisions",
        "semantic_claim_evidence",
        "semantic_formation_decisions",
        "user_model_claim_evidence",
        "world_model_claim_evidence",
        "model_formation_decisions",
        "position_formation_decisions",
        "satori_inclination_evidence",
        "reflection_runs",
        "reflection_sources",
        "reflection_attempts",
        "reflection_outcomes",
        "satori_identities",
        "satori_value_sets",
        "satori_values",
        "personality_evidence",
        "audit_metadata_bindings",
        "source_policy_bindings",
        "source_excluded_roots",
        "source_inputs",
        "source_payload_receipts",
        "owner_source_withdrawals",
        "source_owner_repairs",
        "erasure_policy_bindings",
        "erasure_preparations",
        "erasure_body_bindings",
        "erasure_body_applications",
    ]
)
_NUMERIC = frozenset(
    [
        "semantic_claim_revisions",
        "user_model_claim_revisions",
        "world_model_claim_revisions",
        "satori_position_revisions",
        "satori_inclination_revisions",
        "affective_states",
        "relationship_states",
        "relationship_decisions",
        "relationship_transitions",
        "satori_personality_states",
        "satori_personality_traits",
        "personality_checkpoints",
        "personality_checkpoint_traits",
        "personality_revisions",
    ]
)
_BODY_FIELDS = {
    "conversation_messages": frozenset({"content"}),
    "episodic_memories": frozenset({"summary"}),
    "memory_evidence": frozenset({"quote"}),
    "semantic_claims": frozenset({"value", "normalized_value"}),
    "user_model_claims": frozenset({"value", "normalized_value"}),
    "world_model_claims": frozenset(
        {"value", "normalized_value", "subject_label", "normalized_subject_label"}
    ),
    "satori_positions": frozenset({"proposition", "normalized_proposition"}),
    "satori_position_evidence": frozenset({"quote", "normalized_signature"}),
    "satori_inclinations": frozenset(
        {"topic", "normalized_topic", "alternative_topic", "normalized_alternative_topic"}
    ),
    "reflection_proposals": frozenset({"payload"}),
    "affective_transitions": frozenset({"reason_codes"}),
    "personality_checkpoint_approvals": frozenset({"reason"}),
    "personality_restore_events": frozenset({"reason"}),
    "satori_identities": frozenset({"name"}),
    "satori_values": frozenset({"description"}),
    "audit_events": frozenset({"details"}),
}
_UNIT_FIELDS = frozenset(
    [
        "importance",
        "confidence",
        "prior_confidence",
        "new_confidence",
        "stability",
        "prior_stability",
        "new_stability",
        "appraisal_confidence",
        "decision_confidence",
        "value",
        "baseline_value",
        "before_value",
        "after_value",
        "arousal",
        "tension",
        "curiosity",
        "interest",
        "amusement",
        "concern",
        "frustration",
        "situational_confidence",
        "mood_energy",
        "mood_tension",
        "familiarity",
        "trust",
        "comfort",
        "closeness",
        "intellectual_respect",
        "affection",
        "strength",
    ]
)
_SIGNED_FIELDS = frozenset(
    ["score", "prior_score", "new_score", "applied_delta", "signal", "valence", "mood_valence"]
)
_HASH_FIELDS = frozenset(
    [
        "claim_key",
        "position_key",
        "inclination_key",
        "seed_content_hash",
        "source_set_hash",
        "run_key",
        "content_hash",
        "affective_signal_hash",
        "content_signature",
        "checkpoint_hash",
        "source_checkpoint_hash",
        "preview_digest",
        "event_digest",
        "reviewed_preview_digest",
        "approval_digest",
        "intent_schema_digest",
        "intent_store_digest",
        "intent_routing_digest",
        "schema_digest",
        "store_digest",
        "routing_digest",
        "retention_digest",
        "preparation_digest",
    ]
)
_V1_FIELDS = frozenset(
    [
        "schema_version",
        "identity_version",
        "seed_schema_version",
        "normalization_version",
        "formation_version",
        "policy_version",
        "target_policy_version",
        "emotion_policy_version",
        "appraisal_schema_version",
        "mood_policy_version",
        "personality_schema_version",
        "hash_schema_version",
        "contract_version",
    ]
)
_REVISION_CODES = {
    "semantic_claim_revisions": {
        "created": {"claim_created"},
        "strengthened": {"independent_evidence_added"},
        "superseded": {"explicit_correction", "newer_explicit_evidence"},
        "disputed": {"competing_inferences"},
        "retracted": {"source_support_withdrawn"},
    },
    "user_model_claim_revisions": {
        "created": {"claim_created"},
        "strengthened": {"independent_evidence_added"},
        "superseded": {"explicit_correction", "newer_explicit_evidence"},
        "disputed": {"competing_inferences"},
        "expired": {"freshness_window_elapsed"},
        "retracted": {"source_support_withdrawn"},
    },
    "world_model_claim_revisions": {
        "created": {"claim_created"},
        "strengthened": {"independent_evidence_added"},
        "superseded": {"explicit_correction", "newer_explicit_evidence"},
        "disputed": {"competing_inferences"},
        "expired": {"freshness_window_elapsed"},
        "retracted": {"source_support_withdrawn"},
    },
    "satori_position_revisions": {
        "created": {"eligible_position_created"},
        "strengthened": {"new_independent_material_evidence", "position_owner_accepted"},
        "weakened": {"new_independent_counterevidence", "position_owner_accepted"},
        "competing": {"supported_competing_hypothesis"},
        "superseded": {"explicit_stronger_revision"},
        "retracted": {"source_support_withdrawn"},
    },
    "satori_inclination_revisions": {
        "created": {"eligible_inclination_created"},
        "strengthened": {"inclination_strengthened"},
        "weakened": {"inclination_weakened"},
    },
}
_DECISION_FAMILIES = {
    "episode_formation_decisions": "episode_formation",
    "memory_lifecycle_decisions": "memory_lifecycle",
    "semantic_formation_decisions": "semantic_formation",
    "model_formation_decisions": "user_model_formation",
    "position_formation_decisions": "position_formation",
    "relationship_decisions": "relationship_transition",
}
_SOURCE_OWNERS = frozenset(
    ["semantic_memory", "current_models", "positions", "reflection_backlog", "relationship_backlog"]
)
_PAYLOAD_OWNERS = {
    "episode": ("episodic_memories", "memory_id", "aggregate_version"),
    "semantic_claim": ("semantic_claims", "claim_id", "aggregate_version"),
    "user_model_claim": ("user_model_claims", "claim_id", "aggregate_version"),
    "world_model_claim": ("world_model_claims", "claim_id", "aggregate_version"),
    "position": ("satori_positions", "position_id", "aggregate_version"),
    "inclination": ("satori_inclinations", "inclination_id", "aggregate_version"),
    "reflection_proposal": ("reflection_proposals", "proposal_id", None),
    "assistant_reply": ("conversation_interactions", "interaction_id", None),
}
