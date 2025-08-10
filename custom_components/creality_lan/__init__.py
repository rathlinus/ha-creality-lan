from __future__ import annotations

import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .ws import CrealityWS

PLATFORMS: list[str] = ["sensor", "binary_sensor", "switch", "camera"]
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    host = entry.data["host"]

    client = CrealityWS(host)
    await client.async_start()
    await client.async_fetch_info()  # model/mac for device registry

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}-{host}",
        update_method=client.async_force_poll,  # push-driven, rarely used
        update_interval=None,
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "entry": entry,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    data = hass.data[DOMAIN].pop(entry.entry_id)
    await data["client"].async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
