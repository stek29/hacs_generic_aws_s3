"""Backup platform for the Generic S3 Backup integration.

This is a thin adapter. The backup agent is Home Assistant's official
``aws_s3`` ``S3BackupAgent`` re-exposed under this integration's domain; upload,
download, listing, lookup, deletion, multipart transfer, progress reporting,
pagination, metadata handling, caching and object-key construction are all
inherited unchanged. Only the platform-level discovery hooks
(``async_get_backup_agents`` / ``async_register_backup_agents_listener``) are
re-implemented so they operate on this domain's config entries and listener
collection, leaving the ``aws_s3`` integration's entries and listeners untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.aws_s3.backup import S3BackupAgent as AwsS3BackupAgent
from homeassistant.components.backup import BackupAgent
from homeassistant.core import HomeAssistant, callback

from .const import DATA_BACKUP_AGENT_LISTENERS, DOMAIN
from .models import GenericS3ConfigEntry

if TYPE_CHECKING:
    from homeassistant.components.aws_s3 import S3ConfigEntry as AwsS3ConfigEntry


class S3BackupAgent(AwsS3BackupAgent):
    """Expose Home Assistant's S3 backup agent under this integration's domain.

    Only ``domain`` changes, which makes the inherited ``agent_id``
    (``f"{domain}.{unique_id}"``) and the Backup manager's per-domain agent
    bookkeeping resolve to ``generic_s3`` instead of ``aws_s3``. Every backup
    operation, and its upstream limitations, are inherited verbatim.
    """

    domain = DOMAIN


async def async_get_backup_agents(
    hass: HomeAssistant, **kwargs: Any
) -> list[BackupAgent]:
    """Return an agent for every loaded config entry of this integration."""
    entries: list[GenericS3ConfigEntry] = hass.config_entries.async_loaded_entries(
        DOMAIN
    )
    # The inherited constructor is typed for ``aws_s3``'s config-entry alias
    # (its static type assumes a coordinator in ``runtime_data``). At runtime it
    # only reads ``entry.runtime_data.client`` plus entry data/title/id, which
    # this integration's ``GenericS3RuntimeData`` provides. This cast is the only
    # concession to that annotation; no coordinator is manufactured.
    return [S3BackupAgent(hass, cast("AwsS3ConfigEntry", entry)) for entry in entries]


@callback
def async_register_backup_agents_listener(
    hass: HomeAssistant,
    *,
    listener: Callable[[], None],
    **kwargs: Any,
) -> Callable[[], None]:
    """Register a listener for agent availability changes.

    Despite the name this is a synchronous ``@callback``, matching the core
    Backup manager's expectation. The manager calls this once and expects the
    returned callable to unregister the listener.
    """
    listeners = hass.data.setdefault(DATA_BACKUP_AGENT_LISTENERS, [])
    listeners.append(listener)

    @callback
    def remove_listener() -> None:
        """Remove the listener and drop the empty collection."""
        listeners.remove(listener)
        if not listeners:
            hass.data.pop(DATA_BACKUP_AGENT_LISTENERS, None)

    return remove_listener
