"""Flow tests for the objects -> properties bindings, including ring segments."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.openhasp import OBJECT_SCHEMA, HASPObject

PLATE_TOPIC = "hasp/plate"


class Plate:
    """Small harness around HASPObject that records outbound plate commands."""

    def __init__(self, hass, publish, obj):
        """Initialize the harness."""
        self.hass = hass
        self._publish = publish
        self.obj = obj

    @property
    def sent(self):
        """Return the (topic, payload) tuples published so far."""
        return [(call.args[1], call.args[2]) for call in self._publish.await_args_list]

    def clear(self):
        """Forget everything published so far."""
        self._publish.reset_mock()

    async def settle(self):
        """Let Home Assistant process pending state changes."""
        await self.hass.async_block_till_done()


@pytest.fixture
def publish():
    """Patch the MQTT publish helper used by the integration."""
    with patch(
        "custom_components.openhasp.async_publish", new_callable=AsyncMock
    ) as mock_publish:
        yield mock_publish


async def make_object(hass, publish, obj_id, properties):
    """Create and enable a HASPObject from YAML-shaped configuration."""
    config = OBJECT_SCHEMA({"obj": obj_id, "properties": properties})
    obj = HASPObject(hass, PLATE_TOPIC, config)
    plate = Plate(hass, publish, obj)
    await obj.enable_object()
    await plate.settle()
    return plate


# --- connection regression --------------------------------------------------


@pytest.mark.parametrize("state,expected", [("-450", -450), ("812", 812), ("0", 0)])
async def test_connection_val_unchanged(hass, publish, state, expected):
    """A connection val binding keeps using the plain property pipeline."""
    hass.states.async_set("sensor.net_power", state)
    plate = await make_object(
        hass, publish, "p1c6", {"val": '{{ states("sensor.net_power") }}'}
    )

    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c6.val", expected)]


async def test_badge_val_unchanged(hass, publish):
    """Ordinary properties on a badge object are untouched."""
    hass.states.async_set("sensor.foo", "42")
    plate = await make_object(
        hass,
        publish,
        "p1b2",
        {
            "val": '{{ states("sensor.foo") }}',
            "x": '{{ states("sensor.foo") }}',
            "text": '{{ states("sensor.foo") }}',
        },
    )

    assert sorted(plate.sent) == sorted(
        [
            (f"{PLATE_TOPIC}/command/p1b2.val", 42),
            (f"{PLATE_TOPIC}/command/p1b2.x", 42),
            (f"{PLATE_TOPIC}/command/p1b2.text", 42),
        ]
    )


# --- ring segment wire format -----------------------------------------------


async def test_ring_segment_value_payload(hass, publish):
    """A segment value binding produces a partial ring_segment command."""
    hass.states.async_set("sensor.solar_power", "812")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )

    (topic, payload) = plate.sent[0]
    assert topic == f"{PLATE_TOPIC}/command/p1b3.ring_segment"
    assert json.loads(payload) == {"id": "solar", "value": 812}


async def test_ring_segment_color_payload(hass, publish):
    """Colour strings reach the plate verbatim."""
    hass.states.async_set("sensor.solar_color", "#ff9800ff")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.color": '{{ states("sensor.solar_color") }}'},
    )

    (topic, payload) = plate.sent[0]
    assert topic == f"{PLATE_TOPIC}/command/p1b3.ring_segment"
    assert json.loads(payload) == {"id": "solar", "color": "#ff9800ff"}


@pytest.mark.parametrize("state,expected", [("on", True), ("off", False)])
async def test_ring_segment_enabled_payload(hass, publish, state, expected):
    """Enabled is serialized as a real JSON boolean."""
    hass.states.async_set("input_boolean.solar_ring", state)
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.enabled": (
                '{{ is_state("input_boolean.solar_ring", "on") }}'
            )
        },
    )

    (_topic, payload) = plate.sent[0]
    assert payload in ('{"id": "solar", "enabled": true}', '{"id": "solar", "enabled": false}')
    assert json.loads(payload) == {"id": "solar", "enabled": expected}


async def test_never_sends_a_ring_segments_array(hass, publish):
    """The plugin must never reconstruct the full ring_segments array."""
    hass.states.async_set("sensor.solar_power", "10")
    hass.states.async_set("sensor.grid_power", "20")
    hass.states.async_set("sensor.battery_power", "30")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
            "ring_segment.grid.value": '{{ states("sensor.grid_power") }}',
            "ring_segment.battery.value": '{{ states("sensor.battery_power") }}',
        },
    )

    for topic, payload in plate.sent:
        assert not topic.endswith(".ring_segments")
        assert "ring_segments" not in str(payload)
        assert topic == f"{PLATE_TOPIC}/command/p1b3.ring_segment"
        assert isinstance(json.loads(payload), dict)

    hass.states.async_set("sensor.grid_power", "21")
    plate.clear()
    await plate.settle()
    assert len(plate.sent) == 1
    assert "ring_segments" not in str(plate.sent[0][1])


# --- unavailable handling ---------------------------------------------------


@pytest.mark.parametrize("state", ["unknown", "unavailable", "", "abc"])
async def test_unusable_value_is_not_sent(hass, publish, state):
    """unknown/unavailable/garbage never reach the plate and never become 0."""
    hass.states.async_set("sensor.solar_power", state)
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )

    assert plate.sent == []


async def test_recovers_after_unavailable(hass, publish):
    """A usable value after an unusable one is sent normally."""
    hass.states.async_set("sensor.solar_power", "unavailable")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )
    assert plate.sent == []

    hass.states.async_set("sensor.solar_power", "725")
    await plate.settle()
    assert json.loads(plate.sent[0][1]) == {"id": "solar", "value": 725}


# --- multiple segments / dedupe isolation -----------------------------------


async def test_only_changed_segment_is_sent(hass, publish):
    """Changing one entity updates exactly one segment."""
    hass.states.async_set("sensor.solar_power", "100")
    hass.states.async_set("sensor.grid_power", "200")
    hass.states.async_set("sensor.battery_power", "300")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
            "ring_segment.grid.value": '{{ states("sensor.grid_power") }}',
            "ring_segment.battery.value": '{{ states("sensor.battery_power") }}',
        },
    )
    assert len(plate.sent) == 3

    plate.clear()
    hass.states.async_set("sensor.grid_power", "201")
    await plate.settle()

    assert len(plate.sent) == 1
    assert json.loads(plate.sent[0][1]) == {"id": "grid", "value": 201}


async def test_dedupe_is_isolated_per_property_path(hass, publish):
    """Identical values on different segments are not suppressed by each other."""
    hass.states.async_set("sensor.solar_power", "100")
    hass.states.async_set("sensor.grid_power", "100")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
            "ring_segment.grid.value": '{{ states("sensor.grid_power") }}',
        },
    )

    payloads = [json.loads(payload) for _topic, payload in plate.sent]
    assert {"id": "solar", "value": 100} in payloads
    assert {"id": "grid", "value": 100} in payloads
    assert len(payloads) == 2

    plate.clear()
    hass.states.async_set("sensor.solar_power", "100")
    hass.states.async_set("sensor.grid_power", "101")
    await plate.settle()

    assert [json.loads(payload) for _topic, payload in plate.sent] == [
        {"id": "grid", "value": 101}
    ]


async def test_multiple_fields_on_the_same_segment(hass, publish):
    """Each field of one segment is an independent binding, patched separately."""
    hass.states.async_set("sensor.solar_power", "10")
    hass.states.async_set("sensor.solar_color", "#ff9800ff")
    hass.states.async_set("input_boolean.solar_enabled", "on")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
            "ring_segment.solar.color": '{{ states("sensor.solar_color") }}',
            "ring_segment.solar.enabled": (
                '{{ is_state("input_boolean.solar_enabled", "on") }}'
            ),
        },
    )
    assert len(plate.sent) == 3

    plate.clear()
    hass.states.async_set("sensor.solar_power", "11")
    await plate.settle()

    assert len(plate.sent) == 1
    assert json.loads(plate.sent[0][1]) == {"id": "solar", "value": 11}


# --- lifecycle --------------------------------------------------------------


async def test_startup_sync(hass, publish):
    """Enabling the object sends the current template value straight away."""
    hass.states.async_set("sensor.solar_power", "725")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )

    assert json.loads(plate.sent[0][1]) == {"id": "solar", "value": 725}


async def test_reconnect_resync_resends_all_segments(hass, publish):
    """refresh() re-sends every cached binding, dedupe does not block it."""
    hass.states.async_set("sensor.solar_power", "100")
    hass.states.async_set("sensor.grid_power", "200")
    hass.states.async_set("sensor.net_power", "-450")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {
            "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
            "ring_segment.grid.value": '{{ states("sensor.grid_power") }}',
            "val": '{{ states("sensor.net_power") }}',
        },
    )

    plate.clear()
    await plate.obj.refresh()

    sent = plate.sent
    assert (f"{PLATE_TOPIC}/command/p1b3.val", -450) in sent
    segment_payloads = [
        json.loads(payload)
        for topic, payload in sent
        if topic.endswith(".ring_segment")
    ]
    assert {"id": "solar", "value": 100} in segment_payloads
    assert {"id": "grid", "value": 200} in segment_payloads
    assert len(sent) == 3


async def test_disable_object_stops_updates(hass, publish):
    """Disabling removes the template subscriptions."""
    hass.states.async_set("sensor.solar_power", "100")
    plate = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )

    await plate.obj.disable_object()
    plate.clear()
    hass.states.async_set("sensor.solar_power", "101")
    await plate.settle()

    assert plate.sent == []


# --- connection object references (p<page>c<id>) ----------------------------


async def test_connection_publish_topic(hass, publish):
    """A connection binding publishes to the canonical p1c7 topic."""
    hass.states.async_set("sensor.grid_power", "-450")
    plate = await make_object(
        hass, publish, "p1c7", {"val": '{{ states("sensor.grid_power") }}'}
    )

    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", -450)]
    assert all("p1b7" not in topic for topic, _payload in plate.sent)


async def test_connection_startup_sync(hass, publish):
    """The initial template refresh reaches the connection object."""
    hass.states.async_set("sensor.grid_power", "725")
    plate = await make_object(
        hass, publish, "p1c7", {"val": '{{ states("sensor.grid_power") }}'}
    )

    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", 725)]


async def test_connection_reconnect_resync(hass, publish):
    """After a plate reconnect the cached connection value is resent."""
    hass.states.async_set("sensor.grid_power", "725")
    plate = await make_object(
        hass, publish, "p1c7", {"val": '{{ states("sensor.grid_power") }}'}
    )
    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", 725)]

    plate.clear()
    await plate.obj.refresh()

    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", 725)]


async def test_connection_updates_on_state_change(hass, publish):
    """Connection values keep following their entity."""
    hass.states.async_set("sensor.grid_power", "-450")
    plate = await make_object(
        hass, publish, "p1c7", {"val": '{{ states("sensor.grid_power") }}'}
    )

    plate.clear()
    hass.states.async_set("sensor.grid_power", "1200")
    await plate.settle()

    assert plate.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", 1200)]


async def test_badge_segments_and_connection_side_by_side(hass, publish):
    """A ring-segment badge and a connection object work independently."""
    hass.states.async_set("sensor.solar_power", "812")
    hass.states.async_set("sensor.grid_power", "-450")
    badge = await make_object(
        hass,
        publish,
        "p1b3",
        {"ring_segment.solar.value": '{{ states("sensor.solar_power") }}'},
    )
    connection = await make_object(
        hass, publish, "p1c7", {"val": '{{ states("sensor.grid_power") }}'}
    )

    sent = connection.sent
    assert (f"{PLATE_TOPIC}/command/p1c7.val", -450) in sent
    segment = [
        (topic, payload)
        for topic, payload in sent
        if topic == f"{PLATE_TOPIC}/command/p1b3.ring_segment"
    ]
    assert len(segment) == 1
    assert json.loads(segment[0][1]) == {"id": "solar", "value": 812}

    connection.clear()
    hass.states.async_set("sensor.grid_power", "-460")
    await connection.settle()
    assert connection.sent == [(f"{PLATE_TOPIC}/command/p1c7.val", -460)]

    badge.clear()
    hass.states.async_set("sensor.solar_power", "813")
    await badge.settle()
    assert len(badge.sent) == 1
    assert badge.sent[0][0] == f"{PLATE_TOPIC}/command/p1b3.ring_segment"
    assert json.loads(badge.sent[0][1]) == {"id": "solar", "value": 813}
