"""Stage 11 local position inspection, export and processing command surface."""

from pathlib import Path

from satori.__main__ import build_parser


def test_positions_cli_exposes_identity_global_lifecycle_surfaces() -> None:
    parser = build_parser()

    listed = parser.parse_args(["positions", "list", "--all"])
    assert listed.positions_action == "list"
    assert listed.all is True

    inspected = parser.parse_args(["positions", "inspect", "position-1"])
    assert inspected.positions_action == "inspect"
    assert inspected.position_id == "position-1"

    exported = parser.parse_args(["positions", "export", "--output", "positions.json"])
    assert exported.positions_action == "export"
    assert exported.output == Path("positions.json")

    process = parser.parse_args(["positions", "process", "--interaction", "interaction-1"])
    assert process.positions_action == "process"
    assert process.interaction == "interaction-1"
