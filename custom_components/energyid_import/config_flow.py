"""Config flow for the EnergyID Import integration."""

from collections.abc import Mapping
from typing import Any, Final, override

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import (
    EnergyIDApiError,
    EnergyIDAuthError,
    EnergyIDClient,
    EnergyIDConnectionError,
    Meter,
    Record,
)
from .const import (
    CONF_BACKFILL_MONTHS,
    CONF_METER_IDS,
    CONF_RECORD_ID,
    DEFAULT_BACKFILL_MONTHS,
    DEFAULT_METRICS,
    DOMAIN,
    MAX_BACKFILL_MONTHS,
    MIN_BACKFILL_MONTHS,
)

API_ERRORS: Final = (EnergyIDAuthError, EnergyIDConnectionError, EnergyIDApiError)

API_KEY_SCHEMA: Final = vol.Schema(
    {
        vol.Required(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
)


def _error_key(err: Exception) -> str:
    """Map a client exception to a translation key."""
    if isinstance(err, EnergyIDAuthError):
        return "invalid_auth"
    if isinstance(err, EnergyIDConnectionError):
        return "cannot_connect"
    return "unknown"


def _client(hass: HomeAssistant, api_key: str) -> EnergyIDClient:
    """Create a fail-fast client on the shared HA session; the user is waiting."""
    return EnergyIDClient(async_get_clientsession(hass), api_key, retry_backoff=())


def _meters_schema(
    meters: list[Meter], selected: list[str], backfill_months: int
) -> vol.Schema:
    """Build the schema for choosing meters and the backfill depth."""
    several_connections = len({meter.connection_id for meter in meters}) > 1
    options = [
        SelectOptionDict(
            value=meter.id,
            label=_meter_label(meter, with_ean=several_connections),
        )
        for meter in meters
    ]
    return vol.Schema(
        {
            vol.Required(CONF_METER_IDS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST
                )
            ),
            vol.Required(CONF_BACKFILL_MONTHS, default=backfill_months): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_BACKFILL_MONTHS,
                    max=MAX_BACKFILL_MONTHS,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _meter_label(meter: Meter, *, with_ean: bool) -> str:
    """Describe a meter; add the EAN suffix when the record has several."""
    details = [meter.metric, str(meter.unit)]
    if meter.register:
        details.insert(1, meter.register)
    if with_ean and meter.ean_suffix:
        details.append(f"EAN …{meter.ean_suffix}")
    return f"{meter.display_name} ({', '.join(details)})"


def _options_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Convert the meters form input into entry options."""
    return {
        CONF_METER_IDS: list(user_input[CONF_METER_IDS]),
        CONF_BACKFILL_MONTHS: int(user_input[CONF_BACKFILL_MONTHS]),
    }


class EnergyIDImportConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up one EnergyID record per config entry."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Initialize the per-flow state."""
        self._api_key = ""
        self._records: dict[str, Record] = {}
        self._record_id = ""
        self._record_name = ""
        self._meters: list[Meter] = []

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "EnergyIDImportOptionsFlow":
        """Return the options flow."""
        return EnergyIDImportOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API key and list the records it can read."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                records = await _client(
                    self.hass, user_input[CONF_API_KEY]
                ).async_get_records()
            except API_ERRORS as err:
                errors["base"] = _error_key(err)
            else:
                if not records:
                    return self.async_abort(reason="no_records")
                self._api_key = user_input[CONF_API_KEY]
                self._records = {record.id: record for record in records}
                if len(records) == 1:
                    return await self._async_select_record(records[0])
                return await self.async_step_record()
        return self.async_show_form(
            step_id="user", data_schema=API_KEY_SCHEMA, errors=errors
        )

    async def async_step_record(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose a record when the key can read several."""
        if user_input is not None:
            return await self._async_select_record(
                self._records[user_input[CONF_RECORD_ID]]
            )
        options = [
            SelectOptionDict(value=record.id, label=record.display_name)
            for record in self._records.values()
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_RECORD_ID): SelectSelector(
                    SelectSelectorConfig(options=options)
                )
            }
        )
        return self.async_show_form(step_id="record", data_schema=schema)

    async def _async_select_record(self, record: Record) -> ConfigFlowResult:
        """Lock the flow to one record and continue with the meters."""
        await self.async_set_unique_id(record.id)
        self._abort_if_unique_id_configured()
        self._record_id = record.id
        self._record_name = record.display_name
        return await self.async_step_meters()

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose the meters and how much history to import."""
        errors: dict[str, str] = {}
        if user_input is None:
            try:
                meters = await _client(self.hass, self._api_key).async_get_meters(
                    self._record_id
                )
            except API_ERRORS as err:
                return self.async_abort(reason=_error_key(err))
            self._meters = [meter for meter in meters if meter.importable]
            if not self._meters:
                return self.async_abort(reason="no_meters")
        elif user_input[CONF_METER_IDS]:
            return self.async_create_entry(
                title=self._record_name,
                data={CONF_API_KEY: self._api_key, CONF_RECORD_ID: self._record_id},
                options=_options_from_input(user_input),
            )
        else:
            errors[CONF_METER_IDS] = "no_meters_selected"
        defaults = [m.id for m in self._meters if m.metric in DEFAULT_METRICS]
        return self.async_show_form(
            step_id="meters",
            data_schema=_meters_schema(self._meters, defaults, DEFAULT_BACKFILL_MONTHS),
            errors=errors,
        )

    async def async_step_reauth(
        self, _entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the API rejected the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key after an authentication failure."""
        return await self._async_step_new_key(
            "reauth_confirm", self._get_reauth_entry(), user_input
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user replace the API key."""
        return await self._async_step_new_key(
            "reconfigure", self._get_reconfigure_entry(), user_input
        )

    async def _async_step_new_key(
        self, step_id: str, entry: ConfigEntry, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Validate that a new key can read the entry's record, then store it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                records = await _client(
                    self.hass, user_input[CONF_API_KEY]
                ).async_get_records()
            except API_ERRORS as err:
                errors["base"] = _error_key(err)
            else:
                if any(record.id == entry.unique_id for record in records):
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                    )
                errors["base"] = "record_not_found"
        return self.async_show_form(
            step_id=step_id,
            data_schema=API_KEY_SCHEMA,
            errors=errors,
            description_placeholders={"record": entry.title},
        )


class EnergyIDImportOptionsFlow(OptionsFlowWithReload):
    """Change the meter selection of an entry."""

    def __init__(self) -> None:
        """Initialize the per-flow state."""
        self._meters: list[Meter] = []

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the meters of the record with the current selection."""
        errors: dict[str, str] = {}
        if user_input is None:
            client = _client(self.hass, self.config_entry.data[CONF_API_KEY])
            try:
                meters = await client.async_get_meters(
                    self.config_entry.data[CONF_RECORD_ID]
                )
            except API_ERRORS as err:
                return self.async_abort(reason=_error_key(err))
            self._meters = [meter for meter in meters if meter.importable]
        elif user_input[CONF_METER_IDS]:
            return self.async_create_entry(data=_options_from_input(user_input))
        else:
            errors[CONF_METER_IDS] = "no_meters_selected"
        available = {meter.id for meter in self._meters}
        selected = [
            meter_id
            for meter_id in self.config_entry.options[CONF_METER_IDS]
            if meter_id in available
        ]
        return self.async_show_form(
            step_id="init",
            data_schema=_meters_schema(
                self._meters, selected, self.config_entry.options[CONF_BACKFILL_MONTHS]
            ),
            errors=errors,
        )
