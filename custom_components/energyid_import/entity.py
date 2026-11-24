"""Base entity and devices for EnergyID Import."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Meter
from .const import CONF_RECORD_ID, DOMAIN, MANUFACTURER, WEB_BASE_URL
from .coordinator import EnergyIDCoordinator


def record_device(coordinator: EnergyIDCoordinator) -> DeviceInfo:
    """Return the service device that stands for the record."""
    record_id: str = coordinator.entry.data[CONF_RECORD_ID]
    return DeviceInfo(
        identifiers={(DOMAIN, record_id)},
        name=coordinator.data.record.display_name,
        manufacturer=MANUFACTURER,
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=WEB_BASE_URL,
    )


def meter_identifier(record_id: str, meter_id: str) -> str:
    """Return the device identifier of a meter."""
    return f"{record_id}_{meter_id}"


def meter_device(coordinator: EnergyIDCoordinator, meter: Meter) -> DeviceInfo:
    """Return the device of a meter, linked to the record device."""
    record_id: str = coordinator.entry.data[CONF_RECORD_ID]
    name = f"{meter.display_name} ({meter.register})" if meter.register else None
    return DeviceInfo(
        identifiers={(DOMAIN, meter_identifier(record_id, meter.id))},
        name=name or meter.display_name,
        manufacturer=MANUFACTURER,
        model=meter.metric,
        entry_type=DeviceEntryType.SERVICE,
        via_device=(DOMAIN, record_id),
    )


class EnergyIDEntity(CoordinatorEntity[EnergyIDCoordinator]):
    """An entity on the record device or on one meter's device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyIDCoordinator,
        description: EntityDescription,
        meter: Meter | None = None,
    ) -> None:
        """Attach the entity to the record or to a meter."""
        super().__init__(coordinator)
        self.entity_description = description
        record_id: str = coordinator.entry.data[CONF_RECORD_ID]
        if meter is None:
            self._attr_unique_id = f"{record_id}_{description.key}"
            self._attr_device_info = record_device(coordinator)
        else:
            self._attr_unique_id = f"{record_id}_{meter.id}_{description.key}"
            self._attr_device_info = meter_device(coordinator, meter)
