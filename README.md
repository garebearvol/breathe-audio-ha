# Breathe Audio Elevate 6.6 (BA-6640) Home Assistant Integration

A custom integration for controlling the Breathe Audio Elevate 6.6 multi-zone amplifier via RS-232 serial connection.

## Features

- Control up to 12 zones independently
- Power on/off zones
- Volume control with mute
- Source selection (6 sources)
- Tone controls (bass, treble, balance) - exposed as attributes
- Polling-based state updates with async feedback support
- Automatic reconnection handling

## Installation

### HACS (Recommended)

1. Copy this folder to your `custom_components` directory
2. Restart Home Assistant
3. Go to **Settings** → **Devices & Services** → **Add Integration**
4. Search for "Breathe Audio"

### Manual Installation

1. Copy the `breathe_audio` folder to `<config>/custom_components/`
2. Restart Home Assistant
3. Add the integration via UI

## Configuration

The integration is configured via the Home Assistant UI:

| Option | Description | Default |
|--------|-------------|---------|
| Serial Port | Path to serial device (e.g., `/dev/ttyUSB0` or `COM3`) | Required |
| Device Name | Display name for the device | "Breathe Audio Elevate 6.6" |
| Number of Zones | Number of zones to configure (1-12) | 12 |
| Polling Interval | Seconds between status polls | 30 |

## Serial Connection

- **Baud Rate:** 9600
- **Data Bits:** 8
- **Parity:** None
- **Stop Bits:** 1

### Example Serial Ports

- Linux: `/dev/ttyUSB0`, `/dev/ttyACM0`
- macOS: `/dev/cu.usbserial-*`
- Windows: `COM3`, `COM4`

## Protocol

This integration uses the Breathe Audio RS-232 ASCII protocol:

- **Command Format:** `*ZxxCMD...<CR>`
- **Response Format:** `#Zxx...<CR>`

Where `xx` is the zone number (01-12).

### Supported Commands

| Command | Description |
|---------|-------------|
| `*ZxxPWRON<CR>` | Power on zone xx |
| `*ZxxPWROFF<CR>` | Power off zone xx |
| `*ZxxVOLxx<CR>` | Set volume (00-99) |
| `*ZxxVOL+<CR>` | Volume up |
| `*ZxxVOL-<CR>` | Volume down |
| `*ZxxMUTON<CR>` | Mute on |
| `*ZxxMUTOFF<CR>` | Mute off |
| `*ZxxSRCx<CR>` | Select source (1-6) |
| `*ZxxQST<CR>` | Query zone status |

## Entities

Each zone creates a `media_player` entity with:

- **State:** `on` / `off`
- **Volume:** 0-100%
- **Mute:** On/Off
- **Source:** Source 1-6
- **Attributes:**
  - `zone`: Zone number
  - `bass`: Bass level (-10 to 10)
  - `treble`: Treble level (-10 to 10)
  - `balance`: Balance level (-10 to 10)

## Services

Additional services can be called via `media_player` entity:

```yaml
service: media_player.volume_set
target:
  entity_id: media_player.breathe_audio_zone_1
data:
  volume_level: 0.5
```

## Troubleshooting

### Connection Issues

1. Verify the serial port is correct
2. Check user permissions for serial port access
3. Ensure no other application is using the serial port
4. Verify baud rate settings match (9600, 8N1)

### Enable Debug Logging

```yaml
logger:
  logs:
    custom_components.breathe_audio: debug
```

## License

MIT License