"""Real settings controls with public files only; no credential reads, API or profile."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from satori.desktop import openai_runtime as api
from satori.desktop.openai_settings_ui import OpenAISettingsPanel
from satori.desktop.settings import DesktopPreferences
from satori.desktop.ui import DesktopApplication
from tests.test_desktop_openai_runtime import configuration, legacy_configuration

NOW = datetime(2026, 11, 18, 20, tzinfo=UTC)
PUBLIC_KEY = "sk-public-settings-fixture-not-a-secret-1234567890"


@pytest.fixture(scope="module")
def application() -> QApplication:
    return cast(QApplication, QApplication.instance() or DesktopApplication([]))


@pytest.fixture
def panel(
    application: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[OpenAISettingsPanel]:
    del application
    profile = tmp_path / "public-profile"
    profile.mkdir(mode=0o700)
    profile.chmod(0o700)
    reference = tmp_path / "public-credential.env"
    reference.write_text("SATORI_OPENAI_API_KEY=" + PUBLIC_KEY + "\n")
    reference.chmod(0o600)
    monkeypatch.setattr(api, "EXISTING_KEY_FILE", reference)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("settings must not resolve a credential or construct an API client")

    monkeypatch.setattr(api, "load_referenced_openai_key", forbidden)
    monkeypatch.setattr(api, "OpenAIHttpClient", forbidden)
    result = OpenAISettingsPanel(
        DesktopPreferences(data_directory=profile, repository_directory=tmp_path),
        configured=lambda _path: True,
        use_local=lambda: True,
        can_configure=lambda: True,
        clock=lambda: NOW,
    )
    yield result
    result.close()
    result.deleteLater()


def test_opening_settings_is_no_spend_no_configuration_or_adoption(
    panel: OpenAISettingsPanel,
) -> None:
    profile = panel.preferences.data_directory
    assert list(profile.iterdir()) == []
    assert not panel.prepare_memory_button.isEnabled()
    assert panel.use_saved_button.isEnabled()
    assert not panel.preferences.openai_runtime_config
    assert not (profile / api.USAGE_NAME).exists()


def test_one_explicit_action_writes_only_private_reference_and_fixed_monthly_limit(
    panel: OpenAISettingsPanel,
) -> None:
    profile = panel.preferences.data_directory
    paths: list[Path] = []

    def configured(path: Path) -> bool:
        paths.append(path)
        return True

    panel._configured = configured
    panel.use_saved_button.click()
    assert paths == [profile / api.CONFIG_NAME]
    saved = api.load_openai_runtime_config(paths[0], profile)
    review = api.load_reviewed_desktop_pricing()
    assert saved.authorized is True
    assert saved.model_profile == "all_luna"
    assert saved.budget_period == "2026-11"
    assert saved.cap_micro_rub == 1_000_000_000
    assert saved.reviewed_at == review.reviewed_at < NOW
    assert saved.expires_at == review.expires_at
    assert saved.pricing_review_id == review.review_id
    assert saved.rub_per_usd_ceiling == review.rub_per_usd_ceiling
    assert saved.credential.path == api.EXISTING_KEY_FILE
    assert PUBLIC_KEY not in paths[0].read_text()
    assert list(profile.iterdir()) == paths
    assert not (profile / api.USAGE_NAME).exists()
    assert not panel.prepare_memory_button.isEnabled()


def test_repeated_action_does_not_refresh_review_create_usage_or_renew_month(
    panel: OpenAISettingsPanel,
) -> None:
    profile = panel.preferences.data_directory
    panel.use_saved_button.click()
    before = api.load_openai_runtime_config(profile / api.CONFIG_NAME, profile)
    panel._clock = lambda: NOW + timedelta(days=1)
    panel.use_saved_button.click()
    after = api.load_openai_runtime_config(profile / api.CONFIG_NAME, profile)
    assert before == after
    assert list(profile.iterdir()) == [profile / api.CONFIG_NAME]


def test_explicit_review_refresh_preserves_lower_cap_previous_month_and_reference(
    panel: OpenAISettingsPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = panel.preferences.data_directory
    old = legacy_configuration(configuration(profile, cap=300_000_000))
    old = old.model_copy(update={"budget_period": "2026-10"})
    path = profile / api.CONFIG_NAME
    path.write_text(old.model_dump_json())
    path.chmod(0o600)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("existing configuration must not be replaced by a default credential")

    monkeypatch.setattr(
        "satori.desktop.openai_settings_ui.inspect_existing_credential_reference", forbidden
    )
    panel.refresh()
    assert "300.00" in panel.budget.text()
    assert "2026-10" in panel.budget.text()
    panel.use_saved_button.click()
    current = api.load_openai_runtime_config(path, profile)
    assert current.cap_micro_rub == old.cap_micro_rub
    assert current.budget_period == old.budget_period
    assert current.credential == old.credential
    assert current.profile_id == old.profile_id
    assert current.pricing_review_id == api.load_reviewed_desktop_pricing().review_id
    assert list(profile.iterdir()) == [path]


def test_invalid_existing_configuration_is_not_silently_overwritten(
    panel: OpenAISettingsPanel,
) -> None:
    path = panel.preferences.data_directory / api.CONFIG_NAME
    path.write_bytes(b"invalid public fixture")
    path.chmod(0o600)
    panel.use_saved_button.click()
    assert path.read_bytes() == b"invalid public fixture"
    assert "не изменено" in panel.status.text()


def test_expired_review_blocks_even_direct_activation_without_writing(
    panel: OpenAISettingsPanel,
) -> None:
    review = api.load_reviewed_desktop_pricing()
    panel._clock = lambda: review.expires_at
    panel.refresh()
    assert not panel.use_saved_button.isEnabled()
    panel._configure()
    assert list(panel.preferences.data_directory.iterdir()) == []


def test_busy_runtime_blocks_provider_switch_and_memory_action(
    panel: OpenAISettingsPanel,
) -> None:
    panel._can_configure = lambda: False
    panel.set_runtime_status({"supports_source_answers": True})
    panel._configure()
    panel._prepare_memory()
    assert not panel.use_saved_button.isEnabled()
    assert not panel.prepare_memory_button.isEnabled()
    assert list(panel.preferences.data_directory.iterdir()) == []


def test_missing_stat_reference_never_prompts_for_key_or_writes_config(
    panel: OpenAISettingsPanel, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(api, "EXISTING_KEY_FILE", tmp_path / "does-not-exist.env")
    panel.use_saved_button.click()
    assert list(panel.preferences.data_directory.iterdir()) == []
    assert "не найден" in panel.status.text()


def test_index_action_is_explicit_once_until_result_and_stops_with_core(
    panel: OpenAISettingsPanel,
) -> None:
    requests: list[str] = []
    panel.memory_preparation_requested.connect(lambda: requests.append("prepare"))
    panel.set_runtime_status({"supports_source_answers": True})
    assert requests == []
    assert panel.prepare_memory_button.isEnabled()
    panel.prepare_memory_button.click()
    panel._prepare_memory()
    assert requests == ["prepare"]
    assert not panel.prepare_memory_button.isEnabled()
    panel.memory_preparation_finished("Подготовлено: 3. Остались записи для следующего запуска.")
    assert panel.prepare_memory_button.isEnabled()
    assert requests == ["prepare"]
    panel.core_stopped()
    assert not panel.prepare_memory_button.isEnabled()
    assert requests == ["prepare"]
    assert list(panel.preferences.data_directory.iterdir()) == []


def test_usage_is_plain_text_internal_liability_and_not_provider_invoice(
    panel: OpenAISettingsPanel,
) -> None:
    panel.set_runtime_status(
        {
            "supports_source_answers": True,
            "openai_runtime": {
                "usage": {
                    "period": "2026-11",
                    "cap_micro_rub": 1_000_000_000,
                    "liability_micro_rub": 1_500_000,
                    "unsettled_attempts": 1,
                    "settled_reported_micro_rub": 500_000,
                    "over_cap": False,
                    "provider_invoice_verified": False,
                }
            },
        }
    )
    assert "1.50" in panel.usage.text()
    assert "1 000.00" in panel.usage.text()
    assert "счёт провайдера проверяется отдельно" in panel.usage.text()
    assert panel.usage.textFormat() == Qt.TextFormat.PlainText
    panel.set_runtime_status({"supports_source_answers": False, "openai_runtime": {"usage": {}}})
    assert panel.usage.text() == ""
    assert not panel.prepare_memory_button.isEnabled()
