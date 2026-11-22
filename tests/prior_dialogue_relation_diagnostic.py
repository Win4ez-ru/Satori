"""Versioned public relation-to-selection transport corpus.

Authored prior replies and supplied categorical proposals are synthetic fixtures.
They are not desired current Satori replies or evidence of model understanding.
The optional command runs managed Talk with fake providers only and emits structural
metadata. It has no credential loader, network provider, semantic judge or score.
"""

# ruff: noqa: RUF001  # Public authored Russian transport fixtures.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PRIOR_DIALOGUE_RELATION_DIAGNOSTIC_VERSION = 1
RelationKind = Literal[
    "shift_from_prior_move", "continues", "closes", "prior_already_closed", "abstain"
]


@dataclass(frozen=True, slots=True)
class PriorRelationScenario:
    """A matched off/on input, never a golden current response."""

    name: str
    history: tuple[tuple[str, str], ...]
    user_text: str
    proposal_kind: RelationKind
    expected_shift: bool = False
    expected_operation: bool = True


EXCHANGE_INPUT = "Думаю устроить во дворе вечер обмена вещами для соседей."
EXCHANGE_MOVE = "Мне ближе общий стол: так новичкам проще присоединиться."
NATURAL_SHIFT = "Сегодня впервые починил молнию на рюкзаке."
RELEVANT_SHIFT = "Пока выбираю цвет вывески для этого вечера."
PRIOR_DIALOGUE_RELATION_DIAGNOSTIC: tuple[PriorRelationScenario, ...] = (
    PriorRelationScenario(
        "still-relevant-own-move",
        ((EXCHANGE_INPUT, EXCHANGE_MOVE),),
        RELEVANT_SHIFT,
        "shift_from_prior_move",
        expected_shift=True,
    ),
    PriorRelationScenario(
        "unrelated-natural-shift",
        ((EXCHANGE_INPUT, EXCHANGE_MOVE),),
        NATURAL_SHIFT,
        "abstain",
    ),
    PriorRelationScenario(
        "same-shift-after-resolution",
        (
            (EXCHANGE_INPUT, EXCHANGE_MOVE),
            ("Не продолжай тему. На этом всё.", "Хорошо, на этом остановимся."),
        ),
        RELEVANT_SHIFT,
        "prior_already_closed",
    ),
    PriorRelationScenario(
        "answer-to-own-question",
        ((EXCHANGE_INPUT, "Где ты поставишь общий стол?"),),
        "Поставлю его посередине двора.",
        "continues",
    ),
    PriorRelationScenario(
        "recognized-direct-task",
        ((EXCHANGE_INPUT, EXCHANGE_MOVE),),
        "Объясни два пункта: что делает zip и чем он отличается от enumerate в Python.",
        "shift_from_prior_move",
        expected_operation=False,
    ),
    PriorRelationScenario(
        "explicit-stop",
        ((EXCHANGE_INPUT, EXCHANGE_MOVE),),
        "Не продолжай тему. На этом всё.",
        "closes",
        expected_operation=False,
    ),
    PriorRelationScenario(
        "missing-past", (), NATURAL_SHIFT, "shift_from_prior_move", expected_operation=False
    ),
    PriorRelationScenario(
        "oversize-latest-no-older-scavenging",
        (
            (EXCHANGE_INPUT, "Где ты поставишь общий стол?"),
            ("Есть ещё одно уточнение о площадке.", "SYNTHETIC-OVERSIZE-" + "x" * 9000),
        ),
        NATURAL_SHIFT,
        "shift_from_prior_move",
        expected_operation=False,
    ),
)


def main() -> None:
    """Standalone offline diagnostic, writing a new private metadata file only."""

    import argparse
    import hashlib
    import json
    import os
    import sys
    import tempfile
    from pathlib import Path

    import satori
    from tests.test_prior_dialogue_relation_integration import RelationCaseFactory, run_matched_case

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    assert satori.__file__ is not None
    package_root = Path(satori.__file__).resolve().parent

    def bindings() -> dict[str, object]:
        def hashes(directory: Path) -> dict[str, str]:
            return {
                str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(directory.rglob("*"))
                if path.is_file() and path.suffix in {".py", ".json"}
            }

        source = hashes(project_root / "src" / "satori")
        installed = hashes(package_root)
        scripts = {
            name: hashlib.sha256((project_root / "tests" / name).read_bytes()).hexdigest()
            for name in (
                "prior_dialogue_relation_diagnostic.py",
                "test_prior_dialogue_relation_integration.py",
            )
        }
        return {
            "source_sha256": hashlib.sha256(
                json.dumps(source, sort_keys=True).encode()
            ).hexdigest(),
            "installed_sha256": hashlib.sha256(
                json.dumps(installed, sort_keys=True).encode()
            ).hexdigest(),
            "source_file_count": len(source),
            "installed_file_count": len(installed),
            "installed_equal_source": source == installed,
            "script_sha256": scripts,
        }

    before = bindings()
    if not before["installed_equal_source"]:
        raise RuntimeError("offline diagnostic requires exact installed/source package parity")
    with tempfile.TemporaryDirectory(prefix="satori-prior-relation-offline-") as temporary:
        factory = RelationCaseFactory(Path(temporary), project_root)
        try:
            observations = []
            for scenario in PRIOR_DIALOGUE_RELATION_DIAGNOSTIC:
                try:
                    row = run_matched_case(factory, scenario)
                except Exception as error:
                    row = {
                        "name": scenario.name,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "human_score": None,
                    }
                else:
                    row["status"] = "passed"
                observations.append(row)
        finally:
            factory.dispose()
    passed = all(row["status"] == "passed" for row in observations)
    after = bindings()
    passed = passed and before == after
    report = {
        "schema_version": 1,
        "corpus_version": PRIOR_DIALOGUE_RELATION_DIAGNOSTIC_VERSION,
        "kind": "scripted_actual_talk_relation_transport",
        "workspace": str(project_root),
        "runtime_python": sys.executable,
        "scope": "isolated Character transport; no current Foundations or full gate verdict",
        "status": "passed" if passed else "failed",
        "rows": observations,
        "real_provider_calls": 0,
        "credential_reads": 0,
        "semantic_quality_claimed": False,
        "emotion_inertia_claimed": False,
        "human_score": None,
        "bindings_before": before,
        "bindings_after": after,
        "bindings_unchanged": before == after,
    }
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    print(
        json.dumps(
            {"status": report["status"], "rows": len(observations), "real_provider_calls": 0}
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
