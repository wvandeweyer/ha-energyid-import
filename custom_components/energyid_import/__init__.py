"""The EnergyID Import integration."""

from typing import Final

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EnergyIDClient
from .coordinator import EnergyIDConfigEntry, EnergyIDCoordinator

PLATFORMS: Final[tuple[Platform, ...]] = (Platform.SENSOR,)


async def async_setup_entry(hass: HomeAssistant, entry: EnergyIDConfigEntry) -> bool:
    """Set up EnergyID Import from a config entry."""
    session = async_get_clientsession(hass)
    api_key: str = entry.data[CONF_API_KEY]
    # Setup fails fast: HA retries ConfigEntryNotReady with its own backoff.
    # Later syncs retry within the sync.
    coordinator = EnergyIDCoordinator(
        hass, entry, EnergyIDClient(session, api_key, retry_backoff=())
    )
    await coordinator.async_config_entry_first_refresh()
    coordinator.client = EnergyIDClient(session, api_key)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EnergyIDConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
