"""CLI for activation, persistent conversations, history, and memory debug."""

import argparse
import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from satori.application.affect.use_cases import AffectiveAppraisalProvider
from satori.application.conversation.contracts import TalkInput
from satori.application.conversation.errors import ConversationError
from satori.application.conversation.use_cases import ConversationProvider
from satori.application.erasure.audit_contracts import AuditMetadataStatusRequest
from satori.application.erasure.audit_use_cases import AuditMetadataReader
from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.application.erasure.body_use_cases import BodyErasureManager
from satori.application.erasure.contracts import ErasureCheckRequest, ErasurePreviewRequest
from satori.application.erasure.intent_contracts import (
    ErasureIntentCommand,
    ErasureIntentRequest,
)
from satori.application.erasure.intent_use_cases import ErasureIntentManager
from satori.application.erasure.use_cases import ErasurePlanner
from satori.application.memory.lifecycle_contracts import (
    MemoryLifecycleCommand,
    MemoryLifecycleError,
)
from satori.application.memory.use_cases import EpisodeFormationProvider
from satori.application.models.use_cases import ModelFormationProvider
from satori.application.personality.use_cases import (
    PersonalityCheckpointApprovalProposal,
    PersonalityCheckpointComparison,
    PersonalityInspection,
)
from satori.application.positions.use_cases import PositionFormationProvider
from satori.application.recovery.contracts import RecoveryError
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.application.reflection.ports import ReflectionGenerationPort
from satori.application.relationship.use_cases import RelationshipProvider
from satori.application.retrieval.contracts import RetrievalQuery
from satori.application.retrieval.use_cases import EmbeddingProvider
from satori.application.semantic.use_cases import SemanticFormationProvider
from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.appraisal_evaluation import run_appraisal_model_evaluation
from satori.bootstrap import bootstrap
from satori.composition import (
    InitialSelfServices,
    build_conversation_services,
    build_initial_self_services,
    build_memory_lifecycle_services,
)
from satori.config import (
    ConversationProviderKind,
    EmbeddingProviderKind,
    Environment,
    LogLevel,
    Settings,
    load_settings,
)
from satori.contention_evaluation import (
    OllamaContentionAdapters,
    run_contention_evaluation,
)
from satori.core.clock import SystemClock
from satori.core.conversation import ConversationProviderError
from satori.core.ids import Uuid4Generator
from satori.core.models import ModelFormationProviderError
from satori.core.personality import PersonalityRestoreProposal
from satori.core.positions import PositionFormationProviderError
from satori.core.reflection import ReflectionProviderError, ReflectionPurpose, ReflectionSource
from satori.core.relationship import RelationshipAppraisalProviderError
from satori.core.semantic import SemanticFormationProviderError
from satori.domain.errors import AlreadyActivated, NotActivated
from satori.domain.identity import Identity
from satori.domain.inclinations import SatoriInclination, materialize_inclination_score
from satori.domain.memory import MemoryLifecycleStatus
from satori.domain.memory_scope import MemoryScope
from satori.domain.models import ModelClaimRevision, UserModelClaim, WorldModelClaim
from satori.domain.positions import PositionRevision, SatoriPosition
from satori.domain.reflection import ReflectionTriggerKind
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure import SQLiteErasureInventory
from satori.infrastructure.persistence.erasure_database import open_existing_erasure_database
from satori.infrastructure.persistence.erasure_preparation import SQLiteErasurePreparationStore
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from satori.infrastructure.persistence.source_policy import verify_mirror
from satori.infrastructure.providers.inference_scheduler import OllamaInferenceScheduler
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.ollama_affect import OllamaAffectiveAppraisalAdapter
from satori.infrastructure.providers.ollama_embedding import OllamaEmbeddingAdapter
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter
from satori.infrastructure.providers.ollama_http import OllamaHttpClient
from satori.infrastructure.providers.ollama_models import OllamaModelFormationAdapter
from satori.infrastructure.providers.ollama_positions import OllamaPositionFormationAdapter
from satori.infrastructure.providers.ollama_reflection import OllamaReflectionAdapter
from satori.infrastructure.providers.ollama_relationship import OllamaRelationshipAppraisalAdapter
from satori.infrastructure.providers.ollama_semantic import OllamaSemanticFormationAdapter
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from satori.infrastructure.providers.yandex_ai_studio import (
    YandexAIStudioConversationAdapter,
)
from satori.infrastructure.providers.yandex_ai_studio_http import (
    YandexAIStudioHttpClient,
)
from satori.infrastructure.seeds.loader import JsonSeedLoader
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    canonical_root,
    enroll_installation,
    installation_identity,
    repair_installation,
)
from satori.infrastructure.source_policy.ledger import InstallationPolicyLedger
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from satori.interactive import InteractiveChat
from satori.observability.logging import bind_trace_id, configure_logging
from satori.performance import run_inference_benchmark


def _counterparty_id_argument(value: str) -> str:
    """Normalize a non-empty opaque counterparty partition from the CLI."""

    normalized = value.strip()
    if not normalized:
        raise argparse.ArgumentTypeError("counterparty ID must not be blank")
    return normalized


def _nonnegative_int_argument(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be nonnegative")
    return parsed


def _positive_int_argument(value: str) -> int:
    """Parse a strictly positive integer for bounded CLI reads."""

    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _sha256_argument(value: str) -> str:
    """Validate one explicit lowercase canonical SHA-256 checkpoint hash."""

    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise argparse.ArgumentTypeError("value must be a lowercase SHA-256 hash")
    return value


def _aware_datetime_argument(value: str) -> datetime:
    """Parse an explicit timezone-aware ISO-8601 materialization time."""

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include an explicit timezone")
    return parsed


def _erasure_intent_request_id_argument(value: str) -> str:
    if not value or len(value) > 128 or value.strip() != value:
        raise argparse.ArgumentTypeError("request ID must be nonblank and at most 128 characters")
    return value


def _safe_source_policy_reason(error: SourcePolicyError) -> str:
    reason = error.reason_code
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in reason):
        return "policy_rejection"
    return reason


def _erasure_intent_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """Existing-only operator controls; never bootstrap providers or erase content."""
    database: Database | None = None
    try:
        if arguments.source_action == "erasure-policy":
            if not arguments.upgrade or not arguments.reviewed:
                raise SourcePolicyError("erasure_policy_upgrade_requires_review")
            database = open_existing_erasure_database(settings.database_url)
            runtime = SourcePolicyRuntime(
                settings.source_policy_directory, installation_identity(database)
            )
            snapshot = runtime.upgrade_policy()
            print(
                json.dumps(
                    {
                        "operation": "upgrade_erasure_policy",
                        "floor_id": snapshot.floor_id,
                        "lineage_id": snapshot.lineage_id,
                        "generation": snapshot.generation,
                        "erasure_revision": snapshot.erasure_revision,
                        "physical_erasure": False,
                        "execution_authorized": False,
                    },
                    indent=2,
                )
            )
            return 0
        review_fields = (
            arguments.expected_generation,
            arguments.expected_erasure_revision,
            arguments.sha256,
        )
        if arguments.preview and (
            any(value is not None for value in review_fields) or arguments.request_id is not None
        ):
            raise SourcePolicyError("erasure_intent_preview_does_not_commit")
        if arguments.commit and (
            any(value is None for value in review_fields) or arguments.request_id is None
        ):
            raise SourcePolicyError("erasure_intent_commit_requires_reviewed_state_request")
        if (arguments.resume or arguments.status) and (
            arguments.request_id is None or any(value is not None for value in review_fields)
        ):
            raise SourcePolicyError("erasure_intent_receipt_requires_request_only")
        request = ErasureIntentRequest(
            message_id=arguments.message_id,
            counterparty_id=settings.default_counterparty_id,
        )
        database = open_existing_erasure_database(
            settings.database_url, writable=arguments.commit or arguments.resume
        )
        active_database = database
        runtime = SourcePolicyRuntime(
            settings.source_policy_directory, installation_identity(active_database)
        )

        def verify(snapshot: SourceEligibilitySnapshot) -> None:
            with active_database.session_factory() as session:
                verify_mirror(session, snapshot)

        runtime.verify_mirror = verify
        active_database.session_factory.configure(info={"source_runtime": runtime})
        manager = ErasureIntentManager(
            runtime,
            SQLiteErasureInventory(active_database),
            SQLiteErasurePreparationStore(active_database),
        )
        if arguments.preview:
            preview = manager.preview(request)
            print(preview.model_dump_json(indent=2))
            return 0
        if arguments.commit:
            result = manager.commit(
                ErasureIntentCommand(
                    message_id=request.message_id,
                    counterparty_id=request.counterparty_id,
                    expected_generation=arguments.expected_generation,
                    expected_erasure_revision=arguments.expected_erasure_revision,
                    approval_digest=arguments.sha256,
                    request_id=arguments.request_id,
                )
            )
        elif arguments.resume:
            result = manager.resume(request, request_id=arguments.request_id)
        else:
            result = manager.status(request, request_id=arguments.request_id)
        print(result.model_dump_json(indent=2))
        return 0 if result.main_projection_current else 2
    except SourcePolicyError as error:
        print(f"Erasure intent rejected: {_safe_source_policy_reason(error)}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Erasure intent rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Erasure intent rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Erasure intent unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


def _audit_metadata_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """Scoped read-only retention proof; no provider, migration or mutation path."""
    from satori.infrastructure.persistence.audit_metadata_inventory import (
        SQLiteAuditMetadataInventory,
    )

    database: Database | None = None
    try:
        database = open_existing_erasure_database(settings.database_url)
        runtime = bind_installation(database, settings.source_policy_directory)
        reader = AuditMetadataReader(runtime, SQLiteAuditMetadataInventory(database))
        result = reader.execute(
            AuditMetadataStatusRequest(
                message_id=arguments.message_id,
                counterparty_id=settings.default_counterparty_id,
            )
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.coverage == "complete" else 2
    except SourcePolicyError as error:
        print(f"Audit metadata status rejected: {error.reason_code}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Audit metadata status rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Audit metadata status rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Audit metadata status unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


def _erasure_preview_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """Read-only metadata branch: no migrations, enrollment, providers or domain commits."""
    database: Database | None = None
    try:
        if arguments.preview and (
            arguments.expected_generation is not None or arguments.sha256 is not None
        ):
            raise SourcePolicyError("erasure_preview_does_not_apply")
        if arguments.check and (arguments.expected_generation is None or arguments.sha256 is None):
            raise SourcePolicyError("erasure_check_requires_generation_digest")
        database = open_existing_erasure_database(settings.database_url)
        runtime = bind_installation(database, settings.source_policy_directory)
        planner = ErasurePlanner(runtime, SQLiteErasureInventory(database))
        if arguments.preview:
            preview = planner.preview(
                ErasurePreviewRequest(
                    message_id=arguments.message_id,
                    counterparty_id=settings.default_counterparty_id,
                )
            )
            print(preview.model_dump_json(indent=2))
            return 0 if preview.assessment_status == "complete" else 2
        result = planner.check(
            ErasureCheckRequest(
                message_id=arguments.message_id,
                counterparty_id=settings.default_counterparty_id,
                expected_generation=arguments.expected_generation,
                preview_digest=arguments.sha256,
            )
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.assessment_status == "complete" else 2
    except SourcePolicyError as error:
        print(f"Erasure preview rejected: {error.reason_code}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Erasure preview rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Erasure preview rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Erasure preview unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


def _erasure_body_command(arguments: argparse.Namespace, settings: Settings) -> int:
    database: Database | None = None
    try:
        if arguments.source_action in {
            "erasure-body-policy",
            "erasure-dialogue-policy",
            "erasure-knowledge-policy",
            "erasure-reflection-policy",
            "erasure-appraisal-policy",
        }:
            database = open_existing_erasure_database(settings.database_url)
            runtime = SourcePolicyRuntime(
                settings.source_policy_directory, installation_identity(database)
            )
            snapshot = (
                runtime.upgrade_appraisal_policy()
                if arguments.source_action == "erasure-appraisal-policy"
                else runtime.upgrade_reflection_policy()
                if arguments.source_action == "erasure-reflection-policy"
                else runtime.upgrade_knowledge_policy()
                if arguments.source_action == "erasure-knowledge-policy"
                else runtime.upgrade_dialogue_policy()
                if arguments.source_action == "erasure-dialogue-policy"
                else runtime.upgrade_body_policy()
            )
            print(
                json.dumps(
                    {
                        "operation": "upgrade_appraisal_body_policy"
                        if arguments.source_action == "erasure-appraisal-policy"
                        else "upgrade_reflection_body_policy"
                        if arguments.source_action == "erasure-reflection-policy"
                        else "upgrade_knowledge_body_policy"
                        if arguments.source_action == "erasure-knowledge-policy"
                        else "upgrade_dialogue_body_policy"
                        if arguments.source_action == "erasure-dialogue-policy"
                        else "upgrade_episode_body_policy",
                        "body_revision": snapshot.body_revision,
                        "physical_erasure": False,
                    },
                    indent=2,
                )
            )
            return 0
        review = (
            arguments.expected_generation,
            arguments.expected_erasure_revision,
            arguments.expected_body_revision,
            arguments.sha256,
        )
        if arguments.preview and (
            arguments.request_id is not None or any(value is not None for value in review)
        ):
            raise SourcePolicyError("body_preview_does_not_apply")
        if arguments.apply and (
            arguments.request_id is None or any(value is None for value in review)
        ):
            raise SourcePolicyError("body_apply_requires_fresh_review")
        if (arguments.resume or arguments.status) and (
            arguments.request_id is None or any(value is not None for value in review)
        ):
            raise SourcePolicyError("body_receipt_requires_request_only")
        request = BodyErasureRequest(
            message_id=arguments.message_id,
            counterparty_id=settings.default_counterparty_id,
            intent_request_id=arguments.intent_request_id,
        )
        database = open_existing_erasure_database(
            settings.database_url, writable=arguments.apply or arguments.resume
        )
        runtime = SourcePolicyRuntime(
            settings.source_policy_directory, installation_identity(database)
        )
        from satori.application.erasure.appraisal_use_cases import AppraisalBodyManager
        from satori.application.erasure.dialogue_use_cases import DialogueBodyManager
        from satori.application.erasure.knowledge_use_cases import KnowledgeBodyManager
        from satori.application.erasure.reflection_use_cases import ReflectionBodyManager
        from satori.infrastructure.persistence.erasure_appraisal import SQLiteAppraisalBodyStore
        from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore
        from satori.infrastructure.persistence.erasure_dialogue import SQLiteDialogueBodyStore
        from satori.infrastructure.persistence.erasure_knowledge import SQLiteKnowledgeBodyStore
        from satori.infrastructure.persistence.erasure_reflection import SQLiteReflectionBodyStore

        manager: (
            BodyErasureManager
            | DialogueBodyManager
            | KnowledgeBodyManager
            | ReflectionBodyManager
            | AppraisalBodyManager
        )
        manager = (
            AppraisalBodyManager(runtime, SQLiteAppraisalBodyStore(database))
            if arguments.source_action == "erasure-appraisal"
            else ReflectionBodyManager(runtime, SQLiteReflectionBodyStore(database))
            if arguments.source_action == "erasure-reflection"
            else KnowledgeBodyManager(runtime, SQLiteKnowledgeBodyStore(database))
            if arguments.source_action == "erasure-knowledge"
            else DialogueBodyManager(runtime, SQLiteDialogueBodyStore(database))
            if arguments.source_action == "erasure-dialogue"
            else BodyErasureManager(runtime, SQLiteBodyErasureStore(database))
        )
        if arguments.preview:
            preview = manager.preview(request)
            print(preview.model_dump_json(indent=2))
            return 0 if preview.readiness == "ready" else 2
        if arguments.apply:
            result = manager.commit(
                BodyErasureCommand(
                    **request.model_dump(),
                    request_id=arguments.request_id,
                    expected_generation=arguments.expected_generation,
                    expected_erasure_revision=arguments.expected_erasure_revision,
                    expected_body_revision=arguments.expected_body_revision,
                    approval_digest=arguments.sha256,
                )
            )
        elif arguments.resume:
            result = manager.resume(request, request_id=arguments.request_id)
        else:
            result = manager.status(request, request_id=arguments.request_id)
        print(result.model_dump_json(indent=2))
        return 0 if result.main_projection_current else 2
    except SourcePolicyError as error:
        print(f"Body erasure rejected: {_safe_source_policy_reason(error)}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Body erasure rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Body erasure rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Body erasure unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()


def _source_policy_command(
    arguments: argparse.Namespace, settings: Settings, *, alembic_config: Path
) -> int:
    from dataclasses import asdict

    if arguments.source_action == "audit-metadata":
        return _audit_metadata_command(arguments, settings)
    if arguments.source_action == "erasure":
        return _erasure_preview_command(arguments, settings)
    if arguments.source_action in {"erasure-policy", "erasure-intent"}:
        return _erasure_intent_command(arguments, settings)
    if arguments.source_action in {
        "erasure-body-policy",
        "erasure-bodies",
        "erasure-dialogue-policy",
        "erasure-dialogue",
        "erasure-knowledge-policy",
        "erasure-knowledge",
        "erasure-reflection-policy",
        "erasure-reflection",
        "erasure-appraisal-policy",
        "erasure-appraisal",
    }:
        return _erasure_body_command(arguments, settings)
    database, _services = _open_services(settings, alembic_config=alembic_config)
    try:
        if arguments.source_action == "enroll":
            runtime = enroll_installation(
                database,
                settings.source_policy_directory,
                reviewed=arguments.reviewed,
                new_installation=arguments.new_installation,
            )
            with runtime.shared() as snapshot:
                print(
                    json.dumps(
                        {
                            "floor_id": snapshot.floor_id,
                            "lineage_id": snapshot.lineage_id,
                            "generation": snapshot.generation,
                            "excluded_sources": len(snapshot.excluded_message_ids),
                        }
                    )
                )
            return 0
        runtime = SourcePolicyRuntime(
            settings.source_policy_directory, installation_identity(database)
        )
        if arguments.source_action == "repair":
            with runtime.exclusive() as snapshot:
                receipt = runtime.receipt(arguments.request_id)
                if receipt is None:
                    raise SourcePolicyError("source_request_unknown")
                runtime.mark_repair_pending(receipt.request_id)
                repair_installation(database, runtime, snapshot, receipt)
                result = runtime.mark_repaired(receipt.request_id)
        elif arguments.source_action == "status":
            with runtime.exclusive() as snapshot:
                print(
                    json.dumps(
                        {
                            "floor_id": snapshot.floor_id,
                            "lineage_id": snapshot.lineage_id,
                            "generation": snapshot.generation,
                            "excluded_sources": len(snapshot.excluded_message_ids),
                        }
                    )
                )
            return 0
        else:
            root = canonical_root(
                database,
                runtime,
                arguments.message_id,
                counterparty_id=settings.default_counterparty_id,
            )
            controls = SourcePolicyControls(runtime)
            if arguments.preview:
                print(controls.preview(root).model_dump_json(indent=2))
                return 0
            if (
                arguments.expected_generation is None
                or arguments.sha256 is None
                or arguments.request_id is None
            ):
                raise SourcePolicyError("source_apply_requires_reviewed_generation_digest_request")
            command = SourceExclusionCommand(
                message_id=root.message_id,
                expected_generation=arguments.expected_generation,
                request_id=arguments.request_id,
                preview_digest=arguments.sha256,
            )
            result = controls.apply(
                command,
                root,
                repair=lambda snapshot, receipt: repair_installation(
                    database, runtime, snapshot, receipt
                ),
            )
        print(json.dumps(asdict(result), default=str, ensure_ascii=False, indent=2))
        return 0 if result.phase.value == "complete" else 2
    except (SourcePolicyError, NotActivated, OSError) as error:
        print(f"Source policy rejected: {error}", file=sys.stderr)
        return 2
    finally:
        database.engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive command-line parser."""

    parser = argparse.ArgumentParser(prog="satori")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "bootstrap",
        help="apply migrations and verify persistence connectivity without activation",
    )
    subcommands.add_parser("status", help="show whether Satori is activated")
    subcommands.add_parser("activate", help="activate canonical Satori exactly once")
    source_parser = subcommands.add_parser("sources", help="metadata-only canonical source policy")
    source_actions = source_parser.add_subparsers(dest="source_action", required=True)
    audit_parser = source_actions.add_parser(
        "audit-metadata", help="read-only scoped audit retention coverage"
    )
    audit_parser.add_argument("message_id")
    audit_parser.add_argument("--status", action="store_true", required=True)
    enroll_parser = source_actions.add_parser(
        "enroll", help="explicitly enroll reviewed stored lineage"
    )
    enroll_parser.add_argument("--reviewed", action="store_true", required=True)
    enroll_parser.add_argument(
        "--new-installation",
        action="store_true",
        help="create first floor for a previously unbound installation",
    )
    source_actions.add_parser("status", help="inspect the independent trusted floor")
    repair_parser = source_actions.add_parser("repair", help="resume owner projection repair")
    repair_parser.add_argument("--request-id", required=True)
    exclude_parser = source_actions.add_parser(
        "exclude", help="terminal logical exclusion of canonical USER root"
    )
    exclude_parser.add_argument("message_id")
    exclude_mode = exclude_parser.add_mutually_exclusive_group(required=True)
    exclude_mode.add_argument("--preview", action="store_true")
    exclude_mode.add_argument("--apply", action="store_true")
    exclude_parser.add_argument("--expected-generation", type=_nonnegative_int_argument)
    exclude_parser.add_argument("--sha256", type=_sha256_argument)
    exclude_parser.add_argument("--request-id")
    erasure_parser = source_actions.add_parser(
        "erasure", help="assess retained source data without deletion or provider calls"
    )
    erasure_parser.add_argument("message_id")
    erasure_mode = erasure_parser.add_mutually_exclusive_group(required=True)
    erasure_mode.add_argument("--preview", action="store_true")
    erasure_mode.add_argument("--check", action="store_true")
    erasure_parser.add_argument("--expected-generation", type=_nonnegative_int_argument)
    erasure_parser.add_argument("--sha256", type=_sha256_argument)
    erasure_policy_parser = source_actions.add_parser(
        "erasure-policy", help="explicit reviewed upgrade of the independent erasure policy floor"
    )
    erasure_policy_parser.add_argument("--upgrade", action="store_true", required=True)
    erasure_policy_parser.add_argument("--reviewed", action="store_true", required=True)
    erasure_intent_parser = source_actions.add_parser(
        "erasure-intent", help="record or prepare terminal privacy intent without physical deletion"
    )
    erasure_intent_parser.add_argument("message_id")
    erasure_intent_mode = erasure_intent_parser.add_mutually_exclusive_group(required=True)
    erasure_intent_mode.add_argument("--preview", action="store_true")
    erasure_intent_mode.add_argument("--commit", action="store_true")
    erasure_intent_mode.add_argument("--resume", action="store_true")
    erasure_intent_mode.add_argument("--status", action="store_true")
    erasure_intent_parser.add_argument("--expected-generation", type=_nonnegative_int_argument)
    erasure_intent_parser.add_argument(
        "--expected-erasure-revision", type=_nonnegative_int_argument
    )
    erasure_intent_parser.add_argument("--sha256", type=_sha256_argument)
    erasure_intent_parser.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    body_policy = source_actions.add_parser(
        "erasure-body-policy",
        help="reviewed independent floor upgrade for bounded episode body handling",
    )
    body_policy.add_argument("--upgrade", action="store_true", required=True)
    body_policy.add_argument("--reviewed", action="store_true", required=True)
    body_parser = source_actions.add_parser(
        "erasure-bodies",
        help="review or erase episode summaries, quotes and vectors; partial owner coverage",
    )
    body_parser.add_argument("message_id")
    body_parser.add_argument(
        "--intent-request-id", type=_erasure_intent_request_id_argument, required=True
    )
    body_mode = body_parser.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        body_mode.add_argument(option, action="store_true")
    body_parser.add_argument("--expected-generation", type=_nonnegative_int_argument)
    body_parser.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    body_parser.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    body_parser.add_argument("--sha256", type=_sha256_argument)
    body_parser.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    dialogue_policy = source_actions.add_parser(
        "erasure-dialogue-policy", help="explicit floor upgrade for terminal InteractionLog bodies"
    )
    dialogue_policy.add_argument("--upgrade", action="store_true", required=True)
    dialogue_policy.add_argument("--reviewed", action="store_true", required=True)
    dialogue = source_actions.add_parser(
        "erasure-dialogue", help="review or erase canonical and proved dependent assistant bodies"
    )
    dialogue.add_argument("message_id")
    dialogue.add_argument(
        "--intent-request-id", type=_erasure_intent_request_id_argument, required=True
    )
    dialogue_mode = dialogue.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        dialogue_mode.add_argument(option, action="store_true")
    dialogue.add_argument("--expected-generation", type=_nonnegative_int_argument)
    dialogue.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    dialogue.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    dialogue.add_argument("--sha256", type=_sha256_argument)
    dialogue.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    knowledge_policy = source_actions.add_parser(
        "erasure-knowledge-policy", help="reviewed floor upgrade for terminal derived knowledge"
    )
    knowledge_policy.add_argument("--upgrade", action="store_true", required=True)
    knowledge_policy.add_argument("--reviewed", action="store_true", required=True)
    knowledge = source_actions.add_parser(
        "erasure-knowledge", help="review or erase owned claims, positions and inclinations"
    )
    knowledge.add_argument("message_id")
    knowledge.add_argument(
        "--intent-request-id", type=_erasure_intent_request_id_argument, required=True
    )
    knowledge_mode = knowledge.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        knowledge_mode.add_argument(option, action="store_true")
    knowledge.add_argument("--expected-generation", type=_nonnegative_int_argument)
    knowledge.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    knowledge.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    knowledge.add_argument("--sha256", type=_sha256_argument)
    knowledge.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    reflection_policy = source_actions.add_parser(
        "erasure-reflection-policy", help="reviewed floor upgrade for terminal Reflection payloads"
    )
    reflection_policy.add_argument("--upgrade", action="store_true", required=True)
    reflection_policy.add_argument("--reviewed", action="store_true", required=True)
    reflection = source_actions.add_parser(
        "erasure-reflection", help="review or erase retained terminal Reflection proposal payloads"
    )
    reflection.add_argument("message_id")
    reflection.add_argument(
        "--intent-request-id", type=_erasure_intent_request_id_argument, required=True
    )
    reflection_mode = reflection.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        reflection_mode.add_argument(option, action="store_true")
    reflection.add_argument("--expected-generation", type=_nonnegative_int_argument)
    reflection.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    reflection.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    reflection.add_argument("--sha256", type=_sha256_argument)
    reflection.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    appraisal_policy = source_actions.add_parser(
        "erasure-appraisal-policy", help="reviewed floor upgrade for paired appraisal explanations"
    )
    appraisal_policy.add_argument("--upgrade", action="store_true", required=True)
    appraisal_policy.add_argument("--reviewed", action="store_true", required=True)
    appraisal = source_actions.add_parser(
        "erasure-appraisal", help="review or erase the owned appraisal explanation and audit copy"
    )
    appraisal.add_argument("message_id")
    appraisal.add_argument(
        "--intent-request-id", type=_erasure_intent_request_id_argument, required=True
    )
    appraisal_mode = appraisal.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        appraisal_mode.add_argument(option, action="store_true")
    appraisal.add_argument("--expected-generation", type=_nonnegative_int_argument)
    appraisal.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    appraisal.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    appraisal.add_argument("--sha256", type=_sha256_argument)
    appraisal.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    state_parser = subcommands.add_parser(
        "state", help="whole-state local recovery without providers"
    )
    state_actions = state_parser.add_subparsers(dest="state_action", required=True)
    state_export = state_actions.add_parser(
        "export", aliases=["backup"], help="write a private consistent whole-state archive"
    )
    state_export.add_argument("--output", type=Path, required=True)
    state_export.add_argument("--overwrite", action="store_true")
    state_import = state_actions.add_parser("import", help="validate or restore a reviewed archive")
    state_import.add_argument("archive", type=Path)
    state_mode = state_import.add_mutually_exclusive_group(required=True)
    state_mode.add_argument("--preview", action="store_true")
    state_mode.add_argument("--activate", action="store_true")
    state_import.add_argument("--target", type=Path, help="new absent SQLite database file")
    state_import.add_argument("--sha256", type=_sha256_argument, help="reviewed whole-archive hash")
    retention = state_actions.add_parser(
        "retention", help="read-only whole-main retention coverage"
    )
    retention.add_argument("--identity-id", required=True)
    retention_mode = retention.add_mutually_exclusive_group(required=True)
    retention_mode.add_argument("--status", action="store_true")
    retention_mode.add_argument("--check", action="store_true")
    retention.add_argument("--expected-generation", type=_nonnegative_int_argument)
    retention.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    retention.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    retention.add_argument("--sha256", type=_sha256_argument)
    talk_parser = subcommands.add_parser(
        "talk",
        help="send and durably persist one idempotent text turn",
    )
    talk_parser.add_argument("text", help="current user message")
    talk_parser.add_argument("--session", help="existing explicit session ID")
    talk_parser.add_argument("--request-id", help="stable client retry/idempotency key")
    chat_parser = subcommands.add_parser(
        "chat", help="run a long-lived human-readable interactive conversation"
    )
    chat_session = chat_parser.add_mutually_exclusive_group()
    chat_session.add_argument("--session", help="resume an existing open session ID")
    chat_session.add_argument(
        "--new-session",
        action="store_true",
        help="explicitly start a new session (the default)",
    )
    chat_parser.add_argument("--debug", action="store_true", help="show phase diagnostics")
    benchmark_parser = subcommands.add_parser(
        "benchmark", help="run metadata-only developer benchmarks"
    )
    benchmark_actions = benchmark_parser.add_subparsers(dest="benchmark_action", required=True)
    inference_benchmark = benchmark_actions.add_parser(
        "inference", help="measure real canonical inference distributions"
    )
    inference_benchmark.add_argument("--repetitions", type=int, default=5)
    inference_benchmark.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="scenario ID to include; repeat the option to select several",
    )
    inference_benchmark.add_argument(
        "--include-derived",
        action="store_true",
        help="also await and measure episode/index/semantic processing",
    )
    inference_benchmark.add_argument(
        "--output",
        type=Path,
        help="write metadata-only JSON to this path instead of stdout",
    )
    appraisal_benchmark = benchmark_actions.add_parser(
        "appraisal", help="compare configured Ollama appraisal models on the semantic corpus"
    )
    appraisal_benchmark.add_argument("--repetitions", type=int, default=2)
    appraisal_benchmark.add_argument(
        "--model",
        action="append",
        default=[],
        help="Ollama model to evaluate; repeat to compare models",
    )
    appraisal_benchmark.add_argument(
        "--output",
        type=Path,
        help="write the fixture-only JSON report to this path instead of stdout",
    )
    contention_benchmark = benchmark_actions.add_parser(
        "contention", help="measure foreground inference while derived Qwen work overlaps"
    )
    contention_benchmark.add_argument("--repetitions", type=int, default=3)
    contention_benchmark.add_argument(
        "--scheduled",
        action="store_true",
        help="route all capabilities through the priority scheduler",
    )
    contention_benchmark.add_argument(
        "--output",
        type=Path,
        help="write fixture-only contention JSON to this path instead of stdout",
    )
    session_parser = subcommands.add_parser("session", help="manage explicit sessions")
    session_actions = session_parser.add_subparsers(dest="session_action", required=True)
    session_actions.add_parser("start", help="start a multi-turn conversation container")
    close_parser = session_actions.add_parser("close", help="close a conversation container")
    close_parser.add_argument("session_id")
    history_parser = subcommands.add_parser("history", help="inspect persisted raw dialogue")
    history_parser.add_argument("--session", help="limit output to one session ID")
    memory_parser = subcommands.add_parser("memories", help="inspect episodic memory records")
    memory_parser.add_argument("--interaction", help="limit output to one source interaction ID")
    memory_parser.add_argument(
        "--include-archived",
        action="store_true",
        help="explicitly inspect retained archived episodes",
    )
    memory_actions = memory_parser.add_subparsers(dest="memory_action")
    search_parser = memory_actions.add_parser("search", help="debug semantic retrieval")
    search_parser.add_argument("text", help="retrieval query text")
    memory_actions.add_parser("index", help="backfill missing embeddings")
    memory_actions.add_parser("rebuild", help="replace embeddings in the active space")
    memory_actions.add_parser(
        "scope-status", help="show content-free attribution/quarantine counts"
    )
    for lifecycle_action in ("archive", "reactivate"):
        lifecycle_parser = memory_actions.add_parser(
            lifecycle_action, help=f"explicitly {lifecycle_action} one scoped episode"
        )
        lifecycle_parser.add_argument("memory_id")
        lifecycle_mode = lifecycle_parser.add_mutually_exclusive_group(required=True)
        lifecycle_mode.add_argument("--preview", action="store_true", help="metadata-only review")
        lifecycle_mode.add_argument(
            "--apply", action="store_true", help="apply a reviewed transition"
        )
        lifecycle_parser.add_argument("--expected-version", type=_positive_int_argument)
        lifecycle_parser.add_argument("--sha256", type=_sha256_argument)
        lifecycle_parser.add_argument("--request-id", help="durable operation idempotency key")
    semantic_parser = subcommands.add_parser(
        "semantic", help="inspect or process evidence-grounded semantic claims"
    )
    semantic_actions = semantic_parser.add_subparsers(dest="semantic_action", required=True)
    semantic_list = semantic_actions.add_parser("list", help="list active semantic claims")
    semantic_list.add_argument("--all", action="store_true", help="include historical claims")
    semantic_list.add_argument("--predicate", help="filter by registered predicate")
    semantic_inspect = semantic_actions.add_parser(
        "inspect", help="inspect one claim, provenance, and revision history"
    )
    semantic_inspect.add_argument("claim_id")
    semantic_process = semantic_actions.add_parser(
        "process", help="process one source memory or backfill missing decisions"
    )
    semantic_process.add_argument("--memory", help="one episodic source memory ID")
    semantic_process.add_argument("--limit", type=int, help="maximum missing sources")
    emotion_parser = subcommands.add_parser(
        "emotion", help="inspect current affective state or transition history"
    )
    emotion_actions = emotion_parser.add_subparsers(dest="emotion_action", required=True)
    emotion_actions.add_parser("status", help="show current lazily materialized state")
    emotion_history = emotion_actions.add_parser(
        "history", help="show source-linked transition metadata"
    )
    emotion_history.add_argument("--limit", type=int, default=20)
    relationship_parser = subcommands.add_parser(
        "relationship", help="inspect or retry the counterparty-specific relationship model"
    )
    relationship_actions = relationship_parser.add_subparsers(
        dest="relationship_action", required=True
    )
    relationship_actions.add_parser("status", help="show the current relationship read model")
    relationship_history = relationship_actions.add_parser(
        "history", help="show source-linked relationship transitions"
    )
    relationship_history.add_argument("--limit", type=int, default=20)
    relationship_process = relationship_actions.add_parser(
        "process", help="retry one interaction or oldest eligible missing decisions"
    )
    relationship_process_target = relationship_process.add_mutually_exclusive_group(required=True)
    relationship_process_target.add_argument("--interaction")
    relationship_process_target.add_argument(
        "--limit",
        type=_positive_int_argument,
        help="maximum oldest missing sources for the default counterparty",
    )
    models_parser = subcommands.add_parser(
        "models", help="inspect, export, or process Stage 9 user/world models"
    )
    models_actions = models_parser.add_subparsers(dest="models_action", required=True)
    for owner in ("user", "world"):
        owner_parser = models_actions.add_parser(owner, help=f"inspect {owner} model claims")
        owner_actions = owner_parser.add_subparsers(dest="models_owner_action", required=True)
        owner_list = owner_actions.add_parser("list", help=f"list {owner} model claims")
        owner_list.add_argument("--all", action="store_true", help="include historical claims")
        owner_list.add_argument(
            "--counterparty",
            type=_counterparty_id_argument,
            help="opaque counterparty partition; defaults to SATORI_DEFAULT_COUNTERPARTY_ID",
        )
        owner_inspect = owner_actions.add_parser(
            "inspect", help=f"inspect one {owner} claim and its lineage"
        )
        owner_inspect.add_argument("claim_id")
        owner_inspect.add_argument(
            "--counterparty",
            type=_counterparty_id_argument,
            help="opaque counterparty partition; defaults to SATORI_DEFAULT_COUNTERPARTY_ID",
        )
    models_export = models_actions.add_parser(
        "export", help="partial inspection: one counterparty partition without raw messages"
    )
    models_export.add_argument("--output", type=Path)
    models_export.add_argument("--overwrite", action="store_true")
    models_export.add_argument(
        "--counterparty",
        type=_counterparty_id_argument,
        help="opaque counterparty partition; defaults to SATORI_DEFAULT_COUNTERPARTY_ID",
    )
    models_process = models_actions.add_parser(
        "process", help="retry one interaction or backfill Stage 9 decisions"
    )
    models_process.add_argument("--interaction")
    models_process.add_argument("--limit", type=int)
    positions_parser = subcommands.add_parser(
        "positions", help="inspect/process positions and Stage 13 Satori inclinations"
    )
    positions_actions = positions_parser.add_subparsers(dest="positions_action", required=True)
    positions_list = positions_actions.add_parser("list", help="list current Satori positions")
    positions_list.add_argument("--all", action="store_true", help="include historical positions")
    positions_inspect = positions_actions.add_parser(
        "inspect", help="inspect one position and its complete lineage"
    )
    positions_inspect.add_argument("position_id")
    positions_export = positions_actions.add_parser(
        "export", help="partial inspection: identity-global positions and provenance"
    )
    positions_export.add_argument("--output", type=Path)
    positions_export.add_argument("--overwrite", action="store_true")
    inclinations_list = positions_actions.add_parser(
        "inclinations-list", help="list current evidence-backed Satori inclinations"
    )
    inclinations_list.add_argument(
        "--as-of",
        type=_aware_datetime_argument,
        help="explicit ISO-8601 time for pure score materialization (default: now)",
    )
    inclination_inspect = positions_actions.add_parser(
        "inclination-inspect", help="inspect one inclination and immutable provenance"
    )
    inclination_inspect.add_argument("inclination_id")
    inclination_inspect.add_argument(
        "--as-of",
        type=_aware_datetime_argument,
        help="explicit ISO-8601 time for pure score materialization (default: now)",
    )
    inclination_export = positions_actions.add_parser(
        "inclination-export", help="partial inspection: inclinations without raw source text"
    )
    inclination_export.add_argument("--output", type=Path)
    inclination_export.add_argument("--overwrite", action="store_true")
    inclination_export.add_argument(
        "--as-of",
        type=_aware_datetime_argument,
        help="explicit ISO-8601 materialization time (default: now)",
    )
    positions_process = positions_actions.add_parser(
        "process", help="retry one interaction or backfill Stage 11 decisions"
    )
    positions_process.add_argument("--interaction")
    positions_process.add_argument("--limit", type=int)
    reflection_parser = subcommands.add_parser(
        "reflection", help="inspect or explicitly process bounded Stage 12 reflection runs"
    )
    reflection_actions = reflection_parser.add_subparsers(dest="reflection_action", required=True)
    reflection_list = reflection_actions.add_parser("list", help="list reflection runs")
    reflection_list.add_argument(
        "--limit",
        type=_positive_int_argument,
        default=50,
        help="maximum runs to show (default: 50)",
    )
    reflection_inspect = reflection_actions.add_parser(
        "inspect", help="inspect one run without source quotes by default"
    )
    reflection_inspect.add_argument("run_id")
    reflection_inspect.add_argument(
        "--show-sources",
        action="store_true",
        help="include sensitive local source quotes in output",
    )
    reflection_actions.add_parser(
        "process", help="run explicit eligibility, generation and resumable routing"
    )
    personality_parser = subcommands.add_parser(
        "personality",
        help="inspect, process, approve, compare, export, or restore personality evolution",
    )
    personality_actions = personality_parser.add_subparsers(
        dest="personality_action",
        required=True,
    )
    personality_actions.add_parser(
        "inspect",
        help="inspect the current vector, checkpoints, provenance IDs, and remaining budgets",
    )
    personality_compare = personality_actions.add_parser(
        "compare",
        help="compare the current vector with one immutable checkpoint",
    )
    personality_compare.add_argument("checkpoint_id")
    personality_export = personality_actions.add_parser(
        "export",
        help="partial inspection: Stage 14 state without source quotes or provider text",
    )
    personality_export.add_argument("--output", type=Path)
    personality_export.add_argument("--overwrite", action="store_true")
    personality_actions.add_parser(
        "process",
        help="run explicit-local Reflection V3 eligibility, generation, and owner routing",
    )
    reason_policy = personality_actions.add_parser(
        "reasons-policy", help="reviewed independent floor upgrade for operator reason bodies"
    )
    reason_policy.add_argument("--upgrade", action="store_true", required=True)
    reason_policy.add_argument("--reviewed", action="store_true", required=True)
    reasons = personality_actions.add_parser(
        "reason-bodies", help="review or logically remove exact identity-owned operator reasons"
    )
    reasons.add_argument("--identity-id", type=_erasure_intent_request_id_argument, required=True)
    reasons.add_argument(
        "--approval", type=_erasure_intent_request_id_argument, action="append", default=[]
    )
    reasons.add_argument(
        "--restore", type=_erasure_intent_request_id_argument, action="append", default=[]
    )
    reason_mode = reasons.add_mutually_exclusive_group(required=True)
    for option in ("--preview", "--apply", "--resume", "--status"):
        reason_mode.add_argument(option, action="store_true")
    reasons.add_argument("--expected-generation", type=_nonnegative_int_argument)
    reasons.add_argument("--expected-erasure-revision", type=_nonnegative_int_argument)
    reasons.add_argument("--expected-body-revision", type=_nonnegative_int_argument)
    reasons.add_argument("--sha256", type=_sha256_argument)
    reasons.add_argument("--request-id", type=_erasure_intent_request_id_argument)
    for action, help_text in (
        ("approve", "approve one reviewed current checkpoint as the new budget origin"),
        ("restore", "restore one immutable checkpoint through the deterministic owner"),
    ):
        mutation = personality_actions.add_parser(action, help=help_text)
        mutation.add_argument("checkpoint_id")
        mutation.add_argument(
            "--hash", dest="checkpoint_hash", type=_sha256_argument, required=True
        )
        mutation.add_argument(
            "--expected-version",
            type=_positive_int_argument,
            required=True,
            help="exact current personality aggregate version",
        )
        mutation.add_argument("--reason", required=True)
    return parser


def _whole_state_command(
    arguments: argparse.Namespace, settings: Settings, *, alembic_config: Path
) -> int:
    """Independent composition: never bootstrap a live DB or instantiate a provider."""

    try:
        recovery = WholeStateRecovery(
            SQLiteStoredSnapshot(settings.database_url, alembic_config=alembic_config),
            PrivateArtifactStore(),
            reserved_paths=InstallationPolicyLedger(
                settings.source_policy_directory
            ).reserved_paths,
        )
        if arguments.state_action in {"export", "backup"}:
            preview = recovery.export(arguments.output, overwrite=arguments.overwrite)
        elif arguments.preview:
            if arguments.target is not None or arguments.sha256 is not None:
                raise RecoveryError("preview_does_not_activate")
            preview = recovery.preview(arguments.archive)
        else:
            if arguments.target is None or arguments.sha256 is None:
                raise RecoveryError("activation_requires_target_and_reviewed_hash")
            preview = recovery.restore(
                arguments.archive, arguments.target, expected_sha256=arguments.sha256
            )
    except (RecoveryError, OSError) as error:
        print(f"State recovery rejected: {error}", file=sys.stderr)
        return 2
    print(preview.model_dump_json(indent=2))
    if arguments.state_action == "import" and arguments.activate:
        print(f"Restored offline database: {arguments.target}; automatic_processing=false")
    return 0


def _write_partial_export(
    settings: Settings, output: Path, serialized: str, *, overwrite: bool, alembic_config: Path
) -> bool:
    """Route legacy aggregate file exports through the same private writer."""

    try:
        artifacts = PrivateArtifactStore()
        recovery = WholeStateRecovery(
            SQLiteStoredSnapshot(settings.database_url, alembic_config=alembic_config),
            artifacts,
            reserved_paths=InstallationPolicyLedger(
                settings.source_policy_directory
            ).reserved_paths,
        )
        recovery.check_artifact_target(output)
        artifacts.write(output, (serialized + "\n").encode("utf-8"), overwrite=overwrite)
    except (RecoveryError, OSError) as error:
        print(f"Partial export rejected: {error}", file=sys.stderr)
        return False
    return True


def _open_services(
    settings: Settings,
    *,
    alembic_config: Path,
) -> tuple[Database, InitialSelfServices]:
    """Migrate and compose Stage 2 services without creating an identity."""

    upgrade_database(settings.database_url, config_path=alembic_config)
    database = create_database(settings.database_url)
    return database, build_initial_self_services(database)


def _print_active_identity(identity: Identity) -> None:
    """Print a concise status without dumping personality or values."""

    print("Satori: active")
    print(f"Identity: {identity.identity_id}")
    print(f"Name: {identity.name}")
    print(f"Activated: {identity.activation_time.isoformat()}")
    print(
        "Seed: "
        f"{identity.seed_provenance.seed_id} "
        f"(schema {identity.seed_provenance.seed_schema_version}, "
        f"sha256 {identity.seed_provenance.seed_content_hash})"
    )


def _print_current_model_claim(claim: UserModelClaim | WorldModelClaim) -> None:
    expires_at = claim.expires_at.isoformat() if claim.expires_at else "none"
    subject = (
        f"{claim.subject_kind}:{claim.subject_label}."
        if isinstance(claim, WorldModelClaim)
        else "user."
    )
    print(
        f"[{claim.claim_id}] {claim.status.value} {claim.epistemic_kind.value} "
        f"{subject}{claim.predicate}={claim.value!r} confidence={claim.confidence:.2f} "
        f"expires={expires_at}"
    )


def _print_current_model_inspection(
    claim: UserModelClaim | WorldModelClaim,
    revisions: tuple[ModelClaimRevision, ...],
) -> None:
    valid_until = claim.valid_until.isoformat() if claim.valid_until else "none"
    print(
        f"[{claim.claim_id}] status={claim.status.value} "
        f"kind={claim.epistemic_kind.value} version={claim.aggregate_version}"
    )
    print(
        f"{claim.predicate}={claim.value!r} confidence={claim.confidence:.2f} "
        f"valid_from={claim.valid_from.isoformat()} valid_until={valid_until}"
    )
    for evidence in claim.evidence:
        print(
            f"evidence={evidence.evidence_id} message={evidence.source_message_id} "
            f"interaction={evidence.source_interaction_id}"
        )
    for revision in revisions:
        print(
            f"revision={revision.revision_id} v={revision.claim_version} "
            f"kind={revision.kind.value} reason={revision.reason_code}"
        )


def _print_position(position: SatoriPosition) -> None:
    print(
        f"[{position.position_id}] {position.status.value} {position.kind.value} "
        f"stance={position.stance.value} confidence={position.confidence:.2f} "
        f"version={position.aggregate_version} proposition={position.proposition!r}"
    )


def _print_position_inspection(
    position: SatoriPosition, revisions: tuple[PositionRevision, ...]
) -> None:
    _print_position(position)
    if position.value_key is not None:
        print(f"value_key={position.value_key}")
    if position.competing_with_position_id is not None:
        print(f"competing_with={position.competing_with_position_id}")
    if position.superseded_by_position_id is not None:
        print(f"superseded_by={position.superseded_by_position_id}")
    for evidence in position.evidence:
        print(
            f"evidence={evidence.evidence_id} role={evidence.role.value} "
            f"message={evidence.source_message_id} interaction={evidence.source_interaction_id} "
            f"counterparty={evidence.source_counterparty_id} quote={evidence.quote!r}"
        )
    for revision in revisions:
        print(
            f"revision={revision.revision_id} v={revision.position_version} "
            f"kind={revision.kind.value} reason={revision.reason_code}"
        )


def _print_inclination(inclination: SatoriInclination, *, as_of: datetime) -> None:
    effective = materialize_inclination_score(inclination, at=as_of)
    alternative = (
        f" alternative={inclination.alternative_topic!r}"
        if inclination.alternative_topic is not None
        else ""
    )
    print(
        f"[{inclination.inclination_id}] {inclination.kind.value} "
        f"topic={inclination.topic!r}{alternative} score={inclination.score:.3f} "
        f"effective_score={effective:.3f} confidence={inclination.confidence:.3f} "
        f"stability={inclination.stability:.3f} version={inclination.aggregate_version} "
        f"state_as_of={inclination.state_as_of.isoformat()} "
        f"materialized_at={as_of.isoformat()}"
    )


def _print_inclination_inspection(inclination: SatoriInclination, *, as_of: datetime) -> None:
    _print_inclination(inclination, as_of=as_of)
    for evidence in inclination.evidence:
        print(
            f"evidence={evidence.evidence_id} role={evidence.role.value} "
            f"reflection_source={evidence.reflection_source_id} "
            f"affective_transition={evidence.affective_transition_id} "
            f"affective_state_version={evidence.affective_state_version} "
            f"message={evidence.source_message_id} "
            f"interaction={evidence.source_interaction_id} "
            f"session={evidence.source_session_id} signal={evidence.signal:.3f}"
        )
    for revision in inclination.revisions:
        print(
            f"revision={revision.revision_id} v={revision.inclination_version} "
            f"kind={revision.kind.value} delta={revision.applied_delta:.3f} "
            f"reason={revision.reason_code}"
        )


def _print_personality_inspection(inspection: PersonalityInspection) -> None:
    """Print the explicit local Stage 14 vector and quote-free provenance."""

    personality = inspection.personality
    print(
        f"personality identity={inspection.identity_id} schema={personality.schema_version} "
        f"aggregate_version={personality.aggregate_version}"
    )
    for personality_trait in personality.traits:
        print(
            f"trait[{personality_trait.key}] value={personality_trait.value:.6f} "
            f"baseline={personality_trait.baseline_value:.6f} "
            f"delta={personality_trait.value - personality_trait.baseline_value:+.6f}"
        )
    budgets = inspection.budgets
    print(
        "distance "
        f"activation_linf={budgets.activation_distance_linf:.6f} "
        f"activation_l1={budgets.activation_distance_l1:.6f} "
        f"approved_linf={budgets.approved_checkpoint_distance_linf:.6f} "
        f"approved_l1={budgets.approved_checkpoint_distance_l1:.6f}"
    )
    print(
        "path "
        f"rolling={budgets.rolling_global_path:.6f} "
        f"rolling_remaining={budgets.rolling_global_remaining:.6f} "
        f"lifetime={budgets.lifetime_global_path:.6f} "
        f"lifetime_remaining={budgets.lifetime_global_remaining:.6f}"
    )
    for trait_budget in budgets.traits:
        print(
            f"budget[{trait_budget.trait_key}] rolling={trait_budget.rolling_path:.6f} "
            f"rolling_remaining={trait_budget.rolling_remaining:.6f} "
            f"lifetime={trait_budget.lifetime_path:.6f} "
            f"lifetime_remaining={trait_budget.lifetime_remaining:.6f}"
        )
    approved_id = inspection.approved_checkpoint.snapshot.checkpoint_id
    for checkpoint in inspection.checkpoints:
        snapshot = checkpoint.snapshot
        print(
            f"checkpoint[{snapshot.checkpoint_id}] kind={snapshot.checkpoint_kind.value} "
            f"aggregate_version={snapshot.source_aggregate_version} "
            f"hash={snapshot.checkpoint_hash} "
            f"approved={str(snapshot.checkpoint_id == approved_id).lower()} "
            f"created_at={checkpoint.created_at.isoformat()}"
        )
    for revision in inspection.revisions:
        print(
            f"revision[{revision.revision_id}] kind={revision.revision_kind} "
            f"version={revision.before_aggregate_version}->{revision.after_aggregate_version} "
            f"trait={revision.trait_key or 'multiple'} reason={revision.reason_code} "
            f"source_checkpoint={revision.source_checkpoint_id} "
            f"resulting_checkpoint={revision.resulting_checkpoint_id}"
        )
    for evidence in inspection.evidence:
        print(
            f"evidence[{evidence.evidence_id}] revision={evidence.revision_id} "
            f"source={evidence.reflection_source_id} edge={evidence.evidence_edge_id}"
            f"@{evidence.evidence_edge_version} root={evidence.root_interaction_id}/"
            f"{evidence.root_message_id} lineage={evidence.upstream_lineage_kind.value}:"
            f"{evidence.upstream_lineage_id} role={evidence.citation_role.value} "
            f"content_hash={evidence.content_hash} signature={evidence.normalized_signature}"
        )
    for approval in inspection.approvals:
        print(
            f"approval[{approval.approval_id}] checkpoint={approval.checkpoint_id} "
            f"hash={approval.checkpoint_hash} version={approval.expected_aggregate_version} "
            f"approved_at={approval.approved_at.isoformat()}"
            f" reason_state={approval.reason_state}"
        )
    for restore in inspection.restores:
        print(
            f"restore[{restore.restore_id}] revision={restore.revision_id} "
            f"checkpoint={restore.source_checkpoint_id} "
            f"version={restore.before_aggregate_version}->{restore.after_aggregate_version} "
            f"resulting_checkpoint={restore.resulting_checkpoint_id} "
            f"restored_at={restore.restored_at.isoformat()}"
            f" reason_state={restore.reason_state}"
        )


def _print_personality_comparison(comparison: PersonalityCheckpointComparison) -> None:
    print(
        f"checkpoint={comparison.checkpoint_id} hash={comparison.checkpoint_hash} "
        f"checkpoint_version={comparison.checkpoint_aggregate_version} "
        f"current_version={comparison.current_aggregate_version} "
        f"distance_linf={comparison.distance_linf:.6f} "
        f"distance_l1={comparison.distance_l1:.6f}"
    )
    for diff in comparison.trait_diffs:
        print(
            f"trait[{diff.trait_key}] checkpoint={diff.before_value:.6f} "
            f"current={diff.after_value:.6f} "
            f"delta={diff.after_value - diff.before_value:+.6f}"
        )


def _status(services: InitialSelfServices) -> int:
    """Report activation state without causing activation."""

    try:
        identity = services.get_identity.execute()
    except NotActivated:
        print("Satori: not activated")
        return 0
    _print_active_identity(identity)
    return 0


def _activate(services: InitialSelfServices, *, trace_id: str) -> int:
    """Activate from the validated canonical seed or report the safe repeat outcome."""

    seed = JsonSeedLoader().load_canonical()
    try:
        snapshot = services.activate.execute(seed, trace_id=trace_id)
    except AlreadyActivated:
        print("Satori is already activated; existing state was not changed.")
        _print_active_identity(services.get_identity.execute())
        return 0
    print("Satori activated.")
    _print_active_identity(snapshot.identity)
    return 0


def _configured_conversation_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
    yandex_http_client: YandexAIStudioHttpClient | None = None,
    openai_http_client: OpenAIHttpClient | None = None,
) -> ConversationProvider:
    """Construct the conversation provider selected by runtime configuration."""

    if settings.conversation_provider is ConversationProviderKind.OLLAMA:
        return OllamaConversationAdapter(
            base_url=settings.conversation_provider_base_url,
            model=settings.conversation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    if settings.conversation_provider is ConversationProviderKind.YANDEX_AI_STUDIO:
        api_key = settings.yandex_ai_studio_api_key
        if api_key is None:
            raise AssertionError("validated Yandex AI Studio configuration has no API key")
        return YandexAIStudioConversationAdapter(
            base_url=settings.yandex_ai_studio_base_url,
            api_key=api_key.get_secret_value(),
            model=settings.conversation_model,
            folder_id=settings.yandex_ai_studio_folder_id,
            timeout_seconds=settings.conversation_timeout_seconds,
            reasoning_effort=(
                settings.yandex_ai_studio_reasoning_effort.value
                if settings.yandex_ai_studio_reasoning_effort is not None
                else None
            ),
            http_client=yandex_http_client,
        )
    if settings.conversation_provider is ConversationProviderKind.OPENAI:
        api_key = settings.openai_api_key
        if api_key is None:
            raise AssertionError("validated OpenAI configuration has no API key")
        return OpenAIConversationAdapter(
            base_url=settings.openai_base_url,
            api_key=api_key.get_secret_value(),
            model=settings.conversation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            reasoning_effort=settings.openai_reasoning_effort.value,
            reasoning_token_allowance=settings.openai_reasoning_token_allowance,
            http_client=openai_http_client,
        )
    raise AssertionError(f"unhandled conversation provider: {settings.conversation_provider}")


def _configured_episode_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> EpisodeFormationProvider:
    """Construct the structured capability independently from conversation generation."""

    if settings.episode_formation_provider is ConversationProviderKind.OLLAMA:
        return OllamaEpisodeFormationAdapter(
            base_url=settings.conversation_provider_base_url,
            model=settings.episode_formation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            max_output_tokens=settings.episode_formation_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled episode provider: {settings.episode_formation_provider}")


def _configured_embedding_provider(
    settings: Settings, *, http_client: OllamaHttpClient | None = None
) -> EmbeddingProvider:
    """Construct the independently configured embedding capability."""

    if settings.embedding_provider is EmbeddingProviderKind.OLLAMA:
        return OllamaEmbeddingAdapter(
            base_url=settings.embedding_provider_base_url,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            input_schema_version=1,
            timeout_seconds=settings.embedding_timeout_seconds,
            http_client=http_client,
        )
    raise AssertionError(f"unhandled embedding provider: {settings.embedding_provider}")


def _configured_semantic_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> SemanticFormationProvider:
    """Construct the semantic structured-generation capability independently."""

    if settings.semantic_formation_provider is ConversationProviderKind.OLLAMA:
        return OllamaSemanticFormationAdapter(
            base_url=settings.conversation_provider_base_url,
            model=settings.semantic_formation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            max_output_tokens=settings.semantic_formation_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled semantic provider: {settings.semantic_formation_provider}")


def _configured_model_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> ModelFormationProvider:
    """Construct the independent Stage 9 proposal capability."""

    if settings.model_formation_provider is ConversationProviderKind.OLLAMA:
        return OllamaModelFormationAdapter(
            base_url=settings.conversation_provider_base_url,
            model=settings.model_formation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            max_output_tokens=settings.model_formation_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled model provider: {settings.model_formation_provider}")


def _configured_position_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> PositionFormationProvider:
    """Construct the independent Stage 11 proposal capability."""

    if settings.position_formation_provider is ConversationProviderKind.OLLAMA:
        return OllamaPositionFormationAdapter(
            base_url=settings.conversation_provider_base_url,
            model=settings.position_formation_model,
            timeout_seconds=settings.conversation_timeout_seconds,
            max_output_tokens=settings.position_formation_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled position provider: {settings.position_formation_provider}")


def _configured_reflection_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> ReflectionGenerationPort:
    """Construct the independent bounded Stage 12 reflection capability."""

    if settings.reflection_provider is ConversationProviderKind.OLLAMA:
        return OllamaReflectionAdapter(
            base_url=settings.reflection_provider_base_url,
            model=settings.reflection_model,
            timeout_seconds=settings.reflection_timeout_seconds,
            max_output_tokens=settings.reflection_max_output_tokens,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled reflection provider: {settings.reflection_provider}")


def _configured_affective_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> AffectiveAppraisalProvider:
    """Construct the structured appraisal capability without giving it state access."""

    if settings.affective_appraisal_provider is ConversationProviderKind.OLLAMA:
        return OllamaAffectiveAppraisalAdapter(
            base_url=settings.affective_appraisal_provider_base_url,
            model=settings.affective_appraisal_model,
            timeout_seconds=settings.affective_appraisal_timeout_seconds,
            max_output_tokens=settings.affective_appraisal_max_output_tokens,
            context_window=settings.affective_appraisal_context_window,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(f"unhandled appraisal provider: {settings.affective_appraisal_provider}")


def _configured_relationship_provider(
    settings: Settings,
    *,
    http_client: OllamaHttpClient | None = None,
    scheduler: OllamaInferenceScheduler | None = None,
) -> RelationshipProvider:
    """Construct the independently configured background relationship classifier."""

    if settings.relationship_appraisal_provider is ConversationProviderKind.OLLAMA:
        return OllamaRelationshipAppraisalAdapter(
            base_url=settings.relationship_appraisal_provider_base_url,
            model=settings.relationship_appraisal_model,
            timeout_seconds=settings.relationship_appraisal_timeout_seconds,
            max_output_tokens=settings.relationship_appraisal_max_output_tokens,
            context_window=settings.relationship_appraisal_context_window,
            keep_alive=settings.ollama_keep_alive,
            http_client=http_client,
            scheduler=scheduler,
        )
    raise AssertionError(
        f"unhandled relationship provider: {settings.relationship_appraisal_provider}"
    )


def _benchmark_inference(
    settings: Settings,
    *,
    alembic_config: Path,
    repetitions: int,
    scenario_ids: frozenset[str],
    include_derived: bool,
    output_path: Path | None,
) -> int:
    """Run fixtures against an isolated temporary canonical store."""

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="satori-stage77-") as temporary_directory:
        benchmark_settings = settings.model_copy(
            update={
                "database_url": (
                    "sqlite+pysqlite:///" + str(Path(temporary_directory) / "benchmark.db")
                ),
                "chat_log_path": str(Path(temporary_directory) / "runtime.jsonl"),
            }
        )
        database, initial_self = _open_services(benchmark_settings, alembic_config=alembic_config)
        initial_self.activate.execute(
            JsonSeedLoader().load_canonical(), trace_id="stage77-benchmark-activation"
        )
        http_clients: dict[str, OllamaHttpClient] = {}
        inference_schedulers: dict[str, OllamaInferenceScheduler] = {}

        def client(base_url: str) -> OllamaHttpClient:
            existing = http_clients.get(base_url)
            if existing is None:
                existing = OllamaHttpClient(base_url)
                http_clients[base_url] = existing
            return existing

        def scheduler(base_url: str) -> OllamaInferenceScheduler | None:
            if not benchmark_settings.ollama_serialize_inference:
                return None
            existing = inference_schedulers.get(base_url)
            if existing is None:
                existing = OllamaInferenceScheduler(
                    background_aging_seconds=benchmark_settings.ollama_background_aging_seconds,
                    background_grace_seconds=benchmark_settings.ollama_background_grace_seconds,
                )
                inference_schedulers[base_url] = existing
            return existing

        try:
            conversation = build_conversation_services(
                database,
                initial_self,
                _configured_conversation_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.conversation_provider_base_url),
                    scheduler=scheduler(benchmark_settings.conversation_provider_base_url),
                ),
                _configured_episode_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.conversation_provider_base_url),
                    scheduler=scheduler(benchmark_settings.conversation_provider_base_url),
                ),
                benchmark_settings,
                embedding_provider=_configured_embedding_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.embedding_provider_base_url),
                ),
                semantic_provider=_configured_semantic_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.conversation_provider_base_url),
                    scheduler=scheduler(benchmark_settings.conversation_provider_base_url),
                ),
                appraisal_provider=_configured_affective_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.affective_appraisal_provider_base_url),
                    scheduler=scheduler(benchmark_settings.affective_appraisal_provider_base_url),
                ),
                relationship_provider=_configured_relationship_provider(
                    benchmark_settings,
                    http_client=client(benchmark_settings.relationship_appraisal_provider_base_url),
                    scheduler=scheduler(
                        benchmark_settings.relationship_appraisal_provider_base_url
                    ),
                ),
            )
            report = asyncio.run(
                run_inference_benchmark(
                    conversation,
                    Uuid4Generator(),
                    provider_models={
                        "conversation": benchmark_settings.conversation_model,
                        "appraisal": benchmark_settings.affective_appraisal_model,
                        "relationship": benchmark_settings.relationship_appraisal_model,
                        "episode": benchmark_settings.episode_formation_model,
                        "semantic": benchmark_settings.semantic_formation_model,
                        "embedding": benchmark_settings.embedding_model,
                    },
                    warm_repetitions=repetitions,
                    selected_scenario_ids=scenario_ids,
                    include_derived=include_derived,
                    runtime_preparation_ms=(time.perf_counter() - started) * 1000,
                )
            )
            serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
            if output_path is None:
                print(serialized)
            else:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(serialized + "\n", encoding="utf-8")
                print(f"Benchmark report written: {output_path}")
            return 0
        finally:
            for http_client in http_clients.values():
                http_client.close()
            database.dispose()


def _benchmark_appraisal(
    settings: Settings,
    *,
    repetitions: int,
    models: tuple[str, ...],
    output_path: Path | None,
) -> int:
    """Compare appraisal capabilities without opening or mutating canonical persistence."""

    selected_models = models or (settings.affective_appraisal_model,)
    client = OllamaHttpClient(settings.affective_appraisal_provider_base_url)
    try:
        report = asyncio.run(
            run_appraisal_model_evaluation(
                tuple(
                    OllamaAffectiveAppraisalAdapter(
                        base_url=settings.affective_appraisal_provider_base_url,
                        model=model,
                        timeout_seconds=settings.affective_appraisal_timeout_seconds,
                        max_output_tokens=settings.affective_appraisal_max_output_tokens,
                        context_window=settings.affective_appraisal_context_window,
                        keep_alive=settings.ollama_keep_alive,
                        http_client=client,
                    )
                    for model in selected_models
                ),
                repetitions=repetitions,
            )
        )
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if output_path is None:
            print(serialized)
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized + "\n", encoding="utf-8")
            print(f"Appraisal report written: {output_path}")
        return 0
    finally:
        client.close()


def _benchmark_contention(
    settings: Settings,
    *,
    repetitions: int,
    scheduled: bool,
    output_path: Path | None,
) -> int:
    """Measure raw contention or the same workload under the priority scheduler."""

    client = OllamaHttpClient(settings.conversation_provider_base_url)
    scheduler = (
        OllamaInferenceScheduler(
            background_aging_seconds=settings.ollama_background_aging_seconds,
            background_grace_seconds=settings.ollama_background_grace_seconds,
        )
        if scheduled
        else None
    )
    try:
        report = asyncio.run(
            run_contention_evaluation(
                OllamaContentionAdapters(
                    conversation=_configured_conversation_provider(
                        settings, http_client=client, scheduler=scheduler
                    ),
                    appraisal=_configured_affective_provider(
                        settings, http_client=client, scheduler=scheduler
                    ),
                    episode=_configured_episode_provider(
                        settings, http_client=client, scheduler=scheduler
                    ),
                    semantic=_configured_semantic_provider(
                        settings, http_client=client, scheduler=scheduler
                    ),
                ),
                repetitions=repetitions,
            )
        )
        serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
        if output_path is None:
            print(serialized)
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(serialized + "\n", encoding="utf-8")
            print(f"Contention report written: {output_path}")
        return 0
    finally:
        client.close()


def _talk(
    database: Database,
    services: InitialSelfServices,
    provider: ConversationProvider,
    episode_provider: EpisodeFormationProvider,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    semantic_provider: SemanticFormationProvider | None,
    appraisal_provider: AffectiveAppraisalProvider | None,
    relationship_provider: RelationshipProvider | None,
    model_provider: ModelFormationProvider | None,
    position_provider: PositionFormationProvider | None,
    reflection_provider: ReflectionGenerationPort | None,
    *,
    user_text: str,
    trace_id: str,
    client_request_id: str,
    session_id: str | None,
) -> int:
    """Run one turn and translate typed expected failures into concise CLI output."""

    conversation = build_conversation_services(
        database,
        services,
        provider,
        episode_provider,
        settings,
        embedding_provider=embedding_provider,
        semantic_provider=semantic_provider,
        appraisal_provider=appraisal_provider,
        relationship_provider=relationship_provider,
        model_provider=model_provider,
        position_provider=position_provider,
        reflection_provider=reflection_provider,
    )
    try:

        async def run_turn() -> None:
            reply = await conversation.talk.execute(
                TalkInput(
                    user_text=user_text,
                    trace_id=trace_id,
                    client_request_id=client_request_id,
                    session_id=session_id,
                )
            )
            print(reply.text, flush=True)
            if not reply.replayed:
                report = await conversation.post_response.execute(
                    reply.interaction_id, trace_id=trace_id
                )
                if not report.succeeded:
                    print(
                        "Reply saved; some memory processing remains retryable.",
                        file=sys.stderr,
                    )

        asyncio.run(run_turn())
    except NotActivated:
        print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
        return 2
    except ConversationProviderError as error:
        print(
            f"Conversation unavailable ({error.provider}/{error.model}): {error}",
            file=sys.stderr,
        )
        return 1
    except (ConversationError, ValueError) as error:
        print(f"Conversation rejected: {error}", file=sys.stderr)
        return 2
    return 0


def _memory_lifecycle_command(
    arguments: argparse.Namespace,
    database: Database,
    services: InitialSelfServices,
    settings: Settings,
    *,
    trace_id: str,
) -> int:
    """Run deterministic explicit owner controls before constructing providers."""

    if arguments.interaction is not None or arguments.include_archived:
        print("Memory lifecycle rejected: incompatible_inspection_options", file=sys.stderr)
        return 2
    target = (
        MemoryLifecycleStatus.ARCHIVED
        if arguments.memory_action == "archive"
        else MemoryLifecycleStatus.ACTIVE
    )
    controls = build_memory_lifecycle_services(database)
    scope = MemoryScope(
        services.get_self.execute().identity.identity_id, settings.default_counterparty_id
    )
    try:
        if arguments.preview:
            if any(
                value is not None
                for value in (arguments.expected_version, arguments.sha256, arguments.request_id)
            ):
                raise MemoryLifecycleError("preview_does_not_apply")
            print(
                controls.preview(arguments.memory_id, target, scope=scope).model_dump_json(indent=2)
            )
            return 0
        if any(
            value is None
            for value in (arguments.expected_version, arguments.sha256, arguments.request_id)
        ):
            raise MemoryLifecycleError("apply_requires_version_hash_and_request_id")
        result = controls.execute(
            MemoryLifecycleCommand(
                memory_id=arguments.memory_id,
                target_status=target,
                expected_version=arguments.expected_version,
                request_id=arguments.request_id,
                preview_digest=arguments.sha256,
            ),
            scope=scope,
            trace_id=trace_id,
        )
        print(result.model_dump_json(indent=2))
        return 0
    except MemoryLifecycleError as error:
        print(f"Memory lifecycle rejected: {error.reason_code}", file=sys.stderr)
        return 1 if error.reason_code == "memory_lifecycle_commit_failed" else 2
    except ValueError:
        print("Memory lifecycle rejected: invalid_command", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("Memory lifecycle unavailable: storage_failure", file=sys.stderr)
        return 1


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    alembic_config: Path = Path("alembic.ini"),
    conversation_provider: ConversationProvider | None = None,
    episode_formation_provider: EpisodeFormationProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    semantic_formation_provider: SemanticFormationProvider | None = None,
    affective_appraisal_provider: AffectiveAppraisalProvider | None = None,
    relationship_appraisal_provider: RelationshipProvider | None = None,
    model_formation_provider: ModelFormationProvider | None = None,
    position_formation_provider: PositionFormationProvider | None = None,
    reflection_generation_provider: ReflectionGenerationPort | None = None,
    chat_input: Callable[[str], str] | None = None,
) -> int:
    """Run one explicitly selected lifecycle or conversation command."""

    process_started = time.perf_counter()
    arguments = build_parser().parse_args(argv)
    active_settings = settings or load_settings()
    if active_settings.all_openai_enabled and not all(
        provider is not None
        for provider in (
            conversation_provider,
            episode_formation_provider,
            embedding_provider,
            semantic_formation_provider,
            affective_appraisal_provider,
            relationship_appraisal_provider,
            model_formation_provider,
            position_formation_provider,
            reflection_generation_provider,
        )
    ):
        print("all_openai_requires_budgeted_server_runtime", file=sys.stderr)
        return 2
    if arguments.command == "state":
        if arguments.state_action == "retention":
            from satori.retention_cli import retention_command

            return retention_command(arguments, active_settings)
        return _whole_state_command(arguments, active_settings, alembic_config=alembic_config)
    if arguments.command == "sources":
        return _source_policy_command(arguments, active_settings, alembic_config=alembic_config)
    if arguments.command == "personality" and arguments.personality_action in {
        "reasons-policy",
        "reason-bodies",
    }:
        from satori.checkpoint_reason_cli import checkpoint_reason_command

        return checkpoint_reason_command(arguments, active_settings)
    if arguments.command == "bootstrap":
        bootstrap(active_settings, alembic_config=alembic_config)
        return 0
    if arguments.command == "benchmark":
        configure_logging(LogLevel.CRITICAL)
        if arguments.benchmark_action == "inference":
            return _benchmark_inference(
                active_settings,
                alembic_config=alembic_config,
                repetitions=arguments.repetitions,
                scenario_ids=frozenset(arguments.scenario),
                include_derived=arguments.include_derived,
                output_path=arguments.output,
            )
        if arguments.benchmark_action == "appraisal":
            return _benchmark_appraisal(
                active_settings,
                repetitions=arguments.repetitions,
                models=tuple(arguments.model),
                output_path=arguments.output,
            )
        if arguments.benchmark_action == "contention":
            return _benchmark_contention(
                active_settings,
                repetitions=arguments.repetitions,
                scheduled=arguments.scheduled,
                output_path=arguments.output,
            )
        raise AssertionError(f"unhandled benchmark action: {arguments.benchmark_action}")

    if arguments.command == "chat":
        configure_logging(
            active_settings.log_level,
            console_level=(LogLevel.DEBUG if arguments.debug else LogLevel.CRITICAL),
            file_path=(
                active_settings.chat_log_path
                if active_settings.environment is not Environment.TEST
                else None
            ),
        )
    else:
        configure_logging(active_settings.log_level)
    database_started = time.perf_counter()
    database, services = _open_services(active_settings, alembic_config=alembic_config)
    database_bootstrap_ms = (time.perf_counter() - database_started) * 1000
    http_clients: dict[str, OllamaHttpClient] = {}
    inference_schedulers: dict[str, OllamaInferenceScheduler] = {}
    yandex_client: YandexAIStudioHttpClient | None = None
    openai_client: OpenAIHttpClient | None = None

    def http_client(base_url: str) -> OllamaHttpClient:
        client = http_clients.get(base_url)
        if client is None:
            client = OllamaHttpClient(base_url)
            http_clients[base_url] = client
        return client

    def inference_scheduler(base_url: str) -> OllamaInferenceScheduler | None:
        if not active_settings.ollama_serialize_inference:
            return None
        scheduler = inference_schedulers.get(base_url)
        if scheduler is None:
            scheduler = OllamaInferenceScheduler(
                background_aging_seconds=active_settings.ollama_background_aging_seconds,
                background_grace_seconds=active_settings.ollama_background_grace_seconds,
            )
            inference_schedulers[base_url] = scheduler
        return scheduler

    def configured_conversation_provider() -> ConversationProvider:
        nonlocal openai_client, yandex_client
        if active_settings.conversation_provider is ConversationProviderKind.OLLAMA:
            return _configured_conversation_provider(
                active_settings,
                http_client=http_client(active_settings.conversation_provider_base_url),
                scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
            )
        if active_settings.conversation_provider is ConversationProviderKind.YANDEX_AI_STUDIO:
            api_key = active_settings.yandex_ai_studio_api_key
            if api_key is None:
                raise AssertionError("validated Yandex AI Studio configuration has no API key")
            if yandex_client is None:
                yandex_client = YandexAIStudioHttpClient(
                    active_settings.yandex_ai_studio_base_url,
                    api_key.get_secret_value(),
                )
            return _configured_conversation_provider(
                active_settings,
                yandex_http_client=yandex_client,
            )
        api_key = active_settings.openai_api_key
        if api_key is None:
            raise AssertionError("validated OpenAI configuration has no API key")
        if openai_client is None:
            openai_client = OpenAIHttpClient(
                active_settings.openai_base_url,
                api_key.get_secret_value(),
            )
        return _configured_conversation_provider(
            active_settings,
            openai_http_client=openai_client,
        )

    try:
        trace_id = Uuid4Generator().new()
        with bind_trace_id(trace_id):
            if arguments.command == "activate":
                try:
                    installation_identity(database)
                except NotActivated:
                    activation_result = _activate(services, trace_id=trace_id)
                    enroll_installation(
                        database,
                        active_settings.source_policy_directory,
                        reviewed=True,
                        new_installation=True,
                    )
                    return activation_result
                bind_installation(database, active_settings.source_policy_directory)
                return _activate(services, trace_id=trace_id)
            try:
                bind_installation(database, active_settings.source_policy_directory)
            except NotActivated:
                if arguments.command == "status":
                    return _status(services)
                print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                return 2
            except SourcePolicyError as error:
                print(
                    f"Source policy rejected: {error}. "
                    "Run reviewed `satori sources enroll` or `sources repair`.",
                    file=sys.stderr,
                )
                return 2
            if arguments.command == "status":
                return _status(services)
            if arguments.command in {"session", "history", "memories", "semantic"}:
                try:
                    services.get_self.execute()
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
            if arguments.command == "memories" and arguments.memory_action in {
                "archive",
                "reactivate",
            }:
                return _memory_lifecycle_command(
                    arguments, database, services, active_settings, trace_id=trace_id
                )
            if arguments.command == "talk":
                provider = conversation_provider or configured_conversation_provider()
                episode_provider = episode_formation_provider or _configured_episode_provider(
                    active_settings,
                    http_client=http_client(active_settings.conversation_provider_base_url),
                    scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
                )
                active_embedding_provider = embedding_provider or _configured_embedding_provider(
                    active_settings,
                    http_client=http_client(active_settings.embedding_provider_base_url),
                )
                active_semantic_provider = semantic_formation_provider
                if (
                    active_semantic_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_semantic_provider = _configured_semantic_provider(
                        active_settings,
                        http_client=http_client(active_settings.conversation_provider_base_url),
                        scheduler=inference_scheduler(
                            active_settings.conversation_provider_base_url
                        ),
                    )
                active_appraisal_provider = affective_appraisal_provider
                if (
                    active_appraisal_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_appraisal_provider = _configured_affective_provider(
                        active_settings,
                        http_client=http_client(
                            active_settings.affective_appraisal_provider_base_url
                        ),
                        scheduler=inference_scheduler(
                            active_settings.affective_appraisal_provider_base_url
                        ),
                    )
                active_relationship_provider = relationship_appraisal_provider
                if (
                    active_relationship_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_relationship_provider = _configured_relationship_provider(
                        active_settings,
                        http_client=http_client(
                            active_settings.relationship_appraisal_provider_base_url
                        ),
                        scheduler=inference_scheduler(
                            active_settings.relationship_appraisal_provider_base_url
                        ),
                    )
                active_model_provider = model_formation_provider
                if (
                    active_model_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_model_provider = _configured_model_provider(
                        active_settings,
                        http_client=http_client(active_settings.conversation_provider_base_url),
                        scheduler=inference_scheduler(
                            active_settings.conversation_provider_base_url
                        ),
                    )
                active_position_provider = position_formation_provider
                if (
                    active_position_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_position_provider = _configured_position_provider(
                        active_settings,
                        http_client=http_client(active_settings.conversation_provider_base_url),
                        scheduler=inference_scheduler(
                            active_settings.conversation_provider_base_url
                        ),
                    )
                active_reflection_provider = reflection_generation_provider
                if (
                    active_reflection_provider is None
                    and active_settings.environment is not Environment.TEST
                ):
                    active_reflection_provider = _configured_reflection_provider(
                        active_settings,
                        http_client=http_client(active_settings.reflection_provider_base_url),
                        scheduler=inference_scheduler(active_settings.reflection_provider_base_url),
                    )
                request_id = arguments.request_id or Uuid4Generator().new()
                return _talk(
                    database,
                    services,
                    provider,
                    episode_provider,
                    active_settings,
                    active_embedding_provider,
                    active_semantic_provider,
                    active_appraisal_provider,
                    active_relationship_provider,
                    active_model_provider,
                    active_position_provider,
                    active_reflection_provider,
                    user_text=arguments.text,
                    trace_id=trace_id,
                    client_request_id=request_id,
                    session_id=arguments.session,
                )
            provider = conversation_provider or configured_conversation_provider()
            episode_provider = episode_formation_provider or _configured_episode_provider(
                active_settings,
                http_client=http_client(active_settings.conversation_provider_base_url),
                scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
            )
            active_embedding_provider = embedding_provider or _configured_embedding_provider(
                active_settings,
                http_client=http_client(active_settings.embedding_provider_base_url),
            )
            active_semantic_provider = semantic_formation_provider
            if (
                active_semantic_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_semantic_provider = _configured_semantic_provider(
                    active_settings,
                    http_client=http_client(active_settings.conversation_provider_base_url),
                    scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
                )
            active_appraisal_provider = affective_appraisal_provider
            if (
                active_appraisal_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_appraisal_provider = _configured_affective_provider(
                    active_settings,
                    http_client=http_client(active_settings.affective_appraisal_provider_base_url),
                    scheduler=inference_scheduler(
                        active_settings.affective_appraisal_provider_base_url
                    ),
                )
            active_relationship_provider = relationship_appraisal_provider
            if (
                active_relationship_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_relationship_provider = _configured_relationship_provider(
                    active_settings,
                    http_client=http_client(
                        active_settings.relationship_appraisal_provider_base_url
                    ),
                    scheduler=inference_scheduler(
                        active_settings.relationship_appraisal_provider_base_url
                    ),
                )
            active_model_provider = model_formation_provider
            if (
                active_model_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_model_provider = _configured_model_provider(
                    active_settings,
                    http_client=http_client(active_settings.conversation_provider_base_url),
                    scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
                )
            active_position_provider = position_formation_provider
            if (
                active_position_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_position_provider = _configured_position_provider(
                    active_settings,
                    http_client=http_client(active_settings.conversation_provider_base_url),
                    scheduler=inference_scheduler(active_settings.conversation_provider_base_url),
                )
            active_reflection_provider = reflection_generation_provider
            if (
                active_reflection_provider is None
                and active_settings.environment is not Environment.TEST
            ):
                active_reflection_provider = _configured_reflection_provider(
                    active_settings,
                    http_client=http_client(active_settings.reflection_provider_base_url),
                    scheduler=inference_scheduler(active_settings.reflection_provider_base_url),
                )
            conversation = build_conversation_services(
                database,
                services,
                provider,
                episode_provider,
                active_settings,
                embedding_provider=active_embedding_provider,
                semantic_provider=active_semantic_provider,
                appraisal_provider=active_appraisal_provider,
                relationship_provider=active_relationship_provider,
                model_provider=active_model_provider,
                position_provider=active_position_provider,
                reflection_provider=active_reflection_provider,
            )
            if arguments.command == "chat":
                runner = InteractiveChat(
                    services=conversation,
                    id_generator=Uuid4Generator(),
                    foreground_provider=active_settings.conversation_provider.value,
                    foreground_model=active_settings.conversation_model,
                    debug=arguments.debug,
                    runtime_startup_ms=(time.perf_counter() - process_started) * 1000,
                    database_bootstrap_ms=database_bootstrap_ms,
                    input_fn=chat_input or input,
                )
                try:
                    return asyncio.run(runner.run(session_id=arguments.session))
                except NotActivated:
                    print(
                        "Сатори не активирована. Сначала выполните `satori activate`.",
                        file=sys.stderr,
                    )
                    return 2
                except (ConversationError, ValueError) as error:
                    print(f"Запуск разговора отклонён: {error}", file=sys.stderr)
                    return 2
            if arguments.command == "session":
                if arguments.session_action == "start":
                    session = conversation.start_session.execute()
                    print(session.session_id)
                    return 0
                if arguments.session_action == "close":
                    session = conversation.close_session.execute(arguments.session_id)
                    print(f"Session closed: {session.session_id}")
                    return 0
            if arguments.command == "history":
                history = conversation.history.execute(session_id=arguments.session)
                for interaction in history.interactions:
                    print(
                        f"[{interaction.session_id}/{interaction.interaction_id}] "
                        f"{interaction.status.value}"
                    )
                    print(f"user: {interaction.user_message.content}")
                    if interaction.assistant_message is not None:
                        print(f"assistant: {interaction.assistant_message.content}")
                return 0
            if arguments.command == "memories":
                if arguments.memory_action == "scope-status":
                    print(conversation.memories.scope_status().model_dump_json(indent=2))
                    return 0
                if arguments.memory_action == "search":
                    if conversation.retrieve_memories is None:
                        raise AssertionError("retrieval service was not composed")
                    result = asyncio.run(
                        conversation.retrieve_memories.execute(
                            RetrievalQuery(
                                scope=MemoryScope(
                                    services.get_self.execute().identity.identity_id,
                                    active_settings.default_counterparty_id,
                                ),
                                text=arguments.text,
                                trace_id=trace_id,
                                cutoff=SystemClock().now(),
                                current_interaction_id=None,
                                operator_query=True,
                            )
                        )
                    )
                    print(
                        f"status={result.status.value} candidates={result.candidate_count} "
                        f"selected={len(result.memories)}"
                    )
                    for memory in result.memories:
                        print(
                            f"[{memory.memory_id}] score={memory.final_score:.6f} "
                            f"semantic={memory.semantic_similarity:.6f} "
                            f"importance={memory.importance:.6f} "
                            f"recency={memory.recency_score:.6f} {memory.summary}"
                        )
                    return 0
                if arguments.memory_action in {"index", "rebuild"}:
                    if conversation.index_memories is None:
                        raise AssertionError("index service was not composed")
                    report = asyncio.run(
                        conversation.index_memories.execute(
                            scope=MemoryScope(
                                services.get_self.execute().identity.identity_id,
                                active_settings.default_counterparty_id,
                            ),
                            trace_id=trace_id,
                            rebuild=arguments.memory_action == "rebuild",
                        )
                    )
                    print(
                        f"space={report.space.key if report.space else 'none'} "
                        f"considered={report.considered} indexed={report.indexed} "
                        f"failed={report.failed}"
                    )
                    return 1 if report.failed else 0
                memories = conversation.memories.execute(
                    interaction_id=arguments.interaction,
                    include_archived=arguments.include_archived,
                )
                for stored_memory in memories:
                    source_ids = ",".join(
                        evidence.source_message_id for evidence in stored_memory.evidence
                    )
                    print(
                        f"[{stored_memory.memory_id}] {stored_memory.summary} "
                        f"(importance={stored_memory.importance:.2f}, "
                        f"confidence={stored_memory.confidence:.2f}, "
                        f"interaction={stored_memory.source_interaction_id}, "
                        f"sources={source_ids}, formation={stored_memory.formation_version}, "
                        f"status={stored_memory.lifecycle_status.value}, "
                        f"version={stored_memory.aggregate_version})"
                    )
                return 0
            if arguments.command == "semantic":
                if arguments.semantic_action == "list":
                    claims = conversation.semantic_claims.list(
                        scope=MemoryScope(
                            services.get_self.execute().identity.identity_id,
                            active_settings.default_counterparty_id,
                        ),
                        active_only=not arguments.all,
                        predicate=arguments.predicate,
                    )
                    for claim in claims:
                        sign = "" if claim.polarity else "NOT "
                        print(
                            f"[{claim.claim_id}] {claim.status.value} {claim.claim_kind.value} "
                            f"{claim.subject}.{claim.predicate}={sign}{claim.value!r} "
                            f"confidence={claim.confidence:.2f} evidence={len(claim.evidence)}"
                        )
                    return 0
                if arguments.semantic_action == "inspect":
                    semantic_inspection = conversation.semantic_claims.inspect(
                        arguments.claim_id,
                        scope=MemoryScope(
                            services.get_self.execute().identity.identity_id,
                            active_settings.default_counterparty_id,
                        ),
                    )
                    if semantic_inspection is None:
                        print("Semantic claim not found.", file=sys.stderr)
                        return 2
                    claim, revisions = semantic_inspection
                    print(
                        f"[{claim.claim_id}] status={claim.status.value} "
                        f"kind={claim.claim_kind.value} version={claim.aggregate_version}"
                    )
                    print(
                        f"{claim.subject}.{claim.predicate}={claim.value!r} "
                        f"polarity={claim.polarity} confidence={claim.confidence:.2f}"
                    )
                    print(
                        f"valid_from={claim.valid_from.isoformat()} "
                        "valid_until="
                        f"{claim.valid_until.isoformat() if claim.valid_until else 'none'}"
                    )
                    for evidence in claim.evidence:
                        print(
                            f"evidence={evidence.semantic_evidence_id} "
                            f"memory={evidence.memory_id} root_message={evidence.root_message_id} "
                            f"interaction={evidence.root_interaction_id} "
                            f"source={evidence.source_kind.value}"
                        )
                    for revision in revisions:
                        print(
                            f"revision={revision.revision_id} v={revision.claim_version} "
                            f"kind={revision.kind.value} reason={revision.reason_code}"
                        )
                    return 0
                if conversation.process_semantic is None or conversation.backfill_semantic is None:
                    print("Semantic formation provider is not configured.", file=sys.stderr)
                    return 2
                if arguments.memory:
                    try:
                        decision = asyncio.run(
                            conversation.process_semantic.execute(
                                arguments.memory,
                                trace_id=trace_id,
                                scope=MemoryScope(
                                    services.get_self.execute().identity.identity_id,
                                    active_settings.default_counterparty_id,
                                ),
                            )
                        )
                    except SemanticFormationProviderError as error:
                        print(
                            f"Semantic formation unavailable ({error.provider}/{error.model}): "
                            f"{error}",
                            file=sys.stderr,
                        )
                        return 1
                    except ValueError as error:
                        print(f"Semantic processing rejected: {error}", file=sys.stderr)
                        return 2
                    print(
                        f"decision={decision.kind.value} reason={decision.reason_code} "
                        f"claims={len(decision.claim_ids)}"
                    )
                    return 0
                backfill_report = asyncio.run(
                    conversation.backfill_semantic.execute(
                        scope=MemoryScope(
                            services.get_self.execute().identity.identity_id,
                            active_settings.default_counterparty_id,
                        ),
                        trace_id=trace_id,
                        limit=arguments.limit or active_settings.semantic_backfill_limit,
                    )
                )
                print(
                    f"considered={backfill_report.considered} "
                    f"applied={backfill_report.applied} "
                    f"skipped={backfill_report.skipped} "
                    f"rejected={backfill_report.rejected} "
                    f"failed={backfill_report.failed}"
                )
                return 1 if backfill_report.failed else 0
            if arguments.command == "models":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                counterparty_id = (
                    getattr(arguments, "counterparty", active_settings.default_counterparty_id)
                    or active_settings.default_counterparty_id
                )
                if arguments.models_action in {"user", "world"}:
                    owner = arguments.models_action
                    if arguments.models_owner_action == "list":
                        stage9_claims: tuple[UserModelClaim | WorldModelClaim, ...]
                        if owner == "user":
                            stage9_claims = conversation.current_models.list_user(
                                identity_id=identity_id,
                                counterparty_id=counterparty_id,
                                current_only=not arguments.all,
                            )
                        else:
                            stage9_claims = conversation.current_models.list_world(
                                identity_id=identity_id,
                                counterparty_id=counterparty_id,
                                current_only=not arguments.all,
                            )
                        for stage9_claim in stage9_claims:
                            _print_current_model_claim(stage9_claim)
                        return 0
                    stage9_inspection: (
                        tuple[
                            UserModelClaim | WorldModelClaim,
                            tuple[ModelClaimRevision, ...],
                        ]
                        | None
                    )
                    if owner == "user":
                        stage9_inspection = conversation.current_models.inspect_user(
                            arguments.claim_id,
                            identity_id=identity_id,
                            counterparty_id=counterparty_id,
                        )
                    else:
                        stage9_inspection = conversation.current_models.inspect_world(
                            arguments.claim_id,
                            identity_id=identity_id,
                            counterparty_id=counterparty_id,
                        )
                    if stage9_inspection is None:
                        print(f"{owner.capitalize()} model claim not found.", file=sys.stderr)
                        return 2
                    _print_current_model_inspection(*stage9_inspection)
                    return 0
                if arguments.models_action == "export":
                    serialized = conversation.current_models.export_json(
                        identity_id=identity_id,
                        counterparty_id=counterparty_id,
                        as_of=SystemClock().now(),
                    )
                    if arguments.output is None:
                        print(serialized)
                    else:
                        if not _write_partial_export(
                            active_settings,
                            arguments.output,
                            serialized,
                            overwrite=arguments.overwrite,
                            alembic_config=alembic_config,
                        ):
                            return 2
                        print(f"Models export written: {arguments.output}")
                    return 0
                if conversation.process_models is None or conversation.backfill_models is None:
                    print("Model formation provider is not configured.", file=sys.stderr)
                    return 2
                if arguments.interaction:
                    try:
                        model_decision = asyncio.run(
                            conversation.process_models.execute(
                                arguments.interaction, trace_id=trace_id
                            )
                        )
                    except ModelFormationProviderError as error:
                        print(
                            f"Model formation unavailable ({error.provider}/{error.model}): "
                            f"{error}",
                            file=sys.stderr,
                        )
                        return 1
                    except ValueError as error:
                        print(f"Model processing rejected: {error}", file=sys.stderr)
                        return 2
                    print(
                        f"decision={model_decision.kind.value} reason={model_decision.reason_code} "
                        f"user_claims={len(model_decision.user_claim_ids)} "
                        f"world_claims={len(model_decision.world_claim_ids)}"
                    )
                    return 0
                model_backfill_report = asyncio.run(
                    conversation.backfill_models.execute(
                        trace_id=trace_id,
                        limit=arguments.limit or active_settings.model_backfill_limit,
                    )
                )
                print(
                    f"considered={model_backfill_report.considered} "
                    f"applied={model_backfill_report.applied} "
                    f"skipped={model_backfill_report.skipped} "
                    f"rejected={model_backfill_report.rejected} "
                    f"failed={model_backfill_report.failed}"
                )
                return 1 if model_backfill_report.failed else 0
            if arguments.command == "positions":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                if arguments.positions_action == "list":
                    for position in conversation.positions.list(
                        identity_id=identity_id,
                        current_only=not arguments.all,
                    ):
                        _print_position(position)
                    return 0
                if arguments.positions_action == "inspect":
                    inspection = conversation.positions.inspect(
                        arguments.position_id, identity_id=identity_id
                    )
                    if inspection is None:
                        print("Satori position not found.", file=sys.stderr)
                        return 2
                    _print_position_inspection(*inspection)
                    return 0
                if arguments.positions_action == "export":
                    serialized = conversation.positions.export_json(identity_id=identity_id)
                    if arguments.output is None:
                        print(serialized)
                    else:
                        if not _write_partial_export(
                            active_settings,
                            arguments.output,
                            serialized,
                            overwrite=arguments.overwrite,
                            alembic_config=alembic_config,
                        ):
                            return 2
                        print(f"Positions export written: {arguments.output}")
                    return 0
                if arguments.positions_action == "inclinations-list":
                    as_of = arguments.as_of or SystemClock().now()
                    inclinations = conversation.positions.list_inclinations(identity_id=identity_id)
                    if any(as_of < item.state_as_of for item in inclinations):
                        print(
                            "Inclination materialization time precedes canonical state.",
                            file=sys.stderr,
                        )
                        return 2
                    print(f"materialized_at={as_of.isoformat()}")
                    for inclination in inclinations:
                        _print_inclination(inclination, as_of=as_of)
                    return 0
                if arguments.positions_action == "inclination-inspect":
                    as_of = arguments.as_of or SystemClock().now()
                    inspected_inclination = conversation.positions.inspect_inclination(
                        arguments.inclination_id,
                        identity_id=identity_id,
                    )
                    if inspected_inclination is None:
                        print("Satori inclination not found.", file=sys.stderr)
                        return 2
                    if as_of < inspected_inclination.state_as_of:
                        print(
                            "Inclination materialization time precedes canonical state.",
                            file=sys.stderr,
                        )
                        return 2
                    _print_inclination_inspection(inspected_inclination, as_of=as_of)
                    return 0
                if arguments.positions_action == "inclination-export":
                    as_of = arguments.as_of or SystemClock().now()
                    try:
                        serialized = conversation.positions.export_inclinations_json(
                            identity_id=identity_id,
                            as_of=as_of,
                        )
                    except ValueError as error:
                        print(f"Inclination export rejected: {error}", file=sys.stderr)
                        return 2
                    if arguments.output is None:
                        print(serialized)
                    else:
                        if not _write_partial_export(
                            active_settings,
                            arguments.output,
                            serialized,
                            overwrite=arguments.overwrite,
                            alembic_config=alembic_config,
                        ):
                            return 2
                        print(f"Inclinations export written: {arguments.output}")
                    return 0
                if (
                    conversation.process_positions is None
                    or conversation.backfill_positions is None
                ):
                    print("Position formation provider is not configured.", file=sys.stderr)
                    return 2
                if arguments.interaction:
                    try:
                        position_decision = asyncio.run(
                            conversation.process_positions.execute(
                                arguments.interaction, trace_id=trace_id
                            )
                        )
                    except PositionFormationProviderError as error:
                        print(
                            f"Position formation unavailable ({error.provider}/{error.model}): "
                            f"{error}",
                            file=sys.stderr,
                        )
                        return 1
                    except ValueError as error:
                        print(f"Position processing rejected: {error}", file=sys.stderr)
                        return 2
                    print(
                        f"decision={position_decision.kind.value} "
                        f"reason={position_decision.reason_code} "
                        f"positions={len(position_decision.position_ids)}"
                    )
                    return 0
                position_backfill_report = asyncio.run(
                    conversation.backfill_positions.execute(
                        trace_id=trace_id,
                        limit=arguments.limit or active_settings.position_backfill_limit,
                    )
                )
                print(
                    f"considered={position_backfill_report.considered} "
                    f"applied={position_backfill_report.applied} "
                    f"skipped={position_backfill_report.skipped} "
                    f"rejected={position_backfill_report.rejected} "
                    f"failed={position_backfill_report.failed}"
                )
                return 1 if position_backfill_report.failed else 0
            if arguments.command == "personality":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                if arguments.personality_action == "inspect":
                    personality_inspection = conversation.personality.evolution.inspect(identity_id)
                    if personality_inspection is None:
                        print("Personality state not found.", file=sys.stderr)
                        return 2
                    _print_personality_inspection(personality_inspection)
                    return 0
                if arguments.personality_action == "compare":
                    comparison = conversation.personality.evolution.compare(
                        identity_id,
                        arguments.checkpoint_id,
                    )
                    if comparison is None:
                        print("Personality checkpoint not found.", file=sys.stderr)
                        return 2
                    _print_personality_comparison(comparison)
                    return 0
                if arguments.personality_action == "export":
                    personality_export_json = conversation.personality.evolution.export_json(
                        identity_id
                    )
                    if personality_export_json is None:
                        print("Personality state not found.", file=sys.stderr)
                        return 2
                    if arguments.output is None:
                        print(personality_export_json)
                    else:
                        if not _write_partial_export(
                            active_settings,
                            arguments.output,
                            personality_export_json,
                            overwrite=arguments.overwrite,
                            alembic_config=alembic_config,
                        ):
                            return 2
                        print(f"Personality export written: {arguments.output}")
                    return 0
                if arguments.personality_action == "process":
                    if (
                        conversation.process_reflection is None
                        or conversation.apply_reflection is None
                    ):
                        print("Reflection provider is not configured.", file=sys.stderr)
                        return 2
                    try:
                        reflection_report = asyncio.run(
                            conversation.process_reflection.execute(
                                identity_id,
                                trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
                                trace_id=trace_id,
                                purpose=ReflectionPurpose.PERSONALITY_EVOLUTION,
                            )
                        )
                        processed_run = reflection_report.run
                        if processed_run is not None and processed_run.status.requires_routing:
                            processed_run = conversation.apply_reflection.execute(
                                processed_run.run_id,
                                trace_id=trace_id,
                            )
                    except (ReflectionProviderError, ValueError) as error:
                        print(f"Personality processing rejected: {error}", file=sys.stderr)
                        return 2
                    if processed_run is None:
                        print(
                            "purpose=personality_evolution "
                            f"run=none reason={reflection_report.reason_code}"
                        )
                    else:
                        print(
                            "purpose=personality_evolution "
                            f"run={processed_run.run_id} status={processed_run.status.value} "
                            f"reason={reflection_report.reason_code} "
                            f"provider_called={str(reflection_report.provider_called).lower()}"
                        )
                    return 0
                try:
                    if arguments.personality_action == "approve":
                        approval = conversation.personality.approve_checkpoint.execute(
                            identity_id,
                            PersonalityCheckpointApprovalProposal(
                                checkpoint_id=arguments.checkpoint_id,
                                checkpoint_hash=arguments.checkpoint_hash,
                                expected_personality_version=arguments.expected_version,
                                reason=arguments.reason,
                            ),
                            trace_id=trace_id,
                        )
                        print(
                            f"approved checkpoint={approval.checkpoint_id} "
                            f"hash={approval.checkpoint_hash} "
                            f"version={approval.expected_aggregate_version}"
                        )
                        return 0
                    restore = conversation.personality.restore_checkpoint.execute(
                        identity_id,
                        PersonalityRestoreProposal(
                            checkpoint_id=arguments.checkpoint_id,
                            checkpoint_hash=arguments.checkpoint_hash,
                            expected_personality_version=arguments.expected_version,
                            reason=arguments.reason,
                        ),
                        trace_id=trace_id,
                    )
                except (RuntimeError, ValueError) as error:
                    print(f"Personality command rejected: {error}", file=sys.stderr)
                    return 2
                print(
                    f"restored={str(restore.restored).lower()} "
                    f"reason={restore.evaluation.reason_code} "
                    f"aggregate_version={restore.personality.aggregate_version}"
                )
                return 0 if restore.restored else 2
            if arguments.command == "reflection":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                if arguments.reflection_action == "list":
                    for run in conversation.reflections.list(
                        identity_id=identity_id, limit=arguments.limit
                    ):
                        print(
                            f"[{run.run_id}] status={run.status.value} "
                            f"trigger={run.trigger_kind.value} attempts={run.attempt_count} "
                            f"source_set={run.source_set_hash}"
                        )
                    return 0
                if arguments.reflection_action == "inspect":
                    reflection_inspection = conversation.reflections.inspect(
                        arguments.run_id,
                        identity_id=identity_id,
                        show_sources=arguments.show_sources,
                    )
                    if reflection_inspection is None:
                        print("Reflection run not found.", file=sys.stderr)
                        return 2
                    print(
                        f"run={reflection_inspection.run.run_id} "
                        f"status={reflection_inspection.run.status.value} "
                        f"trigger={reflection_inspection.run.trigger_kind.value} "
                        f"policy={reflection_inspection.run.policy_version} "
                        f"schema={reflection_inspection.run.schema_version}"
                    )
                    for source in reflection_inspection.sources:
                        line = (
                            f"source[{source.source_id}] kind={source.kind.value} "
                            f"edge={source.evidence_edge_id}@{source.evidence_edge_version} "
                            f"root={source.root_interaction_id}/{source.root_message_id} "
                            f"hash={source.content_hash}"
                        )
                        if isinstance(source, ReflectionSource):
                            serialized_quote = json.dumps(source.quote, ensure_ascii=False)
                            line += f" quote={serialized_quote}"
                        print(line)
                    for attempt in reflection_inspection.attempts:
                        print(
                            f"attempt[{attempt.ordinal}] status={attempt.status.value} "
                            f"reason={attempt.reason_code} "
                            f"provider={attempt.provider}/{attempt.model}"
                        )
                    for proposal in reflection_inspection.proposals:
                        serialized_payload = json.dumps(
                            proposal.payload, ensure_ascii=False, sort_keys=True
                        )
                        print(
                            f"proposal[{proposal.ordinal}] id={proposal.proposal_id} "
                            f"target={proposal.target_owner.value} "
                            f"sources={','.join(proposal.evidence_source_ids)} "
                            f"payload={serialized_payload}"
                        )
                    for header in reflection_inspection.unavailable_proposals:
                        print(
                            f"proposal[{header.ordinal}] id={header.proposal_id} "
                            f"target={header.target_owner} "
                            f"sources={','.join(header.evidence_source_ids)} "
                            "payload=unavailable"
                        )
                    for outcome in reflection_inspection.outcomes:
                        print(
                            f"outcome[{outcome.outcome_id}] proposal={outcome.proposal_id} "
                            f"decision={outcome.decision.value} reason={outcome.reason_code}"
                        )
                    return 0
                if conversation.process_reflection is None or conversation.apply_reflection is None:
                    print("Reflection provider is not configured.", file=sys.stderr)
                    return 2
                try:
                    reflection_report = asyncio.run(
                        conversation.process_reflection.execute(
                            identity_id,
                            trigger=ReflectionTriggerKind.EXPLICIT_LOCAL,
                            trace_id=trace_id,
                        )
                    )
                    processed_run = reflection_report.run
                    if processed_run is not None and processed_run.status.requires_routing:
                        processed_run = conversation.apply_reflection.execute(
                            processed_run.run_id, trace_id=trace_id
                        )
                except (ReflectionProviderError, ValueError) as error:
                    print(f"Reflection processing rejected: {error}", file=sys.stderr)
                    return 2
                if processed_run is None:
                    print(f"run=none reason={reflection_report.reason_code}")
                else:
                    print(
                        f"run={processed_run.run_id} status={processed_run.status.value} "
                        f"reason={reflection_report.reason_code} "
                        f"provider_called={str(reflection_report.provider_called).lower()}"
                    )
                return 0
            if arguments.command == "emotion":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                if arguments.emotion_action == "status":
                    emotion = conversation.emotion_status.execute(identity_id)
                    fast = emotion.state.fast
                    mood = emotion.state.mood
                    print(
                        f"state_version={emotion.state.state_version} "
                        f"mood_version={emotion.state.mood_version} "
                        f"as_of={emotion.state.as_of.isoformat()}"
                    )
                    print(
                        "fast "
                        + " ".join(f"{key}={value:.6f}" for key, value in fast.as_mapping().items())
                    )
                    print(
                        "mood "
                        + " ".join(f"{key}={value:.6f}" for key, value in mood.as_mapping().items())
                    )
                    print(f"last_transition={emotion.last_transition_id or 'none'}")
                    return 0
                transitions = conversation.emotion_history.execute(limit=arguments.limit)
                for transition in transitions:
                    print(
                        f"[{transition.transition_id}] interaction={transition.interaction_id} "
                        f"committed_at={transition.committed_at.isoformat()} "
                        f"state={transition.before.state_version}->"
                        f"{transition.after.state_version} "
                        f"policy={transition.after.emotion_policy_version} "
                        f"sources={','.join(transition.source_refs)} "
                        f"explanation={transition.explanation_state}"
                    )
                    print(
                        "delta "
                        + " ".join(
                            f"{key}={value:+.6f}"
                            for key, value in transition.applied_delta.as_mapping().items()
                        )
                    )
                return 0
            if arguments.command == "relationship":
                try:
                    identity_id = services.get_identity.execute().identity_id
                except NotActivated:
                    print("Satori is not activated. Run `satori activate` first.", file=sys.stderr)
                    return 2
                counterparty_id = active_settings.default_counterparty_id
                if arguments.relationship_action == "status":
                    relationship_status = conversation.relationship_status.execute(
                        identity_id, counterparty_id
                    )
                    state = relationship_status.state
                    print(
                        f"relationship={state.relationship_id} "
                        f"counterparty={state.counterparty_id} "
                        f"state_version={state.state_version} "
                        f"policy_version={state.policy_version} "
                        f"maturity={state.maturity:.6f}"
                    )
                    print(
                        "dimensions "
                        + " ".join(
                            f"{key}={value:.6f}" for key, value in state.vector.as_mapping().items()
                        )
                    )
                    print(
                        "evidence "
                        f"processed={state.processed_interaction_count} "
                        f"qualified={state.qualified_interaction_count} "
                        f"sessions={state.distinct_session_count} "
                        f"positive={state.positive_evidence_count} "
                        f"negative={state.negative_evidence_count}"
                    )
                    print(f"last_transition={relationship_status.last_transition_id or 'none'}")
                    return 0
                if arguments.relationship_action == "history":
                    relationship_history = conversation.relationship_history.execute(
                        identity_id,
                        counterparty_id,
                        limit=arguments.limit,
                    )
                    for relationship_transition in relationship_history.transitions:
                        print(
                            f"[{relationship_transition.transition_id}] "
                            f"interaction={relationship_transition.interaction_id} "
                            f"state={relationship_transition.before.state_version}->"
                            f"{relationship_transition.after.state_version} "
                            "categories="
                            f"{
                                ','.join(item.value for item in relationship_transition.categories)
                            } "
                            f"committed_at={relationship_transition.committed_at.isoformat()}"
                        )
                        print(
                            "delta "
                            + " ".join(
                                f"{key}={value:+.6f}"
                                for key, value in relationship_transition.delta.as_mapping().items()
                            )
                        )
                    return 0
                if (
                    conversation.process_relationship is None
                    or conversation.backfill_relationship is None
                ):
                    print("Relationship appraisal provider is not configured.", file=sys.stderr)
                    return 2
                if arguments.limit is not None:
                    relationship_backfill = asyncio.run(
                        conversation.backfill_relationship.execute(
                            identity_id,
                            counterparty_id,
                            limit=arguments.limit,
                        )
                    )
                    print(
                        f"considered={relationship_backfill.considered} "
                        f"attempted={relationship_backfill.attempted} "
                        f"applied={relationship_backfill.applied} "
                        f"skipped={relationship_backfill.skipped} "
                        f"rejected={relationship_backfill.rejected} "
                        f"replayed={relationship_backfill.replayed} "
                        f"failed={relationship_backfill.failed}"
                    )
                    return 1 if relationship_backfill.failed else 0
                try:
                    assert arguments.interaction is not None
                    relationship_report = asyncio.run(
                        conversation.process_relationship.execute(
                            arguments.interaction, trace_id=trace_id
                        )
                    )
                except RelationshipAppraisalProviderError as error:
                    print(
                        f"Relationship appraisal unavailable ({error.provider}/{error.model}): "
                        f"{error}",
                        file=sys.stderr,
                    )
                    return 1
                except ValueError as error:
                    print(f"Relationship processing rejected: {error}", file=sys.stderr)
                    return 2
                print(
                    f"decision={relationship_report.decision_kind} "
                    f"reason={relationship_report.reason_code} "
                    f"replayed={str(relationship_report.replayed).lower()}"
                )
                return 0
    except SourcePolicyError as error:
        print(f"Source policy rejected: {error.reason_code}", file=sys.stderr)
        return 2
    except ConversationError:
        print("Conversation operation unavailable in the current scope.", file=sys.stderr)
        return 2
    finally:
        for client in http_clients.values():
            client.close()
        if yandex_client is not None:
            yandex_client.close()
        if openai_client is not None:
            openai_client.close()
        database.dispose()
    raise AssertionError(f"unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
