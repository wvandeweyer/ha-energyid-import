"""Constants for the EnergyID Import integration."""

from datetime import timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

DOMAIN: Final = "energyid_import"

API_BASE_URL: Final = "https://api.energyid.eu/api/v1"
WEB_BASE_URL: Final = "https://app.energyid.eu"
MANUFACTURER: Final = "EnergyID"

CONF_RECORD_ID: Final = "record_id"
CONF_METER_IDS: Final = "meter_ids"
CONF_BACKFILL_MONTHS: Final = "backfill_months"

DEFAULT_BACKFILL_MONTHS: Final = 12
MIN_BACKFILL_MONTHS: Final = 0
MAX_BACKFILL_MONTHS: Final = 60

UPDATE_INTERVAL: Final = timedelta(hours=6)
REFETCH_DAYS: Final = 35
MAX_WINDOW_DAYS: Final = 31
STALE_AFTER_DAYS: Final = 4
REQUEST_TIMEOUT: Final = 30

MAX_ATTEMPTS: Final = 3
RETRY_BACKOFF: Final = (2.0, 8.0)
MAX_RATE_LIMIT_ATTEMPTS: Final = 3


class Metric(StrEnum):
    """EnergyID meter metrics this integration knows about."""

    ELECTRICITY_IMPORT = "electricityImport"
    ELECTRICITY_EXPORT = "electricityExport"
    NATURAL_GAS_IMPORT = "naturalGasImport"
    DRINKING_WATER_IMPORT = "drinkingWaterImport"


class Unit(StrEnum):
    """EnergyID meter units that can be imported."""

    KILOWATT_HOUR = "kWh"
    WATT_HOUR = "Wh"
    CUBIC_METER = "m³"


class Interval(StrEnum):
    """ISO 8601 durations accepted by the meter data endpoint."""

    FIVE_MINUTES = "PT5M"
    QUARTER_HOUR = "PT15M"
    HOUR = "PT1H"
    DAY = "P1D"
    WEEK = "P7D"
    MONTH = "P1M"
    YEAR = "P1Y"


WINDOW_DAYS: Final = MappingProxyType(
    {
        Interval.QUARTER_HOUR: 7,
        Interval.HOUR: MAX_WINDOW_DAYS,
        Interval.DAY: 731,
    }
)

# Water reaches Home Assistant faster through the P1 port of the digital meter.
EXCLUDED_METRICS: Final = frozenset({Metric.DRINKING_WATER_IMPORT})

DEFAULT_METRICS: Final = frozenset(
    {Metric.ELECTRICITY_IMPORT, Metric.ELECTRICITY_EXPORT}
)
