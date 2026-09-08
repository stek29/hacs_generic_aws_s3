"""Runtime data model for the Generic S3 Backup integration."""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from aiobotocore.client import AioBaseClient as S3Client
from homeassistant.config_entries import ConfigEntry


@dataclass
class GenericS3RuntimeData:
    """Owns the S3 client and the context that must be exited to close it.

    This is intentionally a tiny typed container, not a ``DataUpdateCoordinator``
    substitute. The inherited upstream backup agent only reads
    ``entry.runtime_data.client``; the ``AsyncExitStack`` is what
    ``async_setup_entry`` entered the client through, and ``async_close`` unwinds
    it exactly once.
    """

    client: S3Client
    _stack: AsyncExitStack
    _closed: bool = field(default=False, init=False)

    async def async_close(self) -> None:
        """Close the owned client context. Safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        await self._stack.aclose()


type GenericS3ConfigEntry = ConfigEntry[GenericS3RuntimeData]
