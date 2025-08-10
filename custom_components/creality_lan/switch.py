from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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

    class _BaseSwitch(SwitchEntity):
        _attr_has_entity_name = True
        _attr_entity_registry_enabled_default = True  # <- enabled by default

        @property
        def device_info(self) -> DeviceInfo:
            return device_info

        @property
        def available(self) -> bool:
            # show as unavailable when offline (but not disabled)
            return bool(client.state.get("online"))

    class CrealityLight(_BaseSwitch):
        _attr_name = "Light"
        _attr_unique_id = f"{client.unique_id}_light"

        @property
        def is_on(self) -> bool:
            return client.state["ctrol"].get("lightSw", 0) == 1

        async def async_turn_on(self):
            await client.send_cmd({"lightSw": 1})

        async def async_turn_off(self):
            await client.send_cmd({"lightSw": 0})

    class CrealityFanModel(_BaseSwitch):
        _attr_name = "Model Fan"
        _attr_unique_id = f"{client.unique_id}_fan_model"

        @property
        def is_on(self) -> bool:
            ct = client.state["ctrol"]
            return ct.get("fan", 0) == 1 or ct.get("modelFanPct", 0) > 0

        async def async_turn_on(self):
            await client.send_cmd({"fan": 1})

        async def async_turn_off(self):
            await client.send_cmd({"fan": 0})

    class CrealityFanAux(_BaseSwitch):
        _attr_name = "Auxiliary Fan"
        _attr_unique_id = f"{client.unique_id}_fan_aux"

        @property
        def is_on(self) -> bool:
            ct = client.state["ctrol"]
            return ct.get("fanAuxiliary", 0) == 1 or ct.get("auxiliaryFanPct", 0) > 0

        async def async_turn_on(self):
            await client.send_cmd({"fanAuxiliary": 1})

        async def async_turn_off(self):
            await client.send_cmd({"fanAuxiliary": 0})

    class CrealityFanCase(_BaseSwitch):
        _attr_name = "Case Fan"
        _attr_unique_id = f"{client.unique_id}_fan_case"

        @property
        def is_on(self) -> bool:
            ct = client.state["ctrol"]
            return ct.get("fanCase", 0) == 1 or ct.get("caseFanPct", 0) > 0

        async def async_turn_on(self):
            await client.send_cmd({"fanCase": 1})

        async def async_turn_off(self):
            await client.send_cmd({"fanCase": 0})

    async_add_entities([CrealityLight(), CrealityFanModel(), CrealityFanAux(), CrealityFanCase()])
