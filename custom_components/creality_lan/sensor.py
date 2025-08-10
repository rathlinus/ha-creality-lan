# /config/custom_components/creality_lan/sensor.py
from __future__ import annotations

from typing import Optional, Dict, Any, List

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import UnitOfTemperature, UnitOfTime, PERCENTAGE
from homeassistant.helpers.device_registry import DeviceInfo, CONNECTION_NETWORK_MAC

from .const import DOMAIN, STATE_MAP


# ----------------- Helpers -----------------

def _printer_device_info(entry_obj, client) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, client.unique_id)},
        connections={(CONNECTION_NETWORK_MAC, client.mac)} if client.mac else None,
        manufacturer="Creality",
        model=client.model or "Creality Printer",
        name=entry_obj.title,
        configuration_url=f"http://{entry_obj.data['host']}:80",
    )


def _cfs_device_info(entry_obj, client, box_id: int) -> DeviceInfo:
    # One device per CFS (type==0) box
    return DeviceInfo(
        identifiers={(DOMAIN, f"{client.unique_id}_cfs_{box_id}")},
        manufacturer="Creality",
        model="CFS / Material Box",
        name=f"{entry_obj.title} CFS {box_id}",
        via_device=(DOMAIN, client.unique_id),
        configuration_url=f"http://{entry_obj.data['host']}:80",
    )


def _cfs_boxes(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    info = state.get("boxsInfo") or {}
    boxes = info.get("materialBoxs", []) or []
    # Only “real” CFS boxes: type == 0
    return [b for b in boxes if b.get("type") == 0]


def _cfs_box(state: Dict[str, Any], box_id: int) -> Optional[Dict[str, Any]]:
    for b in _cfs_boxes(state):
        if b.get("id") == box_id:
            return b
    return None


def _slot(box: Dict[str, Any], slot_id: int) -> Optional[Dict[str, Any]]:
    for m in box.get("materials", []) or []:
        if m.get("id") == slot_id:
            return m
    return None


def _norm_color(c: Optional[str]) -> Optional[str]:
    if not c:
        return None
    # Normalize things like "0ffffff", "0000000", "#09ea7ae" → "#ffffff" style 7-chars is odd, but we’ll best-effort.
    s = str(c).strip()
    if s.startswith("#"):
        return s
    # If it looks like 7 chars starting with 0, keep it but add "#"
    if len(s) in (6, 7, 8):
        return "#" + s[-6:]
    return s


# ----------------- Setup -----------------

async def async_setup_entry(hass, entry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    entry_obj = data["entry"]

    # ------- PRINTER SENSORS (always added) -------
    printer_device = _printer_device_info(entry_obj, client)

    class PrinterSensor(SensorEntity):
        _attr_has_entity_name = True
        _attr_entity_registry_enabled_default = True

        def __init__(self, key: str, name: str, unit=None, device_class=None):
            self._key = key
            self._attr_name = name
            self._attr_native_unit_of_measurement = unit
            self._attr_device_class = device_class
            self._attr_unique_id = f"{client.unique_id}_{key}"
            client.add_listener(self.schedule_update_ha_state)

        @property
        def device_info(self) -> DeviceInfo:
            return printer_device

        @property
        def native_value(self):
            s = client.state
            if self._key == "nozzle_temp":
                return s["temperature"]["nozzle"]["value"]
            if self._key == "bed_temp":
                return s["temperature"]["bed"]["value"]
            if self._key == "chamber_temp":
                return s["temperature"]["box"]["value"]
            if self._key == "progress":
                return s["printProgress"]
            if self._key == "time_left":
                return int(s["printLeftTime"])
            if self._key == "job_time":
                return int(s["printJobTime"])
            if self._key == "file":
                return s["printFileName"] or None
            if self._key == "state_text":
                if not s.get("online"):
                    return STATE_MAP.get(-1, "offline")
                return STATE_MAP.get(s.get("state"), "unknown")
            if self._key == "state_code":
                return s.get("state")
            if self._key == "layer":
                return s.get("layer")
            if self._key == "total_layer":
                return s.get("TotalLayer")
            return None

    printer_entities = [
        PrinterSensor("nozzle_temp", "Nozzle Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        PrinterSensor("bed_temp", "Bed Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        PrinterSensor("chamber_temp", "Chamber Temperature", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
        PrinterSensor("progress", "Print Progress", PERCENTAGE),
        PrinterSensor("time_left", "Time Remaining", UnitOfTime.SECONDS),
        PrinterSensor("job_time", "Time Elapsed", UnitOfTime.SECONDS),
        PrinterSensor("file", "Current File"),
        PrinterSensor("state_text", "State"),
        PrinterSensor("state_code", "State Code"),
        PrinterSensor("layer", "Layer"),
        PrinterSensor("total_layer", "Total Layers"),
    ]
    async_add_entities(printer_entities)

    # ------- DYNAMIC CFS SENSORS (only if boxsInfo exists) -------
    created_boxes: set[int] = set()

    async def maybe_add_cfs_entities():
        boxes = _cfs_boxes(client.state)
        new_entities: list[SensorEntity] = []

        for box in boxes:
            box_id = box.get("id")
            if not isinstance(box_id, int):
                continue
            if box_id in created_boxes:
                continue

            cfs_device = _cfs_device_info(entry_obj, client, box_id)

            # Overall CFS temp & humidity
            class CFSTemp(SensorEntity):
                _attr_has_entity_name = True
                _attr_entity_registry_enabled_default = True
                _attr_device_class = SensorDeviceClass.TEMPERATURE
                _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
                def __init__(self, bid: int):
                    self._bid = bid
                    self._attr_name = "Temperature"
                    self._attr_unique_id = f"{client.unique_id}_cfs_{bid}_temp"
                    client.add_listener(self.schedule_update_ha_state)
                @property
                def device_info(self) -> DeviceInfo: return cfs_device
                @property
                def native_value(self):
                    b = _cfs_box(client.state, self._bid)
                    return None if not b else b.get("temp")

            class CFSHumidity(SensorEntity):
                _attr_has_entity_name = True
                _attr_entity_registry_enabled_default = True
                _attr_device_class = SensorDeviceClass.HUMIDITY
                _attr_native_unit_of_measurement = PERCENTAGE
                def __init__(self, bid: int):
                    self._bid = bid
                    self._attr_name = "Humidity"
                    self._attr_unique_id = f"{client.unique_id}_cfs_{bid}_humidity"
                    client.add_listener(self.schedule_update_ha_state)
                @property
                def device_info(self) -> DeviceInfo: return cfs_device
                @property
                def native_value(self):
                    b = _cfs_box(client.state, self._bid)
                    return None if not b else b.get("humidity")

            new_entities += [CFSTemp(box_id), CFSHumidity(box_id)]

            # Per-slot sensors (0..3): percent + extra fields type, color, name, min/max temp, selected, state
            class CFSSlotBase(SensorEntity):
                _attr_has_entity_name = True
                _attr_entity_registry_enabled_default = True
                def __init__(self, bid: int, sid: int, name_suffix: str, key: str = ""):
                    self._bid = bid
                    self._sid = sid
                    self._key = key  # which field from slot dict
                    self._attr_name = f"Slot {sid} {name_suffix}" if name_suffix else f"Slot {sid}"
                    suff = key or name_suffix.replace(" ", "_").lower()
                    self._attr_unique_id = f"{client.unique_id}_cfs_{bid}_slot_{sid}_{suff}"
                    client.add_listener(self.schedule_update_ha_state)
                @property
                def device_info(self) -> DeviceInfo: return cfs_device
                def _slot(self) -> Optional[Dict[str, Any]]:
                    b = _cfs_box(client.state, self._bid)
                    return _slot(b, self._sid) if b else None
                @property
                def extra_state_attributes(self):
                    m = self._slot()
                    if not m:
                        return None
                    return {
                        "box_id": self._bid,
                        "slot_id": self._sid,
                        "name": m.get("name"),
                        "vendor": m.get("vendor"),
                        "type": m.get("type"),
                        "color": _norm_color(m.get("color")),
                        "percent": m.get("percent"),
                        "state": m.get("state"),
                        "selected": m.get("selected"),
                        "min_temp": m.get("minTemp"),
                        "max_temp": m.get("maxTemp"),
                        "rfid": m.get("rfid"),
                    }

            class CFSSlotPercent(CFSSlotBase):
                _attr_native_unit_of_measurement = PERCENTAGE
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Percent", "percent")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("percent")

            class CFSSlotType(CFSSlotBase):
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Type", "type")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("type")

            class CFSSlotName(CFSSlotBase):
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Name", "name")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("name")

            class CFSSlotColor(CFSSlotBase):
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Color", "color")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else _norm_color(m.get("color"))

            class CFSSlotSelected(CFSSlotBase):
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Selected", "selected")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("selected")

            class CFSSlotState(CFSSlotBase):
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "State", "state")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("state")

            class CFSSlotMinTemp(CFSSlotBase):
                _attr_device_class = SensorDeviceClass.TEMPERATURE
                _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Min Temp", "minTemp")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("minTemp")

            class CFSSlotMaxTemp(CFSSlotBase):
                _attr_device_class = SensorDeviceClass.TEMPERATURE
                _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
                def __init__(self, bid: int, sid: int):
                    super().__init__(bid, sid, "Max Temp", "maxTemp")
                @property
                def native_value(self):
                    m = self._slot()
                    return None if not m else m.get("maxTemp")

            for sid in (0, 1, 2, 3):
                new_entities.extend([
                    CFSSlotPercent(box_id, sid),
                    CFSSlotType(box_id, sid),
                    CFSSlotName(box_id, sid),
                    CFSSlotColor(box_id, sid),
                    CFSSlotMinTemp(box_id, sid),
                    CFSSlotMaxTemp(box_id, sid),
                    CFSSlotSelected(box_id, sid),
                    CFSSlotState(box_id, sid),
                ])

            created_boxes.add(box_id)

        if new_entities:
            async_add_entities(new_entities)

    # Run once now (in case boxsInfo already present), and again whenever state updates
    await maybe_add_cfs_entities()
    client.add_listener(lambda: hass.async_create_task(maybe_add_cfs_entities()))
