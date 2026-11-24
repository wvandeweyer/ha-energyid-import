"""Diagnostics download with private data redacted."""

from dataclasses import asdict
from typing import Any, Final

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .const import CONF_BACKFILL_MONTHS, CONF_METER_IDS, CONF_RECORD_ID
from .coordinator import EnergyIDConfigEntry

# The record name is often a street address, so it is redacted as well.
TO_REDACT: Final = frozenset(
    {
        CONF_API_KEY,
        CONF_RECORD_ID,
        "id",
        "display_name",
        "connection_id",
        "ean_suffix",
    }
)


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: EnergyIDConfigEntry
) -> dict[str, Any]:
    """Return the entry, the last sync result and each meter's status."""
    coordinator = entry.runtime_data
    data = coordinator.data
    meters = [
        async_redact_data(asdict(meter), TO_REDACT)
        | {"status": asdict(data.statuses[meter.id])}
        for meter in data.meters
        if meter.id in data.statuses
    ]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": {
                "meter_count": len(entry.options[CONF_METER_IDS]),
                CONF_BACKFILL_MONTHS: entry.options[CONF_BACKFILL_MONTHS],
            },
        },
        "last_update_success": coordinator.last_update_success,
        "synced_at": data.synced_at.isoformat(),
        "record": async_redact_data(asdict(data.record), TO_REDACT),
        "missing_meter_count": len(data.missing_meter_ids),
        "meters": meters,
    }
