from __future__ import annotations

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, DEFAULT_PORT_HTTP


class CrealityFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input:
            ip = user_input["host"].strip()
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"http://{ip}:{DEFAULT_PORT_HTTP}/info", timeout=6) as r:
                        if r.status != 200:
                            raise RuntimeError(f"HTTP {r.status}")
                        data = await r.json()

                unique_id = (data.get("mac") or ip).lower()
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = data.get("model") or f"Creality {ip}"
                return self.async_create_entry(title=title, data={"host": ip})

            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema({vol.Required("host"): str})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
