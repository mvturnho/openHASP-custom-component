"""HASP components module."""
import hashlib
import json
import logging
import math
import os
import pathlib
import re
from datetime import timedelta

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    format_mac,
)
from homeassistant.components.mqtt import async_subscribe, async_publish
import homeassistant.components.mqtt as mqtt
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback, Context
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import device_registry as dr, entity_registry
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity_component import EntityComponent
from homeassistant.helpers.event import (
    TrackTemplate,
    async_track_state_change_event,
    async_track_template_result,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
from homeassistant.helpers.network import get_url
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.script import Script
from homeassistant.util import slugify
import jsonschema
import voluptuous as vol

from .common import HASP_IDLE_SCHEMA
from .const import (
    ATTR_COMMAND_KEYWORD,
    ATTR_COMMAND_PARAMETERS,
    ATTR_CONFIG_PARAMETERS,
    ATTR_CONFIG_SUBMODULE,
    ATTR_FORCE_FITSCREEN,
    ATTR_HEIGHT,
    ATTR_IDLE,
    ATTR_PROXY,
    ATTR_IMAGE,
    ATTR_OBJECT,
    ATTR_PAGE,
    ATTR_PATH,
    ATTR_WIDTH,
    CONF_CHART,
    CONF_CHART_COLOR,
    CONF_CHART_ENTITY,
    CONF_CHART_GRID_COLOR,
    CONF_CHART_HDIV,
    CONF_CHART_HISTORY_HOURS,
    CONF_CHART_INTERVAL,
    CONF_CHART_LINE_WIDTH,
    CONF_CHART_MAX,
    CONF_CHART_MIN,
    CONF_CHART_POINT_COUNT,
    CONF_CHART_POINT_SIZE,
    CONF_CHART_SCALE,
    CONF_CHART_SERIES,
    CONF_CHART_TYPE,
    CONF_CHART_VDIV,
    CONF_CHART_Y_LABEL,
    CONF_CHART_Y_LABEL,
    CONF_CHART_SHOW_SCALE,
    CONF_COMPONENT,
    CONF_EVENT,
    CONF_HWID,
    CONF_OBJECTS,
    CONF_OBJID,
    CONF_PAGES,
    CONF_PAGES_PATH,
    CONF_PLATE,
    CONF_PROPERTIES,
    CONF_TOPIC,
    CONF_TRACK,
    CONF_SUBTOPIC,
    DATA_IMAGES,
    DATA_LISTENER,
    DISCOVERED_MANUFACTURER,
    DISCOVERED_MODEL,
    DISCOVERED_URL,
    DISCOVERED_VERSION,
    DOMAIN,
    EVENT_HASP_PLATE_OFFLINE,
    EVENT_HASP_PLATE_ONLINE,
    HASP_EVENT,
    HASP_EVENT_DOWN,
    HASP_EVENT_RELEASE,
    HASP_EVENT_UP,
    HASP_EVENTS,
    HASP_LWT,
    HASP_NUM_PAGES,
    HASP_ONLINE,
    HASP_VAL,
    MAJOR,
    MINOR,
    SERVICE_CLEAR_PAGE,
    SERVICE_COMMAND,
    SERVICE_CONFIG,
    SERVICE_LOAD_PAGE,
    SERVICE_PAGE_CHANGE,
    SERVICE_PAGE_NEXT,
    SERVICE_PAGE_PREV,
    SERVICE_PUSH_IMAGE,
    SERVICE_WAKEUP,
)
from .image import ImageServeView, image_to_rgb565

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    LIGHT_DOMAIN,
    SWITCH_DOMAIN,
    BINARY_SENSOR_DOMAIN,
    NUMBER_DOMAIN,
    BUTTON_DOMAIN,
]


def hasp_object(value):
    """Validade HASP-LVGL object format."""
    if re.match("p[0-9]+b[0-9]+", value):
        return value
    raise vol.Invalid("Not an HASP-LVGL object p#b#")


# Configuration YAML schemas
EVENT_SCHEMA = cv.schema_with_slug_keys(cv.SCRIPT_SCHEMA)

PROPERTY_SCHEMA = cv.schema_with_slug_keys(cv.template)

CHART_SERIES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHART_ENTITY): cv.entity_id,
        vol.Optional(CONF_CHART_COLOR, default="#ffffff"): cv.string,
    }
)

CHART_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHART_SERIES): vol.All(cv.ensure_list, [CHART_SERIES_SCHEMA]),
        vol.Optional(CONF_CHART_HISTORY_HOURS, default=1): vol.All(
            vol.Coerce(float), vol.Range(min=0.1, max=168)
        ),
        vol.Optional(CONF_CHART_TYPE): vol.All(int, vol.Range(min=0, max=2)),
        vol.Optional(CONF_CHART_MIN): vol.Coerce(float),
        vol.Optional(CONF_CHART_MAX): vol.Coerce(float),
        vol.Optional(CONF_CHART_INTERVAL): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=1440)),
        vol.Optional(CONF_CHART_SCALE, default=1.0): vol.Coerce(float),
        vol.Optional(CONF_CHART_POINT_COUNT): vol.All(int, vol.Range(min=1, max=1000)),
        vol.Optional(CONF_CHART_GRID_COLOR): cv.string,
        vol.Optional(CONF_CHART_HDIV): vol.All(int, vol.Range(min=0, max=255)),
        vol.Optional(CONF_CHART_VDIV): vol.All(int, vol.Range(min=0, max=255)),
        vol.Optional(CONF_CHART_LINE_WIDTH): vol.All(int, vol.Range(min=1, max=20)),
        vol.Optional(CONF_CHART_POINT_SIZE, default=0): vol.All(int, vol.Range(min=0, max=20)),
        vol.Optional(CONF_CHART_Y_LABEL): cv.string,
        vol.Optional(CONF_CHART_Y_LABEL): cv.string,
        vol.Optional(CONF_CHART_SHOW_SCALE, default=False): cv.boolean,
    }
)

OBJECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_OBJID): hasp_object,
        vol.Optional(CONF_TRACK, default=None): vol.Any(cv.entity_id, None),
        vol.Optional(CONF_PROPERTIES, default={}): PROPERTY_SCHEMA,
        vol.Optional(CONF_EVENT, default={}): EVENT_SCHEMA,
        vol.Optional(CONF_SUBTOPIC): cv.string,
        vol.Optional(CONF_CHART): CHART_SCHEMA,
    }
)

PLATE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_OBJECTS): vol.All(cv.ensure_list, [OBJECT_SCHEMA]),
    },
)

CONFIG_SCHEMA = vol.Schema(
    {DOMAIN: vol.Schema({cv.slug: PLATE_SCHEMA})}, extra=vol.ALLOW_EXTRA
)

# JSON Messages from HASP schemas
HASP_VAL_SCHEMA = vol.Schema(
    {vol.Required(HASP_VAL): vol.All(int, vol.Range(min=0, max=1))},
    extra=vol.ALLOW_EXTRA,
)
HASP_EVENT_SCHEMA = vol.Schema(
    {vol.Required(HASP_EVENT): vol.Any(*HASP_EVENTS)}, extra=vol.ALLOW_EXTRA
)

HASP_STATUSUPDATE_SCHEMA = vol.Schema(
    {
        vol.Required("node"): cv.string,
        vol.Required("version"): cv.string,
        vol.Required("uptime"): int,
        vol.Required("canUpdate"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)

HASP_LWT_SCHEMA = vol.Schema(vol.Any(*HASP_LWT))

HASP_PAGE_SCHEMA = vol.Schema(vol.All(vol.Coerce(int), vol.Range(min=0, max=12)))

PUSH_IMAGE_SCHEMA = cv.make_entity_service_schema(
    {
        vol.Required(ATTR_IMAGE): vol.Any(cv.url, cv.isfile),
        vol.Required(ATTR_OBJECT): hasp_object,
        vol.Optional(ATTR_PROXY): cv.url,
        vol.Optional(ATTR_WIDTH): cv.positive_int,
        vol.Optional(ATTR_HEIGHT): cv.positive_int,
        vol.Optional(ATTR_FORCE_FITSCREEN): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass, config):
    """Wait for MQTT to become available before starting."""
    await mqtt.async_wait_for_mqtt_client(hass)

    """Set up the MQTT async example component."""
    conf = config.get(DOMAIN)

    if conf is None:
        # We still depend in YAML so we must fail
        _LOGGER.error(
            "openHASP requires you to setup your plate objects in your YAML configuration."
        )
        return False

    hass.data[DOMAIN] = {CONF_PLATE: {}}

    component = hass.data[DOMAIN][CONF_COMPONENT] = EntityComponent(_LOGGER, DOMAIN, hass)

    # Use cv.make_entity_service_schema for all services for consistency
    component.async_register_entity_service(
        SERVICE_WAKEUP,
        cv.make_entity_service_schema({}),
        "async_wakeup",
    )
    component.async_register_entity_service(
        SERVICE_PAGE_NEXT,
        cv.make_entity_service_schema({}),
        "async_change_page_next",
    )
    component.async_register_entity_service(
        SERVICE_PAGE_PREV,
        cv.make_entity_service_schema({}),
        "async_change_page_prev",
    )
    component.async_register_entity_service(
        SERVICE_PAGE_CHANGE,
        cv.make_entity_service_schema({vol.Required(ATTR_PAGE): int}),
        "async_change_page",
    )
    component.async_register_entity_service(
        SERVICE_LOAD_PAGE,
        cv.make_entity_service_schema({vol.Required(ATTR_PATH): cv.isfile}),
        "async_load_page",
    )
    component.async_register_entity_service(
        SERVICE_CLEAR_PAGE,
        cv.make_entity_service_schema({vol.Optional(ATTR_PAGE): int}),
        "async_clearpage",
    )
    component.async_register_entity_service(
        SERVICE_COMMAND,
        cv.make_entity_service_schema({
            vol.Required(ATTR_COMMAND_KEYWORD): cv.string,
            vol.Optional(ATTR_COMMAND_PARAMETERS, default=""): cv.string,
        }),
        "async_command_service",
    )
    component.async_register_entity_service(
        SERVICE_CONFIG,
        cv.make_entity_service_schema({
            vol.Required(ATTR_CONFIG_SUBMODULE): cv.string,
            vol.Required(ATTR_CONFIG_PARAMETERS): cv.string,
        }),
        "async_config_service",
    )
    component.async_register_entity_service(
        SERVICE_PUSH_IMAGE,
        PUSH_IMAGE_SCHEMA,
        "async_push_image",
    )

    hass.data[DOMAIN][DATA_IMAGES] = dict()
    hass.http.register_view(ImageServeView)

    return True


async def async_update_options(hass, entry):
    """Handle options update."""
    _LOGGER.debug("Reloading")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass, entry) -> bool:
    """Set up OpenHASP via a config entry."""
    plate = entry.data[CONF_NAME]
    _LOGGER.debug("Setup %s", plate)

    hass_config = await async_integration_yaml_config(hass, DOMAIN)

    if DOMAIN not in hass_config or slugify(plate) not in hass_config[DOMAIN]:
        _LOGGER.error(
            "No YAML configuration for %s, \
            please create an entry under 'openhasp' with the slug: %s",
            plate,
            slugify(plate),
        )
        return False

    config = hass_config[DOMAIN][slugify(plate)]

    # Register Plate device
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data[CONF_HWID])},
        manufacturer=entry.data[DISCOVERED_MANUFACTURER],
        model=entry.data[DISCOVERED_MODEL],
        sw_version=entry.data[DISCOVERED_VERSION],
        configuration_url=entry.data.get(DISCOVERED_URL),
        name=plate,
        connections={(CONNECTION_NETWORK_MAC, format_mac(entry.data[CONF_HWID]))},
    )

    # Add entity to component
    component = hass.data[DOMAIN][CONF_COMPONENT]
    plate_entity = SwitchPlate(hass, config, entry)
    await component.async_add_entities([plate_entity])
    hass.data[DOMAIN][CONF_PLATE][plate] = plate_entity

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    listener = entry.add_update_listener(async_update_options)
    entry.async_on_unload(listener)

    return True


async def async_unload_entry(hass, entry):
    """Remove a config entry."""
    plate = entry.data[CONF_NAME]

    _LOGGER.debug("Unload entry for plate %s", plate)

    for domain in PLATFORMS:
        await hass.config_entries.async_forward_entry_unload(entry, domain)

    component = hass.data[DOMAIN][CONF_COMPONENT]
    await component.async_remove_entity(hass.data[DOMAIN][CONF_PLATE][plate].entity_id)

    # Remove Plate entity
    del hass.data[DOMAIN][CONF_PLATE][plate]

    return True


async def async_remove_entry(hass, entry):
    plate = entry.data[CONF_NAME]

    # Only remove services if it is the last
    if len(hass.data[DOMAIN][CONF_PLATE]) == 1:
        _LOGGER.debug("removing services")
        hass.services.async_remove(DOMAIN, SERVICE_WAKEUP)
        hass.services.async_remove(DOMAIN, SERVICE_PAGE_NEXT)
        hass.services.async_remove(DOMAIN, SERVICE_PAGE_PREV)
        hass.services.async_remove(DOMAIN, SERVICE_PAGE_CHANGE)
        hass.services.async_remove(DOMAIN, SERVICE_LOAD_PAGE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_PAGE)
        hass.services.async_remove(DOMAIN, SERVICE_COMMAND)

    device_registry = dr.async_get(hass)
    dev = device_registry.async_get_device(
        identifiers={(DOMAIN, entry.data[CONF_HWID])}
    )
    if entry.entry_id in dev.config_entries:
        _LOGGER.debug("Removing device %s", dev)
        device_registry.async_remove_device(dev.id)

    # Component does not remove entity from entity_registry, so we must do it
    registry = entity_registry.async_get(hass)
    registry.async_remove(hass.data[DOMAIN][CONF_PLATE][plate].entity_id)


# pylint: disable=R0902
class SwitchPlate(RestoreEntity):
    """Representation of an openHASP Plate."""

    def __init__(self, hass, config, entry):
        """Initialize a plate."""
        super().__init__()
        self._entry = entry
        self._topic = entry.data[CONF_TOPIC]
        self._pages_jsonl = entry.options.get(
            CONF_PAGES_PATH, entry.data.get(CONF_PAGES_PATH)
        )

        self._objects = []
        for obj in config[CONF_OBJECTS]:
            if CONF_CHART in obj:
                new_obj = HASPChart(hass, self._topic, obj)
            else:
                new_obj = HASPObject(hass, self._topic, obj)
            self._objects.append(new_obj)
        self._statusupdate = {HASP_NUM_PAGES: entry.data[CONF_PAGES]}
        self._available = False
        self._page = 1

        self._subscriptions = []

        self._attr_unique_id = entry.data[CONF_HWID]
        self._attr_name = entry.data[CONF_NAME]
        self._attr_icon = "mdi:gesture-tap-box"

    def _read_file(self, path):
        """Executor helper to read file."""
        with open(path, "r") as src_file:
            return src_file.read()

    @property
    def state(self):
        """Return the state of the component."""
        return self._page

    @property
    def available(self):
        """Return if entity is available."""
        return self._available

    async def async_will_remove_from_hass(self):
        """Run before entity is removed."""
        _LOGGER.debug("Remove plate %s", self._entry.data[CONF_NAME])

        for obj in self._objects:
            await obj.disable_object()

        for subscription in self._subscriptions:
            subscription()

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        schema_file_contents = await self.hass.async_add_executor_job(
            self._read_file, pathlib.Path(__file__).parent.joinpath("pages_schema.json")
        )
        self.json_schema = json.loads(schema_file_contents)

        state = await self.async_get_last_state()
        if state and state.state not in [STATE_UNAVAILABLE, STATE_UNKNOWN, None]:
            self._page = int(state.state)

        @callback
        async def page_update_received(msg):
            """Process page state."""
            try:
                self._page = HASP_PAGE_SCHEMA(msg.payload)
                _LOGGER.debug("Page changed to %s", self._page)
                self.async_write_ha_state()
            except vol.error.Invalid as err:
                _LOGGER.error("%s in %s", err, msg.payload)

        self._subscriptions.append(
            await async_subscribe(
                self.hass, f"{self._topic}/state/page", page_update_received
            )
        )

        @callback
        async def statusupdate_message_received(msg):
            """Process statusupdate."""

            try:
                message = HASP_STATUSUPDATE_SCHEMA(json.loads(msg.payload))

                major, minor, patch = message["version"].split(".")[:3]
                if (major, minor) != (MAJOR, MINOR):
                    _LOGGER.warning(
                        "%s firmware mismatch %s <> %s",
                        self._entry.data[CONF_NAME],
                        (major, minor),
                        (MAJOR, MINOR),
                    )
                self._available = True
                self._statusupdate = message

                self._page = message[ATTR_PAGE]
                self.async_write_ha_state()

                # Update Plate device information
                device_registry = dr.async_get(self.hass)
                device_registry.async_get_or_create(
                    config_entry_id=self._entry.entry_id,
                    identifiers={(DOMAIN, self._entry.data[CONF_HWID])},
                    manufacturer=self._entry.data[DISCOVERED_MANUFACTURER],
                    model=self._entry.data[DISCOVERED_MODEL],
                    configuration_url=self._entry.data.get(DISCOVERED_URL),
                    sw_version=message["version"],
                    name=self._entry.data[CONF_NAME],
                )

            except vol.error.Invalid as err:
                _LOGGER.error("While processing status update: %s", err)

        self._subscriptions.append(
            await async_subscribe(
                self.hass,
                f"{self._topic}/state/statusupdate",
                statusupdate_message_received,
            )
        )
        await async_publish(
            self.hass, f"{self._topic}/command", "statusupdate", qos=0, retain=False
        )

        @callback
        async def idle_message_received(msg):
            """Process idle message."""
            try:
                self._statusupdate[ATTR_IDLE] = HASP_IDLE_SCHEMA(msg.payload)
                self.async_write_ha_state()
            except vol.error.Invalid as err:
                _LOGGER.error("While processing idle message: %s", err)

        self._subscriptions.append(
            await async_subscribe(
                self.hass, f"{self._topic}/state/idle", idle_message_received
            )
        )

        @callback
        async def lwt_message_received(msg):
            """Process LWT."""
            _LOGGER.debug("Received LWT = %s", msg.payload)
            try:
                message = HASP_LWT_SCHEMA(msg.payload)

                if message == HASP_ONLINE:
                    self._available = True
                    self.hass.bus.async_fire(
                        EVENT_HASP_PLATE_ONLINE,
                        {CONF_PLATE: self._entry.data[CONF_HWID]},
                    )
                    if self._pages_jsonl:
                        await self.async_load_page(self._pages_jsonl)
                    else:
                        await self.refresh()

                    for obj in self._objects:
                        await obj.enable_object()
                else:
                    self._available = False
                    self.hass.bus.async_fire(
                        EVENT_HASP_PLATE_OFFLINE,
                        {CONF_PLATE: self._entry.data[CONF_HWID]},
                    )
                    for obj in self._objects:
                        await obj.disable_object()

                self.async_write_ha_state()

            except vol.error.Invalid as err:
                _LOGGER.error("While processing LWT: %s", err)

        self._subscriptions.append(
            await async_subscribe(self.hass, f"{self._topic}/LWT", lwt_message_received)
        )

    @property
    def state_attributes(self):
        """Return the state attributes."""
        attributes = {}

        if self._statusupdate:
            attributes = {**attributes, **self._statusupdate}

        if ATTR_PAGE in attributes:
            del attributes[
                ATTR_PAGE
            ]  # Page is tracked in the state, don't confuse users

        return attributes

    async def async_wakeup(self):
        """Wake up the display."""
        cmd_topic = f"{self._topic}/command"
        _LOGGER.warning("Wakeup will be deprecated in 0.8.0")  # remove in version 0.8.0
        await async_publish(self.hass, cmd_topic, "wakeup", qos=0, retain=False)

    async def async_change_page_next(self):
        """Change page to next one."""
        cmd_topic = f"{self._topic}/command/page"
        _LOGGER.warning(
            "page next service will be deprecated in 0.8.0"
        )  # remove in version 0.8.0

        await async_publish(self.hass, cmd_topic, "page next", qos=0, retain=False)

    async def async_change_page_prev(self):
        """Change page to previous one."""
        cmd_topic = f"{self._topic}/command/page"
        _LOGGER.warning(
            "page prev service will be deprecated in 0.8.0"
        )  # remove in version 0.8.0

        await async_publish(self.hass, cmd_topic, "page prev", qos=0, retain=False)

    async def async_clearpage(self, page="all"):
        """Clear page."""
        cmd_topic = f"{self._topic}/command"

        await async_publish(
            self.hass, cmd_topic, f"clearpage {page}", qos=0, retain=False
        )

        if page == "all":
            await async_publish(self.hass, cmd_topic, "page 1", qos=0, retain=False)

    async def async_change_page(self, page):
        """Change page to number."""
        cmd_topic = f"{self._topic}/command/page"

        if self._statusupdate:
            num_pages = self._statusupdate[HASP_NUM_PAGES]

            if (
                isinstance(page, int)
                and isinstance(num_pages, int)
                and (page <= 0 or page > num_pages)
            ):
                _LOGGER.error(
                    "Can't change to %s, available pages are 1 to %s", page, num_pages
                )
                return

        self._page = page

        _LOGGER.debug("Change page %s", self._page)
        await async_publish(self.hass, cmd_topic, self._page, qos=0, retain=False)
        self.async_write_ha_state()

    async def async_command_service(self, keyword, parameters):
        """Send commands directly to the plate entity."""
        await async_publish(
            self.hass,
            f"{self._topic}/command",
            f"{keyword} {parameters}".strip(),
            qos=0,
            retain=False,
        )

    async def async_config_service(self, submodule, parameters):
        """Send configuration commands to plate entity."""
        await async_publish(
            self.hass,
            f"{self._topic}/config/{submodule}",
            f"{parameters}".strip(),
            qos=0,
            retain=False,
        )

    async def async_push_image(
        self, image, obj, http_proxy=None, width=None, height=None, fitscreen=False
    ):
        """Update object image."""

        image_id = hashlib.md5(
            image.encode("utf-8") + self._entry.data[CONF_NAME].encode("utf-8")
        ).hexdigest()

        rgb_image = await self.hass.async_add_executor_job(
            image_to_rgb565, image, (width, height), fitscreen
        )

        self.hass.data[DOMAIN][DATA_IMAGES][image_id] = rgb_image

        cmd_topic = f"{self._topic}/command/{obj}.src"

        if http_proxy:
            rgb_image_url = f"{http_proxy}/api/openhasp/serve/{image_id}"
        else:
            rgb_image_url = f"{get_url(self.hass, allow_external=False)}/api/openhasp/serve/{image_id}"
        # self._entry.data
        _LOGGER.debug("Push %s with %s", cmd_topic, rgb_image_url)

        await async_publish(self.hass, cmd_topic, rgb_image_url, qos=0, retain=False)

    async def refresh(self):
        """Refresh objects in the SwitchPlate."""

        _LOGGER.info("Refreshing %s", self._entry.data[CONF_NAME])
        for obj in self._objects:
            await obj.refresh()

        await self.async_change_page(self._page)

    async def async_load_page(self, path):
        """Load pages file on the SwitchPlate, existing pages will not be cleared."""
        cmd_topic = f"{self._topic}/command"
        _LOGGER.info("Load page %s to %s", path, cmd_topic)

        if not self.hass.config.is_allowed_path(path):
            _LOGGER.error("'%s' is not an allowed directory", path)
            return

        async def send_lines(lines):
            mqtt_payload_buffer = ""
            for line in lines:
                if len(mqtt_payload_buffer) + len(line) > 1000:
                    await async_publish(
                        self.hass,
                        f"{cmd_topic}/jsonl",
                        mqtt_payload_buffer,
                        qos=0,
                        retain=False,
                    )
                    mqtt_payload_buffer = line
                else:
                    mqtt_payload_buffer = mqtt_payload_buffer + line
            await async_publish(
                self.hass,
                f"{cmd_topic}/jsonl",
                mqtt_payload_buffer,
                qos=0,
                retain=False,
            )

        try:
            pages_file = await self.hass.async_add_executor_job(self._read_file, path)
            if path.endswith(".json"):
                json_data = json.loads(pages_file)
                jsonschema.validate(instance=json_data, schema=self.json_schema)
                lines = []
                for item in json_data:
                    if isinstance(item, dict):
                        lines.append(json.dumps(item) + "\n")
                await send_lines(lines)
            else:
                await send_lines(pages_file.splitlines(keepends=True))
            await self.refresh()

        except (IndexError, FileNotFoundError, IsADirectoryError, UnboundLocalError):
            _LOGGER.error(
                "File or data not present at the moment: %s",
                os.path.basename(path),
            )

        except json.JSONDecodeError:
            _LOGGER.error(
                "Error decoding .json file: %s",
                os.path.basename(path),
            )

        except jsonschema.ValidationError as e:
            _LOGGER.error(
                "Schema check failed for %s. Validation Error: %s",
                os.path.basename(path),
                e.message,
            )


def _resample_history(timed_values, start_time, end_time, n_points):
    """Resample (datetime, float) pairs to n evenly-spaced time-bucket values.

    Uses last-known-value carry-forward, which is correct for sensor data.
    """
    if not timed_values or n_points <= 0:
        return []

    total_seconds = (end_time - start_time).total_seconds()
    bucket_size = total_seconds / n_points

    result = []
    last_value = timed_values[0][1]
    state_idx = 0

    for i in range(n_points):
        bucket_end = start_time + timedelta(seconds=(i + 1) * bucket_size)
        while state_idx < len(timed_values) and timed_values[state_idx][0] <= bucket_end:
            last_value = timed_values[state_idx][1]
            state_idx += 1
        result.append(last_value)

    return result


class HASPChart:
    """Representation of an openHASP chart object fed by HA entity history."""

    def __init__(self, hass, plate_topic, config):
        """Initialize chart object."""
        self.hass = hass
        self.obj_id = config[CONF_OBJID]
        subtopic = config.get(CONF_SUBTOPIC)
        prefix = f"{plate_topic}/command/{subtopic}/{self.obj_id}." if subtopic else f"{plate_topic}/command/{self.obj_id}."
        self.command_topic = prefix
        chart_cfg = config[CONF_CHART]
        self._series = chart_cfg[CONF_CHART_SERIES]
        self._scale = chart_cfg.get(CONF_CHART_SCALE, 1.0)
        self._chart_min = chart_cfg.get(CONF_CHART_MIN)
        self._chart_max = chart_cfg.get(CONF_CHART_MAX)
        self._history_hours = chart_cfg.get(CONF_CHART_HISTORY_HOURS, 1)
        self._interval_minutes = chart_cfg.get(CONF_CHART_INTERVAL)
        self._point_count = chart_cfg.get(CONF_CHART_POINT_COUNT)
        self._visual = {
            k: chart_cfg[k]
            for k in (
                CONF_CHART_TYPE,
                CONF_CHART_GRID_COLOR,
                CONF_CHART_HDIV,
                CONF_CHART_VDIV,
                CONF_CHART_LINE_WIDTH,
                CONF_CHART_POINT_SIZE,
                CONF_CHART_Y_LABEL,
                CONF_CHART_SHOW_SCALE,
            )
            if k in chart_cfg
        }
        self._subscriptions = []

    async def enable_object(self):
        """Configure chart visuals, send history bootstrap, subscribe to live updates."""
        await self._send_visual_config()
        await self._send_history()

        if self._interval_minutes is not None:
            self._subscriptions.append(
                async_track_time_interval(
                    self.hass,
                    self._interval_append,
                    timedelta(minutes=self._interval_minutes),
                )
            )
        else:
            for idx, series in enumerate(self._series):
                entity_id = series[CONF_CHART_ENTITY]

                def _make_listener(ser_idx):
                    @callback
                    async def _state_changed(event):
                        new_state = event.data.get("new_state")
                        if new_state is None or new_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                            return
                        try:
                            value = round(float(new_state.state), 2)
                        except (ValueError, TypeError):
                            return
                        append_payload = {"ser": ser_idx, "t": int(dt_util.utcnow().timestamp()), "value": value}
                        if self._scale != 1.0:
                            append_payload["scale"] = self._scale
                        _LOGGER.debug("Chart %s append ser=%d val=%s", self.obj_id, ser_idx, value)
                        await async_publish(self.hass, self.command_topic + "append", json.dumps(append_payload), qos=0, retain=False)
                    return _state_changed

                self._subscriptions.append(
                    async_track_state_change_event(self.hass, [entity_id], _make_listener(idx))
                )

    async def disable_object(self):
        """Remove state subscriptions."""
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions = []

    @callback
    async def _interval_append(self, _now):
        """Sample all series at each interval tick and append to the chart."""
        for ser_idx, series in enumerate(self._series):
            entity_id = series[CONF_CHART_ENTITY]
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            try:
                value = round(float(state.state), 2)
            except (ValueError, TypeError):
                continue
            append_payload = {"ser": ser_idx, "t": int(dt_util.utcnow().timestamp()), "value": value}
            if self._scale != 1.0:
                append_payload["scale"] = self._scale
            _LOGGER.debug("Chart %s interval append ser=%d val=%s", self.obj_id, ser_idx, value)
            await async_publish(
                self.hass, self.command_topic + "append", json.dumps(append_payload), qos=0, retain=False
            )

    async def refresh(self):
        """Re-send visual config and full history when plate reconnects."""
        await self._send_visual_config()
        await self._send_history()

    async def _send_visual_config(self):
        """Send series colours (+ optional type) and all visual attributes to the device."""
        colors = [s.get(CONF_CHART_COLOR, "#ffffff") for s in self._series]
        if CONF_CHART_TYPE in self._visual:
            series_payload = json.dumps({"type": self._visual[CONF_CHART_TYPE], "colors": colors})
        else:
            series_payload = json.dumps(colors)
        await async_publish(
            self.hass, self.command_topic + "series", series_payload, qos=0, retain=False
        )

        attr_map = {
            CONF_CHART_GRID_COLOR:  "grid_color",
            CONF_CHART_HDIV:        "hdiv",
            CONF_CHART_VDIV:        "vdiv",
            CONF_CHART_LINE_WIDTH:  "line_width",
            CONF_CHART_POINT_SIZE:  "point_size",
            CONF_CHART_Y_LABEL:     "y_label",
        }
        for conf_key, mqtt_attr in attr_map.items():
            if conf_key in self._visual:
                val = self._visual[conf_key]
                payload = json.dumps(val) if not isinstance(val, str) else val
                await async_publish(
                    self.hass, self.command_topic + mqtt_attr, payload, qos=0, retain=False
                )

        if self._visual.get(CONF_CHART_SHOW_SCALE):
            await async_publish(
                self.hass, self.command_topic + "y_scale", "1", qos=0, retain=False
            )

    async def _send_history(self):
        """Fetch recorder history and push full dataset to each series."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
        except ImportError:
            _LOGGER.warning("Recorder unavailable; chart %s will show current state only", self.obj_id)
            await self._send_current_state()
            return

        now = dt_util.utcnow()

        common_start = now - timedelta(hours=self._history_hours)

        # Collect (timestamp, value) pairs per series.
        raw_series = []
        for series in self._series:
            entity_id = series[CONF_CHART_ENTITY]

            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    get_significant_states,
                    self.hass,
                    common_start,
                    None,
                    [entity_id],
                )
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error("Error fetching history for %s: %s", entity_id, err)
                raw_series.append([])
                continue

            timed_values = []
            for state in states.get(entity_id, []):
                try:
                    timed_values.append((state.last_changed, round(float(state.state), 2)))
                except (ValueError, TypeError):
                    pass
            raw_series.append(timed_values)

        # Determine target point count — interval takes priority, then point_count, then min raw.
        if self._interval_minutes is not None:
            n_points = math.ceil(self._history_hours * 60 / self._interval_minutes)
        elif self._point_count is not None:
            n_points = self._point_count
        else:
            lengths = [len(s) for s in raw_series if s]
            n_points = min(lengths) if lengths else 0

        if n_points == 0:
            return

        for idx, (series, timed_values) in enumerate(zip(self._series, raw_series)):
            if not timed_values:
                _LOGGER.debug("No history for series %d", idx)
                continue

            values = _resample_history(timed_values, common_start, now, n_points)

            t_step_seconds = int(self._interval_minutes * 60) if self._interval_minutes else 0
            # Each resampled point represents the END of its bucket, so values[0] is at
            # common_start + t_step and values[-1] is at now.  Send t_start = common_start + t_step
            # so that firmware label[i] = t_start + i*t_step, and the last label equals now.
            t_start_ts = int(common_start.timestamp()) + t_step_seconds
            data_payload = {
                "ser": idx,
                "t_start": t_start_ts,
                "t_step": t_step_seconds,
                "data": values,
            }
            if self._scale != 1.0:
                data_payload["scale"] = self._scale
            if self._chart_min is not None:
                data_payload["min"] = self._chart_min
            if self._chart_max is not None:
                data_payload["max"] = self._chart_max

            payload = json.dumps(data_payload)
            _LOGGER.debug("Chart %s series %d: bootstrapping %d points", self.obj_id, idx, len(values))
            await async_publish(self.hass, self.command_topic + "data", payload, qos=0, retain=False)

    async def _send_current_state(self):
        """Fallback: push only the current state as a single-point dataset."""
        for idx, series in enumerate(self._series):
            entity_id = series[CONF_CHART_ENTITY]
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                continue
            try:
                value = round(float(state.state), 2)
            except (ValueError, TypeError):
                continue
            data_payload = {"ser": idx, "data": [value]}
            if self._scale != 1.0:
                data_payload["scale"] = self._scale
            if self._chart_min is not None:
                data_payload["min"] = self._chart_min
            if self._chart_max is not None:
                data_payload["max"] = self._chart_max
            await async_publish(self.hass, self.command_topic + "data", json.dumps(data_payload), qos=0, retain=False)


# pylint: disable=R0902
class HASPObject:
    """Representation of an HASP-LVGL object."""

    def __init__(self, hass, plate_topic, config):
        """Initialize an object."""

        self.hass = hass
        self.obj_id = config[CONF_OBJID]
        subtopic = config.get("subtopic")
        if subtopic:
            self.command_topic = f"{plate_topic}/command/{subtopic}/{self.obj_id}."
        else:
            self.command_topic = f"{plate_topic}/command/{self.obj_id}."
        self.state_topic = f"{plate_topic}/state/{self.obj_id}"
        self.cached_properties = {}

        self.properties = config.get(CONF_PROPERTIES)
        self.event_services = {
            event: Script(hass, script, plate_topic, DOMAIN)
            for (event, script) in config[CONF_EVENT].items()
        }
        self._tracked_property_templates = []
        self._freeze_properties = []
        self._subscriptions = []

    async def enable_object(self):
        """Initialize object events and properties subscriptions."""

        if self.event_services:
            _LOGGER.debug("Setup event_services for '%s'", self.obj_id)
            self._subscriptions.append(await self.async_listen_hasp_events())

        for _property, template in self.properties.items():
            self._tracked_property_templates.append(
                await self.async_set_property(_property, template)
            )

    async def disable_object(self):
        """Remove subscriptions and event tracking."""
        _LOGGER.debug("Disabling HASPObject %s", self.obj_id)
        for subscription in self._subscriptions:
            subscription()
        self._subscriptions = []

        for tracked_template in self._tracked_property_templates:
            tracked_template.async_remove()
        self._tracked_property_templates = []

    async def async_set_property(self, _property, template):
        """Set HASP Object property to template value."""

        @callback
        async def _async_template_result_changed(event, updates):
            track_template_result = updates.pop()
            template = track_template_result.template
            result = track_template_result.result

            if isinstance(result, TemplateError) or result is None:
                entity = event and event.data.get("entity_id")
                _LOGGER.error(
                    "TemplateError('%s') "
                    "while processing template '%s' "
                    "in entity '%s'",
                    result,
                    template,
                    entity,
                )
                return

            self.cached_properties[_property] = result
            if _property in self._freeze_properties:
                # Skip update to plate to avoid feedback loops
                return

            _LOGGER.debug(
                "%s.%s - %s changed, updating with: %s",
                self.obj_id,
                _property,
                template,
                result,
            )

            await async_publish(self.hass, self.command_topic + _property, result)

        property_template = async_track_template_result(
            self.hass,
            [TrackTemplate(template, None)],
            _async_template_result_changed,
        )
        property_template.async_refresh()

        return property_template

    async def refresh(self):
        """Refresh based on cached values."""
        for _property, result in self.cached_properties.items():
            _LOGGER.debug("Refresh object %s.%s = %s", self.obj_id, _property, result)
            await async_publish(self.hass, self.command_topic + _property, result)

    async def async_listen_hasp_events(self):
        """Listen to messages on MQTT for HASP events."""

        @callback
        async def message_received(msg):
            """Process object state MQTT message."""
            try:
                message = HASP_EVENT_SCHEMA(json.loads(msg.payload))

                if message[HASP_EVENT] == HASP_EVENT_DOWN:
                    # store properties that shouldn't be updated while button pressed
                    self._freeze_properties = message.keys()
                elif message[HASP_EVENT] in [HASP_EVENT_UP, HASP_EVENT_RELEASE]:
                    self._freeze_properties = []

                for event, script in self.event_services.items():
                    if event in message[HASP_EVENT]:
                        _LOGGER.debug(
                            "Service call for '%s' triggered by '%s' on '%s' with variables %s",
                            event,
                            msg.payload,
                            msg.topic,
                            message,
                        )
                        await script.async_run(
                            run_variables=message,
                            context=Context(),
                        )
            except vol.error.Invalid:
                _LOGGER.debug(
                    "Could not handle openHASP event: '%s' on '%s'",
                    msg.payload,
                    msg.topic,
                )
            except json.decoder.JSONDecodeError as err:
                _LOGGER.error(
                    "Error decoding received JSON message: %s on %s", err.doc, msg.topic
                )

        _LOGGER.debug("Subscribe to '%s' events on '%s'", self.obj_id, self.state_topic)
        return await async_subscribe(self.hass, self.state_topic, message_received)
