"""Config flow to configure OpenHASP component."""
import json
import logging
import os

from homeassistant.components.mqtt import async_publish
from homeassistant import config_entries, data_entry_flow, exceptions
from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.components.mqtt import valid_subscribe_topic
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_DIMLIGHTS,
    CONF_HWID,
    CONF_IDLE_BRIGHTNESS,
    CONF_INPUT,
    CONF_LIGHTS,
    CONF_NODE,
    CONF_PAGES,
    CONF_PAGES_PATH,
    CONF_PLATE,
    CONF_RELAYS,
    CONF_TOPIC,
    DATA_TOPIC_OWNERS,
    DEFAULT_IDLE_BRIGHNESS,
    DISCOVERED_DIM,
    DISCOVERED_HWID,
    DISCOVERED_INPUT,
    DISCOVERED_LIGHT,
    DISCOVERED_MANUFACTURER,
    DISCOVERED_MODEL,
    DISCOVERED_NODE,
    DISCOVERED_NODE_T,
    DISCOVERED_PAGES,
    DISCOVERED_POWER,
    DISCOVERED_URL,
    DISCOVERED_VERSION,
    DOMAIN,
    MAJOR,
    MINOR,
)

_LOGGER = logging.getLogger(__name__)


def canonical_plate_id(discovered):
    """Return the stable canonical identity for a discovered plate."""
    hwid = discovered.get(DISCOVERED_HWID)
    if hwid is None:
        raise data_entry_flow.AbortFlow("invalid_discovery_info")

    hwid = str(hwid).strip().lower()
    if not hwid:
        raise data_entry_flow.AbortFlow("invalid_discovery_info")

    return hwid


def normalize_mqtt_topic(topic):
    """Return a comparable MQTT base topic."""
    return str(topic or "").rstrip("/")


def topic_entries(hass, topic):
    """Return enabled config entries currently storing a topic."""
    normalized_topic = normalize_mqtt_topic(topic)
    if not normalized_topic:
        return []

    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.disabled_by is None
        and normalize_mqtt_topic(entry.data.get(CONF_TOPIC)) == normalized_topic
    ]


def find_topic_collision(hass, topic, plate_id, entry_id=None):
    """Return the enabled config entry using a topic for another plate."""
    normalized_topic = normalize_mqtt_topic(topic)
    if not normalized_topic:
        return None

    for entry in topic_entries(hass, topic):
        if entry_id is not None and entry.entry_id == entry_id:
            continue

        try:
            entry_plate_id = canonical_plate_id(entry.data)
        except data_entry_flow.AbortFlow:
            continue

        if entry_plate_id == plate_id:
            continue

        if normalize_mqtt_topic(entry.data.get(CONF_TOPIC)) == normalized_topic:
            return entry

    return None


async def async_block_topic_collisions(hass, topic, keep_entry=None):
    """Disable all stored owners except an authoritative discovered entry."""
    entries = topic_entries(hass, topic)
    if keep_entry is not None:
        entries = [
            entry for entry in entries if entry.entry_id != keep_entry.entry_id
        ]

    for entry in entries:
        conflict = next(
            (
                candidate
                for candidate in topic_entries(hass, topic)
                if candidate.entry_id != entry.entry_id
            ),
            None,
        )
        if keep_entry is not None:
            log_topic_collision(
                keep_entry.entry_id,
                keep_entry.data.get(CONF_HWID),
                keep_entry.data.get(CONF_NAME),
                topic,
                entry,
            )
        elif conflict is not None:
            log_topic_collision(
                entry.entry_id,
                entry.data.get(CONF_HWID),
                entry.data.get(CONF_NAME),
                topic,
                conflict,
            )
        _LOGGER.error(
            "Blocking stored MQTT topic owner until discovery: "
            "entry_id=%s hwid=%s name=%s topic=%s",
            entry.entry_id,
            entry.data.get(CONF_HWID),
            entry.data.get(CONF_NAME),
            entry.data.get(CONF_TOPIC),
        )
        if entry.entry_id in hass.data.get(DOMAIN, {}).get(CONF_PLATE, {}):
            _LOGGER.error("DISABLING STALE CLAIM entry_id=%s", entry.entry_id)
        await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )


async def async_recover_discovered_owner(
    hass, plate_id, topic, owner_entry=None
):
    """Make a discovered hardware id authoritative for its MQTT topic."""
    normalized_topic = normalize_mqtt_topic(topic)
    owners = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_TOPIC_OWNERS, {})
    owners[normalized_topic] = plate_id

    for entry in hass.config_entries.async_entries(DOMAIN):
        if owner_entry is not None and entry.entry_id == owner_entry.entry_id:
            continue
        try:
            entry_plate_id = canonical_plate_id(entry.data)
        except data_entry_flow.AbortFlow:
            continue

        if entry_plate_id == plate_id:
            continue
        if normalize_mqtt_topic(entry.data.get(CONF_TOPIC)) != normalized_topic:
            continue

        _LOGGER.error(
            "STALE TOPIC CLAIM entry_id=%s hwid=%s topic=%s",
            entry.entry_id,
            entry_plate_id,
            entry.data.get(CONF_TOPIC),
        )
        _LOGGER.error("DISABLING STALE CLAIM entry_id=%s", entry.entry_id)
        await hass.config_entries.async_set_disabled_by(
            entry.entry_id, ConfigEntryDisabler.USER
        )


async def async_apply_discovered_entry(hass, entry, updates):
    """Apply authoritative discovery data and reload the discovered entry."""
    was_loaded = entry.entry_id in hass.data.get(DOMAIN, {}).get(CONF_PLATE, {})
    was_disabled_by_user = entry.disabled_by == ConfigEntryDisabler.USER
    topic = updates.get(CONF_TOPIC)
    topic_changed = topic is not None and normalize_mqtt_topic(
        entry.data.get(CONF_TOPIC)
    ) != topic

    if was_loaded and topic_changed:
        await hass.config_entries.async_unload(entry.entry_id)

    await async_recover_discovered_owner(
        hass, canonical_plate_id(entry.data), topic, owner_entry=entry
    )

    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, **updates},
    )

    if was_disabled_by_user:
        _LOGGER.error("ENABLING DISCOVERED OWNER entry_id=%s", entry.entry_id)
        _LOGGER.error("RELOADING DISCOVERED OWNER entry_id=%s", entry.entry_id)
        await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
    elif not was_loaded or topic_changed:
        _LOGGER.error("RELOADING DISCOVERED OWNER entry_id=%s", entry.entry_id)
        await hass.config_entries.async_setup(entry.entry_id)


def log_topic_collision(entry_id, hwid, name, topic, conflict):
    """Log both sides of an MQTT topic collision."""
    _LOGGER.error(
        "MQTT topic collision: entry_id=%s hwid=%s name=%s topic=%s "
        "conflicts with entry_id=%s hwid=%s name=%s topic=%s",
        entry_id,
        hwid,
        name,
        topic,
        conflict.entry_id,
        conflict.data.get(CONF_HWID),
        conflict.data.get(CONF_NAME),
        conflict.data.get(CONF_TOPIC),
    )


def discovery_entry_updates(discovered):
    """Return dynamic config entry data from a discovery payload."""
    updates = {}
    if (topic := discovered.get(CONF_TOPIC)) is not None:
        updates[CONF_TOPIC] = normalize_mqtt_topic(topic)
    if (url := discovered.get(DISCOVERED_URL)) is not None:
        updates[DISCOVERED_URL] = url
    return updates


def validate_jsonl(path):
    """Validate that the value is an existing file."""
    if path is None:
        raise InvalidJSONL()
    file_in = os.path.expanduser(str(path))

    if not os.path.isfile(file_in):
        raise InvalidJSONL("not a file")
    if not os.access(file_in, os.R_OK):
        raise InvalidJSONL("file not readable")
    return file_in


class OpenHASPFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for OpenHASP component."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_PUSH

    def __init__(self):
        """Init OpenHASPFlowHandler."""
        self._errors = {}
        self.config_data = {
            DISCOVERED_MANUFACTURER: "openHASP",
            DISCOVERED_MODEL: None,
            CONF_RELAYS: [],
        }

    def _async_entry_for_plate_id(self, plate_id):
        """Return an existing config entry matching a canonical plate id."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            try:
                entry_plate_id = canonical_plate_id(entry.data)
            except data_entry_flow.AbortFlow:
                continue

            if entry_plate_id == plate_id:
                return entry

        return None

    async def async_step_user(self, user_input=None):
        """Handle a flow initialized by User."""
        _LOGGER.info("Discovery Only")

        await async_publish(
            self.hass,
            "hasp/broadcast/command/discovery",
            "discovery",
            qos=0,
            retain=False,
        )

        return self.async_abort(reason="discovery_only")

    async def async_step_zeroconf(self, discovery_info=None):
        _discovered = dict(discovery_info.properties)
        _LOGGER.debug("Discovered ZeroConf: %s", _discovered)

        _discovered[CONF_TOPIC] = normalize_mqtt_topic(
            _discovered[DISCOVERED_NODE_T]
        )

        for key in [
            DISCOVERED_PAGES,
            DISCOVERED_POWER,
            DISCOVERED_LIGHT,
            DISCOVERED_DIM,
            DISCOVERED_INPUT,
        ]:
            _LOGGER.debug(
                "[%s] Discovered %s = %s",
                _discovered[CONF_TOPIC],
                key,
                _discovered.get(key),
            )
            _discovered[key] = json.loads(_discovered.get(key))

        return await self._process_discovery(_discovered)

    async def async_step_mqtt(self, discovery_info=None):
        """Handle a flow initialized by MQTT discovery."""
        _discovered = json.loads(discovery_info.payload)
        _LOGGER.debug("Discovered MQTT: %s", _discovered)

        _discovered[CONF_TOPIC] = normalize_mqtt_topic(
            f"{discovery_info.topic.split('/')[0]}/{_discovered[DISCOVERED_NODE]}"
        )

        return await self._process_discovery(_discovered)

    async def _process_discovery(self, _discovered):
        plate_id = canonical_plate_id(_discovered)
        updates = discovery_entry_updates(_discovered)
        discovered_topic = updates.get(CONF_TOPIC)

        if entry := self._async_entry_for_plate_id(plate_id):
            _LOGGER.error(
                "DISCOVERY OWNER hwid=%s entry_id=%s topic=%s",
                plate_id,
                entry.entry_id,
                discovered_topic,
            )
            if updates:
                await async_apply_discovered_entry(self.hass, entry, updates)
            return self.async_abort(reason="already_configured")

        if discovered_topic:
            _LOGGER.error(
                "DISCOVERY OWNER hwid=%s entry_id=<new> topic=%s",
                plate_id,
                discovered_topic,
            )
            # This discovery is authoritative. Any enabled entry that merely
            # stores this topic for another hardware id is stale and must not
            # block the discovered device.
            await async_recover_discovered_owner(
                self.hass, plate_id, discovered_topic
            )

        await self.async_set_unique_id(plate_id)
        self._abort_if_unique_id_configured(updates=updates)

        version = _discovered.get(DISCOVERED_VERSION)
        if version.split(".")[0:2] != [MAJOR, MINOR]:
            _LOGGER.error(
                "Version mismatch! Your plate: %s - openHASP Component: %s",
                version,
                f"{MAJOR}.{MINOR}.x",
            )
            raise data_entry_flow.AbortFlow("mismatch_version")

        self.config_data[DISCOVERED_VERSION] = version

        self.config_data[CONF_HWID] = plate_id
        self.config_data[CONF_NODE] = self.config_data[CONF_NAME] = _discovered[
            DISCOVERED_NODE
        ]
        self.config_data[CONF_TOPIC] = normalize_mqtt_topic(_discovered[CONF_TOPIC])

        self.config_data[DISCOVERED_URL] = _discovered.get(DISCOVERED_URL)
        self.config_data[DISCOVERED_MANUFACTURER] = _discovered.get(
            DISCOVERED_MANUFACTURER
        )
        self.config_data[DISCOVERED_MODEL] = _discovered.get(DISCOVERED_MODEL)
        self.config_data[CONF_PAGES] = _discovered.get(DISCOVERED_PAGES)
        self.config_data[CONF_RELAYS] = _discovered.get(DISCOVERED_POWER)
        self.config_data[CONF_LIGHTS] = _discovered.get(DISCOVERED_LIGHT)
        self.config_data[CONF_DIMLIGHTS] = _discovered.get(DISCOVERED_DIM)
        self.config_data[CONF_INPUT] = _discovered.get(DISCOVERED_INPUT)

        self.context.update(
            {"title_placeholders": {"name": self.config_data[CONF_NODE]}}
        )

        return await self.async_step_personalize()

    async def async_step_personalize(self, user_input=None):
        """Handle a flow initialized by the user."""
        self._errors = {}

        if user_input is not None:
            discovered_topic = self.config_data.get(CONF_TOPIC)
            self.config_data = {**self.config_data, **user_input}

            # Discovery owns the physical MQTT routing topic.
            if discovered_topic is not None:
                self.config_data[CONF_TOPIC] = normalize_mqtt_topic(discovered_topic)

            try:
                valid_subscribe_topic(self.config_data[CONF_TOPIC])

                if CONF_PAGES_PATH in user_input:
                    self.config_data[CONF_PAGES_PATH] = validate_jsonl(
                        user_input[CONF_PAGES_PATH]
                    )

                plate_id = canonical_plate_id(self.config_data)
                await self.async_set_unique_id(plate_id)
                self._abort_if_unique_id_configured()

                conflict = find_topic_collision(
                    self.hass, self.config_data[CONF_TOPIC], plate_id
                )
                if conflict:
                    log_topic_collision(
                        "<new>",
                        plate_id,
                        self.config_data.get(CONF_NAME),
                        self.config_data[CONF_TOPIC],
                        conflict,
                    )
                    return self.async_abort(reason="topic_collision")

                return self.async_create_entry(
                    title=user_input[CONF_NAME], data=self.config_data
                )

            except vol.Invalid:
                return self.async_abort(reason="invalid_discovery_info")

            except InvalidJSONL:
                self._errors[CONF_PAGES_PATH] = "invalid_jsonl_path"

        return self.async_show_form(
            step_id="personalize",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NAME, default=self.config_data.get(CONF_NAME)
                    ): str,
                    vol.Optional(
                        CONF_IDLE_BRIGHTNESS, default=DEFAULT_IDLE_BRIGHNESS
                    ): vol.All(int, vol.Range(min=0, max=255)),
                    vol.Optional(CONF_PAGES_PATH): str,
                }
            ),
            errors=self._errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Set the OptionsFlowHandler."""
        return OpenHASPOptionsFlowHandler()


class OpenHASPOptionsFlowHandler(config_entries.OptionsFlow):
    """ConfigOptions flow for openHASP."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Actually check path is a file

            try:
                if len(user_input[CONF_PAGES_PATH]):
                    user_input[CONF_PAGES_PATH] = validate_jsonl(
                        user_input[CONF_PAGES_PATH]
                    )
            except InvalidJSONL:
                return self.async_abort(reason="invalid_jsonl_path")

            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_IDLE_BRIGHTNESS,
                        default=self.config_entry.options.get(
                            CONF_IDLE_BRIGHTNESS,
                            self.config_entry.data[CONF_IDLE_BRIGHTNESS],
                        ),
                    ): vol.All(int, vol.Range(min=0, max=255)),
                    vol.Optional(
                        CONF_PAGES_PATH,
                        default=self.config_entry.options.get(
                            CONF_PAGES_PATH,
                            self.config_entry.data.get(CONF_PAGES_PATH, ""),
                        ),
                    ): cv.string,
                }
            ),
        )


class InvalidJSONL(exceptions.HomeAssistantError):
    """Error to indicate we cannot load JSONL."""
