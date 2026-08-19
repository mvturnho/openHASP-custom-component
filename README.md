[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/HASwitchPlate/openHASP-custom-component)

# openHASP - Custom Component for Home Assistant

This custom component simplifies synchronization of objects on one or more openHASP [HASP - Open SwitchPlates](https://www.openhasp.com/) with Home Assistant entities. An Open SwitchPlate is basically a small touchscreen device which you can mount on the wall in place of a switch, and you can design your custom user interface for it using json. You can build your own hardware but you can also buy them ready-made.

- [Documentation](https://www.openhasp.com/latest/integrations/home-assistant/howto/)
- [Examples](https://www.openhasp.com/latest/integrations/home-assistant/sampl_conf/)
- [Automations](https://www.openhasp.com/latest/integrations/home-assistant/sampl_autom/)

### Property bindings

Objects are bound to Home Assistant with the `objects` -> `properties` map: every
key is an object property, every value a template. The rendered template result
is sent to the plate whenever it changes.

An object is addressed with an openHASP object reference:

| Reference | Meaning |
|---|---|
| `p<page>b<id>` | ordinary/historical object (label, button, slider, chart, badge, ...) |
| `p<page>c<id>` | connection |

```yaml
objects:
  - obj: "p1b2" # Ordinary object
    properties:
      "val": '{{ states("sensor.foo") }}'

  - obj: "p1c7" # Grid flow connection
    properties:
      "val": '{{ states("sensor.grid_power") }}'
```

#### Badge ring segments

A badge with `ring_source=manual` has individually addressable ring segments.
They use the same `properties` map with a `ring_segment.<segment-id>.<field>`
key, so every segment field stays an independent template binding:

```yaml
objects:
  - obj: "p1b3" # Energy mix badge
    properties:
      "ring_segment.solar.value": '{{ states("sensor.solar_power") }}'
      "ring_segment.grid.value": '{{ states("sensor.grid_power") }}'
      "ring_segment.solar.color": '{{ states("sensor.solar_color") }}'
      "ring_segment.solar.enabled": '{{ is_state("input_boolean.solar_ring", "on") }}'
```

Supported fields are `value` (number), `color` (firmware colour string) and
`enabled` (boolean). When one entity changes, only that one segment field is
patched on the plate — the full segment array is never resent. The dot is a
reserved delimiter, so segment ids may not contain one. `unknown` and
`unavailable` states are skipped instead of being sent as `0`.

Badges driven by connections (`ring_source` other than `manual`) are fed through
the connection `val` property shown above; there is no automatic conversion
between the two.

### Contributions are welcome!

If you want to contribute to this please read the [Contribution guidelines](CONTRIBUTING.md).

### [Release Notes](RELEASE.md)

