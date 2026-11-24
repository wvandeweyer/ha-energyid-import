"""Coordinator that synchronises one EnergyID record."""

from dataclasses import dataclass
import logging
from typing import override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EnergyIDApiError,
    EnergyIDAuthError,
    EnergyIDClient,
    EnergyIDConnectionError,
    Meter,
    Record,
)
from .const import CONF_METER_IDS, CONF_RECORD_ID, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type EnergyIDConfigEntry = ConfigEntry[EnergyIDCoordinator]


@dataclass(frozen=True, slots=True, kw_only=True)
class EnergyIDData:
    """Result of one sync."""

    record: Record
    meters: tuple[Meter, ...]
    missing_meter_ids: frozenset[str]


class EnergyIDCoordinator(DataUpdateCoordinator[EnergyIDData]):
    """Fetch the record and its selected meters every UPDATE_INTERVAL."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: EnergyIDConfigEntry,
        client: EnergyIDClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = config_entry
        self.client = client

    @override
    async def _async_update_data(self) -> EnergyIDData:
        """Fetch the record and the meters chosen in the options."""
        record_id: str = self.entry.data[CONF_RECORD_ID]
        selected = frozenset[str](self.entry.options[CONF_METER_IDS])
        try:
            record = await self.client.async_get_record(record_id)
            meters = await self.client.async_get_meters(record_id)
        except EnergyIDAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EnergyIDConnectionError, EnergyIDApiError) as err:
            raise UpdateFailed(str(err)) from err
        chosen = tuple(meter for meter in meters if meter.id in selected)
        return EnergyIDData(
            record=record,
            meters=chosen,
            missing_meter_ids=selected - {meter.id for meter in chosen},
        )
