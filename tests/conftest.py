"""Shared fixtures for the Generic S3 Backup test suite."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.generic_s3.const import (
    CONF_ACCESS_KEY_ID,
    CONF_ADDRESSING_STYLE,
    CONF_BUCKET,
    CONF_ENDPOINT_URL,
    CONF_PREFIX,
    CONF_REGION,
    CONF_SECRET_ACCESS_KEY,
    DOMAIN,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]

CONNECTION: dict[str, Any] = {
    CONF_ENDPOINT_URL: "https://s3.example.com",
    CONF_REGION: "us-east-1",
    CONF_BUCKET: "ha-backups",
    CONF_ACCESS_KEY_ID: "AKIAEXAMPLE",
    CONF_SECRET_ACCESS_KEY: "s3cr3t",
    CONF_PREFIX: "ha",
    CONF_ADDRESSING_STYLE: "auto",
}


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: Any) -> None:
    """Load the integration from custom_components/ during tests."""
    return


@pytest.fixture
def mock_s3_client() -> AsyncMock:
    """An aiobotocore S3 client double with the operations the agent uses."""
    client = AsyncMock(name="s3_client")
    client.head_bucket.return_value = {}
    client.put_object.return_value = {}
    client.delete_object.return_value = {}
    client.create_multipart_upload.return_value = {"UploadId": "mpu-1"}
    client.upload_part.side_effect = [
        {"ETag": '"etag-1"'},
        {"ETag": '"etag-2"'},
        {"ETag": '"etag-3"'},
    ]
    client.complete_multipart_upload.return_value = {}
    client.abort_multipart_upload.return_value = {}
    return client


@pytest.fixture
def s3_factory_calls() -> list[dict[str, Any]]:
    """Records every keyword set passed to ``session.create_client``."""
    return []


@pytest.fixture
def s3_context() -> dict[str, int]:
    """Counts client-context enters/exits so double-close can be asserted."""
    return {"entered": 0, "exited": 0}


@pytest.fixture
def mock_session(
    monkeypatch: pytest.MonkeyPatch,
    mock_s3_client: AsyncMock,
    s3_factory_calls: list[dict[str, Any]],
    s3_context: dict[str, int],
) -> MagicMock:
    """Patch the single client factory's ``AioSession`` with a double."""
    session = MagicMock(name="AioSession")

    @asynccontextmanager
    async def _create_client(service: str, **kwargs: Any):
        s3_factory_calls.append({"service": service, **kwargs})
        s3_context["entered"] += 1
        try:
            yield mock_s3_client
        finally:
            s3_context["exited"] += 1

    session.create_client.side_effect = _create_client
    monkeypatch.setattr(
        "custom_components.generic_s3.client.AioSession", lambda: session
    )
    return session


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A config entry for this integration with a full connection payload."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="ha-backups/ha (s3.example.com)",
        data=dict(CONNECTION),
        entry_id="01JGENERICS3ENTRY000000000",
    )


@pytest.fixture
async def backup_manager(hass: HomeAssistant) -> None:
    """Set up the core backup integration so its manager discovers platforms."""
    assert await async_setup_component(hass, "backup", {})
    await hass.async_block_till_done()
