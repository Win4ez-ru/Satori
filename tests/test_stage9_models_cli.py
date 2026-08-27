"""Stage 9 local inspection and export command surface."""

from pathlib import Path

from satori.__main__ import build_parser


def test_models_cli_exposes_partitioned_inspection_export_and_processing() -> None:
    parser = build_parser()

    user = parser.parse_args(["models", "user", "list", "--all", "--counterparty", "alice"])
    assert user.models_action == "user"
    assert user.models_owner_action == "list"
    assert user.all is True
    assert user.counterparty == "alice"

    world = parser.parse_args(
        ["models", "world", "inspect", "world-claim-1", "--counterparty", "bob"]
    )
    assert world.models_action == "world"
    assert world.claim_id == "world-claim-1"
    assert world.counterparty == "bob"

    exported = parser.parse_args(
        ["models", "export", "--output", "models.json", "--counterparty", "carol"]
    )
    assert exported.models_action == "export"
    assert exported.output == Path("models.json")
    assert exported.counterparty == "carol"

    process = parser.parse_args(["models", "process", "--interaction", "interaction-1"])
    assert process.models_action == "process"
    assert process.interaction == "interaction-1"
