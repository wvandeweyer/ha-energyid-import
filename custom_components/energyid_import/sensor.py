"""Diagnostic sensors that show whether the import is up to date."""

from datetime import datetime
from typing import Final, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Meter
from .coordinator import EnergyIDConfigEntry, EnergyIDCoordinator, record_time_zone
from .entity import EnergyIDEntity, async_sync_devices
from .statistics import MonthlyPeak, month_start

PARALLEL_UPDATES = 0

LATEST_DATA: Final = SensorEntityDescription(
    key="latest_data",
    translation_key="latest_data",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)
PEAK_CURRENT_MONTH: Final = SensorEntityDescription(
    key="peak_current_month",
    translation_key="peak_current_month",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    suggested_display_precision=2,
)
PEAK_PREVIOUS_MONTH: Final = SensorEntityDescription(
    key="peak_previous_month",
    translation_key="peak_previous_month",
    device_class=SensorDeviceClass.POWER,
    native_unit_of_measurement=UnitOfPower.KILO_WATT,
    suggested_display_precision=2,
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
    if coordinator.data.peaks is not None:
        entities.append(PeakSensor(coordinator, PEAK_CURRENT_MONTH, months_back=0))
        entities.append(PeakSensor(coordinator, PEAK_PREVIOUS_MONTH, months_back=1))
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


class PeakSensor(EnergyIDEntity, SensorEntity):
    """The highest quarter-hour offtake of this or the previous month."""

    def __init__(
        self,
        coordinator: EnergyIDCoordinator,
        description: SensorEntityDescription,
        *,
        months_back: int,
    ) -> None:
        """Follow the month that is months_back before the current one."""
        super().__init__(coordinator, description)
        self._months_back = months_back

    def _peak(self) -> MonthlyPeak | None:
        """Return the peak of the followed month, if known."""
        data = self.coordinator.data
        today = dt_util.now(record_time_zone(data.record)).date()
        month = month_start(today, months_back=self._months_back)
        return data.peaks.get(month) if data.peaks else None

    @property
    @override
    def native_value(self) -> float | None:
        """Return the peak power in kW."""
        peak = self._peak()
        return peak.power if peak else None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return when the peak quarter started."""
        peak = self._peak()
        return {"peak_start": peak.start.isoformat()} if peak else None
