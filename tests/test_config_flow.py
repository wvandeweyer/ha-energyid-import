"""Tests for the EnergyID Import config flow."""

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.energyid_import.api import JsonValue
from custom_components.energyid_import.config_flow import EnergyIDImportConfigFlow
from custom_components.energyid_import.const import (
    API_BASE_URL,
    CONF_BACKFILL_MONTHS,
    CONF_METER_IDS,
    CONF_RECORD_ID,
    DOMAIN,
)

from .conftest import load_fixture, load_list_fixture
from .const import (
    API_KEY,
    EXPORT_METER_ID,
    EXPORT_NIGHT_METER_ID,
    IMPORT_METER_ID,
    IMPORT_NIGHT_METER_ID,
    METERS_URL,
    NEW_API_KEY,
    RECORD_ID,
    RECORD_NAME,
    RECORDS_URL,
)

USER_INPUT = {CONF_API_KEY: API_KEY}


def first_meter() -> dict[str, JsonValue]:
    """Return the first recorded meter as an object."""
    meter = load_list_fixture("meters.json")[0]
    assert isinstance(meter, dict)
    return meter


METER = first_meter()
MULTIPLE_RECORDS_METERS_URL = f"{API_BASE_URL}/records/dummy-record-2/meters"


async def start_flow(hass: HomeAssistant) -> str:
    """Start a user flow and return its id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


@pytest.mark.usefixtures("mock_api")
async def test_full_flow_with_one_record(hass: HomeAssistant) -> None:
    """One record skips the record step; electricity meters are preselected."""
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "meters"
    schema = result["data_schema"]
    assert schema is not None
    defaults = {str(key): key.default() for key in schema.schema}
    assert defaults == {
        CONF_METER_IDS: [
            IMPORT_METER_ID,
            IMPORT_NIGHT_METER_ID,
            EXPORT_METER_ID,
            EXPORT_NIGHT_METER_ID,
        ],
        CONF_BACKFILL_MONTHS: 12,
    }
    selector = schema.schema[CONF_METER_IDS].config
    assert [option["value"] for option in selector["options"]] == [
        IMPORT_METER_ID,
        IMPORT_NIGHT_METER_ID,
        EXPORT_METER_ID,
        EXPORT_NIGHT_METER_ID,
    ]

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_METER_IDS: [IMPORT_METER_ID, IMPORT_NIGHT_METER_ID],
            CONF_BACKFILL_MONTHS: 6.0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == RECORD_NAME
    assert result["data"] == {CONF_API_KEY: API_KEY, CONF_RECORD_ID: RECORD_ID}
    assert result["options"] == {
        CONF_METER_IDS: [IMPORT_METER_ID, IMPORT_NIGHT_METER_ID],
        CONF_BACKFILL_MONTHS: 6,
    }
    assert result["result"].unique_id == RECORD_ID
    assert result["result"].state is ConfigEntryState.LOADED


async def test_flow_with_several_records(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Several records show the record step."""
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records_multiple.json"))
    aioclient_mock.get(MULTIPLE_RECORDS_METERS_URL, json=load_fixture("meters.json"))
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "record"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_RECORD_ID: "dummy-record-2"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "meters"


@pytest.mark.usefixtures("mock_api")
async def test_no_meters_selected(hass: HomeAssistant) -> None:
    """An empty meter selection shows an error and keeps the form."""
    flow_id = await start_flow(hass)
    await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_METER_IDS: [], CONF_BACKFILL_MONTHS: 12}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_METER_IDS: "no_meters_selected"}


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ((401, None, None), "invalid_auth"),
        ((403, None, None), "invalid_auth"),
        ((200, TimeoutError(), None), "cannot_connect"),
        ((500, None, None), "cannot_connect"),
        ((200, None, "not json"), "unknown"),
    ],
)
async def test_user_step_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: tuple[int, BaseException | None, str | None],
    error: str,
) -> None:
    """Validation failures keep the form with an error, then recover."""
    status, exc, text = response
    flow_id = await start_flow(hass)
    aioclient_mock.get(RECORDS_URL, status=status, exc=exc, text=text)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))
    aioclient_mock.get(METERS_URL, json=load_fixture("meters.json"))

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["step_id"] == "meters"


async def test_no_records(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A key without records aborts."""
    aioclient_mock.get(RECORDS_URL, json=[])
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_records"


@pytest.mark.parametrize(
    ("meters", "status", "reason"),
    [
        ([], 200, "no_meters"),
        ([{**METER, "closed": True}], 200, "no_meters"),
        (None, 401, "invalid_auth"),
    ],
)
async def test_meters_step_aborts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    meters: object,
    status: int,
    reason: str,
) -> None:
    """The meters step aborts without importable meters or on API errors."""
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))
    aioclient_mock.get(METERS_URL, status=status, json=meters)
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("mock_api")
async def test_duplicate_record(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A record that is already configured aborts."""
    mock_config_entry.add_to_hass(hass)
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_api")
async def test_aborted_flow_leaves_no_state(hass: HomeAssistant) -> None:
    """State lives on the flow instance, so a new flow starts clean."""
    flow_id = await start_flow(hass)
    await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)
    hass.config_entries.flow.async_abort(flow_id)

    fresh = EnergyIDImportConfigFlow()

    assert vars(fresh) == {
        "_api_key": "",
        "_records": {},
        "_record_id": "",
        "_record_name": "",
        "_meters": [],
    }
    assert not hass.config_entries.flow.async_progress()
    assert not hass.config_entries.async_entries(DOMAIN)


@pytest.mark.usefixtures("mock_api")
async def test_reauth(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Reauth stores a new key that can read the same record."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    flow_id = str(result["flow_id"])

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: NEW_API_KEY}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert mock_config_entry.data[CONF_API_KEY] == NEW_API_KEY


async def test_reauth_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reauth rejects a key without access to the record and API failures."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    flow_id = str(result["flow_id"])
    aioclient_mock.get(RECORDS_URL, json=load_list_fixture("records_multiple.json")[1:])

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: NEW_API_KEY}
    )

    assert result["errors"] == {"base": "record_not_found"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(RECORDS_URL, status=401)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_API_KEY: NEW_API_KEY}
    )

    assert result["errors"] == {"base": "invalid_auth"}
    assert mock_config_entry.data[CONF_API_KEY] == API_KEY


@pytest.mark.usefixtures("mock_api")
async def test_reconfigure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure replaces the key after the same validation as reauth."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        str(result["flow_id"]), {CONF_API_KEY: NEW_API_KEY}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert mock_config_entry.data[CONF_API_KEY] == NEW_API_KEY


@pytest.mark.usefixtures("mock_api")
async def test_options(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """The options flow changes the meter selection and reloads the entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"]
    assert schema is not None
    defaults = {str(key): key.default() for key in schema.schema}
    assert defaults[CONF_METER_IDS] == [IMPORT_METER_ID, EXPORT_METER_ID]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_METER_IDS: [], CONF_BACKFILL_MONTHS: 12}
    )

    assert result["errors"] == {CONF_METER_IDS: "no_meters_selected"}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_METER_IDS: [IMPORT_NIGHT_METER_ID], CONF_BACKFILL_MONTHS: 24},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {
        CONF_METER_IDS: [IMPORT_NIGHT_METER_ID],
        CONF_BACKFILL_MONTHS: 24,
    }
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_options_api_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The options flow aborts when the meters cannot be fetched."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(METERS_URL, exc=TimeoutError())

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_meter_labels_show_ean_suffix_for_several_connections(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Meters on different connections carry the last four EAN digits."""
    second = {
        **METER,
        "id": "dummy-meter-9",
        "gridConnectionId": "dummy-gridconnection-2",
        "connectionNumber": "541400000000000002",
        "register": None,
    }
    aioclient_mock.get(RECORDS_URL, json=load_fixture("records.json"))
    aioclient_mock.get(METERS_URL, json=[METER, second])
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    schema = result["data_schema"]
    assert schema is not None
    labels = [o["label"] for o in schema.schema[CONF_METER_IDS].config["options"]]
    assert labels == [
        "Dummy Grid import day (electricityImport, high, kWh, EAN …0001)",
        "Dummy Grid import day (electricityImport, kWh, EAN …0002)",
    ]
    assert all("541400000000" not in label for label in labels)


@pytest.mark.usefixtures("mock_api")
async def test_meter_labels_without_ean_for_one_connection(
    hass: HomeAssistant,
) -> None:
    """A record with one connection shows no EAN at all."""
    flow_id = await start_flow(hass)

    result = await hass.config_entries.flow.async_configure(flow_id, USER_INPUT)

    schema = result["data_schema"]
    assert schema is not None
    labels = [o["label"] for o in schema.schema[CONF_METER_IDS].config["options"]]
    assert labels[0] == "Dummy Grid import day (electricityImport, high, kWh)"
    assert all("EAN" not in label for label in labels)
