"""Services to sync now and to rebuild a meter's statistics."""

from datetime import date
from typing import Final

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import CONF_RECORD_ID, DOMAIN
from .coordinator import EnergyIDConfigEntry, EnergyIDCoordinator

ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_DEVICE_ID: Final = "device_id"
ATTR_START_DATE: Final = "start_date"

SYNC_SCHEMA: Final = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})
REBUILD_SCHEMA: Final = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_START_DATE): cv.date,
    }
)


def _loaded_coordinator(hass: HomeAssistant, entry_id: str) -> EnergyIDCoordinator:
    """Return the coordinator of a loaded entry or raise a validation error."""
    entry: EnergyIDConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is None
        or entry.domain != DOMAIN
        or (entry.state is not ConfigEntryState.LOADED)
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_not_loaded"
        )
    return entry.runtime_data


def _meter_of_device(hass: HomeAssistant, device_id: str) -> tuple[str, str]:
    """Return (entry id, meter id) for a meter device of this integration."""
    registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
        device = next((d for d in devices if d.id == device_id), None)
        prefix = f"{entry.data[CONF_RECORD_ID]}_"
        for domain, identifier in device.identifiers if device else ():
            if domain == DOMAIN and identifier.startswith(prefix):
                return entry.entry_id, identifier.removeprefix(prefix)
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="not_a_meter"
    )


def _raise_if_failed(coordinator: EnergyIDCoordinator) -> None:
    """Raise when the sync the service started did not succeed."""
    if not coordinator.last_update_success:
        raise HomeAssistantError(
            translation_domain=DOMAIN, translation_key="sync_failed"
        )


async def _async_sync(call: ServiceCall) -> None:
    """Run a normal sync of one entry now."""
    coordinator = _loaded_coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    await coordinator.async_refresh()
    _raise_if_failed(coordinator)


async def _async_rebuild(call: ServiceCall) -> None:
    """Rebuild one meter from a date, or from the start of the backfill."""
    entry_id, meter_id = _meter_of_device(call.hass, call.data[ATTR_DEVICE_ID])
    coordinator = _loaded_coordinator(call.hass, entry_id)
    start: date | None = call.data.get(ATTR_START_DATE)
    await coordinator.async_rebuild(meter_id, start)
    _raise_if_failed(coordinator)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services once for the integration."""
    hass.services.async_register(DOMAIN, "sync", _async_sync, schema=SYNC_SCHEMA)
    hass.services.async_register(
        DOMAIN, "rebuild_statistics", _async_rebuild, schema=REBUILD_SCHEMA
    )
