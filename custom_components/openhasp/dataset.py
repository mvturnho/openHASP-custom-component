"""openHASP dataset — firmware Dataset updates from Home Assistant entity history."""
import json
import logging
import math
from datetime import timedelta

from homeassistant.components.mqtt import async_publish
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    CONF_DATASET_HISTORY_HOURS,
    CONF_DATASET_ID,
    CONF_DATASET_INTERVAL,
    CONF_DATASET_SERIES,
    CONF_DATASET_SERIES_ENTITY,
)

_LOGGER = logging.getLogger(__name__)

DATASET_SERIES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATASET_SERIES_ENTITY): cv.entity_id,
        vol.Optional("label"): cv.string,
        vol.Optional("color"): cv.string,
    },
    extra=vol.ALLOW_EXTRA,
)

DATASET_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DATASET_ID): vol.All(int, vol.Range(min=0, max=65535)),
        vol.Optional("name"): cv.string,
        vol.Required(CONF_DATASET_HISTORY_HOURS): vol.All(
            vol.Coerce(float), vol.Range(min=0.01)
        ),
        vol.Required(CONF_DATASET_INTERVAL): vol.All(
            vol.Coerce(float), vol.Range(min=1)
        ),
        vol.Required(CONF_DATASET_SERIES): vol.All(cv.ensure_list, [DATASET_SERIES_SCHEMA]),
    },
    extra=vol.ALLOW_EXTRA,
)

DATASETS_SCHEMA = vol.All(cv.ensure_list, [DATASET_SCHEMA])


def _resample_history(timed_values, start_time, end_time, n_points):
    """Resample (datetime, float) pairs to n evenly-spaced bucket values using carry-forward."""
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


class HASPDataset:
    """Publishes firmware Dataset Replace commands from Home Assistant entity history."""

    def __init__(self, hass, plate_topic, config):
        self.hass = hass
        self._dataset_id = config[CONF_DATASET_ID]
        self._history_hours = config[CONF_DATASET_HISTORY_HOURS]
        self._interval_seconds = config[CONF_DATASET_INTERVAL]
        self._series = config[CONF_DATASET_SERIES]
        self._replace_topic = f"{plate_topic}/command/d{self._dataset_id}.replace"
        self._subscriptions = []

    async def enable_object(self):
        """Bootstrap history and schedule periodic updates."""
        await self._send_history()
        self._subscriptions.append(
            async_track_time_interval(
                self.hass,
                self._interval_update,
                timedelta(seconds=self._interval_seconds),
            )
        )

    async def disable_object(self):
        """Cancel all scheduled updates."""
        for unsub in self._subscriptions:
            unsub()
        self._subscriptions = []

    async def refresh(self):
        """Re-send full history (called on plate reconnect)."""
        await self._send_history()

    async def _interval_update(self, _now):
        await self._send_history()

    async def _send_history(self):
        """Fetch recorder history for all series and publish Dataset Replace commands."""
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.history import get_significant_states
        except ImportError:
            _LOGGER.warning(
                "Recorder unavailable; dataset %s cannot fetch history", self._dataset_id
            )
            return

        now = dt_util.utcnow()
        t_start_dt = now - timedelta(hours=self._history_hours)
        n_points = math.ceil(self._history_hours * 3600 / self._interval_seconds)

        if n_points <= 0:
            _LOGGER.error("Dataset %s: computed n_points is zero or negative", self._dataset_id)
            return

        t_step = int(self._interval_seconds)
        # Each bucket represents interval_seconds; values[0] covers t_start_dt..t_start_dt+t_step,
        # so firmware label[0] = t_start_dt + t_step.
        t_start_ts = int(t_start_dt.timestamp()) + t_step

        for ser_idx, series in enumerate(self._series):
            entity_id = series[CONF_DATASET_SERIES_ENTITY]

            if self.hass.states.get(entity_id) is None:
                _LOGGER.error(
                    "Dataset %s series %d: entity '%s' does not exist",
                    self._dataset_id,
                    ser_idx,
                    entity_id,
                )
                continue

            try:
                states = await get_instance(self.hass).async_add_executor_job(
                    get_significant_states,
                    self.hass,
                    t_start_dt,
                    None,
                    [entity_id],
                )
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.error(
                    "Dataset %s series %d: history retrieval failed for '%s': %s",
                    self._dataset_id,
                    ser_idx,
                    entity_id,
                    err,
                )
                continue

            timed_values = []
            for state in states.get(entity_id, []):
                try:
                    timed_values.append((state.last_changed, round(float(state.state), 2)))
                except (ValueError, TypeError):
                    pass

            if not timed_values:
                _LOGGER.debug(
                    "Dataset %s series %d: no usable history for '%s'",
                    self._dataset_id,
                    ser_idx,
                    entity_id,
                )
                continue

            values = _resample_history(timed_values, t_start_dt, now, n_points)

            payload = {
                "ser": ser_idx,
                "t_start": t_start_ts,
                "t_step": t_step,
                "data": values,
            }
            _LOGGER.debug(
                "Dataset %s series %d: publishing %d points",
                self._dataset_id,
                ser_idx,
                len(values),
            )
            await async_publish(
                self.hass, self._replace_topic, json.dumps(payload), qos=0, retain=False
            )
