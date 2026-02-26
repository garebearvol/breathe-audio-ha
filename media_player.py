"""Media player platform for Breathe Audio Elevate 6.6."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .breathe_audio import BreatheAudioAPI
from .const import (
    ATTR_BALANCE,
    ATTR_BASS,
    ATTR_MUTE,
    ATTR_PARTY_MODE,
    ATTR_SOURCE,
    ATTR_TREBLE,
    ATTR_VOLUME,
    ATTR_ZONE,
    DOMAIN,
    SOURCES,
    MAX_ATTENUATION,
    VERIFY_DELAY,
)

_LOGGER = logging.getLogger(__name__)

SUPPORT_BREATHE_AUDIO = (
    MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
    | MediaPlayerEntityFeature.VOLUME_MUTE
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Breathe Audio media players from config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    api: BreatheAudioAPI = data["api"]
    zones: int = data["zones"]
    base_name: str = data["name"]
    coordinator = data["coordinator"]
    serial_port = config_entry.data["serial_port"]

    entities = []
    for zone in range(1, zones + 1):
        entities.append(
            BreatheAudioZone(
                api,
                coordinator,
                zone,
                base_name,
                serial_port,
                config_entry.entry_id,
            )
        )

    async_add_entities(entities)


class BreatheAudioZone(MediaPlayerEntity, RestoreEntity):
    """Representation of a Breathe Audio zone."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = SUPPORT_BREATHE_AUDIO

    def __init__(
        self,
        api: BreatheAudioAPI,
        coordinator,
        zone: int,
        base_name: str,
        serial_port: str,
        entry_id: str,
    ) -> None:
        """Initialize the zone."""
        self._api = api
        self._coordinator = coordinator
        self._zone = zone
        self._attr_unique_id = f"{serial_port}_zone_{zone}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial_port)},
            name=base_name,
            manufacturer="Breathe Audio",
            model="Elevate 6.6 (BA-6640)",
        )
        self._attr_source_list = list(SOURCES.values())
        self._entry_id = entry_id

        # State cache
        self._state: Dict[str, Any] = {}
        self._saved_volume: Optional[int] = None

    @property
    def name(self) -> str:
        """Return the name of the zone."""
        return f"Zone {self._zone}"

    @property
    def state(self) -> MediaPlayerState:
        """Return the state of the device."""
        power = self._state.get("power")
        if power is True:
            return MediaPlayerState.ON
        return MediaPlayerState.OFF

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._coordinator.available

    @property
    def volume_level(self) -> Optional[float]:
        """Return the volume level (0.0 to 1.0)."""
        volume = self._state.get("volume")
        if volume is not None:
            # Invert: 0 (-dB) is loud (1.0), 78 (-dB) is quiet (0.0)
            level = (MAX_ATTENUATION - volume) / MAX_ATTENUATION
            return max(0.0, min(1.0, level))
        return None

    @property
    def is_volume_muted(self) -> Optional[bool]:
        """Return True if volume is muted."""
        return self._state.get("mute")

    @property
    def source(self) -> Optional[str]:
        """Return the current input source."""
        source = self._state.get("source")
        if source is not None:
            return SOURCES.get(source, f"Source {source}")
        return None

    @property
    def source_list(self) -> List[str]:
        """Return the list of available input sources."""
        return self._attr_source_list

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        attrs = {
            ATTR_ZONE: self._zone,
        }

        # Add tone controls if available
        if "bass" in self._state:
            attrs[ATTR_BASS] = self._state["bass"]
        if "treble" in self._state:
            attrs[ATTR_TREBLE] = self._state["treble"]
        if "balance" in self._state:
            attrs[ATTR_BALANCE] = self._state["balance"]
        if "party_mode" in self._state:
            attrs[ATTR_PARTY_MODE] = self._state["party_mode"]
        if self._saved_volume is not None:
            attrs["saved_volume"] = self._saved_volume

        return attrs

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to hass."""
        await super().async_added_to_hass()

        # Restore saved volume from previous session
        last_state = await self.async_get_last_state()
        if last_state and last_state.attributes:
            saved = last_state.attributes.get("saved_volume")
            if saved is not None:
                self._saved_volume = int(saved)
                _LOGGER.debug(
                    "Zone %d: restored saved volume %d from previous session",
                    self._zone,
                    self._saved_volume,
                )

        # Register for state updates
        self._coordinator.register_listener(self._zone, self._handle_update)
        # Get initial state
        self._state = self._coordinator.get_zone_state(self._zone)

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity will be removed from hass."""
        self._coordinator.unregister_listener(self._zone, self._handle_update)
        await super().async_will_remove_from_hass()

    @callback
    def _handle_update(self) -> None:
        """Handle state update from coordinator."""
        self._state = self._coordinator.get_zone_state(self._zone)
        # Track volume while zone is on so we always have the latest
        if self._state.get("power") and self._state.get("volume") is not None:
            self._saved_volume = self._state["volume"]
        self.async_write_ha_state()

    # Command methods - Optimistic UI Updates + post-command verification

    def _schedule_verify(self) -> None:
        """Schedule a delayed verification query to confirm device state."""
        async def _verify() -> None:
            await asyncio.sleep(VERIFY_DELAY)
            await self._coordinator.async_refresh_zone(self._zone)

        self.hass.async_create_task(_verify())

    async def async_turn_on(self) -> None:
        """Turn the zone on."""
        # Optimistic update: Show ON immediately
        self._state["power"] = True
        self.async_write_ha_state()

        await self._api.zone_power_on(self._zone)
        # Restore saved volume (amp needs time to initialize after power-on)
        if self._saved_volume is not None:
            await asyncio.sleep(1.0)
            await self._api.set_volume(self._zone, self._saved_volume)
            self._state["volume"] = self._saved_volume
            self.async_write_ha_state()
        self._schedule_verify()

    async def async_turn_off(self) -> None:
        """Turn the zone off."""
        # Optimistic update: Show OFF immediately
        self._state["power"] = False
        self.async_write_ha_state()

        # Save current volume before turning off
        current_vol = self._state.get("volume")
        if current_vol is not None:
            self._saved_volume = current_vol
        await self._api.zone_power_off(self._zone)
        self._schedule_verify()

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level (0.0 to 1.0)."""
        # Invert: 1.0 -> 0 (-dB), 0.0 -> 78 (-dB)
        vol_int = int(MAX_ATTENUATION - (volume * MAX_ATTENUATION))
        vol_int = max(0, min(MAX_ATTENUATION, vol_int))

        # Optimistic update + save for restore on next power-on
        self._state["volume"] = vol_int
        self._saved_volume = vol_int
        self.async_write_ha_state()

        await self._api.set_volume(self._zone, vol_int)
        self._schedule_verify()

    async def async_volume_up(self) -> None:
        """Volume up."""
        await self._api.volume_up(self._zone)
        self._schedule_verify()

    async def async_volume_down(self) -> None:
        """Volume down."""
        await self._api.volume_down(self._zone)
        self._schedule_verify()

    async def async_mute_volume(self, mute: bool) -> None:
        """Mute or unmute."""
        # Optimistic update
        self._state["mute"] = mute
        self.async_write_ha_state()

        if mute:
            await self._api.mute_on(self._zone)
        else:
            await self._api.mute_off(self._zone)
        self._schedule_verify()

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        # Find source number from name
        source_num = None
        for num, name in SOURCES.items():
            if name == source:
                source_num = num
                break

        if source_num is None:
            # Try to parse "Source X" format
            if source.startswith("Source "):
                try:
                    source_num = int(source.split()[1])
                except (ValueError, IndexError):
                    pass

        if source_num:
            await self._api.set_source(self._zone, source_num)
            self._schedule_verify()

    # Service methods for tone controls

    async def async_set_bass(self, level: int) -> None:
        """Set bass level (-10 to 10)."""
        await self._api.set_bass(self._zone, level)

    async def async_set_treble(self, level: int) -> None:
        """Set treble level (-10 to 10)."""
        await self._api.set_treble(self._zone, level)

    async def async_set_balance(self, level: int) -> None:
        """Set balance level (-10 to 10)."""
        await self._api.set_balance(self._zone, level)

    async def async_update(self) -> None:
        """Update the entity state."""
        await self._coordinator.async_refresh_zone(self._zone)
