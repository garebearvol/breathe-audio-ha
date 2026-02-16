"""The Breathe Audio Elevate 6.6 integration."""

import asyncio
import logging
from typing import Any, Callable, Dict, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .breathe_audio import BreatheAudioAPI
from .const import (
    CONF_SERIAL_PORT,
    CONF_ZONES,
    DEFAULT_NAME,
    DEFAULT_ZONES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: List[Platform] = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Breathe Audio from a config entry."""
    serial_port = entry.data[CONF_SERIAL_PORT]
    zones = entry.data.get(CONF_ZONES, DEFAULT_ZONES)
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)

    # Create API instance
    api = BreatheAudioAPI(serial_port)

    # Try to connect
    if not await api.connect():
        await api.disconnect()
        raise ConfigEntryNotReady(f"Failed to connect to {serial_port}")

    # Create coordinator/data manager
    coordinator = BreatheAudioData(hass, api, entry, zones)

    # Store in hass data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "zones": zones,
        "name": name,
    }

    # Start state subscriptions + initial refresh
    await coordinator.async_start()

    # Register device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, serial_port)},
        name=name,
        manufacturer="Breathe Audio",
        model="Elevate 6.6 (BA-6640)",
        sw_version="1.0.0",
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Add update listener for options
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator: BreatheAudioData = data["coordinator"]
        await coordinator.async_stop()
        api: BreatheAudioAPI = data["api"]
        await api.disconnect()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


class BreatheAudioData:
    """Manages state updates for Breathe Audio."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: BreatheAudioAPI,
        entry: ConfigEntry,
        zones: int,
    ) -> None:
        """Initialize the data manager."""
        self.hass = hass
        self.api = api
        self.entry = entry
        self.zones = zones
        self._zone_data: Dict[int, Dict[str, Any]] = {}
        self._listeners: Dict[int, List[Callable[[], None]]] = {}
        self._available = api.connected

    @property
    def zone_data(self) -> Dict[int, Dict[str, Any]]:
        """Return cached zone data."""
        return self._zone_data

    def get_zone_state(self, zone: int) -> Dict[str, Any]:
        """Get state for a specific zone."""
        return self._zone_data.get(zone, {})

    @property
    def available(self) -> bool:
        """Return connection availability."""
        return self._available

    def register_listener(self, zone: int, callback_fn: Callable[[], None]) -> None:
        """Register a state update listener for a zone."""
        if zone not in self._listeners:
            self._listeners[zone] = []
        self._listeners[zone].append(callback_fn)

    def unregister_listener(self, zone: int, callback_fn: Callable[[], None]) -> None:
        """Unregister a state update listener."""
        if zone in self._listeners and callback_fn in self._listeners[zone]:
            self._listeners[zone].remove(callback_fn)

    @callback
    def _notify_listeners(self, zone: int) -> None:
        """Notify listeners of state update."""
        if zone in self._listeners:
            for callback_fn in self._listeners[zone]:
                callback_fn()

    def _handle_zone_update(self, zone: int, state: Dict[str, Any]) -> None:
        """Handle async zone state update."""
        _LOGGER.debug("Zone %d update received: %s", zone, state)
        if zone not in self._zone_data:
            self._zone_data[zone] = {}
        self._zone_data[zone].update(state)
        self.hass.add_job(self._notify_listeners, zone)

    def _handle_connection_update(self, available: bool) -> None:
        """Handle connection availability change."""
        self._available = available
        for zone in range(1, self.zones + 1):
            self.hass.add_job(self._notify_listeners, zone)

    async def async_start(self) -> None:
        """Start subscriptions and initial refresh."""
        # Register callbacks for async feedback
        for zone in range(1, self.zones + 1):
            self.api.register_state_callback(
                zone, lambda state, z=zone: self._handle_zone_update(z, state)
            )
        self.api.register_connection_callback(self._handle_connection_update)

        # Perform initial refresh
        for zone in range(1, self.zones + 1):
            await self.async_refresh_zone(zone)
            await asyncio.sleep(0.1)

    async def async_stop(self) -> None:
        """Stop subscriptions."""
        # Unregister callbacks
        for zone in range(1, self.zones + 1):
            self.api.unregister_state_callback(zone)
        self.api.unregister_connection_callback(self._handle_connection_update)

    async def async_refresh_zone(self, zone: int) -> None:
        """Refresh a single zone."""
        try:
            state = await self.api.query_zone_status(zone)
            if state:
                if zone not in self._zone_data:
                    self._zone_data[zone] = {}
                self._zone_data[zone].update(state)
                self._notify_listeners(zone)
        except Exception as err:
            _LOGGER.debug("Error refreshing zone %d: %s", zone, err)
