"""Library/API wrapper for Breathe Audio Elevate 6.6 RS-232 serial communication."""

import logging
from typing import Any, Callable, Dict, Optional, List

try:
    from .const import COMMAND_PREFIX, MAX_ZONE, MIN_ZONE
    from .serial_manager import SerialConnectionManager
except ImportError:  # pragma: no cover - standalone script fallback
    from const import COMMAND_PREFIX, MAX_ZONE, MIN_ZONE  # type: ignore[no-redef]
    from serial_manager import SerialConnectionManager  # type: ignore[no-redef]

_LOGGER = logging.getLogger(__name__)


class BreatheAudioAPI:
    """API wrapper for Breathe Audio Elevate 6.6."""

    def __init__(self, serial_port: str) -> None:
        """Initialize the API."""
        self._serial_port = serial_port
        self._state_callbacks: Dict[int, Callable[[Dict[str, Any]], None]] = {}
        self._connection_callbacks: List[Callable[[bool], None]] = []
        self._manager = SerialConnectionManager(
            serial_port, self._handle_state, self._handle_connection_change
        )

    @property
    def connected(self) -> bool:
        """Return True if connected."""
        return self._manager.available

    def register_state_callback(
        self, zone: int, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Register a callback for zone state updates."""
        self._state_callbacks[zone] = callback

    def unregister_state_callback(self, zone: int) -> None:
        """Unregister a callback for zone state updates."""
        self._state_callbacks.pop(zone, None)

    def register_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Register a callback for availability updates."""
        self._connection_callbacks.append(callback)

    def unregister_connection_callback(self, callback: Callable[[bool], None]) -> None:
        """Unregister a callback for availability updates."""
        if callback in self._connection_callbacks:
            self._connection_callbacks.remove(callback)

    async def connect(self) -> bool:
        """Start connection manager."""
        return await self._manager.start()

    async def disconnect(self) -> None:
        """Stop connection manager."""
        await self._manager.stop()
        _LOGGER.info("Disconnected from Breathe Audio")

    def _handle_state(self, state: Dict[str, Any]) -> None:
        """Process incoming state update."""
        zone = state.get("zone")
        if zone is not None and zone in self._state_callbacks:
            self._state_callbacks[zone](state)

    def _handle_connection_change(self, available: bool) -> None:
        """Notify availability listeners."""
        for callback in list(self._connection_callbacks):
            callback(available)

    async def _send_command(
        self,
        command: str,
        zone: Optional[int] = None,
        wait_for_response: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Send a command with optional response wait."""
        full_command = f"{COMMAND_PREFIX}{command}"
        return await self._manager.send_command(
            full_command, zone=zone, wait_for_response=wait_for_response
        )

    # Zone control commands - Fire and Forget (No waiting)
    async def zone_power_on(self, zone: int) -> None:
        """Turn zone power on."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}on", zone=zone)

    async def zone_power_off(self, zone: int) -> None:
        """Turn zone power off."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}off", zone=zone)

    async def set_volume(self, zone: int, volume: int) -> None:
        """Set zone volume (0-100)."""
        if MIN_ZONE <= zone <= MAX_ZONE and 0 <= volume <= 100:
            await self._send_command(f"Z{zone:02d}vol{volume:02d}", zone=zone)

    async def volume_up(self, zone: int) -> None:
        """Increase zone volume."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}vol+", zone=zone)

    async def volume_down(self, zone: int) -> None:
        """Decrease zone volume."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}vol-", zone=zone)

    async def mute_on(self, zone: int) -> None:
        """Mute zone."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}mton", zone=zone)

    async def mute_off(self, zone: int) -> None:
        """Unmute zone."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}mtoff", zone=zone)

    async def set_source(self, zone: int, source: int) -> None:
        """Set zone source (1-6)."""
        if MIN_ZONE <= zone <= MAX_ZONE and 1 <= source <= 6:
            await self._send_command(f"Z{zone:02d}src{source}", zone=zone)

    async def set_bass(self, zone: int, level: int) -> None:
        """Set zone bass level (-10 to 10)."""
        if MIN_ZONE <= zone <= MAX_ZONE and -10 <= level <= 10:
            sign = "+" if level >= 0 else ""
            await self._send_command(f"Z{zone:02d}bas{sign}{level:02d}", zone=zone)

    async def set_treble(self, zone: int, level: int) -> None:
        """Set zone treble level (-10 to 10)."""
        if MIN_ZONE <= zone <= MAX_ZONE and -10 <= level <= 10:
            sign = "+" if level >= 0 else ""
            await self._send_command(f"Z{zone:02d}tre{sign}{level:02d}", zone=zone)

    async def set_balance(self, zone: int, level: int) -> None:
        """Set zone balance (-10 to 10)."""
        if MIN_ZONE <= zone <= MAX_ZONE and -10 <= level <= 10:
            sign = "+" if level >= 0 else ""
            await self._send_command(f"Z{zone:02d}bal{sign}{level:02d}", zone=zone)

    # Query commands - These still wait (used by polling)
    async def query_zone_status(self, zone: int) -> Optional[Dict[str, Any]]:
        """Query full status of a zone."""
        if MIN_ZONE <= zone <= MAX_ZONE:
            return await self._send_command(
                f"Z{zone:02d}CONSR", zone=zone, wait_for_response=True
            )
        return None
