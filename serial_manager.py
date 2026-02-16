"""Serial connection manager and parser for Breathe Audio Elevate 6.6."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import random
import re
from typing import Any, Callable, Dict, List, Optional

import serial
import serial_asyncio

try:
    from .const import (
        BAUDRATE,
        BYTESIZE,
        COMMAND_TERMINATOR,
        COMMAND_TIMEOUT,
        PARITY,
        RESPONSE_PREFIX,
        STOPBITS,
        TIMEOUT,
    )
except ImportError:  # pragma: no cover - standalone script fallback
    from const import (  # type: ignore[no-redef]
        BAUDRATE,
        BYTESIZE,
        COMMAND_TERMINATOR,
        COMMAND_TIMEOUT,
        PARITY,
        RESPONSE_PREFIX,
        STOPBITS,
        TIMEOUT,
    )

_LOGGER = logging.getLogger(__name__)


class SerialMessageParser:
    """Buffering parser for chunked RS-232 ASCII frames."""

    def __init__(self, terminator: str = COMMAND_TERMINATOR) -> None:
        self._buffer = bytearray()
        self._terminator = terminator.encode("ascii")

    def feed_data(self, data: bytes) -> List[str]:
        """Append data and return any complete frames."""
        frames: List[str] = []
        self._buffer.extend(data)
        while True:
            idx = self._buffer.find(self._terminator)
            if idx == -1:
                break
            raw = bytes(self._buffer[:idx])
            del self._buffer[: idx + len(self._terminator)]
            text = raw.decode("ascii", errors="ignore").strip()
            if text:
                frames.append(text)
        return frames

    @staticmethod
    def parse_state(
        message: str, last_zone: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse a response message into a state dictionary."""
        if not message:
            return None

        text = message.strip()
        if "##" in text:
            text = text.replace("##", "#")

        if text.startswith(RESPONSE_PREFIX):
            text = text[len(RESPONSE_PREFIX) :]

        zone: Optional[int] = None
        rest = text

        if rest.startswith("Z"):
            match = re.match(r"Z(\d{1,2})", rest)
            if match:
                zone = int(match.group(1))
                rest = rest[match.end() :]
        elif len(rest) >= 2 and rest[:2].isdigit():
            zone = int(rest[:2])
            rest = rest[2:]

        if zone is None and last_zone is not None and "PWR" in rest.upper():
            zone = last_zone

        tokens = [rest] if "," not in rest else rest.split(",")
        state: Dict[str, Any] = {}
        if zone is not None:
            state["zone"] = zone

        for token in tokens:
            part = token.strip().upper()
            if not part:
                continue
            if part.startswith("PWR"):
                state["power"] = part[3:] == "ON"
            elif part.startswith("VOL"):
                vol_str = part[3:]
                if vol_str.startswith(("+", "-")):
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
            elif part.startswith("BAS"):
                try:
                    state["bass"] = int(part[3:].replace("+", ""))
                except ValueError:
                    pass
            elif part.startswith("TRE"):
                try:
                    state["treble"] = int(part[3:].replace("+", ""))
                except ValueError:
                    pass
            elif part.startswith("BAL"):
                try:
                    state["balance"] = int(part[3:].replace("+", ""))
                except ValueError:
                    pass

        return state or None


class _SerialProtocol(asyncio.Protocol):
    """Asyncio protocol wrapper for serial data."""

    def __init__(
        self,
        data_callback: Callable[[bytes], None],
        connection_lost_callback: Callable[[Optional[Exception]], None],
    ) -> None:
        self._data_callback = data_callback
        self._connection_lost_callback = connection_lost_callback
        self._transport: Optional[asyncio.Transport] = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]
        _LOGGER.debug("Serial connection established")

    def data_received(self, data: bytes) -> None:
        self._data_callback(data)

    def connection_lost(self, exc: Optional[Exception]) -> None:
        _LOGGER.warning("Serial connection lost")
        self._connection_lost_callback(exc)

    def write(self, data: bytes) -> None:
        if self._transport:
            self._transport.write(data)

    def close(self) -> None:
        if self._transport:
            self._transport.close()


@dataclass
class DeviceState:
    """Tracks device availability and last-known zone states."""

    available: bool = False
    zones: Dict[int, Dict[str, Any]] = field(default_factory=dict)


class SerialConnectionManager:
    """Maintain a resilient serial connection with auto-reconnect."""

    def __init__(
        self,
        serial_port: str,
        on_state: Callable[[Dict[str, Any]], None],
        on_connection_change: Callable[[bool], None],
    ) -> None:
        self._serial_port = serial_port
        self._on_state = on_state
        self._on_connection_change = on_connection_change
        self._parser = SerialMessageParser()
        self._protocol: Optional[_SerialProtocol] = None
        self._transport: Optional[asyncio.Transport] = None
        self._stop_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self._available_event = asyncio.Event()
        self._connection_task: Optional[asyncio.Task] = None
        self._command_lock = asyncio.Lock()
        self._response_waiter: Optional[asyncio.Future] = None
        self._last_commanded_zone: Optional[int] = None
        self._available = False
        self._initial_connect_event = asyncio.Event()

    @property
    def available(self) -> bool:
        return self._available

    async def start(self) -> bool:
        """Start the connection loop."""
        if self._connection_task:
            return self._available
        self._stop_event.clear()
        self._connection_task = asyncio.create_task(self._connection_loop())
        try:
            await asyncio.wait_for(self._initial_connect_event.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            _LOGGER.debug("Initial connection attempt timed out")
        return self._available

    async def stop(self) -> None:
        """Stop the connection loop and close transport."""
        self._stop_event.set()
        self._available_event.clear()
        if self._protocol:
            self._protocol.close()
        if self._connection_task:
            self._connection_task.cancel()
            try:
                await self._connection_task
            except asyncio.CancelledError:
                pass
            self._connection_task = None
        self._cleanup_transport()
        self._set_available(False)

    async def send_command(
        self,
        command: str,
        zone: Optional[int] = None,
        wait_for_response: bool = False,
        timeout: float = COMMAND_TIMEOUT,
    ) -> Optional[Dict[str, Any]]:
        """Send a command with optional response wait."""
        if zone is not None:
            self._last_commanded_zone = zone

        async with self._command_lock:
            if not self._available:
                connected = await self._wait_for_available(timeout)
                if not connected:
                    _LOGGER.warning("Serial not available; dropping command %s", command)
                    return None

            if not self._protocol:
                _LOGGER.warning("Serial protocol missing; dropping command %s", command)
                return None

            response_future: Optional[asyncio.Future] = None
            if wait_for_response:
                response_future = asyncio.get_running_loop().create_future()
                self._response_waiter = response_future

            payload = f"{command}{COMMAND_TERMINATOR}".encode("ascii")
            self._protocol.write(payload)

            if response_future is None:
                return None

            try:
                result = await asyncio.wait_for(response_future, timeout=timeout)
                return result  # type: ignore[return-value]
            finally:
                self._response_waiter = None

    def _handle_data(self, data: bytes) -> None:
        frames = self._parser.feed_data(data)
        for frame in frames:
            state = self._parser.parse_state(frame, self._last_commanded_zone)
            if not state:
                _LOGGER.debug("Ignoring unparsed frame: %s", frame)
                continue
            self._on_state(state)
            if self._response_waiter and not self._response_waiter.done():
                self._response_waiter.set_result(state)

    def _handle_disconnect(self, _exc: Optional[Exception]) -> None:
        self._disconnect_event.set()

    async def _connection_loop(self) -> None:
        backoff = 0.5
        initial_attempt_done = False

        while not self._stop_event.is_set():
            try:
                await self._connect()
                self._set_available(True)
                if not initial_attempt_done:
                    self._initial_connect_event.set()
                    initial_attempt_done = True
                backoff = 0.5

                disconnect_task = asyncio.create_task(self._disconnect_event.wait())
                stop_task = asyncio.create_task(self._stop_event.wait())
                done, _ = await asyncio.wait(
                    [disconnect_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    disconnect_task.cancel()
                    break
                if disconnect_task in done:
                    stop_task.cancel()
                    self._disconnect_event.clear()

            except Exception as err:
                _LOGGER.error("Serial connection error: %s", err)
                if not initial_attempt_done:
                    self._initial_connect_event.set()
                    initial_attempt_done = True
            finally:
                self._set_available(False)
                self._cleanup_transport()

            if self._stop_event.is_set():
                break

            jitter = random.uniform(0.0, 0.25)
            await asyncio.sleep(min(backoff + jitter, 30.0))
            backoff = min(backoff * 2, 30.0)

    async def _connect(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await serial_asyncio.create_serial_connection(
            loop,
            lambda: _SerialProtocol(self._handle_data, self._handle_disconnect),
            self._serial_port,
            baudrate=BAUDRATE,
            bytesize=BYTESIZE,
            parity=serial.PARITY_NONE if PARITY == "N" else PARITY,
            stopbits=serial.STOPBITS_ONE if STOPBITS == 1 else STOPBITS,
            timeout=TIMEOUT,
        )
        _LOGGER.info("Connected to Breathe Audio at %s", self._serial_port)

    async def _wait_for_available(self, timeout: float) -> bool:
        if self._available:
            return True
        try:
            await asyncio.wait_for(self._available_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return self._available

    def _set_available(self, available: bool) -> None:
        if self._available == available:
            return
        self._available = available
        if available:
            self._available_event.set()
        else:
            self._available_event.clear()
        self._on_connection_change(available)

    def _cleanup_transport(self) -> None:
        if self._protocol:
            self._protocol.close()
        self._protocol = None
        self._transport = None
