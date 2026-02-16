import asyncio

import pytest

from custom_components.breathe_audio.serial_manager import SerialConnectionManager, SerialMessageParser


class _FakeProtocol:
    def __init__(self) -> None:
        self.writes = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def close(self) -> None:
        pass


def test_parser_handles_chunked_frames() -> None:
    parser = SerialMessageParser()
    frames = parser.feed_data(b"#Z01PWRON\r#Z02PWRO")
    assert frames == ["#Z01PWRON"]
    frames = parser.feed_data(b"FF\r")
    assert frames == ["#Z02PWROFF"]


def test_parser_extracts_zone_and_state() -> None:
    state = SerialMessageParser.parse_state("#Z03PWRON,VOL10,MUTON")
    assert state["zone"] == 3
    assert state["power"] is True
    assert state["volume"] == 10
    assert state["mute"] is True


def test_parser_uses_last_zone_for_truncated_power() -> None:
    state = SerialMessageParser.parse_state("PWRON", last_zone=5)
    assert state["zone"] == 5
    assert state["power"] is True


@pytest.mark.asyncio
async def test_send_command_waits_for_response() -> None:
    states = []
    manager = SerialConnectionManager("TEST", states.append, lambda _available: None)
    manager._protocol = _FakeProtocol()
    manager._available = True
    manager._available_event.set()

    task = asyncio.create_task(
        manager.send_command("*Z01CONSR", zone=1, wait_for_response=True, timeout=1.0)
    )
    await asyncio.sleep(0)
    manager._handle_data(b"#Z01PWRON\r")
    result = await task

    assert manager._protocol.writes == [b"*Z01CONSR\r"]
    assert result["zone"] == 1
    assert result["power"] is True
