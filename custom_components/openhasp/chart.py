"""openHASP chart object — history-fed LVGL chart support."""
import json
import logging
import math
from datetime import timedelta

from homeassistant.components.mqtt import async_publish
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
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
    CONF_CHART_SHOW_SCALE,
    CONF_CHART_TYPE,
    CONF_CHART_VDIV,
    CONF_CHART_Y_LABEL,
    CONF_OBJID,
    CONF_SUBTOPIC,
)

_LOGGER = logging.getLogger(__name__)

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
        vol.Optional(CONF_CHART_SHOW_SCALE, default=False): cv.boolean,
    }
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
