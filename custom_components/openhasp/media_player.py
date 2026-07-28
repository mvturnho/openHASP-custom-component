"""Support for the openHASP plate speaker."""
import json
import logging

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.components.mqtt import async_publish, async_subscribe
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_call_later
import voluptuous as vol
from yarl import URL

from .common import HASPEntity
from .const import (
    CONF_HWID,
    CONF_PLATE,
    CONF_TOPIC,
    EVENT_HASP_PLATE_ONLINE,
)

_LOGGER = logging.getLogger(__name__)

# Payload of <topic>/state/audio as published by the plate's audio subsystem.
HASP_AUDIO_SCHEMA = vol.Schema(
    {
        vol.Required("state"): cv.string,
        vol.Optional("src"): cv.string,
        vol.Required("volume"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("loop"): cv.boolean,
    },
    extra=vol.REMOVE_EXTRA,
)

# The plate reports its own playback lifecycle; Home Assistant only has the three
# resting states, so the transient ones collapse onto them. "error" is surfaced
# in the log rather than as a state, because media_player has no error state.
HASP_STATE_TO_HA = {
    "idle": MediaPlayerState.IDLE,
    "opening": MediaPlayerState.BUFFERING,
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "stopping": MediaPlayerState.IDLE,
    "finished": MediaPlayerState.IDLE,
    "error": MediaPlayerState.IDLE,
}

# Text-to-speech defaults to MP3, which the plate cannot decode. These options
# are accepted by every TTS engine: an engine that can produce the format does
# so natively, otherwise Home Assistant transcodes with ffmpeg. 22.05 kHz mono
# 16-bit keeps the stream at ~44 kB/s, which the plate sustains comfortably.
# Home Assistant batches the broker-side SUBSCRIBE behind a cooldown (0.1 s, or
# 0.5 s just after connecting), so async_subscribe() returns before the broker
# knows about the subscription. A probe published immediately afterwards is
# answered by the plate within milliseconds - into a subscription that is not
# live yet. The plate's audio state is not retained and both probe and reply are
# QoS 0, so a missed reply is missed for good and the entity would stay
# unavailable forever. Re-probe until the plate answers; one that has no audio
# never answers and correctly stays unavailable.
PROBE_RETRY_DELAYS = (2, 10, 30)

TTS_MEDIA_SOURCE_PREFIX = "media-source://tts/"
TTS_QUERY_OPTIONS = "tts_options"
# Query parameters that are not engine options; same exclusion list Home
# Assistant itself uses when it reads loose options off a tts media source id.
TTS_RESERVED_QUERY = ("message", "language", "cache")
TTS_WAV_OPTIONS = {
    "preferred_format": "wav",
    "preferred_sample_rate": 22050,
    "preferred_sample_channels": 1,
    "preferred_sample_bytes": 2,
}


def request_wav_from_tts(media_id: str) -> str:
    """Ask a tts media source for WAV instead of the engine's default format.

    Options travel in the media source id's query either as loose parameters or
    bundled in a "tts_options" JSON blob. The blob always wins when present, so
    loose options get folded into it rather than silently dropped. The blob is
    also the only spelling that keeps integers as integers, which matters because
    Home Assistant's ffmpeg conversion compares the sample size numerically.
    """
    url = URL(media_id)
    query = dict(url.query)

    if TTS_QUERY_OPTIONS in query:
        try:
            options = json.loads(query[TTS_QUERY_OPTIONS])
        except json.JSONDecodeError:
            _LOGGER.debug("Leaving unparsable tts options alone: %s", media_id)
            return media_id
        if not isinstance(options, dict):
            _LOGGER.debug("Leaving non-object tts options alone: %s", media_id)
            return media_id
    else:
        options = {
            key: value
            for key, value in query.items()
            if key not in TTS_RESERVED_QUERY
        }
        for key in options:
            query.pop(key)

    options.update(TTS_WAV_OPTIONS)
    query[TTS_QUERY_OPTIONS] = json.dumps(options, separators=(",", ":"))
    return str(url.with_query(query))


# pylint: disable=W0613
async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
):
    """Set up the plate speaker based on a config entry."""

    async_add_entities(
        [
            HASPMediaPlayer(
                entry.data[CONF_NAME],
                entry.data[CONF_HWID],
                entry.data[CONF_TOPIC],
            )
        ]
    )

    return True


class HASPMediaPlayer(HASPEntity, MediaPlayerEntity):
    """Representation of the speaker on an openHASP plate.

    The plate plays PCM WAV only, either from its own filesystem ("L:/x.wav") or
    straight from an http:// URL. Anything else has to be transcoded before it
    gets here; see async_play_media.
    """

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    def __init__(self, name, hwid, topic):
        """Initialize the speaker."""
        super().__init__(name, hwid, topic, "audio")
        self._attr_name = f"{name} speaker"
        self._attr_state = MediaPlayerState.IDLE
        self._attr_volume_level = None
        # Audio is an optional, board-dependent openHASP feature and discovery
        # does not advertise it. A plate built without audio has no "audio"
        # command at all, so it never answers the probe below and the entity
        # stays unavailable instead of pretending to have a speaker.
        self._audio_seen = False

    @property
    def available(self):
        """Return if the plate is online AND actually has audio."""
        return super().available and self._audio_seen

    async def refresh(self):
        """Ask the plate for its current audio state.

        An empty audio payload is the plate's own "report status" request, so
        this doubles as the capability probe and as a resync after a reboot.
        """
        await async_publish(
            self.hass,
            f"{self._topic}/command/audio",
            "",
            qos=0,
            retain=False,
        )

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        @callback
        async def audio_state_message_received(msg):
            """Process the plate's audio state."""

            # TEMP DEBUG: proves the callback fires and shows the raw payload.
            _LOGGER.debug(
                "%s RX %s: %r", self.entity_id, msg.topic, msg.payload
            )

            try:
                message = HASP_AUDIO_SCHEMA(json.loads(msg.payload))
            except (vol.error.Invalid, json.JSONDecodeError) as err:
                _LOGGER.error("%s invalid audio state: %s", self.entity_id, err)
                return

            _LOGGER.debug("%s audio state = %s", self.entity_id, message)

            plate_state = message["state"]
            if plate_state == "error":
                _LOGGER.warning(
                    "%s playback failed for %s",
                    self.entity_id,
                    message.get("src", "unknown source"),
                )

            self._audio_seen = True
            self._available = True
            self._attr_state = HASP_STATE_TO_HA.get(plate_state, MediaPlayerState.IDLE)
            self._attr_volume_level = message["volume"] / 100
            # The plate clears src once it goes idle, which is what we want: no
            # stale "now playing" left behind after playback ends.
            source = message.get("src")
            self._attr_media_content_id = source
            self._attr_media_title = source
            self.async_write_ha_state()

            # TEMP DEBUG: the three values that decide whether HA shows this
            # entity as available, sampled after they have been updated.
            _LOGGER.debug(
                "%s audio_seen=%s available=%s state=%s volume=%s",
                self.entity_id,
                self._audio_seen,
                self.available,
                self.state,
                self._attr_volume_level,
            )

        state_topic = f"{self._topic}/state/audio"
        # TEMP DEBUG: confirms the exact topic subscribed to.
        _LOGGER.debug("%s subscribing to %s", self.entity_id, state_topic)
        self._subscriptions.append(
            await async_subscribe(
                self.hass,
                state_topic,
                audio_state_message_received,
            )
        )

        @callback
        async def plate_online(event):
            """Re-probe after the plate reboots; its state is gone."""
            if event.data[CONF_PLATE] == self._hwid:
                await self.refresh()

        self._subscriptions.append(
            self.hass.bus.async_listen(EVENT_HASP_PLATE_ONLINE, plate_online)
        )

        @callback
        def retry_probe(_now):
            """Probe again while the plate has still not answered."""
            if self._audio_seen:
                return
            _LOGGER.debug(
                "%s no audio state yet, probing %s again",
                self.entity_id,
                f"{self._topic}/command/audio",
            )
            self.hass.async_create_task(self.refresh())

        for delay in PROBE_RETRY_DELAYS:
            self._subscriptions.append(
                async_call_later(self.hass, delay, retry_probe)
            )

        await self.refresh()

    async def async_browse_media(
        self, media_content_type=None, media_content_id=None
    ) -> BrowseMedia:
        """Let the user pick from the Home Assistant media sources."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(self, media_type, media_id, **kwargs):
        """Play a WAV on the plate.

        The plate fetches the URL itself, so Home Assistant has to hand over an
        absolute URL that is reachable from the plate's network, not a relative
        one. The plate decodes PCM WAV only, so anything that Home Assistant can
        transcode is asked for as WAV before it is resolved.
        """
        if media_id.startswith(TTS_MEDIA_SOURCE_PREFIX):
            media_id = request_wav_from_tts(media_id)

        if media_source.is_media_source_id(media_id):
            play_item = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = play_item.url

        media_id = async_process_play_media_url(self.hass, media_id)

        # Pre-flight check so an unsupported format is an actionable Home
        # Assistant log line instead of a silent rejection on the plate.
        path = media_id.split("?", 1)[0].split("#", 1)[0]
        if "." in path.rsplit("/", 1)[-1] and not path.lower().endswith(".wav"):
            _LOGGER.error(
                "%s can only play PCM WAV, refusing %s. Configure the source "
                "(or your TTS engine) to produce WAV",
                self.entity_id,
                media_id,
            )
            return

        _LOGGER.debug("%s play %s", self.entity_id, media_id)
        await async_publish(
            self.hass,
            f"{self._topic}/command/audio",
            json.dumps({"command": "play", "src": media_id}),
            qos=0,
            retain=False,
        )

    async def async_media_stop(self):
        """Stop playback, aborting an in-flight network read on the plate."""
        await async_publish(
            self.hass,
            f"{self._topic}/command/audio",
            json.dumps({"command": "stop"}),
            qos=0,
            retain=False,
        )

    async def async_set_volume_level(self, volume: float):
        """Set the plate's PCM volume (0..100 on the wire)."""
        await async_publish(
            self.hass,
            f"{self._topic}/command/audio",
            json.dumps({"command": "volume", "volume": round(volume * 100)}),
            qos=0,
            retain=False,
        )
