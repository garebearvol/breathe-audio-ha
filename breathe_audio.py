"""Library/API wrapper for Breathe Audio Elevate 6.6 RS-232 serial communication."""

import asyncio
import logging
from typing import Callable, Dict, Optional, Any
import serial_asyncio
import serial

from .const import (
    BAUDRATE,
    BYTESIZE,
    COMMAND_PREFIX,
    COMMAND_TERMINATOR,
    MAX_ZONE,
    MIN_ZONE,
    PARITY,
    RESPONSE_PREFIX,
    STOPBITS,
    TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class BreatheAudioProtocol(asyncio.Protocol):
    """Serial protocol handler for Breathe Audio Elevate 6.6."""

    def __init__(
        self,
        message_callback: Callable[[str], None],
        connection_lost_callback: Callable[[], None],
    ) -> None:
        """Initialize the protocol handler."""
        self._message_callback = message_callback
        self._connection_lost_callback = connection_lost_callback
        self._transport: Optional[asyncio.Transport] = None
        self._buffer = ""

    def connection_made(self, transport: asyncio.Transport) -> None:
        """Handle connection established."""
        self._transport = transport
        _LOGGER.debug("Serial connection established")

    def data_received(self, data: bytes) -> None:
        """Handle incoming data from serial port."""
        try:
            _LOGGER.debug("RAW DATA RECEIVED: %s", data)
            decoded = data.decode("ascii", errors="ignore")
            self._buffer += decoded
            
            while COMMAND_TERMINATOR in self._buffer:
                message, self._buffer = self._buffer.split(COMMAND_TERMINATOR, 1)
                message = message.strip()
                
                if "##" in message:
                    message = message.replace("##", "#")
                
                if message and (message.startswith(RESPONSE_PREFIX) or "PWR" in message):
                    if not message.startswith(RESPONSE_PREFIX):
                        if message.startswith("Z"):
                            message = f"{RESPONSE_PREFIX}{message}"
                        elif message[0].isdigit():
                            message = f"{RESPONSE_PREFIX}Z{message}"
                        
                    _LOGGER.debug("Received message: %s", message)
                    self._message_callback(message)
        except Exception as err:
            _LOGGER.error("Error processing serial data: %s", err)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        """Handle connection lost."""
        _LOGGER.warning("Serial connection lost")
        if self._connection_lost_callback:
            self._connection_lost_callback()

    def write(self, data: str) -> None:
        """Write data to serial port."""
        if self._transport:
            self._transport.write(data.encode("ascii"))


class BreatheAudioAPI:
    """API wrapper for Breathe Audio Elevate 6.6."""

    def __init__(self, serial_port: str) -> None:
        """Initialize the API."""
        self._serial_port = serial_port
        self._protocol: Optional[BreatheAudioProtocol] = None
        self._transport: Optional[asyncio.Transport] = None
        self._lock = asyncio.Lock()
        self._state_callbacks: Dict[int, Callable[[Dict[str, Any]], None]] = {}
        self._connected = False
        self._last_commanded_zone: Optional[int] = None  # Track last zone for truncated responses

    @property
    def connected(self) -> bool:
        """Return True if connected."""
        return self._connected and self._transport is not None

    def register_state_callback(
        self, zone: int, callback: Callable[[Dict[str, Any]], None]
    ) -> None:
        """Register a callback for zone state updates."""
        self._state_callbacks[zone] = callback

    def unregister_state_callback(self, zone: int) -> None:
        """Unregister a callback for zone state updates."""
        self._state_callbacks.pop(zone, None)

    async def connect(self) -> bool:
        """Establish serial connection."""
        try:
            loop = asyncio.get_event_loop()
            self._transport, self._protocol = await serial_asyncio.create_serial_connection(
                loop,
                lambda: BreatheAudioProtocol(
                    self._handle_message, self._handle_disconnect
                ),
                self._serial_port,
                baudrate=BAUDRATE,
                bytesize=BYTESIZE,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=TIMEOUT,
            )
            self._connected = True
            _LOGGER.info("Connected to Breathe Audio at %s", self._serial_port)
            return True
        except serial.SerialException as err:
            _LOGGER.error("Failed to connect to %s: %s", self._serial_port, err)
            return False

    async def disconnect(self) -> None:
        """Close serial connection."""
        self._connected = False
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol = None
        _LOGGER.info("Disconnected from Breathe Audio")

    def _handle_message(self, message: str) -> None:
        """Process incoming message."""
        try:
            state = self._parse_response(message)
            if state:
                zone = state.get("zone")
                # If zone is missing from response but we have power info, use last commanded zone
                if zone is None and "power" in state and self._last_commanded_zone is not None:
                    zone = self._last_commanded_zone
                    state["zone"] = zone
                    _LOGGER.debug("Using last commanded zone %d for response without zone ID", zone)
                
                if zone is not None and zone in self._state_callbacks:
                    self._state_callbacks[zone](state)
        except Exception as err:
            _LOGGER.error("Error parsing response: %s", err)

    def _handle_disconnect(self) -> None:
        """Handle connection loss."""
        self._connected = False

    def _parse_response(self, message: str) -> Optional[Dict[str, Any]]:
        """Parse a response message into a state dictionary."""
        import re
        match = re.search(r'Z(\d+)', message)
        
        if not match:
            return None
            
        try:
            zone = int(match.group(1))
            cmd_start = match.end()
            cmd = message[cmd_start:].upper()
            state: Dict[str, Any] = {"zone": zone}

            if "," in cmd:
                parts = cmd.split(",")
                for part in parts:
                    if part.startswith("PWR"):
                        state["power"] = part[3:] == "ON"
                    elif part.startswith("VOL"):
                        vol_str = part[3:]
                        if vol_str.startswith("-"):
                            vol_str = vol_str[1:]
                        try:
                            state["volume"] = int(vol_str)
                        except ValueError:
                            pass
                    elif part.startswith("MUT"):
                        state["mute"] = part[3:] == "ON"
                    elif part.startswith("SRC"):
                        try:
                            state["source"] = int(part[3:])
                        except ValueError:
                            pass
            elif cmd.startswith("PWR"):
                state["power"] = cmd[3:] == "ON"
            elif cmd.startswith("VOL"):
                vol_str = cmd[3:]
                if vol_str.startswith("-"):
                    vol_str = vol_str[1:]
                try:
                    state["volume"] = int(vol_str)
                except ValueError:
                    pass
            elif cmd.startswith("MUT"):
                state["mute"] = cmd[3:] == "ON"
            elif cmd.startswith("SRC"):
                try:
                    state["source"] = int(cmd[3:])
                except ValueError:
                    pass
            elif cmd.startswith("BAS"):
                try:
                    state["bass"] = int(cmd[3:])
                except ValueError:
                    pass
            elif cmd.startswith("TRE"):
                try:
                    state["treble"] = int(cmd[3:])
                except ValueError:
                    pass
            elif cmd.startswith("BAL"):
                try:
                    state["balance"] = int(cmd[3:])
                except ValueError:
                    pass

            return state
        except (ValueError, IndexError) as err:
            _LOGGER.error("Failed to parse message '%s': %s", message, err)
            return None

    async def _send_command(self, command: str, zone: Optional[int] = None) -> None:
        """Send a command (fire and forget - no waiting)."""
        if not self.connected:
            _LOGGER.debug("Not connected, attempting to reconnect...")
            if not await self.connect():
                _LOGGER.error("Cannot send command: not connected")
                return

        if not self._protocol:
            return

        async with self._lock:
            # Track the zone for this command (used when response lacks zone ID)
            if zone is not None:
                self._last_commanded_zone = zone
                
            full_command = f"{COMMAND_PREFIX}{command}{COMMAND_TERMINATOR}"
            _LOGGER.debug("Sending command: %s", full_command.strip())
            try:
                self._protocol.write(full_command)
            except Exception as err:
                _LOGGER.error("Failed to write to protocol: %s", err)
                await self.disconnect()

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
        # Fire and forget - we rely on the callback for the response
        if MIN_ZONE <= zone <= MAX_ZONE:
            await self._send_command(f"Z{zone:02d}CONSR")
        return None
