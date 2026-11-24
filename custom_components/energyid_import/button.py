"""A button that syncs the record now."""

from typing import Final, override

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EnergyIDConfigEntry
from .entity import EnergyIDEntity

PARALLEL_UPDATES = 1

SYNC: Final = ButtonEntityDescription(
    key="sync",
    translation_key="sync",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EnergyIDConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sync button to the record device."""
    async_add_entities([SyncButton(entry.runtime_data, SYNC)])


class SyncButton(EnergyIDEntity, ButtonEntity):
    """Runs a normal sync of the record when pressed."""

    @override
    async def async_press(self) -> None:
        """Sync now and report a failure to the caller."""
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="sync_failed"
            )
