"""Unit tests for the ring_segment.<segment-id>.<field> property-path parser."""
import math

import pytest
import voluptuous as vol

from custom_components.openhasp.ring_segment import (
    RING_SEGMENT_PROPERTY,
    RingSegmentValueError,
    build_ring_segment_command,
    hasp_property_key,
    is_ring_segment_path,
    parse_ring_segment_path,
)


@pytest.mark.parametrize(
    "path,segment_id,field",
    [
        ("ring_segment.solar.value", "solar", "value"),
        ("ring_segment.segment-1.value", "segment-1", "value"),
        ("ring_segment.pv_1.enabled", "pv_1", "enabled"),
        ("ring_segment.grid.color", "grid", "color"),
        ("ring_segment.battery.enabled", "battery", "enabled"),
    ],
)
def test_parse_valid_paths(path, segment_id, field):
    """Valid paths resolve to segment id and field."""
    parsed = parse_ring_segment_path(path)
    assert parsed.segment_id == segment_id
    assert parsed.field == field
    assert parsed.path == path


@pytest.mark.parametrize(
    "path",
    [
        "ring_segment",
        "ring_segment.solar",
        "ring_segment..value",
        "ring_segment.solar.foo",
        "ring_segment.solar.value.extra",
        "ring_segment. .value",
        "ring_segment.two words.value",
        "ring_segment.solar.",
        "ring_segment...",
        "val",
    ],
)
def test_parse_invalid_paths(path):
    """Invalid paths raise a configuration error, never a silent fallback."""
    with pytest.raises(vol.Invalid):
        parse_ring_segment_path(path)


@pytest.mark.parametrize(
    "key", ["val", "x", "text", "bg_color", "hidden", "ring_segment"]
)
def test_normal_keys_are_not_intercepted(key):
    """Only keys starting with 'ring_segment.' are claimed by the parser."""
    assert is_ring_segment_path(key) is False
    assert hasp_property_key(key) == key


def test_ring_segment_paths_are_intercepted():
    """Dotted ring segment keys are claimed and validated."""
    assert is_ring_segment_path("ring_segment.solar.value") is True
    assert hasp_property_key("ring_segment.solar.value") == "ring_segment.solar.value"


@pytest.mark.parametrize(
    "key", ["ring_segment.solar", "ring_segment.solar.foo", "ring_segment..value"]
)
def test_property_key_validation_rejects_bad_paths(key):
    """A malformed ring segment path is a config error, not a plain property."""
    with pytest.raises(vol.Invalid):
        hasp_property_key(key)


@pytest.mark.parametrize("key", ["Not A Slug", "with space"])
def test_property_key_validation_keeps_slug_rules(key):
    """Non ring-segment keys keep the existing slug validation."""
    with pytest.raises(vol.Invalid):
        hasp_property_key(key)


# --- value semantics --------------------------------------------------------


@pytest.mark.parametrize(
    "rendered,expected",
    [
        ("812", 812),
        (812, 812),
        ("0", 0),
        ("12", 12),
        ("12.5", 12.5),
        ("812.5", 812.5),
        ("812.0", 812.0),
        ("-450", -450),
        (-450, -450),
        ("-50", -50),
        (-50.5, -50.5),
        ("  725  ", 725),
    ],
)
def test_value_payload(rendered, expected):
    """Numeric results are sent as JSON numbers, negatives untouched."""
    path = parse_ring_segment_path("ring_segment.solar.value")
    command = build_ring_segment_command(path, rendered)
    assert command == {RING_SEGMENT_PROPERTY: {"id": "solar", "value": expected}}
    assert isinstance(command[RING_SEGMENT_PROPERTY]["value"], (int, float))
    assert not isinstance(command[RING_SEGMENT_PROPERTY]["value"], bool)


@pytest.mark.parametrize(
    "rendered",
    [
        "unknown",
        "unavailable",
        "none",
        "null",
        None,
        "",
        "   ",
        "abc",
        "NaN",
        "inf",
        "-inf",
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
    ],
)
def test_value_unusable_is_not_sent(rendered):
    """unknown/unavailable/non-numeric never become 0."""
    path = parse_ring_segment_path("ring_segment.solar.value")
    with pytest.raises(RingSegmentValueError):
        build_ring_segment_command(path, rendered)


def test_value_zero_is_still_sent():
    """A real zero is a legitimate value."""
    path = parse_ring_segment_path("ring_segment.solar.value")
    assert build_ring_segment_command(path, "0") == {
        RING_SEGMENT_PROPERTY: {"id": "solar", "value": 0}
    }


def test_value_never_infinite():
    """Guard the finite check used for float results."""
    path = parse_ring_segment_path("ring_segment.solar.value")
    with pytest.raises(RingSegmentValueError):
        build_ring_segment_command(path, math.inf)


# --- color semantics --------------------------------------------------------


@pytest.mark.parametrize("rendered", ["#ff9800ff", "#40a0ff80"])
def test_color_payload(rendered):
    """Colour strings are forwarded verbatim."""
    path = parse_ring_segment_path("ring_segment.solar.color")
    assert build_ring_segment_command(path, rendered) == {
        RING_SEGMENT_PROPERTY: {"id": "solar", "color": rendered}
    }


@pytest.mark.parametrize("rendered", ["unknown", "unavailable", "", None])
def test_color_unusable_is_not_sent(rendered):
    """No colour update for unusable states."""
    path = parse_ring_segment_path("ring_segment.solar.color")
    with pytest.raises(RingSegmentValueError):
        build_ring_segment_command(path, rendered)


# --- enabled semantics ------------------------------------------------------


@pytest.mark.parametrize(
    "rendered,expected",
    [
        (True, True),
        (False, False),
        ("True", True),
        ("False", False),
        ("on", True),
        ("off", False),
        ("yes", True),
        ("no", False),
        ("1", True),
        ("0", False),
        (1, True),
        (0, False),
    ],
)
def test_enabled_payload(rendered, expected):
    """Enabled uses the existing cv.boolean conversion and stays a real bool."""
    path = parse_ring_segment_path("ring_segment.solar.enabled")
    command = build_ring_segment_command(path, rendered)
    assert command == {RING_SEGMENT_PROPERTY: {"id": "solar", "enabled": expected}}
    assert isinstance(command[RING_SEGMENT_PROPERTY]["enabled"], bool)


@pytest.mark.parametrize("rendered", ["unknown", "unavailable", "", None, "maybe"])
def test_enabled_unusable_is_not_sent(rendered):
    """No enabled update for unusable states."""
    path = parse_ring_segment_path("ring_segment.solar.enabled")
    with pytest.raises(RingSegmentValueError):
        build_ring_segment_command(path, rendered)


def test_command_never_builds_a_segment_array():
    """The plugin only ever patches a single segment."""
    path = parse_ring_segment_path("ring_segment.solar.value")
    command = build_ring_segment_command(path, 812)
    assert "ring_segments" not in command
    assert set(command) == {RING_SEGMENT_PROPERTY}
    assert set(command[RING_SEGMENT_PROPERTY]) == {"id", "value"}
