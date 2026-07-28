"""HASP-LVGL Commonalities."""
import logging

from homeassistant.core import callback
from homeassistant.helpers.entity import Entity, ToggleEntity
import voluptuous as vol

from .const import (
    CONF_PLATE,
    DATA_AVAILABILITY,
    DOMAIN,
    EVENT_HASP_PLATE_OFFLINE,
    EVENT_HASP_PLATE_ONLINE,
    HASP_IDLE_STATES,
)

_LOGGER = logging.getLogger(__name__)


HASP_IDLE_SCHEMA = vol.Schema(vol.Any(*HASP_IDLE_STATES))


@callback
def async_update_plate_availability(hass, hwid: str, available: bool) -> None:
    """Latch a plate's online state centrally, keyed by hardware id.

    The plate's LWT is retained, so the broker delivers it exactly once: to
    whoever happens to be subscribed at that moment. SwitchPlate subscribes
    while the config entry is being set up, which is before the platform
    entities exist, so the ONLINE event it fires can reach no listeners at all
    and is never repeated. The events stay in charge of subsequent changes; this
    latch is what lets an entity added afterwards read the current truth.
    """
    hass.data[DOMAIN].setdefault(DATA_AVAILABILITY, {})[hwid] = available


@callback
def plate_availability(hass, hwid: str) -> bool:
    """Return a plate's latched online state, False until its first LWT."""
    return hass.data[DOMAIN].get(DATA_AVAILABILITY, {}).get(hwid, False)


class HASPEntity(Entity):
    """Generic HASP entity (base class)."""

    def __init__(self, name, hwid: str, topic: str, gpio=None) -> None:
        """Initialize the HASP entity."""
        super().__init__()
        self._name = name
        self._hwid = hwid
        self._topic = topic
        self._gpio = gpio
        self._state = None
        self._available = False
        self._subscriptions = []
        self._attr_unique_id = f"{self._hwid}.{gpio}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._hwid)},
        }

    @property
    def available(self):
        """Return if entity is available."""
        return self._available

    async def refresh(self):
        """Sync local state back to plate."""
        raise NotImplementedError()

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        # TEMP DEBUG: proves this entity reached the point where it can observe
        # availability at all, and what it believed beforehand.
        _LOGGER.debug(
            "%s added to hass (hwid=%s), _available=%s before seeding",
            self.entity_id,
            self._hwid,
            self._available,
        )

        @callback
        async def online(event):
            if event.data[CONF_PLATE] == self._hwid:
                self._available = True
                if self._state:
                    await self.refresh()
                else:
                    self.async_write_ha_state()  # Just to update availability
                _LOGGER.debug(
                    "%s is available, %s",
                    self.entity_id,
                    "refresh" if self._state else "stale",
                )

        self._subscriptions.append(
            self.hass.bus.async_listen(EVENT_HASP_PLATE_ONLINE, online)
        )

        @callback
        async def offline(event):
            if event.data[CONF_PLATE] == self._hwid:
                self._available = False
                self.async_write_ha_state()

        self._subscriptions.append(
            self.hass.bus.async_listen(EVENT_HASP_PLATE_OFFLINE, offline)
        )

        # Seed from the central latch, deliberately AFTER registering both
        # listeners. In that order an LWT processed at any instant is caught by
        # exactly one of the two paths: earlier than this entity existed -> the
        # latch already holds it; later -> the listeners above fire. Seeding
        # first would leave a gap in between where neither applies.
        # Home Assistant writes this entity's state right after this coroutine
        # returns, so no explicit async_write_ha_state() is needed here.
        self._available = plate_availability(self.hass, self._hwid)

        # TEMP DEBUG: proves the initial availability came from the latch.
        _LOGGER.debug(
            "%s seeded _available=%s from latch, available=%s",
            self.entity_id,
            self._available,
            self.available,
        )

    async def async_will_remove_from_hass(self):
        """Run when entity about to be removed."""
        await super().async_will_remove_from_hass()

        for subscription in self._subscriptions:
            subscription()


class HASPToggleEntity(HASPEntity, ToggleEntity):
    """Representation of HASP ToggleEntity."""

    def __init__(self, name, hwid, topic, gpio):
        """Initialize the relay."""
        super().__init__(name, hwid, topic, gpio)

    @property
    def is_on(self):
        """Return true if device is on."""
        return self._state

    async def async_turn_on(self, **kwargs):
        """Turn on."""
        self._state = True
        await self.refresh()

    async def async_turn_off(self, **kwargs):
        """Turn off."""
        self._state = False
        await self.refresh()
