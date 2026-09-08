"""Setup, unload, reload and shutdown lifecycle tests."""

from __future__ import annotations

import asyncio
from typing import Any

from botocore.exceptions import ClientError, EndpointConnectionError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.generic_s3 import async_setup_entry
from custom_components.generic_s3.backup import async_get_backup_agents
from custom_components.generic_s3.const import DOMAIN

pytestmark = pytest.mark.usefixtures("mock_session")


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        "HeadBucket",
    )


async def test_setup_success(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_s3_client: Any,
    s3_factory_calls: list[dict[str, Any]],
) -> None:
    """A good entry loads, validates with HeadBucket and stores runtime data."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_s3_client.head_bucket.assert_awaited_once_with(Bucket="ha-backups")
    assert mock_config_entry.runtime_data.client is mock_s3_client
    # auto addressing style -> no override
    assert s3_factory_calls[-1]["config"].s3 is None


async def test_setup_retryable_becomes_not_ready(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_s3_client: Any
) -> None:
    """Connection failure is a transient ConfigEntryNotReady."""
    mock_s3_client.head_bucket.side_effect = EndpointConnectionError(
        endpoint_url="https://s3.example.com"
    )
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failure_triggers_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_s3_client: Any
) -> None:
    """Explicit credential rejection raises ConfigEntryAuthFailed and reauth."""
    mock_s3_client.head_bucket.side_effect = _client_error("InvalidAccessKeyId", 403)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_setup_permanent_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_s3_client: Any
) -> None:
    """A missing bucket is a permanent ConfigEntryError, not a retry."""
    mock_s3_client.head_bucket.side_effect = _client_error("NoSuchBucket", 404)
    mock_config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_cancellation_closes_context_and_propagates(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_s3_client: Any,
    s3_context: dict[str, int],
) -> None:
    """Cancellation during setup is re-raised and still closes the client."""
    mock_s3_client.head_bucket.side_effect = asyncio.CancelledError

    mock_config_entry.add_to_hass(hass)
    with pytest.raises(asyncio.CancelledError):
        await async_setup_entry(hass, mock_config_entry)

    assert s3_context["entered"] == 1
    assert s3_context["exited"] == 1


async def test_failed_setup_closes_context(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_s3_client: Any,
    s3_context: dict[str, int],
) -> None:
    """A classified setup failure still unwinds the entered client context."""
    mock_s3_client.head_bucket.side_effect = _client_error("NoSuchBucket", 404)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)

    assert s3_context["entered"] == 1
    assert s3_context["exited"] == 1


async def test_unload_closes_client_exactly_once(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    s3_context: dict[str, int],
) -> None:
    """Normal unload closes the owned context once."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert s3_context == {"entered": 1, "exited": 1}


async def test_shutdown_then_unload_is_single_close(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    s3_context: dict[str, int],
) -> None:
    """HA shutdown closes the client; a later unload does not close it again."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    assert s3_context["exited"] == 1

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert s3_context["exited"] == 1


async def test_reload_keeps_agent_identity(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Agent id is stable across a reload because the config entry is retained."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    before = [a.agent_id for a in await async_get_backup_agents(hass)]

    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    after = [a.agent_id for a in await async_get_backup_agents(hass)]

    assert before == after == [f"{DOMAIN}.{mock_config_entry.entry_id}"]
