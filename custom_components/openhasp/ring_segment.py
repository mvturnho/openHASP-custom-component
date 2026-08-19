"""Badge ring-segment property paths for the existing objects -> properties model.

This module only adds a specialised *configuration* property-path syntax on top
of the regular ``objects -> properties`` bindings::

    objects:
      - obj: "p1b3"
        properties:
          "ring_segment.solar.value": '{{ states("sensor.solar_power") }}'
          "ring_segment.grid.value": '{{ states("sensor.grid_power") }}'

Every entry stays an independent template binding, so a change of a single
entity results in a single partial update for a single segment.

The dot-path is plugin configuration syntax only.  On the wire the plate still
receives the plain ``ring_segment`` property with an object payload::

    {"page": 1, "id": 3, "ring_segment": {"id": "solar", "value": 812}}

The full ``ring_segments`` array is never reconstructed - the plugin does not
know the complete segment state and the firmware applies partial patches.
"""
import math

import homeassistant.helpers.config_validation as cv
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
import voluptuous as vol

# Wire property name sent to the plate; the dot-path never reaches the firmware.
RING_SEGMENT_PROPERTY = "ring_segment"
RING_SEGMENT_PREFIX = f"{RING_SEGMENT_PROPERTY}."

# Fields supported in this step.
RING_SEGMENT_FIELD_VALUE = "value"
RING_SEGMENT_FIELD_COLOR = "color"
RING_SEGMENT_FIELD_ENABLED = "enabled"
RING_SEGMENT_FIELDS = (
    RING_SEGMENT_FIELD_VALUE,
    RING_SEGMENT_FIELD_COLOR,
    RING_SEGMENT_FIELD_ENABLED,
)

# Template results that mean "no usable data"; never coerced to 0 / false.
UNUSABLE_RESULTS = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE, "none", "null", ""})


class RingSegmentValueError(ValueError):
    """The rendered template result cannot be sent to the plate."""


class RingSegmentPath:
    """Parsed ``ring_segment.<segment-id>.<field>`` configuration property path."""

    __slots__ = ("path", "segment_id", "field")

    def __init__(self, path, segment_id, field):
        """Initialize a parsed path."""
        self.path = path
        self.segment_id = segment_id
        self.field = field

    def __eq__(self, other):
        """Compare two parsed paths."""
        if not isinstance(other, RingSegmentPath):
            return NotImplemented
        return (self.path, self.segment_id, self.field) == (
            other.path,
            other.segment_id,
            other.field,
        )

    def __hash__(self):
        """Hash on the canonical path."""
        return hash((self.path, self.segment_id, self.field))

    def __repr__(self):
        """Return the debug representation."""
        return f"RingSegmentPath({self.path!r}, {self.segment_id!r}, {self.field!r})"


def is_ring_segment_path(property_name):
    """Return True for keys that the ring-segment parser claims.

    Only keys explicitly starting with ``ring_segment.`` are intercepted, so
    ordinary properties such as ``val``, ``x``, ``text`` or ``bg_color`` keep
    using the existing pipeline unchanged.
    """
    return isinstance(property_name, str) and property_name.startswith(
        RING_SEGMENT_PREFIX
    )


def parse_ring_segment_path(property_name):
    """Parse ``ring_segment.<segment-id>.<field>`` or raise vol.Invalid.

    The dot is a reserved delimiter: segment ids may not contain one and no
    escaping convention is supported.
    """
    if not is_ring_segment_path(property_name):
        raise vol.Invalid(
            f"'{property_name}' is not a ring segment property, "
            f"expected '{RING_SEGMENT_PREFIX}<segment-id>.<field>'"
        )

    parts = property_name[len(RING_SEGMENT_PREFIX) :].split(".")
    if len(parts) != 2:
        raise vol.Invalid(
            f"Invalid ring segment property '{property_name}', "
            f"expected '{RING_SEGMENT_PREFIX}<segment-id>.<field>' "
            f"with one of {', '.join(RING_SEGMENT_FIELDS)}"
        )

    segment_id, field = parts

    if not segment_id or not segment_id.strip() or segment_id != segment_id.strip():
        raise vol.Invalid(
            f"Invalid ring segment property '{property_name}', "
            "segment id must not be empty or contain surrounding whitespace"
        )
    if any(char.isspace() for char in segment_id):
        raise vol.Invalid(
            f"Invalid ring segment property '{property_name}', "
            "segment id must not contain whitespace"
        )
    if field not in RING_SEGMENT_FIELDS:
        raise vol.Invalid(
            f"Invalid ring segment field '{field}' in '{property_name}', "
            f"supported fields are {', '.join(RING_SEGMENT_FIELDS)}"
        )

    return RingSegmentPath(property_name, segment_id, field)


def hasp_property_key(value):
    """Validate a key of the ``properties`` map.

    ``ring_segment.<id>.<field>`` paths are validated by the specialised parser,
    every other key keeps the existing slug validation.  There is no silent
    fallback: an invalid ring-segment path is a configuration error.
    """
    if is_ring_segment_path(value):
        parse_ring_segment_path(value)
        return value
    return cv.slug(value)


def _text(result):
    """Return the stripped textual form of a rendered template result."""
    return "" if result is None else str(result).strip()


def _is_unusable(text):
    """Return True for unknown/unavailable/empty template results."""
    return text.lower() in UNUSABLE_RESULTS


def _coerce_value(result):
    """Coerce a rendered result to a JSON number.

    Negative values are passed through unchanged; the firmware owns the
    clamping semantics for manual badge segments.
    """
    if isinstance(result, bool):
        raise RingSegmentValueError(f"'{result}' is not numeric")

    if isinstance(result, int):
        return result
    if isinstance(result, float):
        if not math.isfinite(result):
            raise RingSegmentValueError(f"'{result}' is not finite")
        return result

    text = _text(result)
    if _is_unusable(text):
        raise RingSegmentValueError(f"'{text}' has no usable value")

    try:
        return int(text)
    except ValueError:
        pass

    try:
        number = float(text)
    except ValueError:
        raise RingSegmentValueError(f"'{text}' is not numeric") from None

    if not math.isfinite(number):
        raise RingSegmentValueError(f"'{text}' is not finite")
    return number


def _coerce_color(result):
    """Coerce a rendered result to a firmware colour string.

    Colour strings are not validated harder than existing colour properties;
    the firmware stays responsible for parsing the RGBA value.
    """
    text = _text(result)
    if _is_unusable(text):
        raise RingSegmentValueError(f"'{text}' has no usable colour")
    return text


def _coerce_enabled(result):
    """Coerce a rendered result to a real JSON boolean using cv.boolean."""
    if isinstance(result, str) and _is_unusable(_text(result)):
        raise RingSegmentValueError(f"'{result}' has no usable boolean")
    try:
        return cv.boolean(result)
    except vol.Invalid as err:
        raise RingSegmentValueError(f"'{result}' is not a boolean") from err


_COERCERS = {
    RING_SEGMENT_FIELD_VALUE: _coerce_value,
    RING_SEGMENT_FIELD_COLOR: _coerce_color,
    RING_SEGMENT_FIELD_ENABLED: _coerce_enabled,
}


def build_ring_segment_command(path, result):
    """Build the partial ring-segment command for a rendered template result.

    Returns ``{"ring_segment": {"id": <segment-id>, <field>: <value>}}``.
    Raises RingSegmentValueError when the result must not be sent.
    """
    return {
        RING_SEGMENT_PROPERTY: {
            "id": path.segment_id,
            path.field: _COERCERS[path.field](result),
        }
    }
