import asyncio

import pytest

from serial_manager import SerialConnectionManager, SerialMessageParser


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


def test_parser_handles_vol_negative_format() -> None:
    state = SerialMessageParser.parse_state("#Z01PWRON,SRC3,GRP0,VOL-45,PMST")
    assert state["zone"] == 1
    assert state["power"] is True
    assert state["source"] == 3
    assert state["group"] == 0
    assert state["volume"] == 45
    assert state["party_mode"] == "MST"


def test_parser_handles_vol_muted() -> None:
    state = SerialMessageParser.parse_state("#Z02PWRON,VOLMT")
    assert state["zone"] == 2
    assert state["mute"] is True


def test_parser_handles_vol_external_mute() -> None:
    state = SerialMessageParser.parse_state("#Z05PWRON,VOLXM")
    assert state["zone"] == 5
    assert state["external_mute"] is True


def test_parser_handles_alloff_broadcast() -> None:
    state = SerialMessageParser.parse_state("#ALLOFF")
    assert state == {"broadcast": "alloff"}


def test_parser_handles_extmon_broadcast() -> None:
    state = SerialMessageParser.parse_state("#EXTMON")
    assert state == {"broadcast": "extmon"}


def test_parser_handles_extmoff_broadcast() -> None:
    state = SerialMessageParser.parse_state("#EXTMOFF")
    assert state == {"broadcast": "extmoff"}


def test_parser_handles_error_response() -> None:
    state = SerialMessageParser.parse_state("#?")
    assert state is None


def test_parser_handles_party_mode_off() -> None:
    state = SerialMessageParser.parse_state("#Z03PWRON,POFF")
    assert state["party_mode"] == "OFF"


def test_parser_handles_party_mode_slave() -> None:
    state = SerialMessageParser.parse_state("#Z04PWRON,PSLV")
    assert state["party_mode"] == "SLV"


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
