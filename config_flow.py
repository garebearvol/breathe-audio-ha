"""Config flow for Breathe Audio Elevate 6.6 integration."""

import logging
import os
import re
from typing import Any, Dict, Optional

import serial
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

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


def get_serial_port_options() -> list[str]:
    """Get list of potential serial ports."""
    ports = []
    
    # Check for Linux /dev/serial/by-id/ paths (most reliable for USB adapters)
    by_id_path = "/dev/serial/by-id"
    if os.path.isdir(by_id_path):
        try:
            for entry in sorted(os.listdir(by_id_path)):
                full_path = os.path.join(by_id_path, entry)
                ports.append(full_path)
        except (OSError, PermissionError):
            pass
    
    # Check for standard Linux/Mac serial ports
    if os.path.isdir("/dev"):
        try:
            for entry in os.listdir("/dev"):
                # Common serial port patterns
                if entry.startswith("ttyUSB") or entry.startswith("ttyACM"):
                    ports.append(f"/dev/{entry}")
                elif entry.startswith("tty.SLAB") or entry.startswith("tty.wch"):
                    ports.append(f"/dev/{entry}")
        except (OSError, PermissionError):
            pass
    
    # Check for Windows COM ports (common range)
    for i in range(1, 33):
        ports.append(f"COM{i}")
    
    return sorted(set(ports))


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

        # Get detected serial ports for the dropdown
        # Using custom_value=True allows users to type any path, including
        # specific by-id paths like /dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02QFFJ-if00-port0
        port_options = get_serial_port_options()
        
        data_schema = vol.Schema(
            {
                vol.Required(CONF_SERIAL_PORT): SelectSelector(
                    SelectSelectorConfig(
                        options=port_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,  # Allow manual entry of any path
                        sort=True,
                    )
                ),
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