"""Pinned version-one physical row shapes, independent of live ORM declarations."""

ROW_FIELDS: dict[str, str] = {
    "affective_states": (
        "identity_id:s128 schema_version:i state_version:i mood_version:i as_of:t "
        "emotion_policy_version:i appraisal_schema_version:i mood_policy_version:i "
        "valence:f arousal:f tension:f curiosity:f interest:f amusement:f concern:f "
        "frustration:f situational_confidence:f mood_valence:f mood_energy:f "
        "mood_tension:f"
    ),
    "affective_transitions": (
        "transition_id:s128 identity_id:s128 interaction_id:s128 source_message_id:s128 "
        "trace_id:s128 appraisal_schema_version:i emotion_policy_version:i "
        "mood_policy_version:i base_state_version:i resulting_state_version:i "
        "base_mood_version:i resulting_mood_version:i appraised_at:t committed_at:t "
        "appraisal_confidence:f appraisal_payload:j source_refs:j reason_codes:j? "
        "applied_delta:j mood_delta:j state_before:j state_after:j provider:s128 "
        "model:s256 appraisal_method:s128 explanation_state:s16 "
        "explanation_action_id:s128? source_input_id:s128? audit_event_id:s128?"
    ),
    "alembic_version": ("version_num:s32"),
    "audit_events": (
        "event_id:s128 schema_version:i event_type:s64 aggregate_type:s64 "
        "aggregate_id:s128 occurred_at:t trace_id:s128 details:j"
    ),
    "audit_metadata_bindings": (
        "event_id:s128 identity_id:s128 contract_version:i family:s32 event_digest:s64 "
        "source_input_id:s128? episode_decision_id:s128? lifecycle_decision_id:s128? "
        "semantic_decision_id:s128? model_decision_id:s128? position_decision_id:s128? "
        "reflection_outcome_id:s128? relationship_decision_id:s128? "
        "checkpoint_approval_id:s128? restore_event_id:s128? "
        "withdrawal_receipt_id:s128?"
    ),
    "conversation_interactions": (
        "interaction_id:s128 session_id:s128 client_request_id:s128 trace_id:s128 "
        "schema_version:i status:s16 started_at:t completed_at:t? provider:s128? "
        "model:s256? finish_status:s64? input_tokens:i? output_tokens:i? "
        "context_schema_version:i? context_manifest_schema_version:i? policy_id:s128? "
        "policy_schema_version:i? retrieval_status:s64? retrieved_memory_ids:j? "
        "semantic_retrieval_status:s64? retrieved_semantic_claim_ids:j? "
        "emotion_appraisal_status:s64? emotion_context_schema_version:i? "
        "emotion_state_version:i? mood_state_version:i? emotion_state_as_of:t? "
        "relationship_context_schema_version:i? relationship_state_version:i? "
        "failure_kind:s128? failure_reason:s64? relationship_processing_required:b "
        "model_processing_required:b position_processing_required:b "
        "model_context_status:s32? user_model_context_schema_version:i? "
        "user_model_context_claim_ids:j? world_model_context_schema_version:i? "
        "world_model_context_claim_ids:j? position_context_status:s32? "
        "position_context_schema_version:i? position_context_ids:j? "
        "inclination_context_status:s32? inclination_context_schema_version:i? "
        "inclination_context_ids:j? inclination_curiosity_influence:f? "
        "personality_aggregate_version:i? personality_expression_schema_version:i? "
        "personality_expression_cues:j?"
    ),
    "conversation_messages": (
        "message_id:s128 session_id:s128 interaction_id:s128 schema_version:i role:s16 "
        "content:s131072? created_at:t sequence:i body_state:s16 body_action_id:s128?"
    ),
    "conversation_sessions": (
        "session_id:s128 identity_id:s128 counterparty_id:s128 scope_verified:b "
        "schema_version:i kind:s16 status:s16 started_at:t ended_at:t?"
    ),
    "episode_formation_decisions": (
        "decision_id:s128 idempotency_key:s256 source_interaction_id:s128 "
        "formation_version:i policy_version:i kind:s16 reason_code:s64 decided_at:t "
        "trace_id:s128 formation_method:s128 provider:s128 model:s256 memory_id:s128?"
    ),
    "episodic_memories": (
        "memory_id:s128 schema_version:i source_interaction_id:s128 occurred_at:t "
        "summary:s500? importance:f confidence:f created_at:t formation_method:s128 "
        "formation_version:i lifecycle_status:s32 aggregate_version:i body_state:s16 "
        "body_action_id:s128?"
    ),
    "episodic_memory_embeddings": (
        "embedding_id:s64 memory_id:s128 provider:s128 model:s256 dimensions:i "
        "input_schema_version:i vector:j indexed_at:t"
    ),
    "erasure_body_applications": (
        "action_id:s128 request_id:s128 identity_id:s128 root_message_id:s128? "
        "floor_id:s128 lineage_id:s128 body_revision:i action:j applied_at:t"
    ),
    "erasure_body_bindings": ("identity_id:s128 floor_id:s128 lineage_id:s128 body_revision:i"),
    "erasure_policy_bindings": (
        "identity_id:s128 floor_id:s128 lineage_id:s128 erasure_revision:i"
    ),
    "erasure_preparations": (
        "request_id:s128 preparation_revision:i owner:s64 decision_id:s128 "
        "identity_id:s128 counterparty_id:s128 root_message_id:s128 "
        "root_interaction_id:s128 root_session_id:s128 floor_id:s128 "
        "installation_id:s128 lineage_id:s128 policy_version:s128 retention_policy:s128 "
        "expected_source_generation:i expected_erasure_revision:i "
        "result_erasure_revision:i source_generation:i reviewed_preview_digest:s64 "
        "approval_digest:s64 intent_schema_revision:s128 intent_schema_digest:s64 "
        "intent_store_digest:s64 intent_routing_digest:s64 intent_assessment_status:s16 "
        "decided_at:t trace_id:s128 contract_version:i schema_revision:s128 "
        "schema_digest:s64 store_digest:s64 routing_digest:s64 retention_digest:s64 "
        "preparation_digest:s64 assessment_status:s16 body_readiness:s32 reason_codes:j "
        "counts:j prepared_at:t"
    ),
    "memory_evidence": (
        "evidence_id:s128 memory_id:s128 source_message_id:s128 provenance_kind:s64 "
        "quote:s500? observed_at:t body_state:s16 body_action_id:s128?"
    ),
    "memory_lifecycle_decisions": (
        "decision_id:s128 request_id:s128 memory_id:s128 identity_id:s128 "
        "counterparty_id:s128 from_status:s32 to_status:s32 expected_version:i "
        "result_version:i policy_version:i reason_code:s64 decided_at:t trace_id:s128 "
        "approved_by:s64 preview_digest:s64"
    ),
    "model_formation_decisions": (
        "decision_id:s128 idempotency_key:s256 source_interaction_id:s128 "
        "source_message_id:s128 identity_id:s128 counterparty_id:s128 "
        "formation_version:i policy_version:i kind:s16 reason_code:s64 "
        "user_created_count:i user_merged_count:i user_superseded_count:i "
        "user_disputed_count:i user_rejected_count:i world_created_count:i "
        "world_merged_count:i world_superseded_count:i world_disputed_count:i "
        "world_rejected_count:i user_claim_ids:j world_claim_ids:j decided_at:t "
        "trace_id:s128 formation_method:s128 provider:s128 model:s256"
    ),
    "owner_source_withdrawals": (
        "receipt_id:s128 owner:s64 aggregate_id:s128 request_id:s128 "
        "policy_generation:i before_status:s32 after_status:s32 before_version:i "
        "after_version:i root_message_ids:j recorded_at:t"
    ),
    "personality_checkpoint_approvals": (
        "approval_id:s128 identity_id:s128 checkpoint_id:s128 checkpoint_hash:s64 "
        "expected_aggregate_version:i reason:s500? reason_state:s16 "
        "reason_action_id:s128? approved_at:t"
    ),
    "personality_checkpoint_traits": ("checkpoint_id:s128 trait_key:s64 value:f baseline_value:f"),
    "personality_checkpoints": (
        "checkpoint_id:s128 identity_id:s128 personality_schema_version:i "
        "source_aggregate_version:i checkpoint_kind:s16 hash_schema_version:i "
        "checkpoint_hash:s64 created_at:t"
    ),
    "personality_evidence": (
        "evidence_id:s128 revision_id:s128 identity_id:s128 trait_key:s64 direction:s16 "
        "reflection_run_id:s128 reflection_proposal_id:s128 reflection_source_id:s128 "
        "evidence_edge_id:s128 evidence_edge_version:i root_interaction_id:s128 "
        "root_message_id:s128 root_session_id:s128 root_counterparty_id:s128 "
        "upstream_lineage_kind:s32 upstream_lineage_id:s128 content_hash:s64 "
        "normalized_signature:s64 citation_role:s16 observed_at:t accepted_at:t"
    ),
    "personality_restore_events": (
        "restore_id:s128 revision_id:s128 identity_id:s128 source_checkpoint_id:s128 "
        "source_checkpoint_hash:s64 resulting_checkpoint_id:s128 "
        "before_aggregate_version:i after_aggregate_version:i trait_diffs:j "
        "reason:s500? reason_state:s16 reason_action_id:s128? restored_at:t"
    ),
    "personality_revisions": (
        "revision_id:s128 identity_id:s128 revision_kind:s16 before_aggregate_version:i "
        "after_aggregate_version:i trait_key:s64? direction:s16? before_value:f? "
        "after_value:f? applied_delta:f? decision_confidence:f? policy_version:i "
        "reason_code:s64 source_checkpoint_id:s128 resulting_checkpoint_id:s128 "
        "reflection_outcome_id:s128? trait_diffs:j activation_distance_linf:f "
        "activation_distance_l1:f approved_checkpoint_distance_linf:f "
        "approved_checkpoint_distance_l1:f rolling_trait_path:f? rolling_total_path:f "
        "lifetime_trait_path:f? lifetime_total_path:f occurred_at:t"
    ),
    "position_formation_decisions": (
        "decision_id:s128 idempotency_key:s256 source_interaction_id:s128 "
        "source_message_id:s128 identity_id:s128 formation_version:i policy_version:i "
        "kind:s16 reason_code:s64 created_count:i merged_count:i superseded_count:i "
        "competing_count:i rejected_count:i position_ids:j decided_at:t trace_id:s128 "
        "formation_method:s128 provider:s128 model:s256"
    ),
    "reflection_attempts": (
        "attempt_id:s128 run_id:s128 ordinal:i status:s16 reason_code:s64 provider:s128 "
        "model:s256 formation_method:s128 started_at:t finished_at:t metrics:j"
    ),
    "reflection_outcomes": (
        "outcome_id:s128 proposal_id:s128 target_policy_version:i decision:s16 "
        "reason_code:s64 target_aggregate_type:s64? target_aggregate_id:s128? "
        "decided_at:t"
    ),
    "reflection_proposals": (
        "proposal_id:s128 run_id:s128 ordinal:i target_owner:s32 payload:j? "
        "evidence_source_ids:j created_at:t body_state:s16 body_action_id:s128?"
    ),
    "reflection_runs": (
        "run_id:s128 run_key:s64 identity_id:s128 schema_version:i policy_version:i "
        "purpose:s32 trigger_kind:s32 source_set_hash:s64 status:s32 "
        "aggregate_version:i attempt_count:i created_at:t updated_at:t completed_at:t?"
    ),
    "reflection_sources": (
        "source_id:s128 run_id:s128 ordinal:i kind:s32 evidence_edge_id:s128 "
        "evidence_edge_version:i root_interaction_id:s128 root_message_id:s128 "
        "root_counterparty_id:s128 observed_at:t content_hash:s64 "
        "upstream_lineage_kind:s32? upstream_lineage_id:s128? "
        "affective_transition_id:s128? affective_state_version:i? "
        "affective_signal_hash:s64?"
    ),
    "relationship_decisions": (
        "decision_id:s128 relationship_id:s128 interaction_id:s128 "
        "source_user_message_id:s128 session_id:s128 trace_id:s128 kind:s16 "
        "reason_code:s128 categories:j confidence:f provider:s128 model:s256 "
        "appraisal_method:s128 appraisal_schema_version:i policy_version:i decided_at:t "
        "transition_id:s128?"
    ),
    "relationship_states": (
        "relationship_id:s128 identity_id:s128 counterparty_id:s128 schema_version:i "
        "state_version:i policy_version:i familiarity:f trust:f comfort:f closeness:f "
        "intellectual_respect:f affection:f processed_interaction_count:i "
        "qualified_interaction_count:i distinct_session_count:i "
        "positive_evidence_count:i negative_evidence_count:i updated_at:t"
    ),
    "relationship_transitions": (
        "transition_id:s128 relationship_id:s128 interaction_id:s128 "
        "source_user_message_id:s128 session_id:s128 trace_id:s128 categories:j "
        "confidence:f base_state_version:i resulting_state_version:i state_before:j "
        "applied_delta:j state_after:j provider:s128 model:s256 appraisal_method:s128 "
        "appraisal_schema_version:i policy_version:i committed_at:t"
    ),
    "satori_identities": (
        "identity_id:s128 installation_slot:i name:s100 activation_time:t "
        "identity_version:i seed_id:s128 seed_schema_version:i seed_content_hash:s64"
    ),
    "satori_inclination_evidence": (
        "evidence_id:s128 inclination_id:s128 reflection_source_id:s128 "
        "affective_transition_id:s128 affective_state_version:i "
        "affective_signal_hash:s64 source_message_id:s128 source_interaction_id:s128 "
        "source_session_id:s128 source_counterparty_id:s128 content_hash:s64 "
        "content_signature:s64 role:s16 signal:f observed_at:t accepted_at:t"
    ),
    "satori_inclination_revisions": (
        "revision_id:s128 inclination_id:s128 inclination_version:i "
        "reflection_outcome_id:s128 kind:s32 prior_score:f? new_score:f applied_delta:f "
        "prior_confidence:f? new_confidence:f prior_stability:f? new_stability:f "
        "state_as_of:t reason_code:s64 occurred_at:t"
    ),
    "satori_inclinations": (
        "inclination_id:s128 inclination_key:s64 identity_id:s128 schema_version:i "
        "aggregate_version:i policy_version:i normalization_version:i kind:s16 "
        "topic:s96? normalized_topic:s96? alternative_topic:s96? "
        "normalized_alternative_topic:s96? score:f confidence:f stability:f "
        "state_as_of:t last_accepted_at:t created_at:t updated_at:t body_state:s16 "
        "body_action_id:s128?"
    ),
    "satori_personality_states": (
        "identity_id:s128 schema_version:i aggregate_version:i created_at:t"
    ),
    "satori_personality_traits": ("identity_id:s128 trait_key:s64 value:f baseline_value:f"),
    "satori_position_evidence": (
        "evidence_id:s128 position_id:s128 source_message_id:s128 "
        "source_interaction_id:s128 source_counterparty_id:s128 quote:s131072? "
        "normalized_signature:s512? role:s32 observed_at:t"
    ),
    "satori_position_revisions": (
        "revision_id:s128 position_id:s128 position_version:i decision_id:s128? "
        "reflection_outcome_id:s128? kind:s32 prior_status:s16? new_status:s16 "
        "prior_confidence:f? new_confidence:f reason_code:s64 occurred_at:t"
    ),
    "satori_positions": (
        "position_id:s128 position_key:s64 identity_id:s128 schema_version:i "
        "aggregate_version:i policy_version:i formation_version:i "
        "normalization_version:i proposition:s240? normalized_proposition:s240? "
        "kind:s16 stance:s16 confidence:f status:s16 value_key:s64? "
        "competing_with_position_id:s128? superseded_by_position_id:s128? created_at:t "
        "updated_at:t body_state:s16 body_action_id:s128?"
    ),
    "satori_value_sets": ("identity_id:s128 schema_version:i aggregate_version:i created_at:t"),
    "satori_values": ("identity_id:s128 value_key:s64 strength:f description:s500 origin:s32"),
    "semantic_claim_evidence": (
        "semantic_evidence_id:s128 claim_id:s128 memory_id:s128 memory_evidence_id:s128 "
        "root_message_id:s128 root_interaction_id:s128 source_kind:s64 observed_at:t"
    ),
    "semantic_claim_revisions": (
        "revision_id:s128 claim_id:s128 claim_version:i decision_id:s128 kind:s32 "
        "prior_status:s16? new_status:s16 prior_confidence:f? new_confidence:f "
        "reason_code:s64 occurred_at:t"
    ),
    "semantic_claims": (
        "claim_id:s128 claim_key:s64 schema_version:i aggregate_version:i subject:s64 "
        "predicate:s64 value_kind:s16 value:j? normalized_value:s500? polarity:b "
        "claim_kind:s32 confidence:f status:s16 valid_from:t valid_until:t? "
        "superseded_by_claim_id:s128? created_at:t updated_at:t formation_method:s128 "
        "formation_version:i normalization_version:i body_state:s16 "
        "body_action_id:s128?"
    ),
    "semantic_formation_decisions": (
        "decision_id:s128 idempotency_key:s256 source_memory_id:s128 "
        "formation_version:i policy_version:i kind:s16 reason_code:s64 created_count:i "
        "merged_count:i superseded_count:i disputed_count:i rejected_count:i "
        "claim_ids:j decided_at:t trace_id:s128 formation_method:s128 provider:s128 "
        "model:s256"
    ),
    "source_excluded_roots": ("message_id:s128 identity_id:s128 policy_generation:i"),
    "source_inputs": (
        "input_id:s128 identity_id:s128 policy_generation:i proof_status:s16 legacy:b "
        "root_message_ids:j payload_refs:j"
    ),
    "source_owner_repairs": ("owner:s64 identity_id:s128 policy_generation:i"),
    "source_payload_receipts": (
        "kind:s64 payload_id:s128 payload_version:i identity_id:s128 "
        "policy_generation:i proof_status:s16 legacy:b root_message_ids:j "
        "payload_refs:j eligible:b"
    ),
    "source_policy_bindings": (
        "identity_id:s128 installation_id:s128 floor_id:s128 lineage_id:s128 policy_generation:i"
    ),
    "user_model_claim_evidence": (
        "evidence_id:s128 claim_id:s128 source_message_id:s128 "
        "source_interaction_id:s128 observed_at:t"
    ),
    "user_model_claim_revisions": (
        "revision_id:s128 claim_id:s128 claim_version:i decision_id:s128 kind:s32 "
        "prior_status:s16? new_status:s16 prior_confidence:f? new_confidence:f "
        "prior_expires_at:t? new_expires_at:t? reason_code:s64 occurred_at:t"
    ),
    "user_model_claims": (
        "claim_id:s128 claim_key:s64 identity_id:s128 counterparty_id:s128 "
        "schema_version:i aggregate_version:i policy_version:i formation_version:i "
        "normalization_version:i predicate:s64 value_kind:s16 value:j? "
        "normalized_value:s160? epistemic_kind:s32 confidence:f status:s16 valid_from:t "
        "valid_until:t? last_observed_at:t expires_at:t? superseded_by_claim_id:s128? "
        "created_at:t updated_at:t body_state:s16 body_action_id:s128?"
    ),
    "world_model_claim_evidence": (
        "evidence_id:s128 claim_id:s128 source_message_id:s128 "
        "source_interaction_id:s128 observed_at:t"
    ),
    "world_model_claim_revisions": (
        "revision_id:s128 claim_id:s128 claim_version:i decision_id:s128 kind:s32 "
        "prior_status:s16? new_status:s16 prior_confidence:f? new_confidence:f "
        "prior_expires_at:t? new_expires_at:t? reason_code:s64 occurred_at:t"
    ),
    "world_model_claims": (
        "claim_id:s128 claim_key:s64 identity_id:s128 counterparty_id:s128 "
        "schema_version:i aggregate_version:i policy_version:i formation_version:i "
        "normalization_version:i subject_kind:s32 subject_label:s120? "
        "normalized_subject_label:s120? predicate:s64 value_kind:s16 value:j? "
        "normalized_value:s160? epistemic_kind:s32 confidence:f status:s16 valid_from:t "
        "valid_until:t? last_observed_at:t expires_at:t? superseded_by_claim_id:s128? "
        "created_at:t updated_at:t body_state:s16 body_action_id:s128?"
    ),
}

FOREIGN_KEYS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "affective_states": (("identity_id", "satori_identities", "identity_id"),),
    "affective_transitions": (
        ("identity_id", "satori_identities", "identity_id"),
        ("interaction_id", "conversation_interactions", "interaction_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("explanation_action_id", "erasure_body_applications", "action_id"),
        ("source_input_id", "source_inputs", "input_id"),
        ("audit_event_id", "audit_events", "event_id"),
    ),
    "audit_metadata_bindings": (
        ("event_id", "audit_events", "event_id"),
        ("identity_id", "satori_identities", "identity_id"),
        ("source_input_id", "source_inputs", "input_id"),
        ("episode_decision_id", "episode_formation_decisions", "decision_id"),
        ("lifecycle_decision_id", "memory_lifecycle_decisions", "decision_id"),
        ("semantic_decision_id", "semantic_formation_decisions", "decision_id"),
        ("model_decision_id", "model_formation_decisions", "decision_id"),
        ("position_decision_id", "position_formation_decisions", "decision_id"),
        ("reflection_outcome_id", "reflection_outcomes", "outcome_id"),
        ("relationship_decision_id", "relationship_decisions", "decision_id"),
        ("checkpoint_approval_id", "personality_checkpoint_approvals", "approval_id"),
        ("restore_event_id", "personality_restore_events", "restore_id"),
        ("withdrawal_receipt_id", "owner_source_withdrawals", "receipt_id"),
    ),
    "conversation_interactions": (("session_id", "conversation_sessions", "session_id"),),
    "conversation_messages": (("body_action_id", "erasure_body_applications", "action_id"),),
    "conversation_sessions": (("identity_id", "satori_identities", "identity_id"),),
    "episode_formation_decisions": (
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
        ("memory_id", "episodic_memories", "memory_id"),
    ),
    "episodic_memories": (
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "episodic_memory_embeddings": (("memory_id", "episodic_memories", "memory_id"),),
    "erasure_body_applications": (
        ("identity_id", "satori_identities", "identity_id"),
        ("root_message_id", "conversation_messages", "message_id"),
    ),
    "erasure_preparations": (
        ("identity_id", "satori_identities", "identity_id"),
        ("root_message_id", "conversation_messages", "message_id"),
        ("root_interaction_id", "conversation_interactions", "interaction_id"),
        ("root_session_id", "conversation_sessions", "session_id"),
    ),
    "memory_evidence": (
        ("memory_id", "episodic_memories", "memory_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "memory_lifecycle_decisions": (
        ("memory_id", "episodic_memories", "memory_id"),
        ("identity_id", "satori_identities", "identity_id"),
    ),
    "model_formation_decisions": (
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("identity_id", "satori_identities", "identity_id"),
    ),
    "personality_checkpoint_approvals": (
        ("identity_id", "satori_personality_states", "identity_id"),
        ("checkpoint_id", "personality_checkpoints", "checkpoint_id"),
        ("reason_action_id", "erasure_body_applications", "action_id"),
    ),
    "personality_checkpoint_traits": (
        ("checkpoint_id", "personality_checkpoints", "checkpoint_id"),
    ),
    "personality_checkpoints": (("identity_id", "satori_personality_states", "identity_id"),),
    "personality_evidence": (
        ("revision_id", "personality_revisions", "revision_id"),
        ("identity_id", "satori_personality_states", "identity_id"),
        ("reflection_run_id", "reflection_runs", "run_id"),
        ("reflection_proposal_id", "reflection_proposals", "proposal_id"),
        ("reflection_source_id", "reflection_sources", "source_id"),
        ("root_interaction_id", "conversation_interactions", "interaction_id"),
        ("root_message_id", "conversation_messages", "message_id"),
        ("root_session_id", "conversation_sessions", "session_id"),
    ),
    "personality_restore_events": (
        ("revision_id", "personality_revisions", "revision_id"),
        ("identity_id", "satori_personality_states", "identity_id"),
        ("source_checkpoint_id", "personality_checkpoints", "checkpoint_id"),
        ("resulting_checkpoint_id", "personality_checkpoints", "checkpoint_id"),
        ("reason_action_id", "erasure_body_applications", "action_id"),
    ),
    "personality_revisions": (
        ("identity_id", "satori_personality_states", "identity_id"),
        ("source_checkpoint_id", "personality_checkpoints", "checkpoint_id"),
        ("resulting_checkpoint_id", "personality_checkpoints", "checkpoint_id"),
        ("reflection_outcome_id", "reflection_outcomes", "outcome_id"),
    ),
    "position_formation_decisions": (
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("identity_id", "satori_identities", "identity_id"),
    ),
    "reflection_attempts": (("run_id", "reflection_runs", "run_id"),),
    "reflection_outcomes": (("proposal_id", "reflection_proposals", "proposal_id"),),
    "reflection_proposals": (
        ("run_id", "reflection_runs", "run_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "reflection_runs": (("identity_id", "satori_identities", "identity_id"),),
    "reflection_sources": (
        ("run_id", "reflection_runs", "run_id"),
        ("root_interaction_id", "conversation_interactions", "interaction_id"),
        ("root_message_id", "conversation_messages", "message_id"),
        ("affective_transition_id", "affective_transitions", "transition_id"),
    ),
    "relationship_decisions": (
        ("relationship_id", "relationship_states", "relationship_id"),
        ("interaction_id", "conversation_interactions", "interaction_id"),
        ("source_user_message_id", "conversation_messages", "message_id"),
    ),
    "relationship_states": (("identity_id", "satori_identities", "identity_id"),),
    "relationship_transitions": (
        ("relationship_id", "relationship_states", "relationship_id"),
        ("interaction_id", "conversation_interactions", "interaction_id"),
        ("source_user_message_id", "conversation_messages", "message_id"),
    ),
    "satori_inclination_evidence": (
        ("inclination_id", "satori_inclinations", "inclination_id"),
        ("reflection_source_id", "reflection_sources", "source_id"),
        ("affective_transition_id", "affective_transitions", "transition_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
        ("source_session_id", "conversation_sessions", "session_id"),
    ),
    "satori_inclination_revisions": (
        ("inclination_id", "satori_inclinations", "inclination_id"),
        ("reflection_outcome_id", "reflection_outcomes", "outcome_id"),
    ),
    "satori_inclinations": (
        ("identity_id", "satori_identities", "identity_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "satori_personality_states": (("identity_id", "satori_identities", "identity_id"),),
    "satori_personality_traits": (("identity_id", "satori_personality_states", "identity_id"),),
    "satori_position_evidence": (
        ("position_id", "satori_positions", "position_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
    ),
    "satori_position_revisions": (
        ("position_id", "satori_positions", "position_id"),
        ("decision_id", "position_formation_decisions", "decision_id"),
        ("reflection_outcome_id", "reflection_outcomes", "outcome_id"),
    ),
    "satori_positions": (
        ("identity_id", "satori_identities", "identity_id"),
        ("competing_with_position_id", "satori_positions", "position_id"),
        ("superseded_by_position_id", "satori_positions", "position_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "satori_value_sets": (("identity_id", "satori_identities", "identity_id"),),
    "satori_values": (("identity_id", "satori_value_sets", "identity_id"),),
    "semantic_claim_evidence": (
        ("claim_id", "semantic_claims", "claim_id"),
        ("memory_id", "episodic_memories", "memory_id"),
        ("memory_evidence_id", "memory_evidence", "evidence_id"),
        ("root_message_id", "conversation_messages", "message_id"),
        ("root_interaction_id", "conversation_interactions", "interaction_id"),
    ),
    "semantic_claim_revisions": (
        ("claim_id", "semantic_claims", "claim_id"),
        ("decision_id", "semantic_formation_decisions", "decision_id"),
    ),
    "semantic_claims": (
        ("superseded_by_claim_id", "semantic_claims", "claim_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "semantic_formation_decisions": (("source_memory_id", "episodic_memories", "memory_id"),),
    "user_model_claim_evidence": (
        ("claim_id", "user_model_claims", "claim_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
    ),
    "user_model_claim_revisions": (
        ("claim_id", "user_model_claims", "claim_id"),
        ("decision_id", "model_formation_decisions", "decision_id"),
    ),
    "user_model_claims": (
        ("identity_id", "satori_identities", "identity_id"),
        ("superseded_by_claim_id", "user_model_claims", "claim_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
    "world_model_claim_evidence": (
        ("claim_id", "world_model_claims", "claim_id"),
        ("source_message_id", "conversation_messages", "message_id"),
        ("source_interaction_id", "conversation_interactions", "interaction_id"),
    ),
    "world_model_claim_revisions": (
        ("claim_id", "world_model_claims", "claim_id"),
        ("decision_id", "model_formation_decisions", "decision_id"),
    ),
    "world_model_claims": (
        ("identity_id", "satori_identities", "identity_id"),
        ("superseded_by_claim_id", "world_model_claims", "claim_id"),
        ("body_action_id", "erasure_body_applications", "action_id"),
    ),
}

ENUM_FIELDS: dict[tuple[str, str], frozenset[str]] = {
    ("conversation_interactions", "inclination_context_status"): frozenset(
        ("not_requested", "empty", "available")
    ),
    ("conversation_interactions", "status"): frozenset(("pending", "failed", "completed")),
    ("conversation_messages", "role"): frozenset(("user", "assistant")),
    ("conversation_sessions", "kind"): frozenset(("explicit", "implicit")),
    ("conversation_sessions", "status"): frozenset(("open", "closed")),
    ("episode_formation_decisions", "kind"): frozenset(("created", "skipped", "rejected")),
    ("episodic_memories", "lifecycle_status"): frozenset(("active", "archived")),
    ("erasure_preparations", "assessment_status"): frozenset(("complete", "blocked")),
    ("erasure_preparations", "body_readiness"): frozenset(
        ("retain_only", "blocked_executor", "blocked_owner_policy", "blocked_provenance")
    ),
    ("erasure_preparations", "intent_assessment_status"): frozenset(("complete", "blocked")),
    ("erasure_preparations", "owner"): frozenset(
        (
            "interaction_log",
            "memory_manager",
            "retrieval_index",
            "semantic_memory",
            "current_models",
            "positions",
            "reflection",
            "affect",
            "relationship",
            "personality",
            "audit",
            "initial_self",
            "initial_values",
            "source_policy",
            "schema_registry",
        )
    ),
    ("memory_evidence", "provenance_kind"): frozenset(("explicit_user_statement",)),
    ("memory_lifecycle_decisions", "approved_by"): frozenset(("local_operator",)),
    ("memory_lifecycle_decisions", "from_status"): frozenset(("active", "archived")),
    ("memory_lifecycle_decisions", "to_status"): frozenset(("active", "archived")),
    ("model_formation_decisions", "kind"): frozenset(("applied", "skipped", "rejected")),
    ("personality_checkpoints", "checkpoint_kind"): frozenset(
        ("activation", "evolution", "restore", "manual")
    ),
    ("personality_evidence", "citation_role"): frozenset(("support", "counterevidence")),
    ("personality_evidence", "direction"): frozenset(("increase", "decrease")),
    ("personality_evidence", "upstream_lineage_kind"): frozenset(("position", "episodic_memory")),
    ("personality_revisions", "direction"): frozenset(("increase", "decrease")),
    ("personality_revisions", "revision_kind"): frozenset(("evolution", "restore")),
    ("position_formation_decisions", "kind"): frozenset(("applied", "skipped", "rejected")),
    ("reflection_attempts", "status"): frozenset(("succeeded", "failed")),
    ("reflection_outcomes", "decision"): frozenset(("accepted", "rejected")),
    ("reflection_proposals", "target_owner"): frozenset(
        ("satori_positions", "satori_inclinations", "personality", "values")
    ),
    ("reflection_runs", "purpose"): frozenset(("general", "personality_evolution")),
    ("reflection_runs", "status"): frozenset(
        (
            "pending_generation",
            "proposals_ready",
            "applying",
            "completed",
            "retryable_failure",
            "exhausted",
        )
    ),
    ("reflection_runs", "trigger_kind"): frozenset(("automatic", "explicit_local")),
    ("reflection_sources", "kind"): frozenset(("position_evidence", "episodic_memory_evidence")),
    ("reflection_sources", "upstream_lineage_kind"): frozenset(("position", "episodic_memory")),
    ("relationship_decisions", "kind"): frozenset(("applied", "skipped", "rejected")),
    ("satori_inclination_evidence", "role"): frozenset(("topic", "option_a", "option_b")),
    ("satori_inclination_revisions", "kind"): frozenset(("created", "strengthened", "weakened")),
    ("satori_inclinations", "kind"): frozenset(("interest", "preference")),
    ("satori_position_evidence", "role"): frozenset(
        ("argument", "observation", "counterexample", "verified_record")
    ),
    ("satori_position_revisions", "kind"): frozenset(
        ("created", "strengthened", "weakened", "competing", "superseded", "retracted")
    ),
    ("satori_positions", "kind"): frozenset(("fact", "belief", "opinion", "hypothesis")),
    ("satori_positions", "stance"): frozenset(("support", "oppose", "uncertain")),
    ("satori_positions", "status"): frozenset(("active", "competing", "superseded", "retracted")),
    ("semantic_claim_evidence", "source_kind"): frozenset(
        ("explicit_user_statement", "episode_inference")
    ),
    ("semantic_claim_revisions", "kind"): frozenset(
        ("created", "strengthened", "superseded", "disputed", "retracted")
    ),
    ("semantic_claims", "claim_kind"): frozenset(
        ("explicit_fact", "inferred_fact", "hypothesis", "attributed_statement")
    ),
    ("semantic_claims", "status"): frozenset(("active", "superseded", "disputed", "retracted")),
    ("semantic_claims", "subject"): frozenset(("user",)),
    ("semantic_claims", "value_kind"): frozenset(("text", "number", "boolean")),
    ("semantic_formation_decisions", "kind"): frozenset(("applied", "skipped", "rejected")),
    ("source_inputs", "proof_status"): frozenset(("complete", "unknown", "overflow")),
    ("source_payload_receipts", "proof_status"): frozenset(("complete", "unknown", "overflow")),
    ("user_model_claims", "value_kind"): frozenset(("text", "number", "boolean")),
    ("world_model_claims", "predicate"): frozenset(("status",)),
    ("world_model_claims", "subject_kind"): frozenset(
        ("project", "situation", "commitment", "outcome")
    ),
    ("world_model_claims", "value_kind"): frozenset(("text",)),
}
