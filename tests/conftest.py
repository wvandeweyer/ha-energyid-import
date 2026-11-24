"""Shared fixtures for the EnergyID Import tests."""

from collections.abc import Iterator
import json
import logging
from pathlib import Path

from homeassistant.components.recorder.core import Recorder
from homeassistant.const import CONF_API_KEY
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.energyid_import.api import JsonValue
from custom_components.energyid_import.const import (
    CONF_BACKFILL_MONTHS,
    CONF_METER_IDS,
    CONF_RECORD_ID,
    DOMAIN,
)

from .const import (
    API_KEY,
    EXPORT_METER_ID,
    IMPORT_METER_ID,
    METERS_URL,
    RECORD_ID,
    RECORD_NAME,
    RECORD_URL,
    RECORDS_URL,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Home Assistant logs this for every custom integration it loads.
_EXPECTED_HA_WARNING = "has not been tested by Home Assistant"


def load_fixture(name: str) -> JsonValue:
    """Load a JSON fixture from tests/fixtures."""
    value: JsonValue = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return value


def load_list_fixture(name: str) -> list[JsonValue]:
    """Load a JSON fixture whose top level is a list."""
    value = load_fixture(name)
    assert isinstance(value, list)
    return value


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers used by the suite."""
    config.addinivalue_line(
        "markers", "allow_warnings: the test expects WARNING or higher log lines"
    )


@pytest.fixture(autouse=True)
def auto_setup(recorder_mock: Recorder, enable_custom_integrations: None) -> None:
    """Start a real in-memory recorder before hass and allow custom integrations."""


@pytest.fixture(autouse=True)
def check_log_output(
    request: pytest.FixtureRequest, caplog: pytest.LogCaptureFixture
) -> Iterator[None]:
    """Fail on leaked API keys and on unexpected warnings."""
    caplog.set_level(logging.DEBUG)
    yield
    records = caplog.get_records("setup") + caplog.get_records("call")
    leaked = [r for r in records if API_KEY in r.getMessage()]
    assert not leaked, "API key found in log output"
    if request.node.get_closest_marker("allow_warnings") is not None:
        return
    warnings = [
        r.getMessage()
        for r in records
        if r.levelno >= logging.WARNING and _EXPECTED_HA_WARNING not in r.getMessage()
    ]
    assert not warnings, f"Unexpected log lines: {warnings}"


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for the dummy record."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=RECORD_NAME,
        unique_id=RECORD_ID,
        data={CONF_API_KEY: API_KEY, CONF_RECORD_ID: RECORD_ID},
        options={
            CONF_METER_IDS: [IMPORT_METER_ID, EXPORT_METER_ID],
            CONF_BACKFILL_MONTHS: 12,
        },
    )


@pytest.fixture
def mock_api(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer the record endpoints with the dummy fixtures."""
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))
    aioclient_mock.get(RECORD_URL, json=load_fixture("record.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))
    return aioclient_mock
