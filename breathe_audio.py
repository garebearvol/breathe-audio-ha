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
            decoded = data.decode("ascii")
            self._buffer += decoded
            
            # Process complete messages (terminated by CR)
            while COMMAND_TERMINATOR in self._buffer:
                message, self._buffer = self._buffer.split(COMMAND_TERMINATOR, 1)
                if message:
                    _LOGGER.debug("Received message: %s", message)
                    self._message_callback(message)
        except UnicodeDecodeError as err:
            _LOGGER.error("Failed to decode serial data: %s", err)

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
        self._pending_response: Optional[asyncio.Future] = None
        self._response_event = asyncio.Event()
        self._last_response: Optional[str] = None
        self._state_callbacks: Dict[int, Callable[[Dict[str, Any]], None]] = {}
        self._connected = False

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
                parity=getattr(serial, f"PARITY_{PARITY}"),
                stopbits=getattr(serial, f"STOPBITS_{STOPBITS}"),
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
        if not message.startswith(RESPONSE_PREFIX):
            _LOGGER.warning("Unexpected message format: %s", message)
            return

        # Store response and signal completion
        self._last_response = message
        self._response_event.set()

        # Parse and dispatch to zone callback if registered
        try:
            state = self._parse_response(message)
            if state and "zone" in state:
                zone = state["zone"]
                if zone in self._state_callbacks:
                    self._state_callbacks[zone](state)
        except Exception as err:
            _LOGGER.error("Error parsing response: %s", err)

    def _handle_disconnect(self) -> None:
        """Handle connection loss."""
        self._connected = False
        self._response_event.set()  # Wake up any waiting commands

    def _parse_response(self, message: str) -> Optional[Dict[str, Any]]:
        """Parse a response message into a state dictionary."""
        # Format: #ZxxCMD... (where xx is zone 01-12)
        if len(message) < 5 or not message.startswith(RESPONSE_PREFIX):
            return None

        try:
            zone = int(message[2:4])
            cmd = message[4:]
            state: Dict[str, Any] = {"zone": zone}

            # Parse different response types
            if cmd.startswith("PWR"):
                state["power"] = cmd[3:] == "ON"
            elif cmd.startswith("VOL"):
                state["volume"] = int(cmd[3:])
            elif cmd.startswith("MUT"):
                state["mute"] = cmd[3:] == "ON"
            elif cmd.startswith("SRC"):
                state["source"] = int(cmd[3:])
            elif cmd.startswith("BAS"):
                state["bass"] = int(cmd[3:])
            elif cmd.startswith("TRE"):
                state["treble"] = int(cmd[3:])
            elif cmd.startswith("BAL"):
                state["balance"] = int(cmd[3:])
            elif cmd == "PWRON":
                state["power"] = True
            elif cmd == "PWROFF":
                state["power"] = False
            elif cmd == "MUTON":
                state["mute"] = True
            elif cmd == "MUTOFF":
                state["mute"] = False
            elif cmd.startswith("Z"):  # Full zone status
                # Format: #ZxxPWROnVolxxMutOffSrcxBasxxTrexxBalxx
                state["power"] = "PWRON" in cmd or "PWROn" in cmd
                state["mute"] = "MUTON" in cmd or "MutOn" in cmd
                # Extract other values with basic parsing
                if "Vol" in cmd:
                    try:
                        vol_start = cmd.find("Vol") + 3
                        vol_str = cmd[vol_start:vol_start + 2]
                        state["volume"] = int(vol_str)
                    except (ValueError, IndexError):
                        pass
                if "Src" in cmd:
                    try:
                        src_start = cmd.find("Src") + 3
                        state["source"] = int(cmd[src_start:src_start + 1])
                    except (ValueError, IndexError):
                        pass

            return state
        except (ValueError, IndexError) as err:
            _LOGGER.error("Failed to parse message '%s': %s", message, err)
            return None

    async def _send_command(self, command: str, expect_response: bool = True) -> Optional[str]:
        """Send a command and optionally wait for response."""
        if not self._protocol or not self._connected:
            _LOGGER.error("Cannot send command: not connected")
            return None

        async with self._lock:
            full_command = f"{COMMAND_PREFIX}{command}{COMMAND_TERMINATOR}"
            _LOGGER.debug("Sending command: %s", full_command.strip())
            
            self._response_event.clear()
            self._last_response = None
            self._protocol.write(full_command)

            if expect_response:
                try:
                    await asyncio.wait_for(self._response_event.wait(), timeout=2.0)
                    return self._last_response
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for response to %s", command)
                    return None
            return None

    # Zone control commands
    async def zone_power_on(self, zone: int) -> bool:
        """Turn zone power on."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}PWRON")
        return response is not None

    async def zone_power_off(self, zone: int) -> bool:
        """Turn zone power off."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}PWROFF")
        return response is not None

    async def set_volume(self, zone: int, volume: int) -> bool:
        """Set zone volume (0-100)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not 0 <= volume <= 100:
            return False
        response = await self._send_command(f"Z{zone:02d}VOL{volume:02d}")
        return response is not None

    async def volume_up(self, zone: int) -> bool:
        """Increase zone volume."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}VOL+")
        return response is not None

    async def volume_down(self, zone: int) -> bool:
        """Decrease zone volume."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}VOL-")
        return response is not None

    async def mute_on(self, zone: int) -> bool:
        """Mute zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}MUTON")
        return response is not None

    async def mute_off(self, zone: int) -> bool:
        """Unmute zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}MUTOFF")
        return response is not None

    async def set_source(self, zone: int, source: int) -> bool:
        """Set zone source (1-6)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not 1 <= source <= 6:
            return False
        response = await self._send_command(f"Z{zone:02d}SRC{source}")
        return response is not None

    async def set_bass(self, zone: int, level: int) -> bool:
        """Set zone bass level (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}BAS{sign}{level:02d}")
        return response is not None

    async def set_treble(self, zone: int, level: int) -> bool:
        """Set zone treble level (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}TRE{sign}{level:02d}")
        return response is not None

    async def set_balance(self, zone: int, level: int) -> bool:
        """Set zone balance (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}BAL{sign}{level:02d}")
        return response is not None

    # Query commands
    async def query_zone_status(self, zone: int) -> Optional[Dict[str, Any]]:
        """Query full status of a zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}QST")
        if response:
            return self._parse_response(response)
        return None

    async def query_power(self, zone: int) -> Optional[bool]:
        """Query zone power state."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}QPW")
        if response:
            state = self._parse_response(response)
            return state.get("power") if state else None
        return None

    async def query_volume(self, zone: int) -> Optional[int]:
        """Query zone volume."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}QVL")
        if response:
            state = self._parse_response(response)
            return state.get("volume") if state else None
        return None

    async def query_mute(self, zone: int) -> Optional[bool]:
        """Query zone mute state."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}QMT")
        if response:
            state = self._parse_response(response)
            return state.get("mute") if state else None
        return None

    async def query_source(self, zone: int) -> Optional[int]:
        """Query zone source."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}QSR")
        if response:
            state = self._parse_response(response)
            return state.get("source") if state else None
        return None