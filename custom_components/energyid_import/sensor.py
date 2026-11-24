"""Diagnostic sensors that show whether the import is up to date."""

from datetime import datetime
from typing import Final, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Meter
from .coordinator import EnergyIDConfigEntry, EnergyIDCoordinator
from .entity import EnergyIDEntity, async_sync_devices

PARALLEL_UPDATES = 0

LATEST_DATA: Final = SensorEntityDescription(
    key="latest_data",
    translation_key="latest_data",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)
LAST_SYNC: Final = SensorEntityDescription(
    key="last_sync",
    translation_key="last_sync",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyIDConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one last-sync sensor and one latest-data sensor per meter."""
    coordinator = entry.runtime_data
    meters = coordinator.data.meters
    async_sync_devices(hass, entry)
    entities: list[SensorEntity] = [LastSyncSensor(coordinator, LAST_SYNC)]
    entities.extend(LatestDataSensor(coordinator, LATEST_DATA, m) for m in meters)
    async_add_entities(entities)


class LastSyncSensor(EnergyIDEntity, SensorEntity):
    """When the last successful sync finished."""

    @property
    @override
    def native_value(self) -> datetime:
        """Return the time of the last successful sync."""
        return self.coordinator.data.synced_at


class LatestDataSensor(EnergyIDEntity, SensorEntity):
    """The end of the last hour imported for a meter."""

    def __init__(
        self,
        coordinator: EnergyIDCoordinator,
        description: SensorEntityDescription,
        meter: Meter,
    ) -> None:
        """Remember which meter this sensor follows."""
        super().__init__(coordinator, description, meter)
        self._meter_id = meter.id

    @property
    @override
    def native_value(self) -> datetime | None:
        """Return the end of the last imported hour, if any."""
        status = self.coordinator.data.statuses.get(self._meter_id)
        return status.latest_data if status else None
