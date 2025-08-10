from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.device_registry import DeviceInfo, CONNECTION_NETWORK_MAC

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    entry_obj = data["entry"]

    device_info = DeviceInfo(
        identifiers={(DOMAIN, client.unique_id)},
        connections={(CONNECTION_NETWORK_MAC, client.mac)} if client.mac else None,
        manufacturer="Creality",
        model=client.model or "Creality Printer",
        name=entry_obj.title,
        configuration_url=f"http://{entry_obj.data['host']}:80",
    )

    class _Base(BinarySensorEntity):
        _attr_has_entity_name = True
        _attr_entity_registry_enabled_default = True  # <- enabled by default

        @property
        def device_info(self) -> DeviceInfo:
            return device_info

    class CrealityOnline(_Base):
        _attr_name = "Online"
        _attr_unique_id = f"{client.unique_id}_online"
        @property
        def is_on(self) -> bool:
            return bool(client.state.get("online"))

    class CrealityPrinting(_Base):
        _attr_name = "Printing"
        _attr_unique_id = f"{client.unique_id}_printing"
        @property
        def is_on(self) -> bool:
            st = client.state.get("state")
            return st == 1 or str(st).lower() == "printing"

    async_add_entities([CrealityOnline(), CrealityPrinting()])
