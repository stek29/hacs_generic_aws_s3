"""Thin-adapter contract tests for the inherited S3 backup agent."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
import json
from typing import Any
from unittest.mock import MagicMock

from homeassistant.components.aws_s3.backup import (
    MULTIPART_MIN_PART_SIZE_BYTES,
    S3BackupAgent as UpstreamS3BackupAgent,
    suggested_filenames,
)
from homeassistant.components.backup import AgentBackup, async_get_manager
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.generic_s3.backup import (
    S3BackupAgent,
    async_get_backup_agents,
    async_register_backup_agents_listener,
)
from custom_components.generic_s3.const import DATA_BACKUP_AGENT_LISTENERS, DOMAIN
from custom_components.generic_s3.models import GenericS3RuntimeData

pytestmark = pytest.mark.usefixtures("mock_session")

BACKUP = AgentBackup(
    addons=[],
    backup_id="abc123",
    date="2026-09-08T00:00:00+00:00",
    database_included=True,
    extra_metadata={},
    folders=[],
    homeassistant_included=True,
    homeassistant_version="2026.9.0",
    name="Test",
    protected=False,
    size=1024,
)
TAR_KEY, META_KEY = (f"ha/{n}" for n in suggested_filenames(BACKUP))


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self) -> bytes:
        return self._payload

    async def iter_chunks(self) -> AsyncIterator[bytes]:
        yield self._payload


class _Paginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    async def paginate(self, **_: Any) -> AsyncIterator[dict[str, Any]]:
        for page in self._pages:
            yield page


def test_is_thin_subclass() -> None:
    """Only ``domain`` is overridden; every operation is inherited verbatim."""
    assert issubclass(S3BackupAgent, UpstreamS3BackupAgent)
    assert S3BackupAgent.domain == DOMAIN
    for name in (
        "async_upload_backup",
        "async_download_backup",
        "async_delete_backup",
        "async_list_backups",
        "async_get_backup",
        "_upload_simple",
        "_upload_multipart",
    ):
        assert getattr(S3BackupAgent, name) is getattr(UpstreamS3BackupAgent, name)


@pytest.fixture
def agent(hass: HomeAssistant, mock_s3_client: Any) -> S3BackupAgent:
    """An agent built on the real runtime-data shape and valid entry metadata."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ha-backups/ha (s3.example.com)",
        data={
            "endpoint_url": "https://s3.example.com",
            "region": "us-east-1",
            "bucket": "ha-backups",
            "access_key_id": "AKIA",
            "secret_access_key": "secret",
            "prefix": "ha",
            "addressing_style": "auto",
        },
    )
    entry.add_to_hass(hass)
    entry.runtime_data = GenericS3RuntimeData(
        client=mock_s3_client, _stack=AsyncExitStack()
    )

    meta = json.dumps(BACKUP.as_dict()).encode()
    mock_s3_client.get_paginator = MagicMock(
        return_value=_Paginator([{"Contents": [{"Key": META_KEY}]}])
    )

    async def _get_object(*, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        assert Bucket == "ha-backups"
        return {"Body": _Body(meta if Key == META_KEY else b"tar-bytes")}

    mock_s3_client.get_object.side_effect = _get_object
    return S3BackupAgent(hass, entry)  # type: ignore[arg-type]


async def test_small_upload_writes_tar_and_metadata_with_prefix(
    agent: S3BackupAgent, mock_s3_client: Any
) -> None:
    """A sub-threshold upload uses put_object for both objects under the prefix."""

    async def _chunks() -> AsyncIterator[bytes]:
        yield b"x" * 1024

    async def _open_stream() -> AsyncIterator[bytes]:
        return _chunks()

    await agent.async_upload_backup(
        open_stream=_open_stream, backup=BACKUP, on_progress=lambda **_: None
    )

    keys = {c.kwargs["Key"] for c in mock_s3_client.put_object.await_args_list}
    assert keys == {TAR_KEY, META_KEY}


async def test_multipart_upload_uses_inherited_helper(
    agent: S3BackupAgent, mock_s3_client: Any
) -> None:
    """An over-threshold size drives the inherited multipart path (>= 2 parts)."""
    big = AgentBackup.from_dict(
        {**BACKUP.as_dict(), "size": MULTIPART_MIN_PART_SIZE_BYTES + 1024}
    )

    async def _chunks() -> AsyncIterator[bytes]:
        yield b"a" * MULTIPART_MIN_PART_SIZE_BYTES
        yield b"b" * 1024

    async def _open_stream() -> AsyncIterator[bytes]:
        return _chunks()

    await agent.async_upload_backup(
        open_stream=_open_stream, backup=big, on_progress=lambda **_: None
    )

    mock_s3_client.create_multipart_upload.assert_awaited_once()
    assert mock_s3_client.upload_part.await_count >= 2
    mock_s3_client.complete_multipart_upload.assert_awaited_once()
    mock_s3_client.abort_multipart_upload.assert_not_awaited()


async def test_list_and_get_roundtrip(agent: S3BackupAgent) -> None:
    """Listing parses the metadata object; get returns the same backup."""
    listed = await agent.async_list_backups()
    assert [b.backup_id for b in listed] == ["abc123"]
    assert (await agent.async_get_backup("abc123")).backup_id == "abc123"


async def test_download_streams_tar_object(agent: S3BackupAgent) -> None:
    """Download resolves the id, reads the tar key and yields its bytes."""
    chunks = [chunk async for chunk in await agent.async_download_backup("abc123")]
    assert b"".join(chunks) == b"tar-bytes"


async def test_delete_removes_tar_and_metadata(
    agent: S3BackupAgent, mock_s3_client: Any
) -> None:
    """Delete removes both the backup object and its metadata object."""
    await agent.async_delete_backup("abc123")
    keys = {c.kwargs["Key"] for c in mock_s3_client.delete_object.await_args_list}
    assert keys == {TAR_KEY, META_KEY}


async def test_listener_subscribe_and_unsubscribe(hass: HomeAssistant) -> None:
    """The listener collection is created, appended to, and dropped when empty."""
    calls: list[int] = []
    unsub = async_register_backup_agents_listener(
        hass, listener=lambda: calls.append(1)
    )
    assert hass.data[DATA_BACKUP_AGENT_LISTENERS]
    unsub()
    assert DATA_BACKUP_AGENT_LISTENERS not in hass.data


async def test_backup_manager_discovers_loaded_entries_only(
    hass: HomeAssistant, mock_s3_client: Any
) -> None:
    """Real Backup manager sees an agent per loaded entry, gone after unload."""
    assert await async_setup_component(hass, "backup", {})

    entries = [
        MockConfigEntry(
            domain=DOMAIN,
            title=f"b{i}",
            data={
                "endpoint_url": f"https://s3{i}.example.com",
                "region": "us-east-1",
                "bucket": f"bucket{i}",
                "access_key_id": "AKIA",
                "secret_access_key": "secret",
                "prefix": "",
                "addressing_style": "auto",
            },
        )
        for i in range(2)
    ]
    for entry in entries:
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    manager = async_get_manager(hass)
    agent_ids = {a for a in manager.backup_agents if a.startswith(f"{DOMAIN}.")}
    assert agent_ids == {f"{DOMAIN}.{e.entry_id}" for e in entries}

    assert await hass.config_entries.async_unload(entries[0].entry_id)
    await hass.async_block_till_done()
    remaining = {a for a in manager.backup_agents if a.startswith(f"{DOMAIN}.")}
    assert remaining == {f"{DOMAIN}.{entries[1].entry_id}"}


async def test_excludes_unloaded_and_failed_entries(
    hass: HomeAssistant, mock_s3_client: Any
) -> None:
    """async_get_backup_agents only returns agents for loaded entries."""
    loaded = MockConfigEntry(
        domain=DOMAIN,
        data={
            "endpoint_url": "https://ok.example.com",
            "region": "us-east-1",
            "bucket": "ok",
            "access_key_id": "A",
            "secret_access_key": "s",
            "prefix": "",
            "addressing_style": "auto",
        },
    )
    loaded.add_to_hass(hass)
    assert await hass.config_entries.async_setup(loaded.entry_id)

    not_loaded = MockConfigEntry(domain=DOMAIN, data=dict(loaded.data))
    not_loaded.add_to_hass(hass)

    await hass.async_block_till_done()
    agent_ids = [a.agent_id for a in await async_get_backup_agents(hass)]
    assert agent_ids == [f"{DOMAIN}.{loaded.entry_id}"]
