from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.helpers.device_registry import DeviceInfo, CONNECTION_NETWORK_MAC
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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
        """Camera entity that grabs a single JPEG frame from the printer."""

        _attr_name = "Webcam"
        _attr_has_entity_name = True
        _attr_unique_id = f"{client.unique_id}_webcam"

        def __init__(self, host: str):
            super().__init__()
            self._host = host
            # Preferred (old) stream URL – we keep this if it works
            self._mjpeg_url = f"http://{host}:8080/?action=stream"
            self._session: aiohttp.ClientSession | None = None

            # Fallbacks to probe (in this order) only if the preferred one fails
            self._mjpeg_candidates = [
                f"http://{host}:8080/?action=stream",   # keep if working
                f"http://{host}:8000/?action=stream",   # alt port
                f"http://{host}/webcam/?action=stream", # no port, /webcam path
                f"http://{host}:80/webcam/?action=stream",
            ]

            # Snapshot endpoints (used as a last-resort single-frame fetch)
            self._snapshot_candidates = [
                f"http://{host}:8080/?action=snapshot",
                f"http://{host}:8000/webcam?action=snapshot",
                f"http://{host}:8000/?action=snapshot",
                f"http://{host}/webcam?action=snapshot",
            ]

        @property
        def device_info(self) -> DeviceInfo:
            return device_info

        async def async_added_to_hass(self) -> None:
            # Reuse HA's shared session
            self._session = async_get_clientsession(self.hass)
            # Probe and keep the original URL if it works; otherwise pick the first working fallback
            selected = await self._select_stream_url()
            if selected:
                self._mjpeg_url = selected

        async def _probe_url(self, url: str, *, bytes_to_read: int = 64) -> bool:
            """Return True if the URL looks like a live MJPEG stream."""
            assert self._session is not None
            try:
                timeout = aiohttp.ClientTimeout(total=2)
                async with self._session.get(url, timeout=timeout) as resp:
                    if resp.status != 200:
                        return False
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if "multipart/x-mixed-replace" in ctype or "mjpeg" in ctype:
                        return True
                    # Peek a small chunk: boundary or JPEG SOI is a good hint
                    chunk = await resp.content.read(bytes_to_read)
                    return chunk.startswith(b"--") or b"\xff\xd8" in chunk
            except asyncio.TimeoutError:
                return False
            except Exception:
                return False

        async def _select_stream_url(self) -> Optional[str]:
            """Keep the first (old) URL if it works; otherwise choose the first working fallback."""
            # First check the currently set (old) URL
            if await self._probe_url(self._mjpeg_candidates[0]):
                return self._mjpeg_candidates[0]
            # Try fallbacks
            for url in self._mjpeg_candidates[1:]:
                if await self._probe_url(url):
                    return url
            return None  # none worked; we'll rely on snapshot later

        async def async_camera_image(
            self, width: Optional[int] = None, height: Optional[int] = None
        ) -> bytes | None:
            """Return one JPEG frame."""
            assert self._session is not None
            # First try to extract a frame from MJPEG
            if self._mjpeg_url:
                try:
                    timeout = aiohttp.ClientTimeout(total=5)
                    async with self._session.get(self._mjpeg_url, timeout=timeout) as resp:
                        ctype = resp.headers.get("Content-Type", "")
                        boundary = None
                        if "boundary=" in ctype:
                            boundary = ctype.split("boundary=", 1)[1].strip().strip('"')
                        if not boundary:
                            boundary = "boundary"
                        boundary_bytes = ("--" + boundary).encode()

                        reader = resp.content
                        max_bytes = 2_000_000
                        start_marker = b"\xff\xd8"
                        end_marker = b"\xff\xd9"

                        # Skip to first boundary
                        while True:
                            line = await reader.readline()
                            if not line:
                                break
                            if boundary_bytes in line:
                                break

                        # Read first part
                        while True:
                            # headers
                            while True:
                                line = await reader.readline()
                                if not line:
                                    break
                                if line.strip() == b"":
                                    break
                            buf = bytearray()
                            while True:
                                chunk = await reader.read(4096)
                                if not chunk:
                                    break
                                bpos = chunk.find(boundary_bytes)
                                if bpos != -1:
                                    buf.extend(chunk[:bpos])
                                    break
                                buf.extend(chunk)
                                if len(buf) > max_bytes:
                                    break

                            sidx = buf.find(start_marker)
                            eidx = buf.rfind(end_marker)
                            if sidx != -1 and eidx != -1 and eidx > sidx:
                                return bytes(buf[sidx : eidx + 2])

                            # consume CRLF after boundary and try next part
                            await reader.readline()
                            await reader.readline()
                except Exception:
                    pass  # fall back to snapshots next

            # Fallback: try direct snapshot endpoints
            for url in self._snapshot_candidates:
                try:
                    timeout = aiohttp.ClientTimeout(total=3)
                    async with self._session.get(url, timeout=timeout) as resp:
                        if resp.status == 200:
                            data = await resp.read()
                            if data.startswith(b"\xff\xd8"):
                                return data
                except Exception:
                    continue

            return None  # nothing worked

        async def stream_source(self):
            return self._mjpeg_url

    async_add_entities([CrealityWebcam(entry_obj.data["host"])])
