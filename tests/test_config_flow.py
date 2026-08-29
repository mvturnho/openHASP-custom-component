"""Config flow tests for openHASP discovery identity."""
import json
from types import SimpleNamespace

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.openhasp.const import (
    CONF_HWID,
    CONF_IDLE_BRIGHTNESS,
    CONF_TOPIC,
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
)


def zeroconf_info(
    *,
    hwid="AA:BB:CC:DD:EE:01",
    node="squareplate",
    node_t="hasp/squareplate/",
    url="http://192.0.2.10",
):
    """Return zeroconf discovery info matching openHASP firmware properties."""
    return SimpleNamespace(
        properties={
            DISCOVERED_HWID: hwid,
            DISCOVERED_NODE: node,
            DISCOVERED_NODE_T: node_t,
            DISCOVERED_VERSION: "0.7.10",
            DISCOVERED_MANUFACTURER: "openHASP",
            DISCOVERED_MODEL: "plate",
            DISCOVERED_URL: url,
            DISCOVERED_PAGES: json.dumps(3),
            DISCOVERED_POWER: json.dumps([]),
            DISCOVERED_LIGHT: json.dumps([]),
            DISCOVERED_DIM: json.dumps([]),
            DISCOVERED_INPUT: json.dumps([]),
        }
    )


async def start_zeroconf_flow(hass, info):
    """Start an openHASP zeroconf flow."""
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_ZEROCONF},
        data=info,
    )


async def finish_personalize_flow(hass, flow_id, *, name="squareplate", topic="hasp/squareplate"):
    """Complete the personalization form."""
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_NAME: name,
            CONF_TOPIC: topic,
            CONF_IDLE_BRIGHTNESS: DEFAULT_IDLE_BRIGHNESS,
        },
    )


async def test_first_discovery_sets_canonical_unique_id(
    hass, enable_openhasp_integration
):
    """A first discovery creates one flow keyed by canonical hardware id."""
    result = await start_zeroconf_flow(hass, zeroconf_info())

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "personalize"

    created = await finish_personalize_flow(hass, result["flow_id"])

    assert created["type"] == FlowResultType.CREATE_ENTRY
    assert created["data"][CONF_HWID] == "aa:bb:cc:dd:ee:01"
    assert created["data"][CONF_TOPIC] == "hasp/squareplate"


async def test_repeated_discovery_has_one_flow(hass, enable_openhasp_integration):
    """Repeated discovery events for the same hardware id share one flow."""
    results = [
        await start_zeroconf_flow(hass, zeroconf_info(url=f"http://192.0.2.{idx}"))
        for idx in range(10, 15)
    ]

    assert results[0]["type"] == FlowResultType.FORM
    assert [result["type"] for result in results[1:]] == [
        FlowResultType.ABORT,
        FlowResultType.ABORT,
        FlowResultType.ABORT,
        FlowResultType.ABORT,
    ]
    assert {result["reason"] for result in results[1:]} == {"already_in_progress"}


async def test_discovery_aborts_when_already_configured(
    hass, enable_openhasp_integration
):
    """A configured plate is not rediscovered as a new device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="squareplate",
        unique_id="aa:bb:cc:dd:ee:01",
        data={
            CONF_HWID: "aa:bb:cc:dd:ee:01",
            CONF_NAME: "squareplate",
            CONF_TOPIC: "hasp/squareplate",
            CONF_IDLE_BRIGHTNESS: DEFAULT_IDLE_BRIGHNESS,
        },
    )
    entry.add_to_hass(hass)

    result = await start_zeroconf_flow(hass, zeroconf_info())

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_updates_changed_connection_metadata(
    hass, enable_openhasp_integration
):
    """A changed discovery URL/topic updates metadata without duplicating entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="squareplate",
        unique_id="aa:bb:cc:dd:ee:01",
        data={
            CONF_HWID: "aa:bb:cc:dd:ee:01",
            CONF_NAME: "squareplate",
            CONF_TOPIC: "hasp/squareplate",
            DISCOVERED_URL: "http://192.0.2.10",
            CONF_IDLE_BRIGHTNESS: DEFAULT_IDLE_BRIGHNESS,
        },
    )
    entry.add_to_hass(hass)

    result = await start_zeroconf_flow(
        hass,
        zeroconf_info(
            node_t="hasp/squareplate-renamed/",
            url="http://192.0.2.99",
        ),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_TOPIC] == "hasp/squareplate-renamed"
    assert entry.data[DISCOVERED_URL] == "http://192.0.2.99"


async def test_same_hostname_different_plate_gets_separate_flow(
    hass, enable_openhasp_integration
):
    """Name/hostname collisions do not deduplicate different hardware ids."""
    first = await start_zeroconf_flow(
        hass,
        zeroconf_info(hwid="AA:BB:CC:DD:EE:01", node="squareplate"),
    )
    second = await start_zeroconf_flow(
        hass,
        zeroconf_info(hwid="AA:BB:CC:DD:EE:02", node="squareplate"),
    )

    assert first["type"] == FlowResultType.FORM
    assert second["type"] == FlowResultType.FORM


async def test_manual_personalize_then_discovery_deduplicates(
    hass, enable_openhasp_integration
):
    """A personalized discovered plate and later zeroconf share identity."""
    result = await start_zeroconf_flow(hass, zeroconf_info())
    created = await finish_personalize_flow(hass, result["flow_id"])

    assert created["type"] == FlowResultType.CREATE_ENTRY

    rediscovered = await start_zeroconf_flow(hass, zeroconf_info())

    assert rediscovered["type"] == FlowResultType.ABORT
    assert rediscovered["reason"] == "already_configured"
