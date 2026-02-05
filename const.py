"""Constants for the Breathe Audio Elevate 6.6 integration."""

from typing import Final

DOMAIN: Final = "breathe_audio"

# Configuration keys
CONF_SERIAL_PORT: Final = "serial_port"
DEFAULT_SERIAL_PORT: Final = "/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG02QFFJ-if00-port0"
CONF_ZONES: Final = "zones"
CONF_POLLING_INTERVAL: Final = "polling_interval"

# Default values
DEFAULT_NAME: Final = "Breathe Audio Elevate 6.6"
DEFAULT_POLLING_INTERVAL: Final = 30  # seconds
DEFAULT_ZONES: Final = 12

# Serial connection settings
BAUDRATE: Final = 9600
BYTESIZE: Final = 8
PARITY: Final = "N"
STOPBITS: Final = 1
TIMEOUT: Final = 5

# Command/Response delimiters
COMMAND_PREFIX: Final = "*"
RESPONSE_PREFIX: Final = "#"
COMMAND_TERMINATOR: Final = "\r"
RESPONSE_TERMINATOR: Final = "\r"

# Zone range
MIN_ZONE: Final = 1
MAX_ZONE: Final = 12

# Command timeouts
COMMAND_TIMEOUT: Final = 2.0
CONNECTION_RETRY_INTERVAL: Final = 10

# Entity attributes
ATTR_ZONE: Final = "zone"
ATTR_SOURCE: Final = "source"
ATTR_VOLUME: Final = "volume"
ATTR_MUTE: Final = "mute"
ATTR_BASS: Final = "bass"
ATTR_TREBLE: Final = "treble"
ATTR_BALANCE: Final = "balance"
ATTR_PARTY_MODE: Final = "party_mode"

# Source mapping (assuming 6 sources based on 6.6 naming convention)
SOURCES = {
    1: "Source 1",
    2: "Source 2",
    3: "Source 3",
    4: "Source 4",
    5: "Source 5",
    6: "Source 6",
}

# Volume limits
MIN_VOLUME: Final = 0
MAX_VOLUME: 100

# Tone control limits
MIN_TONE: Final = -10
MAX_TONE: Final = 10

# Balance limits
MIN_BALANCE: Final = -10
MAX_BALANCE: Final = 10