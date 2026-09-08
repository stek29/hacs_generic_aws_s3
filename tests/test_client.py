"""Tests for the single S3 client factory."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.generic_s3.client import async_get_client


async def _open(**overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "endpoint_url": "https://s3.example.com",
        "region": "eu-central-1",
        "access_key_id": "AKIA",
        "secret_access_key": "secret",
    }
    kwargs.update(overrides)
    async with async_get_client(**kwargs) as client:
        assert client is not None


@pytest.mark.usefixtures("mock_session")
async def test_shared_settings_and_region(
    s3_factory_calls: list[dict[str, Any]],
) -> None:
    """Region is forwarded and both checksum knobs are set to when_required."""
    await _open(region="eu-central-1")

    call = s3_factory_calls[-1]
    assert call["service"] == "s3"
    assert call["region_name"] == "eu-central-1"
    assert call["endpoint_url"] == "https://s3.example.com"
    assert call["aws_access_key_id"] == "AKIA"
    assert call["aws_secret_access_key"] == "secret"

    config = call["config"]
    assert config.request_checksum_calculation == "when_required"
    assert config.response_checksum_validation == "when_required"


@pytest.mark.usefixtures("mock_session")
async def test_addressing_auto_omits_override(
    s3_factory_calls: list[dict[str, Any]],
) -> None:
    """``auto`` must not set an addressing-style override at all."""
    await _open(addressing_style="auto")
    assert s3_factory_calls[-1]["config"].s3 is None


@pytest.mark.usefixtures("mock_session")
@pytest.mark.parametrize("style", ["path", "virtual"])
async def test_addressing_explicit(
    s3_factory_calls: list[dict[str, Any]], style: str
) -> None:
    """``path``/``virtual`` set their literal botocore value."""
    await _open(addressing_style=style)
    assert s3_factory_calls[-1]["config"].s3 == {"addressing_style": style}
