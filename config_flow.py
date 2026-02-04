"""Config flow for Breathe Audio Elevate 6.6 integration."""

import logging
import re
from typing import Any, Dict, Optional

import serial
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_POLLING_INTERVAL,
    CONF_SERIAL_PORT,
    CONF_ZONES,
    DEFAULT_NAME,
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_ZONES,
    DOMAIN,
    MAX_ZONE,
    MIN_ZONE,
)

_LOGGER = logging.getLogger(__name__)


def validate_serial_port(port: str) -> bool:
    """Validate serial port exists."""
    try:
        # On Linux/Mac, check if port exists
        if port.startswith("/dev/"):
            import os
            return os.path.exists(port)
        # On Windows, basic COM port validation
        if re.match(r"^COM\d+$", port, re.IGNORECASE):
            return True
        # For serial URLs (socket://, etc.)
        if "://" in port:
            return True
        return False
    except Exception:
        return False


class BreatheAudioConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Breathe Audio Elevate 6.6."""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            serial_port = user_input[CONF_SERIAL_PORT]

            # Validate serial port
            if not validate_serial_port(serial_port):
                errors[CONF_SERIAL_PORT] = "invalid_serial_port"
            else:
                # Check if already configured
                await self.async_set_unique_id(serial_port)
                self._abort_if_unique_id_configured()

                # Test connection
                try:
                    if await self._test_connection(serial_port):
                        return self.async_create_entry(
                            title=user_input.get(CONF_NAME, DEFAULT_NAME),
                            data=user_input,
                        )
                    errors["base"] = "cannot_connect"
                except serial.SerialException as err:
                    _LOGGER.error("Serial error: %s", err)
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.exception("Unexpected error: %s", err)
                    errors["base"] = "unknown"

        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERIAL_PORT): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Optional(CONF_ZONES, default=DEFAULT_ZONES): vol.All(
                    vol.Coerce(int), vol.Range(min=MIN_ZONE, max=MAX_ZONE)
                ),
                vol.Optional(
                    CONF_POLLING_INTERVAL, default=DEFAULT_POLLING_INTERVAL
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def _test_connection(self, serial_port: str) -> bool:
        """Test the serial connection."""
        try:
            from .breathe_audio import BreatheAudioAPI

            api = BreatheAudioAPI(serial_port)
            connected = await api.connect()
            if connected:
                await api.disconnect()
            return connected
        except Exception as err:
            _LOGGER.error("Connection test failed: %s", err)
            return False

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "BreatheAudioOptionsFlowHandler":
        """Get the options flow for this handler."""
        return BreatheAudioOptionsFlowHandler(config_entry)


class BreatheAudioOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Breathe Audio."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Manage the options."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_POLLING_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                vol.Optional(
                    CONF_ZONES,
                    default=self.config_entry.options.get(
                        CONF_ZONES, self.config_entry.data.get(CONF_ZONES, DEFAULT_ZONES)
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_ZONE, max=MAX_ZONE)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=options_schema,
            errors=errors,
        )