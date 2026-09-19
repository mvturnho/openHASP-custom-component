"""Tests for openHASP object event handling."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.openhasp import HASPObject, HASP_EVENT_SCHEMA, OBJECT_SCHEMA
from custom_components.openhasp.const import HASP_EVENT_CLICK, HASP_EVENTS


PLATE_TOPIC = "hasp/plate"


@pytest.fixture
def mqtt_subscription():
    """Capture the MQTT callback registered by a HASP object."""
    with patch(
        "custom_components.openhasp.async_subscribe",
        new_callable=AsyncMock,
    ) as subscribe:
        def unsubscribe():
            """Unsubscribe from the mocked MQTT topic."""

        subscribe.return_value = unsubscribe
        yield subscribe


async def make_event_object(hass, mqtt_subscription, events):
    """Create an enabled HASP object with event scripts mocked out."""
    scripts = {event: AsyncMock() for event in events}

    def script_factory(_hass, _sequence, _name, _domain):
        return scripts[script_factory.events.pop(0)]

    script_factory.events = list(events)
    config = OBJECT_SCHEMA(
        {
            "obj": "p1b1",
            "event": {event: [{"service": "test.event"}] for event in events},
        }
    )
    with patch("custom_components.openhasp.Script", side_effect=script_factory):
        obj = HASPObject(hass, PLATE_TOPIC, config)
        await obj.enable_object()

    assert mqtt_subscription.await_count == 1
    callback = mqtt_subscription.await_args.args[2]
    return scripts, callback


def test_click_is_a_supported_openhasp_event():
    """The firmware's exact click event name is accepted."""
    assert HASP_EVENT_CLICK == "click"
    assert HASP_EVENT_CLICK in HASP_EVENTS
    assert HASP_EVENT_SCHEMA({"event": HASP_EVENT_CLICK}) == {
        "event": HASP_EVENT_CLICK
    }


async def test_click_reaches_object_script_with_payload_intact(
    hass, mqtt_subscription
):
    """A received click follows the normal object-event script path."""
    scripts, callback = await make_event_object(hass, mqtt_subscription, ["click"])
    payload = {"event": "click", "val": 1, "extra": {"source": "firmware"}}

    await callback(
        SimpleNamespace(
            payload=json.dumps(payload), topic=f"{PLATE_TOPIC}/state/p1b1"
        )
    )

    scripts["click"].async_run.assert_awaited_once()
    assert scripts["click"].async_run.await_args.kwargs["run_variables"] == payload


async def test_down_up_do_not_synthesize_click(hass, mqtt_subscription):
    """The component does not infer click from separate down and up messages."""
    scripts, callback = await make_event_object(
        hass, mqtt_subscription, ["down", "up", "click"]
    )

    for event in ("down", "up"):
        await callback(
            SimpleNamespace(
                payload=json.dumps({"event": event}),
                topic=f"{PLATE_TOPIC}/state/p1b1",
            )
        )

    scripts["down"].async_run.assert_awaited_once()
    scripts["up"].async_run.assert_awaited_once()
    scripts["click"].async_run.assert_not_awaited()
