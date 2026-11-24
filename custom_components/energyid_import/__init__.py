"""The EnergyID Import integration."""

from typing import Final

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import EnergyIDClient
from .const import CONF_METER_IDS, DOMAIN
from .coordinator import ISSUE_KINDS, EnergyIDConfigEntry, EnergyIDCoordinator
from .services import async_setup_services

PLATFORMS: Final[tuple[Platform, ...]] = (Platform.SENSOR,)
CONFIG_SCHEMA: Final = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Register the integration's services once."""
    async_setup_services(hass)
    return True


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


async def async_remove_entry(hass: HomeAssistant, entry: EnergyIDConfigEntry) -> None:
    """Delete the repair issues of a removed entry; its statistics stay."""
    for meter_id in entry.options.get(CONF_METER_IDS, []):
        for kind in ISSUE_KINDS:
            ir.async_delete_issue(hass, DOMAIN, f"{kind}_{meter_id}")
