from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Callable, Optional

import aiohttp

from .const import HEARTBEAT_SEC, RECONNECT_BACKOFF

_LOGGER = logging.getLogger(__name__)


class CrealityWS:
    """Creality LAN websocket client + state store for HA (ws://<ip>:9999/)."""

    def __init__(self, host: str):
        self._host = host
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None

        # Outgoing commands (wrapped as {"method":"set","params":{...}} or {"method":"get","params":{...}})
        self._out_queue: "asyncio.Queue[dict]" = asyncio.Queue()

        # Device identity
        self.model: Optional[str] = None
        self.mac: Optional[str] = None
        self.unique_id: str = host

        # CFS support tracking
        self.cfs_supported: Optional[bool] = None  # None = unknown, True/False once determined
        self._cfs_probe: Optional[asyncio.Task] = None
        self._cfs_poller: Optional[asyncio.Task] = None

        # Shared state
        self.state = {
            "timeStamp": -1,
            "boxInfoTimeStamp": -1,
            "online": False,
            "data": {},
            "err": {"errcode": 0, "key": 0, "errLevel": 0},
            "temperature": {
                "nozzle": {"value": 0.0, "target": 0.0, "max": 350.0},
                "bed": {"value": 0.0, "target": 0.0, "max": 120.0},
                "box": {"value": 0.0, "target": 0.0, "max": 60.0},
            },
            "printProgress": 0,
            "printLeftTime": 0,
            "printJobTime": 0,
            "printFileName": "",
            "deviceState": None,
            "state": None,
            "ctrol": {
                "curFeedratePct": 100,
                "fan": 0,
                "modelFanPct": 0,
                "auxiliaryFanPct": 0,
                "caseFanPct": 0,
                "lightSw": 0,
                # ensure keys exist even if firmware skips them
                "fanAuxiliary": 0,
                "fanCase": 0,
            },
            "previewimg": f"http://{host}:80/downloads/original/current_print_image.png",
            # CFS / boxsInfo (present only if device supports CFS)
            "boxsInfo": None,
        }

        self._listeners: list[Callable[[], None]] = []

    # ---------- Public API ----------

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    async def async_start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._runner())

    async def async_stop(self) -> None:
        # cancel background tasks
        for t in (self._cfs_probe, self._cfs_poller):
            if t:
                t.cancel()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def async_force_poll(self):
        # Coordinator hook (mostly unused; we’re push-based)
        return self.state

    async def async_fetch_info(self) -> None:
        """Fetch /info to learn model + mac for device registry."""
        if not self._session:
            return
        try:
            url = f"http://{self._host}:80/info"
            _LOGGER.debug("GET %s", url)
            async with self._session.get(url, timeout=6) as r:
                if r.status == 200:
                    data = await r.json()
                    self.model = data.get("model")
                    self.mac = data.get("mac")
                    self.unique_id = self.mac or self._host
                    _LOGGER.info("Creality info: model=%s mac=%s", self.model, self.mac)
                else:
                    _LOGGER.warning("GET /info returned HTTP %s", r.status)
        except Exception as e:
            _LOGGER.warning("Failed to GET /info from %s: %s", self._host, e)
            self.unique_id = self._host

    async def send_cmd(self, params: dict) -> None:
        """Queue a command in the envelope {"method":"set","params":{...}}."""
        try:
            self._out_queue.put_nowait({"method": "set", "params": params})
        except Exception as e:
            _LOGGER.debug("send_cmd queue error: %s", e)

    async def request_boxs_info(self) -> None:
        """Ask the printer to send boxsInfo now."""
        try:
            self._out_queue.put_nowait({"method": "get", "params": {"boxsInfo": 1}})
        except Exception as e:
            _LOGGER.debug("request_boxs_info queue error: %s", e)

    # ---------- Internal: WS loop ----------

    async def _runner(self):
        backoffs = iter(RECONNECT_BACKOFF)
        while True:
            try:
                ws_url = f"ws://{self._host}:9999/"
                _LOGGER.info("Connecting to %s", ws_url)
                async with self._session.ws_connect(ws_url, timeout=10) as ws:
                    self._ws = ws
                    self.state["online"] = True
                    # reset CFS support on each (re)connect
                    self.cfs_supported = None
                    for cb in self._listeners:
                        cb()

                    # tasks: rx/tx + an initial CFS probe loop
                    receiver = asyncio.create_task(self._recv_loop(ws))
                    sender = asyncio.create_task(self._send_loop(ws))
                    self._cfs_probe = asyncio.create_task(self._probe_cfs_support())

                    pending = {receiver, sender, self._cfs_probe}
                    done, pending = await asyncio.wait(
                        pending, return_when=asyncio.FIRST_EXCEPTION
                    )
                    for task in pending:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
            except Exception as e:
                _LOGGER.warning("WS disconnected (%s). Reconnecting…", e)
                self.state["online"] = False
                for cb in self._listeners:
                    cb()
                await asyncio.sleep(next(backoffs, RECONNECT_BACKOFF[-1]))
                continue
            else:
                # On clean loop, reset backoff
                backoffs = iter(RECONNECT_BACKOFF)

    async def _probe_cfs_support(self):
        """Try a few times to get boxsInfo; if no reply, mark unsupported and stop."""
        attempts = 0
        max_attempts = 5
        while self.cfs_supported is None and attempts < max_attempts:
            attempts += 1
            _LOGGER.debug("CFS probe attempt %s/%s", attempts, max_attempts)
            await self.request_boxs_info()
            # wait a bit for a reply
            for _ in range(10):
                if self.cfs_supported is True:
                    break
                await asyncio.sleep(1)
            if self.cfs_supported is True:
                break

        if self.cfs_supported is None:
            # never saw a boxsInfo frame → treat as unsupported on this device
            self.cfs_supported = False
            _LOGGER.info("CFS not detected on this printer; disabling CFS polling.")

    async def _start_cfs_poller(self):
        """Start periodic polling once we know CFS is supported."""
        if self._cfs_poller and not self._cfs_poller.done():
            return
        async def _loop():
            while True:
                await asyncio.sleep(20)
                await self.request_boxs_info()
        self._cfs_poller = asyncio.create_task(_loop())

    async def _recv_loop(self, ws: aiohttp.ClientWebSocketResponse):
        last_ping = time.time()
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    self._ingest(data)
                    for cb in self._listeners:
                        cb()
                except Exception as e:
                    _LOGGER.debug("Bad JSON from WS: %s", e)
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError(f"WS error: {ws.exception()}")

            # keepalive ping
            if time.time() - last_ping > HEARTBEAT_SEC:
                with contextlib.suppress(Exception):
                    await ws.ping()
                last_ping = time.time()

    async def _send_loop(self, ws: aiohttp.ClientWebSocketResponse):
        """Drain queued commands to the printer."""
        while not ws.closed:
            envelope = await self._out_queue.get()
            try:
                await ws.send_str(json.dumps(envelope))
                _LOGGER.debug("WS -> %s", envelope)
            except Exception as e:
                _LOGGER.debug("Failed to send WS cmd: %s", e)

    # ---------- Ingestion helpers ----------

    @staticmethod
    def _num(x, default=0.0):
        try:
            return float(x)
        except Exception:
            try:
                return int(x)
            except Exception:
                return default

    def _ingest(self, r: dict):
        s = self.state
        s["timeStamp"] = int(time.time() * 1000)

        # Whole CFS object
        if "boxsInfo" in r:
            s["boxsInfo"] = r["boxsInfo"]
            s["boxInfoTimeStamp"] = s["timeStamp"]
            if self.cfs_supported is None:
                self.cfs_supported = True
                _LOGGER.info("CFS detected; starting periodic polling.")
                # kick off poller
                asyncio.create_task(self._start_cfs_poller())

        # Direct copies (with light normalization)
        if "printProgress" in r:
            s["printProgress"] = int(r["printProgress"])
        if "printLeftTime" in r:
            s["printLeftTime"] = int(self._num(r["printLeftTime"], 0))
        if "printJobTime" in r:
            s["printJobTime"] = int(self._num(r["printJobTime"], 0))
        if "printFileName" in r:
            s["printFileName"] = str(r["printFileName"]).split("/")[-1]
        if "deviceState" in r:
            s["deviceState"] = r["deviceState"]
        if "state" in r:
            s["state"] = r["state"]

        # Temperatures
        if "nozzleTemp" in r:
            s["temperature"]["nozzle"]["value"] = self._num(r["nozzleTemp"], 0.0)
        if "bedTemp0" in r:
            s["temperature"]["bed"]["value"] = self._num(r["bedTemp0"], 0.0)
        if "boxTemp" in r:
            s["temperature"]["box"]["value"] = self._num(r["boxTemp"], 0.0)

        if "targetNozzleTemp" in r:
            s["temperature"]["nozzle"]["target"] = self._num(r["targetNozzleTemp"], 0.0)
        if "targetBedTemp0" in r:
            s["temperature"]["bed"]["target"] = self._num(r["targetBedTemp0"], 0.0)
        if "maxNozzleTemp" in r:
            s["temperature"]["nozzle"]["max"] = self._num(r["maxNozzleTemp"], 350.0)
        if "maxBedTemp" in r:
            s["temperature"]["bed"]["max"] = self._num(r["maxBedTemp"], 120.0)

        # Controls/fans/lights
        for k in (
            "curFeedratePct",
            "fan",
            "modelFanPct",
            "auxiliaryFanPct",
            "caseFanPct",
            "lightSw",
            "fanAuxiliary",
            "fanCase",
        ):
            if k in r:
                s["ctrol"][k] = r[k]

        # Errors
        if "err" in r and isinstance(r["err"], dict):
            s["err"] = r["err"]

        # Keep raw data for debugging
        for k, v in r.items():
            s["data"][k] = v
