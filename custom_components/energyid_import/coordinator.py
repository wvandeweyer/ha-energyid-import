"""Coordinator that synchronises one EnergyID record into statistics."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from functools import partial
import logging
from types import MappingProxyType
from typing import override
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    StatisticsRow,
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
    DOMAIN,
    UPDATE_INTERVAL,
    Interval,
    Unit,
)
from .statistics import (
    StatisticRow,
    local_midnight,
    normalize,
    plan,
    statistic_name,
    statistic_spec,
    window_start,
)

_LOGGER = logging.getLogger(__name__)

type EnergyIDConfigEntry = ConfigEntry[EnergyIDCoordinator]

ONE_HOUR = timedelta(hours=1)
ONE_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True, kw_only=True)
class MeterStatus:
    """Outcome of one meter's sync."""

    latest_data: datetime | None
    written: int
    error: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EnergyIDData:
    """Result of one sync."""

    record: Record
    meters: tuple[Meter, ...]
    missing_meter_ids: frozenset[str]
    statuses: Mapping[str, MeterStatus]


def _to_row(row: StatisticsRow) -> StatisticRow:
    """Convert a recorder row to a StatisticRow."""
    return StatisticRow(
        start=datetime.fromtimestamp(row["start"], UTC),
        state=row.get("state") or 0.0,
        sum=row.get("sum") or 0.0,
    )


class EnergyIDCoordinator(DataUpdateCoordinator[EnergyIDData]):
    """Import the selected meters of one record every UPDATE_INTERVAL."""

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
        """Fetch the record and its selected meters, then sync each meter."""
        record_id: str = self.entry.data[CONF_RECORD_ID]
        selected = frozenset[str](self.entry.options[CONF_METER_IDS])
        try:
            record = await self.client.async_get_record(record_id)
            meters = await self.client.async_get_meters(record_id)
            chosen = tuple(m for m in meters if m.id in selected and m.importable)
            statuses = await self._async_sync_meters(record, chosen)
        except EnergyIDAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (EnergyIDConnectionError, EnergyIDApiError) as err:
            raise UpdateFailed(str(err)) from err
        return EnergyIDData(
            record=record,
            meters=chosen,
            missing_meter_ids=selected - {meter.id for meter in chosen},
            statuses=MappingProxyType(statuses),
        )

    async def _async_sync_meters(
        self, record: Record, meters: tuple[Meter, ...]
    ) -> dict[str, MeterStatus]:
        """Sync every meter in turn."""
        with_ean = len({meter.connection_id for meter in meters}) > 1
        now = dt_util.utcnow()
        return {
            meter.id: await self._async_sync_meter(
                record, meter, now=now, with_ean=with_ean
            )
            for meter in meters
        }

    async def _async_sync_meter(
        self, record: Record, meter: Meter, *, now: datetime, with_ean: bool
    ) -> MeterStatus:
        """Fetch one meter's window and rewrite its hourly statistics."""
        # Only importable meters reach this point, so the unit is always known.
        unit = meter.unit or Unit.KILOWATT_HOUR
        name = statistic_name(
            record.display_name,
            meter.display_name,
            register=meter.register,
            ean_suffix=meter.ean_suffix if with_ean else None,
        )
        spec = statistic_spec(meter.id, name, unit)
        time_zone = _time_zone(record)
        last = await self._async_last_row(spec.statistic_id)
        today = now.astimezone(time_zone).date()
        start = window_start(
            last_start=last.start if last else None,
            today=today,
            time_zone=time_zone,
            backfill_months=self.entry.options[CONF_BACKFILL_MONTHS],
        )
        interval = Interval.HOUR
        points = await self.client.async_get_data(
            meter.id, start, today + ONE_DAY, interval
        )
        normalized = normalize(points, interval=interval, now=now)
        existing = await self._async_rows(
            spec.statistic_id, local_midnight(start, time_zone)
        )
        rows = plan(
            fresh=normalized.hours,
            existing=existing,
            previous_sum=last.sum if last else 0.0,
        )
        if rows:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=spec.name,
                    source=DOMAIN,
                    statistic_id=spec.statistic_id,
                    unit_class=spec.unit_class,
                    unit_of_measurement=spec.unit,
                ),
                [StatisticData(start=r.start, state=r.state, sum=r.sum) for r in rows],
            )
        newest = rows[-1] if rows else last
        return MeterStatus(
            latest_data=newest.start + ONE_HOUR if newest else None,
            written=len(rows),
            error=None,
        )

    async def _async_last_row(self, statistic_id: str) -> StatisticRow | None:
        """Return the newest stored row of a statistic."""
        query = partial(
            get_last_statistics,
            self.hass,
            1,
            statistic_id,
            convert_units=True,
            types={"state", "sum"},
        )
        result = await get_instance(self.hass).async_add_executor_job(query)
        stored = result.get(statistic_id, [])
        return _to_row(stored[0]) if stored else None

    async def _async_rows(
        self, statistic_id: str, start: datetime
    ) -> tuple[StatisticRow, ...]:
        """Return the stored rows of a statistic from start onwards."""
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            None,
            {statistic_id},
            "hour",
            None,
            {"state", "sum"},
        )
        return tuple(_to_row(row) for row in result.get(statistic_id, []))


def _time_zone(record: Record) -> tzinfo:
    """Return the record's time zone, or Home Assistant's when it has none."""
    if record.time_zone:
        return ZoneInfo(record.time_zone)
    return dt_util.get_default_time_zone()
