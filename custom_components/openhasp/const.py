"""Constants for HASP Open Hardware Edition custom component."""

# Version
MAJOR = "0"
MINOR = "7"

DOMAIN = "openhasp"

CONF_COMPONENT = "component"
CONF_OBJID = "obj"
CONF_PROPERTIES = "properties"
CONF_EVENT = "event"
CONF_TRACK = "track"
CONF_TOPIC = "topic"
CONF_PAGES = "pages"
CONF_PAGES_PATH = "path"
CONF_OBJECTS = "objects"
CONF_VAL = "val"
CONF_IDLE_BRIGHTNESS = "idle_brightness"
CONF_AWAKE_BRIGHTNESS = "awake_brightness"
CONF_PLATE = "plate"
CONF_RELAYS = "relay"
CONF_LIGHTS = "light"
CONF_DIMLIGHTS = "dimlight"
CONF_LEDS = "led"
CONF_PWMS = "pwm"
CONF_GPIO = "gpio"
CONF_NODE = "node"
CONF_HWID = "hwid"
CONF_INPUT = "input"
CONF_SUBTOPIC = "subtopic"

# Chart object config keys
CONF_CHART = "chart"
CONF_CHART_SERIES = "series"
CONF_CHART_ENTITY = "entity"
CONF_CHART_COLOR = "color"
CONF_CHART_HISTORY_HOURS = "history_hours"
CONF_CHART_INTERVAL = "interval"
CONF_CHART_TYPE = "type"
CONF_CHART_MIN = "min"
CONF_CHART_MAX = "max"
CONF_CHART_POINT_COUNT = "point_count"
CONF_CHART_GRID_COLOR = "grid_color"
CONF_CHART_HDIV = "hdiv"
CONF_CHART_VDIV = "vdiv"
CONF_CHART_LINE_WIDTH = "line_width"
CONF_CHART_POINT_SIZE = "point_size"
CONF_CHART_SCALE = "scale"
CONF_CHART_Y_LABEL = "y_label"
CONF_CHART_SHOW_SCALE = "show_scale"


DATA_LISTENER = "listener"
DATA_IMAGES = "images"

DEFAULT_TOPIC = "hasp"
DEFAULT_PATH = "pages.jsonl"
DEFAULT_IDLE_BRIGHNESS = 25

DISCOVERED_NODE = "node"
DISCOVERED_NODE_T = "node_t"
DISCOVERED_MODEL = "mdl"
DISCOVERED_MANUFACTURER = "mf"
DISCOVERED_HWID = "hwid"
DISCOVERED_PAGES = "pages"
DISCOVERED_INPUT = "input"
DISCOVERED_POWER = "power"
DISCOVERED_LIGHT = "light"
DISCOVERED_DIM = "dim"
DISCOVERED_VERSION = "sw"
DISCOVERED_INPUT = "input"
DISCOVERED_URL = "uri"

HASP_NUM_PAGES = "numPages"
HASP_VAL = "val"
HASP_EVENT = "event"
HASP_EVENT_ON = "on"
HASP_EVENT_OFF = "off"
HASP_EVENT_DOWN = "down"
HASP_EVENT_UP = "up"
HASP_EVENT_SHORT = "short"
HASP_EVENT_LONG = "long"
HASP_EVENT_CHANGED = "changed"
HASP_EVENT_RELEASE = "release"
HASP_EVENT_HOLD = "hold"
HASP_EVENTS = (
    HASP_EVENT_ON,
    HASP_EVENT_OFF,
    HASP_EVENT_DOWN,
    HASP_EVENT_UP,
    HASP_EVENT_SHORT,
    HASP_EVENT_LONG,
    HASP_EVENT_CHANGED,
    HASP_EVENT_RELEASE,
    HASP_EVENT_HOLD,
)
HASP_IDLE_OFF = "off"
HASP_IDLE_SHORT = "short"
HASP_IDLE_LONG = "long"
HASP_IDLE_STATES = (HASP_IDLE_OFF, HASP_IDLE_SHORT, HASP_IDLE_LONG)
HASP_ONLINE = "online"
HASP_OFFLINE = "offline"
HASP_LWT = (HASP_ONLINE, HASP_OFFLINE)

ATTR_FORCE_FITSCREEN = "fit_screen"
ATTR_PAGE = "page"
ATTR_CURRENT_DIM = "dim"
ATTR_IDLE = "idle"
ATTR_PATH = "path"
ATTR_AWAKE_BRIGHTNESS = "awake brightness"
ATTR_IDLE_BRIGHTNESS = "idle brightness"
ATTR_COMMAND_KEYWORD = "keyword"
ATTR_COMMAND_PARAMETERS = "parameters"
ATTR_CONFIG_SUBMODULE = "submodule"
ATTR_CONFIG_PARAMETERS = "parameters"
ATTR_PROXY = "http_proxy"
ATTR_IMAGE = "image"
ATTR_OBJECT = "obj"
ATTR_WIDTH = "width"
ATTR_HEIGHT = "height"

SERVICE_WAKEUP = "wakeup"
SERVICE_CLEAR_PAGE = "clear_page"
SERVICE_LOAD_PAGE = "load_pages"
SERVICE_PAGE_CHANGE = "change_page"
SERVICE_PAGE_NEXT = "next_page"
SERVICE_PAGE_PREV = "prev_page"
SERVICE_COMMAND = "command"
SERVICE_CONFIG = "config"
SERVICE_PUSH_IMAGE = "push_image"

EVENT_HASP_PLATE_ONLINE = "openhasp_plate_online"
EVENT_HASP_PLATE_OFFLINE = "openhasp_plate_offline"
