"""Config flow to configure OpenHASP component."""
import json
import logging
import os

from homeassistant.components.mqtt import async_publish
from homeassistant import config_entries, data_entry_flow, exceptions
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
    CONF_RELAYS,
    CONF_TOPIC,
    DEFAULT_IDLE_BRIGHNESS,
    DEFAULT_TOPIC,
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


def discovery_entry_updates(discovered):
    """Return dynamic config entry data from a discovery payload."""
    return {
        key: value
        for key in (CONF_TOPIC, DISCOVERED_URL)
        if (value := discovered.get(key)) is not None
    }


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

        _discovered[CONF_TOPIC] = _discovered[DISCOVERED_NODE_T][:-1]

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

        _discovered[
            CONF_TOPIC
        ] = f"{discovery_info.topic.split('/')[0]}/{_discovered[DISCOVERED_NODE]}"

        return await self._process_discovery(_discovered)

    async def _process_discovery(self, _discovered):
        plate_id = canonical_plate_id(_discovered)
        updates = discovery_entry_updates(_discovered)

        if entry := self._async_entry_for_plate_id(plate_id):
            if updates:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **updates}
                )
            return self.async_abort(reason="already_configured")

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
        self.config_data[CONF_TOPIC] = _discovered[CONF_TOPIC]

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
            self.config_data = {**self.config_data, **user_input}

            # Remove / from base topic
            if user_input[CONF_TOPIC].endswith("/"):
                user_input[CONF_TOPIC] = user_input[CONF_TOPIC][:-1]
                self.config_data[CONF_TOPIC] = user_input[CONF_TOPIC]

            try:
                valid_subscribe_topic(self.config_data[CONF_TOPIC])

                if CONF_PAGES_PATH in user_input:
                    self.config_data[CONF_PAGES_PATH] = validate_jsonl(
                        user_input[CONF_PAGES_PATH]
                    )

                await self.async_set_unique_id(
                    canonical_plate_id(self.config_data)
                )
                self._abort_if_unique_id_configured()

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
                        CONF_TOPIC,
                        default=self.config_data.get(CONF_TOPIC, DEFAULT_TOPIC),
                    ): str,
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
