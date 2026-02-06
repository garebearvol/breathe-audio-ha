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
            # Decode and strip NULL bytes/garbage
            decoded = data.decode("ascii", errors="ignore")
            self._buffer += decoded
            
            # Process complete messages (terminated by CR)
            while COMMAND_TERMINATOR in self._buffer:
                message, self._buffer = self._buffer.split(COMMAND_TERMINATOR, 1)
                
                # Cleanup message
                message = message.strip()
                
                # Handle double hash or split messages
                if "##" in message:
                    message = message.replace("##", "#")
                
                # Only process if it looks like a valid response or part of one
                # Usually starts with # or contains zone data like Z01...
                if message and (message.startswith(RESPONSE_PREFIX) or "PWR" in message):
                    # Fix partial messages if they lost the # prefix
                    if not message.startswith(RESPONSE_PREFIX):
                        if message.startswith("Z"):
                            message = f"{RESPONSE_PREFIX}{message}"
                        elif message[0].isdigit():
                            # Handle case where #Z is missing (e.g. "05PWROFF")
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
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=TIMEOUT,
            )
            self._connected = True
            _LOGGER.info("Connected to Breathe Audio at %s", self._serial_port)
            
            # Send a wakeup CR to clear any pending command buffer on the device
            await asyncio.sleep(0.1)
            self._protocol.write(COMMAND_TERMINATOR)
            await asyncio.sleep(0.1)
            
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
        # Example: #Z01PWRON,SRC1,GRP0,VOL-62,POFF
        
        # Use regex to find Zone ID safely (handles Z1, Z01, Z10)
        import re
        match = re.search(r'Z(\d+)', message)
        if not match:
            return None
            
        try:
            zone = int(match.group(1))
            
            # Remove the #Zxx part to get the command body
            # Find where the match ended
            cmd_start = match.end()
            cmd = message[cmd_start:].upper()  # Normalize to uppercase
            state: Dict[str, Any] = {"zone": zone}

            # Parse composite status string (contains commas)
            if "," in cmd:
                parts = cmd.split(",")
                for part in parts:
                    if part.startswith("PWR"):
                        state["power"] = part[3:] == "ON"
                    elif part.startswith("VOL"):
                        # Handle VOL-xx or VOLxx
                        vol_str = part[3:]
                        if vol_str.startswith("-"):
                            vol_str = vol_str[1:] # Strip negative sign
                        try:
                            state["volume"] = int(vol_str)
                        except ValueError:
                            pass # Skip if MT or XM
                    elif part.startswith("MUT"):
                        state["mute"] = part[3:] == "ON"
                    elif part.startswith("SRC"):
                        try:
                            state["source"] = int(part[3:])
                        except ValueError:
                            pass
                    elif part.startswith("GRP"):
                        # Group logic if needed
                        pass
            # Parse single command responses
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

    async def _send_command(self, command: str, expect_response: bool = True) -> Optional[str]:
        """Send a command and optionally wait for response."""
        # Auto-reconnect if needed
        if not self.connected:
            _LOGGER.debug("Not connected, attempting to reconnect...")
            if not await self.connect():
                _LOGGER.error("Cannot send command: not connected")
                return None

        if not self._protocol:
            return None

        async with self._lock:
            if not self._protocol:
                _LOGGER.error("Protocol lost before write")
                return None

            # Clear software buffer to remove stale data from previous timeouts
            if self._protocol:
                self._protocol._buffer = ""

            full_command = f"{COMMAND_PREFIX}{command}{COMMAND_TERMINATOR}"
            _LOGGER.debug("Sending command: %s", full_command.strip())
            
            self._response_event.clear()
            self._last_response = None
            try:
                self._protocol.write(full_command)
            except Exception as err:
                _LOGGER.error("Failed to write to protocol: %s", err)
                await self.disconnect()
                return None

            if expect_response:
                try:
                    await asyncio.wait_for(self._response_event.wait(), timeout=5.0)
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
        response = await self._send_command(f"Z{zone:02d}on")
        return response is not None

    async def zone_power_off(self, zone: int) -> bool:
        """Turn zone power off."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}off")
        return response is not None

    async def set_volume(self, zone: int, volume: int) -> bool:
        """Set zone volume (0-100)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not 0 <= volume <= 100:
            return False
        response = await self._send_command(f"Z{zone:02d}vol{volume:02d}")
        return response is not None

    async def volume_up(self, zone: int) -> bool:
        """Increase zone volume."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}vol+")
        return response is not None

    async def volume_down(self, zone: int) -> bool:
        """Decrease zone volume."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}vol-")
        return response is not None

    async def mute_on(self, zone: int) -> bool:
        """Mute zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}mton")
        return response is not None

    async def mute_off(self, zone: int) -> bool:
        """Unmute zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return False
        response = await self._send_command(f"Z{zone:02d}mtoff")
        return response is not None

    async def set_source(self, zone: int, source: int) -> bool:
        """Set zone source (1-6)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not 1 <= source <= 6:
            return False
        response = await self._send_command(f"Z{zone:02d}src{source}")
        return response is not None

    async def set_bass(self, zone: int, level: int) -> bool:
        """Set zone bass level (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}bas{sign}{level:02d}")
        return response is not None

    async def set_treble(self, zone: int, level: int) -> bool:
        """Set zone treble level (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}tre{sign}{level:02d}")
        return response is not None

    async def set_balance(self, zone: int, level: int) -> bool:
        """Set zone balance (-10 to 10)."""
        if not MIN_ZONE <= zone <= MAX_ZONE or not -10 <= level <= 10:
            return False
        sign = "+" if level >= 0 else ""
        response = await self._send_command(f"Z{zone:02d}bal{sign}{level:02d}")
        return response is not None

    # Query commands
    async def query_zone_status(self, zone: int) -> Optional[Dict[str, Any]]:
        """Query full status of a zone."""
        if not MIN_ZONE <= zone <= MAX_ZONE:
            return None
        response = await self._send_command(f"Z{zone:02d}CONSR")
        if response:
            return self._parse_response(response)
        return None

    async def query_power(self, zone: int) -> Optional[bool]:
        """Query zone power state."""
        return (await self.query_zone_status(zone) or {}).get("power")

    async def query_volume(self, zone: int) -> Optional[int]:
        """Query zone volume."""
        return (await self.query_zone_status(zone) or {}).get("volume")

    async def query_mute(self, zone: int) -> Optional[bool]:
        """Query zone mute state."""
        return (await self.query_zone_status(zone) or {}).get("mute")

    async def query_source(self, zone: int) -> Optional[int]:
        """Query zone source."""
        return (await self.query_zone_status(zone) or {}).get("source")