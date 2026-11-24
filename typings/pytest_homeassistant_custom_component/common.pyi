# Typed subset of pytest_homeassistant_custom_component.common.

from collections.abc import Mapping
from datetime import datetime

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

class MockConfigEntry(ConfigEntry[object]):
    def __init__(
        self,
        *,
        domain: str,
        title: str = ...,
        unique_id: str | None = ...,
        data: Mapping[str, object] = ...,
        options: Mapping[str, object] = ...,
        version: int = ...,
        minor_version: int = ...,
        entry_id: str | None = ...,
        state: ConfigEntryState = ...,
    ) -> None: ...
    def add_to_hass(self, hass: HomeAssistant) -> None: ...
    async def start_reauth_flow(
        self, hass: HomeAssistant, context: Mapping[str, object] | None = ...
    ) -> Mapping[str, object]: ...
    async def start_reconfigure_flow(
        self, hass: HomeAssistant, *, show_advanced_options: bool = ...
    ) -> Mapping[str, object]: ...

def async_fire_time_changed(
    hass: HomeAssistant, datetime_: datetime | None = ..., *, fire_all: bool = ...
) -> None: ...
