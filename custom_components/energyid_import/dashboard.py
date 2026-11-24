"""Build a ready-made dashboard for one record."""

from typing import Final

from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util.json import JsonObjectType, JsonValueType

from .api import Meter
from .const import CONF_RECORD_ID, DOMAIN, Metric
from .coordinator import EnergyIDConfigEntry
from .statistics import statistic_id

DAYS_TO_SHOW: Final = 14
GROUPS: Final = (
    ("Grid consumption", Metric.ELECTRICITY_IMPORT),
    ("Return to grid", Metric.ELECTRICITY_EXPORT),
    ("Gas", Metric.NATURAL_GAS_IMPORT),
)
RECORD_ENTITIES: Final = (
    ("button", "sync"),
    (SENSOR_DOMAIN, "last_sync"),
    (SENSOR_DOMAIN, "peak_current_month"),
    (SENSOR_DOMAIN, "peak_previous_month"),
)


def _label(meter: Meter) -> str:
    """Return a short label for a meter."""
    return (
        f"{meter.display_name} ({meter.register})"
        if meter.register
        else (meter.display_name)
    )


def _statistic_card(meter: Meter, name: str, period: str, offset: int) -> JsonValueType:
    """Return a statistic card with one meter's total for a calendar period."""
    return {
        CONF_TYPE: "statistic",
        "entity": statistic_id(meter.id),
        "name": f"{_label(meter)}, {name}",
        "stat_type": "change",
        "period": {"calendar": {"period": period, "offset": offset}},
    }


def _group_cards(title: str, meters: list[Meter]) -> list[JsonValueType]:
    """Return a daily graph and yesterday and this-month totals for meters."""
    cards: list[JsonValueType] = [
        {
            CONF_TYPE: "statistics-graph",
            "title": f"{title} per day",
            "entities": [
                {"entity": statistic_id(m.id), "name": _label(m)} for m in meters
            ],
            "period": "day",
            "stat_types": ["change"],
            "chart_type": "bar",
            "days_to_show": DAYS_TO_SHOW,
        }
    ]
    cards.extend(_statistic_card(m, "yesterday", "day", -1) for m in meters)
    cards.extend(_statistic_card(m, "this month", "month", 0) for m in meters)
    return cards


def _entity_ids(hass: HomeAssistant, entry: EnergyIDConfigEntry) -> list[JsonValueType]:
    """Return the entity ids of the record and meter entities that exist."""
    record_id: str = entry.data[CONF_RECORD_ID]
    wanted = [(platform, f"{record_id}_{key}") for platform, key in RECORD_ENTITIES]
    wanted += [
        (SENSOR_DOMAIN, f"{record_id}_{meter.id}_latest_data")
        for meter in entry.runtime_data.data.meters
    ]
    registry = er.async_get(hass)
    found = (registry.async_get_entity_id(p, DOMAIN, uid) for p, uid in wanted)
    return [entity_id for entity_id in found if entity_id]


def build_dashboard(hass: HomeAssistant, entry: EnergyIDConfigEntry) -> JsonObjectType:
    """Return a dashboard configuration for the record of an entry."""
    meters = list(entry.runtime_data.data.meters)
    cards: list[JsonValueType] = []
    for title, metric in GROUPS:
        group = [meter for meter in meters if meter.metric == metric]
        if group:
            cards.extend(_group_cards(title, group))
    cards.append(
        {
            CONF_TYPE: "entities",
            "title": "EnergyID",
            "entities": _entity_ids(hass, entry),
        }
    )
    return {
        "title": "EnergyID",
        "views": [{"title": entry.title, "path": "energyid", "cards": cards}],
    }
