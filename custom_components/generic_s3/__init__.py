"""The Generic S3 Backup integration.

Exposes an arbitrary, sufficiently S3-compatible object store as a native Home
Assistant backup destination by reusing the official ``aws_s3`` backup agent.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
import logging

from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback

from .client import async_get_client
from .const import (
    ADDRESSING_STYLE_AUTO,
    CONF_ACCESS_KEY_ID,
    CONF_ADDRESSING_STYLE,
    CONF_BUCKET,
    CONF_ENDPOINT_URL,
    CONF_REGION,
    CONF_SECRET_ACCESS_KEY,
    DATA_BACKUP_AGENT_LISTENERS,
    DEFAULT_REGION,
)
from .errors import classify_s3_error, setup_error_from_key
from .models import GenericS3ConfigEntry, GenericS3RuntimeData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: GenericS3ConfigEntry) -> bool:
    """Set up a Generic S3 backup destination from a config entry."""
    data = entry.data
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(
            async_get_client(
                endpoint_url=data[CONF_ENDPOINT_URL],
                region=data.get(CONF_REGION) or DEFAULT_REGION,
                access_key_id=data[CONF_ACCESS_KEY_ID],
                secret_access_key=data[CONF_SECRET_ACCESS_KEY],
                addressing_style=data.get(CONF_ADDRESSING_STYLE, ADDRESSING_STYLE_AUTO),
            )
        )
        await client.head_bucket(Bucket=data[CONF_BUCKET])
    except BaseException as err:
        # Always close the context we started, including on cancellation, then
        # re-raise. Cancellation propagates unchanged; it is never converted to
        # a configuration error.
        await stack.aclose()
        if isinstance(err, Exception):
            raise setup_error_from_key(classify_s3_error(err)) from err
        raise

    runtime = GenericS3RuntimeData(client=client, _stack=stack)
    entry.runtime_data = runtime

    async def _async_close_on_shutdown(_: Event) -> None:
        """Close the client on Home Assistant shutdown.

        HA shutdown does not necessarily run ordinary config-entry unload, so
        the client is closed here too. ``async_close`` is idempotent, so a later
        unload that also closes is a no-op.
        """
        await runtime.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_close_on_shutdown)
    )

    @callback
    def _notify_backup_listeners() -> None:
        for listener in hass.data.get(DATA_BACKUP_AGENT_LISTENERS, []):
            listener()

    # Notify the Backup manager whenever this entry changes state (add, unload,
    # reload). The manager, not this setup, owns listener registration itself.
    entry.async_on_unload(entry.async_on_state_change(_notify_backup_listeners))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: GenericS3ConfigEntry) -> bool:
    """Unload a config entry and close its owned client exactly once."""
    await entry.runtime_data.async_close()
    return True
