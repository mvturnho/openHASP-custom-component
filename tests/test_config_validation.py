"""Configuration validation tests for objects -> properties."""
import pytest
import voluptuous as vol

from custom_components.openhasp import OBJECT_SCHEMA, PUSH_IMAGE_SCHEMA, hasp_object


# --- object reference grammar: p<page>[bc]<id> ------------------------------


@pytest.mark.parametrize(
    "obj_id",
    [
        # historical / generic object notation
        "p0b0",
        "p1b1",
        "p1b2",
        "p1b3",
        "p1b27",
        "p1b999",
        "p12b34",
        "p12b40",
        # connection notation
        "p0c0",
        "p1c1",
        "p1c6",
        "p1c7",
        "p12c34",
        "p12c345",
    ],
)
def test_valid_object_refs(obj_id):
    """Both b (object) and c (connection) references are accepted."""
    assert hasp_object(obj_id) == obj_id


@pytest.mark.parametrize(
    "obj_id",
    [
        "p1x7",
        "p1c",
        "pc7",
        "p1",
        "b3",
        "1b3",
        "badge",
        "p1cc7",
        "p1bc7",
        "p1c-7",
        "p-1c7",
        "p1c7.foo",
        "foo-p1c7",
        " p1c7",
        "p1c7 ",
        "P1C7",
        "p1B7",
        "",
    ],
)
def test_invalid_object_refs(obj_id):
    """Anything outside p<page>[bc]<id> is a configuration error."""
    with pytest.raises(vol.Invalid):
        hasp_object(obj_id)


def test_invalid_object_ref_message_mentions_both_kinds():
    """The error must tell the user that both b and c exist."""
    with pytest.raises(vol.Invalid) as err:
        hasp_object("p1x7")
    message = str(err.value)
    assert "p<page>b<id>" in message
    assert "p<page>c<id>" in message


@pytest.mark.parametrize("obj_id", [None, 17, ["p1c7"]])
def test_non_string_object_refs(obj_id):
    """Non-string values fail validation instead of blowing up."""
    with pytest.raises(vol.Invalid):
        hasp_object(obj_id)


@pytest.mark.parametrize("obj_id", ["p1b3", "p1c7"])
async def test_object_schema_accepts_both_kinds(hass, obj_id):
    """The object schema uses the shared reference validator."""
    assert OBJECT_SCHEMA({"obj": obj_id})["obj"] == obj_id


@pytest.mark.parametrize("obj_id", ["p1b10", "p1c7"])
def test_push_image_service_accepts_both_kinds(obj_id):
    """The push_image service uses the same object reference grammar."""
    config = PUSH_IMAGE_SCHEMA(
        {
            "entity_id": "openhasp.plate",
            "image": "http://example.com/image.png",
            "obj": obj_id,
        }
    )
    assert config["obj"] == obj_id


# --- the exact failure scene from the bug report ----------------------------


async def test_designer_connection_config_validates(hass):
    """The Designer-generated connection binding loads without a config error."""
    config = OBJECT_SCHEMA(
        {
            "obj": "p1c7",
            "properties": {
                "val": '{{ states("sensor.visconti_slimmelezer_p1_net_power") }}'
            },
        }
    )
    assert config["obj"] == "p1c7"
    assert set(config["properties"]) == {"val"}


async def test_connection_example(hass):
    """The documented connection example validates."""
    config = OBJECT_SCHEMA(
        {"obj": "p1c6", "properties": {"val": '{{ states("sensor.grid_power") }}'}}
    )
    assert set(config["properties"]) == {"val"}


# --- ordinary object regression ---------------------------------------------


async def test_ordinary_object_config_regression(hass):
    """Existing p#b# configurations keep validating unchanged."""
    for obj_id, properties in (
        ("p1b2", {"val": '{{ states("sensor.foo") }}'}),
        ("p1b1", {"x": '{{ states("sensor.bar") }}'}),
    ):
        config = OBJECT_SCHEMA({"obj": obj_id, "properties": properties})
        assert config["obj"] == obj_id
        assert set(config["properties"]) == set(properties)


@pytest.mark.parametrize("key", ["val", "x", "text", "bg_color", "hidden"])
async def test_ordinary_properties_still_validate(hass, key):
    """Existing property keys keep working."""
    config = OBJECT_SCHEMA({"obj": "p1b3", "properties": {key: "{{ 1 }}"}})
    assert set(config["properties"]) == {key}


# --- ring segment property paths (PROPERTY_SCHEMA regression) ---------------


async def test_manual_ring_example(hass):
    """The documented manual ring example validates."""
    config = OBJECT_SCHEMA(
        {
            "obj": "p1b3",
            "properties": {
                "ring_segment.solar.value": '{{ states("sensor.solar_power") }}',
                "ring_segment.grid.value": '{{ states("sensor.grid_power") }}',
                "ring_segment.battery.value": '{{ states("sensor.battery_power") }}',
            },
        }
    )
    assert set(config["properties"]) == {
        "ring_segment.solar.value",
        "ring_segment.grid.value",
        "ring_segment.battery.value",
    }


@pytest.mark.parametrize(
    "key",
    [
        "ring_segment.solar",
        "ring_segment..value",
        "ring_segment.solar.foo",
        "ring_segment.solar.value.extra",
    ],
)
async def test_invalid_ring_segment_paths_are_config_errors(hass, key):
    """No silent fallback to a plain property for malformed paths."""
    with pytest.raises(vol.Invalid):
        OBJECT_SCHEMA({"obj": "p1b3", "properties": {key: "{{ 1 }}"}})


async def test_ring_segment_paths_on_a_connection_object(hass):
    """The property grammar is independent of the object reference kind."""
    config = OBJECT_SCHEMA(
        {
            "obj": "p1c7",
            "properties": {"ring_segment.solar.value": '{{ states("sensor.solar") }}'},
        }
    )
    assert set(config["properties"]) == {"ring_segment.solar.value"}


async def test_combined_badge_and_connection_config(hass):
    """A badge with ring segments and a connection coexist in one objects list."""
    objects = [
        OBJECT_SCHEMA(obj)
        for obj in (
            {
                "obj": "p1b3",
                "properties": {
                    "ring_segment.solar.value": '{{ states("sensor.solar_power") }}'
                },
            },
            {
                "obj": "p1c7",
                "properties": {"val": '{{ states("sensor.grid_power") }}'},
            },
        )
    ]
    assert [obj["obj"] for obj in objects] == ["p1b3", "p1c7"]
    assert set(objects[0]["properties"]) == {"ring_segment.solar.value"}
    assert set(objects[1]["properties"]) == {"val"}
