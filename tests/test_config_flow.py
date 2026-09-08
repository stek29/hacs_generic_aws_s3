"""Config-flow tests: user, reconfigure and reauth."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    SSLError,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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

pytestmark = pytest.mark.usefixtures("mock_session")

USER_INPUT: dict[str, Any] = {
    CONF_ENDPOINT_URL: "HTTPS://S3.Example.com:443/",
    CONF_REGION: "us-east-1",
    CONF_BUCKET: "ha-backups",
    CONF_ACCESS_KEY_ID: "AKIAEXAMPLE",
    CONF_SECRET_ACCESS_KEY: "  s3cr3t  ",
    CONF_PREFIX: "/ha/nested/",
    CONF_ADDRESSING_STYLE: "auto",
}


def _client_error(code: str, status: int, headers: dict | None = None) -> ClientError:
    meta: dict[str, Any] = {"HTTPStatusCode": status}
    if headers is not None:
        meta["HTTPHeaders"] = headers
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": meta}, "HeadBucket"
    )


async def _run_user_flow(hass: HomeAssistant, **overrides: Any) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, **overrides}
    )


async def test_user_flow_success_normalizes(hass: HomeAssistant) -> None:
    """A good submission creates an entry with normalized endpoint/prefix."""
    result = await _run_user_flow(hass)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_ENDPOINT_URL] == "https://s3.example.com"
    assert data[CONF_PREFIX] == "ha/nested"
    # secret is stored verbatim, without trimming
    assert data[CONF_SECRET_ACCESS_KEY] == "  s3cr3t  "
    assert "s3cr3t" not in result["title"]
    assert result["title"] == "ha-backups/ha/nested (s3.example.com)"


@pytest.mark.parametrize(
    ("endpoint", "field"),
    [
        ("ftp://s3.example.com", CONF_ENDPOINT_URL),
        ("https://user:pw@s3.example.com", CONF_ENDPOINT_URL),
        ("https://s3.example.com/?x=1", CONF_ENDPOINT_URL),
    ],
)
async def test_user_flow_rejects_bad_endpoint(
    hass: HomeAssistant, endpoint: str, field: str
) -> None:
    """Endpoint syntax problems attach to the endpoint field."""
    result = await _run_user_flow(hass, **{CONF_ENDPOINT_URL: endpoint})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: "invalid_endpoint_url"}


async def test_user_flow_invalid_bucket_name(
    hass: HomeAssistant, mock_s3_client: Any
) -> None:
    """A ParamValidationError for the bucket name attaches to the bucket field."""
    mock_s3_client.head_bucket.side_effect = ParamValidationError(
        report="Invalid bucket name"
    )
    result = await _run_user_flow(hass)
    assert result["errors"] == {CONF_BUCKET: "invalid_bucket_name"}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (EndpointConnectionError(endpoint_url="x"), "cannot_connect"),
        (SSLError(endpoint_url="x", error="bad cert"), "cannot_connect"),
        (_client_error("InternalError", 500), "cannot_connect"),
        (NoCredentialsError(), "invalid_auth"),
        (_client_error("InvalidAccessKeyId", 403), "invalid_auth"),
        (_client_error("SignatureDoesNotMatch", 403), "invalid_auth"),
        (_client_error("AccessDenied", 403), "access_denied"),
        (_client_error("NoSuchBucket", 404), "bucket_unavailable"),
        (_client_error("", 404), "bucket_unavailable"),
        (_client_error("", 400), "unknown"),
        (_client_error("PermanentRedirect", 301), "wrong_region"),
        (
            _client_error("", 400, {"x-amz-bucket-region": "eu-central-1"}),
            "wrong_region",
        ),
    ],
)
async def test_user_flow_error_classification(
    hass: HomeAssistant, mock_s3_client: Any, exc: Exception, expected: str
) -> None:
    """Representative SDK failures map to the documented result keys on base."""
    mock_s3_client.head_bucket.side_effect = exc
    result = await _run_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}


async def test_user_flow_duplicate_aborts(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Same (endpoint, bucket, prefix) as an existing entry aborts the add."""
    mock_config_entry.add_to_hass(hass)
    result = await _run_user_flow(
        hass,
        **{
            CONF_ENDPOINT_URL: "https://s3.example.com",
            CONF_BUCKET: "ha-backups",
            CONF_PREFIX: "ha",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_changes_destination(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reconfigure updates entry data; a blank secret keeps the stored one."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ENDPOINT_URL: "https://other.example.com",
            CONF_REGION: "eu-central-1",
            CONF_BUCKET: "ha-backups",
            CONF_ACCESS_KEY_ID: "AKIANEW",
            CONF_SECRET_ACCESS_KEY: "",
            CONF_PREFIX: "",
            CONF_ADDRESSING_STYLE: "path",
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_ENDPOINT_URL] == "https://other.example.com"
    assert mock_config_entry.data[CONF_ACCESS_KEY_ID] == "AKIANEW"
    # blank replacement secret -> stored secret preserved
    assert mock_config_entry.data[CONF_SECRET_ACCESS_KEY] == "s3cr3t"
    # cleared prefix persists as ""
    assert mock_config_entry.data[CONF_PREFIX] == ""


async def test_reconfigure_replaces_secret_without_trimming(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A supplied non-empty secret replaces the old value verbatim."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_ENDPOINT_URL: "https://s3.example.com",
            CONF_REGION: "us-east-1",
            CONF_BUCKET: "ha-backups",
            CONF_ACCESS_KEY_ID: "AKIAEXAMPLE",
            CONF_SECRET_ACCESS_KEY: "  new-secret  ",
            CONF_PREFIX: "ha",
            CONF_ADDRESSING_STYLE: "auto",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert mock_config_entry.data[CONF_SECRET_ACCESS_KEY] == "  new-secret  "


async def test_reconfigure_duplicate_excludes_self_but_catches_others(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Editing an entry to its own identity is fine; colliding with another aborts."""
    mock_config_entry.add_to_hass(hass)
    other = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_ENDPOINT_URL: "https://taken.example.com",
            CONF_REGION: "us-east-1",
            CONF_BUCKET: "b",
            CONF_ACCESS_KEY_ID: "A",
            CONF_SECRET_ACCESS_KEY: "s",
            CONF_PREFIX: "",
            CONF_ADDRESSING_STYLE: "auto",
        },
    )
    other.add_to_hass(hass)

    base = {
        CONF_REGION: "us-east-1",
        CONF_ACCESS_KEY_ID: "AKIAEXAMPLE",
        CONF_SECRET_ACCESS_KEY: "",
        CONF_ADDRESSING_STYLE: "auto",
    }

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **base,
            CONF_ENDPOINT_URL: "https://s3.example.com",
            CONF_BUCKET: "ha-backups",
            CONF_PREFIX: "ha",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **base,
            CONF_ENDPOINT_URL: "https://taken.example.com",
            CONF_BUCKET: "b",
            CONF_PREFIX: "",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_credentials_and_reloads(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth validates and swaps only the credentials for the same entry."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_ACCESS_KEY_ID: "AKIAROTATED", CONF_SECRET_ACCESS_KEY: "rotated"},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_ACCESS_KEY_ID] == "AKIAROTATED"
    assert mock_config_entry.data[CONF_SECRET_ACCESS_KEY] == "rotated"
    # unchanged destination
    assert mock_config_entry.data[CONF_ENDPOINT_URL] == "https://s3.example.com"
    assert mock_config_entry.data[CONF_PREFIX] == "ha"
