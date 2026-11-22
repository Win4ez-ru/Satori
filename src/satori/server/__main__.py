"""Private headless operator worker. No public listener or automatic migration."""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TextIO

from pydantic import SecretStr, ValidationError

from satori.config import OpenAIModelProfile
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from satori.server.budget import BudgetedOpenAITransport, PriceAssumptions
from satori.server.openai_runtime import (
    OPENAI_ROLES,
    budget_model_for_role,
    build_openai_providers,
    build_openai_settings,
)
from satori.server.runtime import HeadlessCommand, HeadlessError, HeadlessRuntime
from satori.server.usage import UsageLedger

MAX_RECORD_CHARS = 64_000


def _money(value: str) -> Decimal:
    try:
        result = Decimal(value)
        if not result.is_finite() or not 0 < result <= 3000 or result * 1_000_000 % 1:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("expected_positive_rub_at_most_3000") from None


def _utc_date(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except ValueError:
        raise argparse.ArgumentTypeError("expected_aware_utc_datetime") from None


async def serve_private_stream(
    runtime: HeadlessRuntime, source: TextIO, destination: TextIO
) -> None:
    """Trusted local pipe; its responses are IPC, never diagnostic logs.

    A remote client must use the separately authenticated gateway, not expose
    this stream through a public command/file endpoint. EOF drains existing work.
    """
    while True:
        line = await asyncio.to_thread(source.readline, MAX_RECORD_CHARS + 1)
        if not line:
            break
        try:
            if len(line) > MAX_RECORD_CHARS or not line.endswith("\n"):
                while line and not line.endswith("\n"):
                    line = await asyncio.to_thread(source.readline, MAX_RECORD_CHARS + 1)
                raise ValueError
            command = HeadlessCommand.model_validate_json(line)
            value = await runtime.execute(command)
            result = {"event": "result", "data": value}
        except HeadlessError as error:
            result = {"event": "error", "code": error.code}
        except (ValidationError, ValueError):
            result = {"event": "error", "code": "command_rejected"}
        except Exception:
            result = {"event": "error", "code": "operation_failed"}
        destination.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        destination.flush()


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    client: OpenAIHttpClient | None = None
    ledger: UsageLedger | None = None
    if arguments.openai:
        key = os.environ.get("SATORI_OPENAI_API_KEY", "")
        if (
            not key
            or not arguments.usage_directory
            or arguments.rub_per_usd_ceiling is None
            or arguments.tariff_reviewed_utc is None
            or arguments.tariff_expires_utc is None
        ):
            raise ValueError("openai_configuration_required")
        # Explicit caller values only. No project .env or arbitrary provider keys
        # are discovered by this command; secret values are never CLI arguments.
        settings = build_openai_settings(
            arguments.data_directory,
            SecretStr(key),
            model_profile=OpenAIModelProfile(arguments.model_profile),
        )
        now = datetime.now(UTC)
        prices = PriceAssumptions(
            arguments.rub_per_usd_ceiling,
            arguments.tariff_reviewed_utc,
            arguments.tariff_expires_utc,
        )
        if not prices.reviewed_at <= now < prices.expires_at:
            raise ValueError("price_review_expired")
        ledger = UsageLedger(arguments.usage_directory)
        try:
            ledger.configure_period(
                now.strftime("%Y-%m"), int(arguments.model_budget_rub * 1_000_000)
            )
            client = OpenAIHttpClient(settings.openai_base_url, key)
            transports = {
                role: BudgetedOpenAITransport(
                    client, ledger, role, prices, model=budget_model_for_role(settings, role)
                )
                for role in OPENAI_ROLES
            }
            providers = build_openai_providers(settings, transports)
            runtime = HeadlessRuntime(
                arguments.data_directory,
                arguments.repository_directory,
                providers=providers,
                runtime_settings=settings,
            )
        except Exception:
            if client is not None:
                client.close()
            ledger.close()
            raise
    else:
        if (
            arguments.operation != "check"
            or arguments.model_profile != OpenAIModelProfile.ALL_LUNA.value
        ):
            raise ValueError("explicit_openai_mode_required")
        runtime = HeadlessRuntime(arguments.data_directory, arguments.repository_directory)
    try:
        await runtime.start()
        try:
            if arguments.operation == "serve-stdio":
                await serve_private_stream(runtime, sys.stdin, sys.stdout)
        finally:
            result = await runtime.close()
    finally:
        # The runtime close contract drains before transport/store disposal.
        # A failed drain is unknown; this CLI exits rather than starting a second owner.
        if client is not None:
            client.close()
        if ledger is not None:
            ledger.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Satori private headless operator worker")
    parser.add_argument("operation", choices=["check", "serve-stdio"])
    parser.add_argument("--data-directory", type=Path, required=True)
    parser.add_argument("--repository-directory", type=Path, required=True)
    parser.add_argument("--openai", action="store_true")
    parser.add_argument(
        "--model-profile",
        choices=[profile.value for profile in OpenAIModelProfile],
        default=OpenAIModelProfile.ALL_LUNA.value,
    )
    parser.add_argument("--usage-directory", type=Path)
    parser.add_argument("--model-budget-rub", type=_money, default=Decimal("500"))
    parser.add_argument("--rub-per-usd-ceiling", type=_money)
    parser.add_argument("--tariff-reviewed-utc", type=_utc_date)
    parser.add_argument("--tariff-expires-utc", type=_utc_date)
    arguments = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(_run(arguments))
    except HeadlessError as error:
        print(json.dumps({"state": "rejected", "reason": error.code}))
        return 1
    except Exception:
        print(json.dumps({"state": "rejected", "reason": "configuration_or_runtime_rejected"}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
