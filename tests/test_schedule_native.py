"""Compile actual native validation/classification with an account-free fixture entry point.

The fixture replaces only the production main call. It never invokes handle, accessStatus,
EKEventStore, Vision or Calendar permission APIs; all inputs are public synthetic records.
"""

import copy
import hashlib
import json
import platform
import shutil
import subprocess
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_SHARED_CORPUS = (
    Path(__file__).resolve().parents[1] / "docs/contracts/schedule-import-v1.examples.json"
)
_SHARED_ACCEPTED = json.loads(_SHARED_CORPUS.read_text(encoding="utf-8"))["accepted"]

_HARNESS = r"""
private func fixtureMain() -> Int32 {
    do {
        let request = try readRequest()
        if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "eligibility" {
            let allows = CommandLine.arguments[2] == "true"
            let supports = CommandLine.arguments[3] == "true"
            let descriptor = CalendarDescriptor(source_id: "PUBLIC SOURCE",
                calendar_id: "PUBLIC CALENDAR", title: "PUBLIC CALENDAR",
                source_title: "PUBLIC SOURCE",
                writable: deriveEligibility(allowsModification: allows, supportsEvents: supports),
                allows_modification: allows, supports_events: supports, source_kind: "local")
            print(String(data: try JSONEncoder().encode(descriptor), encoding: .utf8)!)
            return 0
        }
        if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "bounds" {
            let rectangle: CGRect
            switch CommandLine.arguments[2] {
            case "valid": rectangle = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4)
            case "edge": rectangle = CGRect(x: 0, y: 0, width: 1, height: 1)
            case "negative_x": rectangle = CGRect(x: -0.1, y: 0.2, width: 0.3, height: 0.4)
            case "negative_y": rectangle = CGRect(x: 0.1, y: -0.2, width: 0.3, height: 0.4)
            case "zero_width": rectangle = CGRect(x: 0.1, y: 0.2, width: 0, height: 0.4)
            case "zero_height": rectangle = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0)
            case "negative_width": rectangle = CGRect(x: 0.1, y: 0.2, width: -0.3, height: 0.4)
            case "overflow_x": rectangle = CGRect(x: 0.9, y: 0.2, width: 0.3, height: 0.4)
            case "overflow_y": rectangle = CGRect(x: 0.1, y: 0.9, width: 0.3, height: 0.4)
            case "nan": rectangle = CGRect(x: CGFloat.nan, y: 0.2, width: 0.3, height: 0.4)
            default: rectangle = CGRect(x: 0.1, y: 0.2, width: CGFloat.infinity, height: 0.4)
            }
            let line = OCRLine(text: "PUBLIC OCR", confidence: 0.75,
                               bounds: try normalizedOCRBounds(rectangle))
            print(String(data: try JSONEncoder().encode(line), encoding: .utf8)!)
            return 0
        }
        if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "envelope" {
            let output = Output(descriptor: dup(STDOUT_FILENO))
            output.context(request, access: .unknown)
            output.send(output.reply(error: .operationTimeout))
            output.send(output.reply()) // A late success must never replace the first outcome.
            return output.exitCode
        }
        if CommandLine.arguments.count > 1 && CommandLine.arguments[1] == "classify" {
            let scope = try scheduleScope(request)
            let row = scope.rows[0]
            let name = CommandLine.arguments[2]
            var probes: [ScheduleEventProbe] = []
            if name != "absent" {
                let shifted = name == "changed_time"
                let other = name == "unowned" || name == "other_token"
                let probe = ScheduleEventProbe(
                    title: name == "changed_title" ? "OTHER PUBLIC TITLE"
                        : row.row.title.precomposedStringWithCanonicalMapping,
                    start: shifted ? row.start.addingTimeInterval(60) : row.start,
                    end: row.end, zone: name == "changed_zone" ? "UTC" : row.row.time_zone,
                    url: name == "unowned" ? nil : scheduleMarker(
                        other ? String(repeating: "b", count: 64) : row.row.owned_token),
                    allDay: name == "all_day", recurring: name == "recurrence",
                    alarms: name == "alarms", attendees: name == "attendees")
                probes = name == "duplicate_owned" ? [probe, probe] : [probe]
            }
            print(classifyScheduleRow(row, probes: probes).rawValue)
        } else {
            switch request.operation {
            case .preflightSchedule, .createSchedule, .reconcileSchedule:
                _ = try scheduleScope(request)
            case .readEvents:
                _ = try readScope(request)
            default: break
            }
            print("valid")
        }
        return 0
    } catch {
        print("invalid")
        return 1
    }
}
exit(fixtureMain())
"""


@pytest.fixture(scope="module")
def native_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if platform.system() != "Darwin" or shutil.which("xcrun") is None:
        pytest.skip("Account-free native fixtures require the macOS Swift compiler")
    root = Path(__file__).resolve().parents[1]
    source = (root / "src/satori/desktop/calendar_native/main.swift").read_text(encoding="utf-8")
    assert source.endswith("exit(main())\n")
    directory = tmp_path_factory.mktemp("schedule-native-fixture")
    harness = directory / "main.swift"
    harness.write_text(source.removesuffix("exit(main())\n") + _HARNESS, encoding="utf-8")
    binary = directory / "account-free-fixture"
    result = subprocess.run(
        [
            "/usr/bin/xcrun",
            "swiftc",
            "-swift-version",
            "5",
            "-module-cache-path",
            str(directory / "module-cache"),
            "-framework",
            "Foundation",
            "-framework",
            "EventKit",
            "-framework",
            "Vision",
            "-framework",
            "ImageIO",
            "-framework",
            "CoreGraphics",
            "-framework",
            "CryptoKit",
            str(harness),
            "-o",
            str(binary),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    return binary


def schedule_request(operation: str = "preflight_schedule") -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": 1,
        "request_id": "123e4567-e89b-42d3-a456-426614174000",
        "operation": operation,
        "target": {"source_id": "PUBLIC SOURCE", "calendar_id": "PUBLIC CALENDAR"},
        "plan_fingerprint": "c" * 64,
        "rows": [
            {
                "row_id": "123e4567-e89b-42d3-a456-426614174001",
                "title": "PUBLIC SHIFT",
                "start": "2026-10-26T09:00:00+03:00",
                "end": "2026-10-26T17:00:00+03:00",
                "time_zone": "Europe/Moscow",
                "owned_token": "a" * 64,
            }
        ],
    }
    refresh_token(result)
    return result


def refresh_token(request: dict[str, Any]) -> None:
    for row in request["rows"]:
        start = datetime.fromisoformat(row["start"]).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = datetime.fromisoformat(row["end"]).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = unicodedata.normalize("NFC", row["title"])
        body = f"satori-shift-v1\n{start}\n{end}\n{title}"
        row["owned_token"] = hashlib.sha256(body.encode()).hexdigest()


def run_fixture(binary: Path, request: dict[str, Any] | str, *arguments: str) -> tuple[int, str]:
    payload = request if isinstance(request, str) else json.dumps(request, ensure_ascii=False)
    result = subprocess.run(
        [str(binary), *arguments],
        input=payload.encode(),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert not result.stderr
    return result.returncode, result.stdout.decode().strip()


@pytest.mark.parametrize(
    "operation", ["preflight_schedule", "create_schedule", "reconcile_schedule"]
)
def test_native_schedule_validates_without_access_or_event_store(
    native_fixture: Path, operation: str
) -> None:
    assert run_fixture(native_fixture, schedule_request(operation)) == (0, "valid")


@pytest.mark.parametrize(
    ("allows", "supports", "writable"),
    [(False, False, False), (False, True, False), (True, False, False), (True, True, True)],
)
def test_native_catalog_eligibility_exposes_facts_without_changing_the_conjunction(
    native_fixture: Path, allows: bool, supports: bool, writable: bool
) -> None:
    code, encoded = run_fixture(
        native_fixture,
        schedule_request(),
        "eligibility",
        str(allows).lower(),
        str(supports).lower(),
    )
    assert code == 0
    descriptor = json.loads(encoded)
    assert set(descriptor) == {
        "source_id",
        "calendar_id",
        "title",
        "source_title",
        "writable",
        "allows_modification",
        "supports_events",
        "source_kind",
    }
    assert descriptor["allows_modification"] is allows
    assert descriptor["supports_events"] is supports
    assert descriptor["writable"] is writable


@pytest.mark.parametrize("name", ["valid", "edge"])
def test_native_ocr_bounds_use_valid_normalized_bottom_left_coordinates(
    native_fixture: Path, name: str
) -> None:
    code, encoded = run_fixture(native_fixture, schedule_request(), "bounds", name)
    assert code == 0
    line = json.loads(encoded)
    assert set(line) == {"text", "confidence", "bounds"}
    assert line["text"] == "PUBLIC OCR"
    assert line["confidence"] == 0.75
    bounds = line["bounds"]
    assert set(bounds) == {"x", "y", "width", "height"}
    if name == "valid":
        assert bounds == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    else:
        assert bounds == {"x": 0, "y": 0, "width": 1, "height": 1}


@pytest.mark.parametrize(
    "name",
    [
        "negative_x",
        "negative_y",
        "zero_width",
        "zero_height",
        "negative_width",
        "overflow_x",
        "overflow_y",
        "nan",
        "infinity",
    ],
)
def test_native_ocr_bounds_reject_invalid_geometry_without_ocr_or_accounts(
    native_fixture: Path, name: str
) -> None:
    assert run_fixture(native_fixture, schedule_request(), "bounds", name) == (1, "invalid")


@pytest.mark.parametrize("vector", _SHARED_ACCEPTED, ids=lambda vector: vector["id"])
def test_native_independently_accepts_shared_portable_fingerprint_goldens(
    native_fixture: Path, vector: dict[str, Any]
) -> None:
    """The parent validates portable top-level keys; native validates the lower execution wire.

    Feed the authored expected tokens directly, without Python token recomputation, so
    scheduleScope must independently agree with all five cross-client SHA256 goldens.
    The fixture entry point never invokes handle or an EventKit authorization/store API.
    """

    assert len(_SHARED_ACCEPTED) == 5
    portable = vector["plan"]
    request = {
        "schema_version": 1,
        "request_id": str(uuid4()),
        "operation": "preflight_schedule",
        "target": {"source_id": "PUBLIC SOURCE", "calendar_id": "PUBLIC CALENDAR"},
        "plan_fingerprint": uuid4().hex + uuid4().hex,
        "rows": [
            {
                "row_id": str(uuid4()),
                "title": shift["title"],
                "start": shift["start"],
                "end": shift["end"],
                "time_zone": portable["time_zone"],
                "owned_token": fingerprint,
            }
            for shift, fingerprint in zip(
                portable["shifts"], vector["expected_row_fingerprints"], strict=True
            )
        ],
    }
    assert run_fixture(native_fixture, request) == (0, "valid")


_INVALID_MUTATIONS: list[Callable[[dict[str, Any]], None]] = [
    lambda value: value.update(schema_version=True),
    lambda value: value.update(schema_version=1.0),
    lambda value: value.update(command="arbitrary executor"),
    lambda value: value.update(operation="delete_events"),
    lambda value: value.update(plan_fingerprint="C" * 64),
    lambda value: value.update(plan_fingerprint="c" * 63),
    lambda value: value.update(target={"source_id": "PUBLIC", "calendar_id": "*"}),
    lambda value: value["target"].update(unreviewed=True),
    lambda value: value.update(rows=[]),
    lambda value: value.update(rows=value["rows"] * 101),
    lambda value: value["rows"][0].update(row_id="00000000-0000-0000-0000-000000000000"),
    lambda value: value["rows"][0].update(owned_token="not-a-fingerprint"),
    lambda value: value["rows"][0].update(title=""),
    lambda value: value["rows"][0].update(title="   "),
    lambda value: value["rows"][0].update(title="x" * 121),
    lambda value: value["rows"][0].update(title="PUBLIC\nSHIFT"),
    lambda value: value["rows"][0].update(title="PUBLIC\u0085SHIFT"),
    lambda value: value["rows"][0].update(alarms=[1]),
    lambda value: value["rows"][0].update(attendees=["PUBLIC"]),
    lambda value: value["rows"][0].update(start="2026-10-26T09:00:00"),
    lambda value: value["rows"][0].update(start="2026-10-26T09:00:00.001+03:00"),
    lambda value: value["rows"][0].update(start="2026-02-30T09:00:00+03:00"),
    lambda value: value["rows"][0].update(start="2026-10-26T09:00:00Z"),
    lambda value: value["rows"][0].update(end="2026-10-26T09:00:00+03:00"),
    lambda value: value["rows"][0].update(end="2026-10-28T09:01:00+03:00"),
    lambda value: value["rows"][0].update(time_zone="Unknown/Zone"),
]


@pytest.mark.parametrize("mutation", _INVALID_MUTATIONS)
def test_native_rejects_unreviewed_scope_before_any_account_api(
    native_fixture: Path, mutation: Callable[[dict[str, Any]], None]
) -> None:
    request = schedule_request()
    mutation(request)
    assert run_fixture(native_fixture, request) == (1, "invalid")


def test_native_rejects_duplicate_and_escaped_equivalent_keys(native_fixture: Path) -> None:
    payload = json.dumps(schedule_request())
    for key in ('"title":"SECOND",', '"t\\u0069tle":"SECOND",'):
        duplicate = payload.replace('"title":', key + '"title":', 1)
        assert run_fixture(native_fixture, duplicate) == (1, "invalid")


def test_native_rejects_duplicate_rows_and_excessive_plan_span(native_fixture: Path) -> None:
    request = schedule_request()
    second = copy.deepcopy(request["rows"][0])
    second["row_id"] = "123e4567-e89b-42d3-a456-426614174002"
    second["owned_token"] = "b" * 64
    request["rows"].append(second)
    assert run_fixture(native_fixture, request) == (1, "invalid")
    second["start"] = "2026-12-28T09:00:00+03:00"
    second["end"] = "2026-12-28T17:00:00+03:00"
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (1, "invalid")


def test_native_accepts_explicit_dst_branch_and_rejects_nonexistent_local_offset(
    native_fixture: Path,
) -> None:
    request = schedule_request()
    row = request["rows"][0]
    row.update(
        start="2026-11-01T01:30:00-04:00",
        end="2026-11-01T02:30:00-05:00",
        time_zone="America/New_York",
    )
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (0, "valid")
    row.update(start="2026-03-08T02:30:00-05:00", end="2026-03-08T04:30:00-04:00")
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (1, "invalid")


def test_native_portable_token_uses_utc_seconds_and_nfc_title(native_fixture: Path) -> None:
    request = schedule_request()
    request["rows"][0]["title"] = "PUBLIC Cafe\u0301 SHIFT"
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (0, "valid")
    request["rows"][0]["owned_token"] = "a" * 64
    assert run_fixture(native_fixture, request) == (1, "invalid")


def test_native_accepts_exact_whole_seconds_from_portable_plan(native_fixture: Path) -> None:
    request = schedule_request()
    request["rows"][0].update(start="2026-10-26T09:00:17+03:00", end="2026-10-26T17:00:41+03:00")
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (0, "valid")


@pytest.mark.parametrize("title", ["  PUBLIC SHIFT  ", "PUBLIC\u200eSHIFT"])
def test_native_preserves_portable_title_spaces_and_noncontrol_format_characters(
    native_fixture: Path, title: str
) -> None:
    request = schedule_request()
    request["rows"][0]["title"] = title
    refresh_token(request)
    assert run_fixture(native_fixture, request) == (0, "valid")


def test_native_owned_matching_uses_nfc_title_and_utc_instants(native_fixture: Path) -> None:
    request = schedule_request()
    request["rows"][0]["title"] = "PUBLIC Cafe\u0301 SHIFT"
    refresh_token(request)
    assert run_fixture(native_fixture, request, "classify", "changed_zone") == (0, "owned_exact")


@pytest.mark.parametrize(
    ("operation", "error"),
    [("create_schedule", "operation_unknown"), ("preflight_schedule", "operation_timeout")],
)
def test_native_timeout_is_unknown_for_writes_and_late_success_cannot_replace_it(
    native_fixture: Path, operation: str, error: str
) -> None:
    code, text = run_fixture(native_fixture, schedule_request(operation), "envelope")
    assert code == 1
    result = json.loads(text)
    assert set(result) == {
        "schema_version",
        "request_id",
        "operation",
        "status",
        "access",
        "error",
        "observed_at",
    }
    assert result["operation"] == operation
    assert result["status"] == "error"
    assert result["error"] == error
    assert result["access"] == "unknown"


@pytest.mark.parametrize(
    ("name", "outcome"),
    [
        ("absent", "absent"),
        ("owned", "owned_exact"),
        ("unowned", "conflict"),
        ("other_token", "conflict"),
        ("changed_title", "conflict"),
        ("changed_time", "conflict"),
        ("changed_zone", "owned_exact"),
        ("all_day", "conflict"),
        ("recurrence", "conflict"),
        ("alarms", "conflict"),
        ("attendees", "conflict"),
        ("duplicate_owned", "conflict"),
    ],
)
def test_native_duplicate_classification_uses_owned_marker_and_exact_reviewed_fields(
    native_fixture: Path, name: str, outcome: str
) -> None:
    assert run_fixture(native_fixture, schedule_request(), "classify", name) == (0, outcome)
