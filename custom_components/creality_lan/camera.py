from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from homeassistant.components.camera import Camera
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

    class CrealityWebcam(Camera):
        """Camera entity that grabs a single JPEG frame from the printer's MJPEG stream."""

        _attr_name = "Webcam"
        _attr_has_entity_name = True
        _attr_unique_id = f"{client.unique_id}_webcam"

        def __init__(self, host: str):
            super().__init__()
            # Creality MJPEG stream (no auth): multipart/x-mixed-replace
            self._mjpeg_url = f"http://{host}:8080/?action=stream"

        @property
        def device_info(self) -> DeviceInfo:
            return device_info

        async def async_camera_image(self, width: Optional[int] = None, height: Optional[int] = None) -> bytes | None:
            """Return one JPEG frame from the MJPEG stream."""
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(self._mjpeg_url) as resp:
                    # Expect multipart/x-mixed-replace; boundary=...
                    ctype = resp.headers.get("Content-Type", "")
                    # Try to extract boundary; fall back to default if missing
                    boundary = None
                    if "boundary=" in ctype:
                        boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
                    if not boundary:
                        # most mjpeg servers use '--boundary' style
                        boundary = "boundary"

                    boundary_bytes = ("--" + boundary).encode()

                    # Read the stream until we get a full JPEG frame
                    # We cap the amount we buffer for safety.
                    reader = resp.content
                    buf = bytearray()
                    max_bytes = 2_000_000  # 2 MB safety cap
                    start_marker = b"\xff\xd8"
                    end_marker = b"\xff\xd9"

                    # Skip everything up to first boundary
                    while True:
                        line = await reader.readline()
                        if not line:
                            return None
                        if boundary_bytes in line:
                            break

                    # Now parse headers of the first part, then read its body
                    while True:
                        # Part headers
                        while True:
                            line = await reader.readline()
                            if not line:
                                return None
                            if line.strip() == b"":
                                # End of headers
                                break
                            # we ignore headers within the part

                        # Part body: read until next boundary or we found a full JPEG
                        buf.clear()
                        while True:
                            chunk = await reader.read(4096)
                            if not chunk:
                                return None
                            # Look for boundary start in chunk
                            bpos = chunk.find(boundary_bytes)
                            if bpos != -1:
                                # append up to boundary (exclude boundary itself)
                                buf.extend(chunk[:bpos])
                                break
                            else:
                                buf.extend(chunk)

                            if len(buf) > max_bytes:
                                # Too large, abort this part and try the next part
                                break

                        # Try to extract JPEG from buf (between SOI and EOI)
                        sidx = buf.find(start_marker)
                        eidx = buf.rfind(end_marker)
                        if sidx != -1 and eidx != -1 and eidx > sidx:
                            return bytes(buf[sidx : eidx + 2])

                        # If we didn’t get a proper JPEG, continue to next part boundary
                        # Read rest of the boundary line to align
                        # There may be trailing CRLF, so consume a couple of lines
                        await reader.readline()
                        await reader.readline()

        # Optional: expose the stream URL in case HA wants to try stream via UI later.
        async def stream_source(self):
            return self._mjpeg_url

    async_add_entities([CrealityWebcam(entry_obj.data["host"])])
