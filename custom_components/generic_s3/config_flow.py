"""Config flow for the Generic S3 Backup integration.

Standalone flow (not a subclass of the ``aws_s3`` flow). ``user``,
``reconfigure`` and a credential-only ``reauth`` all share one validation path:
normalize -> data-based duplicate check -> enter a temporary client from the
single factory -> ``HeadBucket`` -> close the client on every exit. No probe
objects are written. There is no options flow.
"""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
from typing import Any
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .client import async_get_client
from .const import (
    ADDRESSING_STYLE_AUTO,
    ADDRESSING_STYLES,
    CONF_ACCESS_KEY_ID,
    CONF_ADDRESSING_STYLE,
    CONF_BUCKET,
    CONF_ENDPOINT_URL,
    CONF_PREFIX,
    CONF_REGION,
    CONF_SECRET_ACCESS_KEY,
    DEFAULT_REGION,
    DOMAIN,
)
from .errors import (
    RESULT_INVALID_ENDPOINT_URL,
    GenericS3ValidationError,
    classify_s3_error,
    flow_error_field,
)

_ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}

_STEP_USER = "user"
_STEP_RECONFIGURE = "reconfigure"
_STEP_REAUTH = "reauth_confirm"


def normalize_endpoint_url(raw: str) -> str:
    """Normalize an endpoint URL conservatively.

    Lowercases scheme and host, drops a default port, and collapses an empty or
    ``/`` path to no path so ``http://h`` and ``http://h/`` compare equal.
    Meaningful non-root paths (their case, escaping and a significant trailing
    slash) are preserved. The path is never mined for a bucket or prefix.
    Embedded credentials, query strings and fragments are rejected. HTTP is
    allowed for compatible local deployments.
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL)

    split = urlsplit(candidate)
    scheme = split.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL)
    if split.username or split.password:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL)
    if split.query or split.fragment:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL)

    host = split.hostname
    if not host:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL)
    host = host.lower()

    try:
        port = split.port
    except ValueError as err:
        raise GenericS3ValidationError(RESULT_INVALID_ENDPOINT_URL) from err

    try:
        is_ipv6 = isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        is_ipv6 = False
    netloc = f"[{host}]" if is_ipv6 else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"

    path = "" if split.path == "/" else split.path
    return f"{scheme}://{netloc}{path}"


def normalize_prefix(raw: str | None) -> str:
    """Match upstream prefix normalization: strip leading/trailing ``/`` only."""
    return (raw or "").strip("/")


def _endpoint_host(normalized_endpoint: str) -> str:
    """Return ``host[:port]`` of a normalized endpoint, for a readable title."""
    return urlsplit(normalized_endpoint).netloc


def _canonical(entry_data: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return the credential-free identity tuple used for duplicate detection."""
    return (
        normalize_endpoint_url(entry_data[CONF_ENDPOINT_URL]),
        entry_data[CONF_BUCKET],
        normalize_prefix(entry_data.get(CONF_PREFIX, "")),
    )


class GenericS3ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Generic S3 Backup config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a user-initiated flow."""
        return await self._async_handle_step(_STEP_USER, user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reconfigure flow for an existing entry."""
        return await self._async_handle_step(_STEP_RECONFIGURE, user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start a credential-only reauth flow for the same destination."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect replacement credentials."""
        return await self._async_handle_step(_STEP_REAUTH, user_input)

    # -- shared implementation ----------------------------------------------

    def _schema(self, step_id: str, defaults: Mapping[str, Any]) -> vol.Schema:
        """Build the voluptuous schema for a step."""
        secret_selector = TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
        text_selector = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))

        if step_id == _STEP_REAUTH:
            return vol.Schema(
                {
                    vol.Required(
                        CONF_ACCESS_KEY_ID,
                        default=defaults.get(CONF_ACCESS_KEY_ID, ""),
                    ): text_selector,
                    vol.Required(CONF_SECRET_ACCESS_KEY): secret_selector,
                }
            )

        # ``user`` requires the secret; ``reconfigure`` makes it optional so an
        # empty value keeps the stored secret.
        if step_id == _STEP_USER:
            secret_field: vol.Marker = vol.Required(CONF_SECRET_ACCESS_KEY)
        else:
            secret_field = vol.Optional(CONF_SECRET_ACCESS_KEY, default="")

        return vol.Schema(
            {
                vol.Required(
                    CONF_ENDPOINT_URL, default=defaults.get(CONF_ENDPOINT_URL, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(
                    CONF_REGION, default=defaults.get(CONF_REGION, DEFAULT_REGION)
                ): text_selector,
                vol.Required(
                    CONF_BUCKET, default=defaults.get(CONF_BUCKET, "")
                ): text_selector,
                vol.Required(
                    CONF_ACCESS_KEY_ID,
                    default=defaults.get(CONF_ACCESS_KEY_ID, ""),
                ): text_selector,
                secret_field: secret_selector,
                vol.Optional(
                    CONF_PREFIX, default=defaults.get(CONF_PREFIX, "")
                ): text_selector,
                vol.Required(
                    CONF_ADDRESSING_STYLE,
                    default=defaults.get(CONF_ADDRESSING_STYLE, ADDRESSING_STYLE_AUTO),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(ADDRESSING_STYLES),
                        translation_key=CONF_ADDRESSING_STYLE,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def _async_handle_step(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        """Render or process one of the three flow steps."""
        reconfigure = step_id == _STEP_RECONFIGURE
        reauth = step_id == _STEP_REAUTH

        existing = None
        if reconfigure:
            existing = self._get_reconfigure_entry()
        elif reauth:
            existing = self._get_reauth_entry()

        defaults: dict[str, Any] = {}
        if existing is not None:
            # Prepopulate non-secret fields only; never the stored secret.
            defaults = {
                k: v for k, v in existing.data.items() if k != CONF_SECRET_ACCESS_KEY
            }

        placeholders = (
            {"name": existing.title} if reauth and existing is not None else None
        )

        if user_input is None:
            return self.async_show_form(
                step_id=step_id,
                data_schema=self._schema(step_id, defaults),
                description_placeholders=placeholders,
            )

        errors: dict[str, str] = {}
        ignore_id = existing.entry_id if existing is not None else None
        try:
            settings = self._merge_settings(step_id, existing, user_input)
            endpoint = settings[CONF_ENDPOINT_URL]
            bucket = settings[CONF_BUCKET]
            prefix = settings[CONF_PREFIX]

            if self._find_duplicate(endpoint, bucket, prefix, ignore_id):
                return self.async_abort(reason="already_configured")

            async with async_get_client(
                endpoint_url=endpoint,
                region=settings[CONF_REGION],
                access_key_id=settings[CONF_ACCESS_KEY_ID],
                secret_access_key=settings[CONF_SECRET_ACCESS_KEY],
                addressing_style=settings[CONF_ADDRESSING_STYLE],
            ) as client:
                await client.head_bucket(Bucket=bucket)
        except GenericS3ValidationError as err:
            errors[flow_error_field(err.key)] = err.key
        except Exception as err:  # noqa: BLE001 - classified below, not swallowed
            key = classify_s3_error(err)
            errors[flow_error_field(key)] = key
        else:
            # Re-check for a duplicate created by a concurrent flow while we
            # awaited validation.
            if self._find_duplicate(endpoint, bucket, prefix, ignore_id):
                return self.async_abort(reason="already_configured")

            title = bucket if not prefix else f"{bucket}/{prefix}"
            title = f"{title} ({_endpoint_host(endpoint)})"

            if existing is not None:
                return self.async_update_reload_and_abort(
                    existing,
                    title=title if reconfigure else existing.title,
                    data=settings,
                )
            return self.async_create_entry(title=title, data=settings)

        return self.async_show_form(
            step_id=step_id,
            data_schema=self._schema(step_id, {**defaults, **user_input}),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _merge_settings(
        self,
        step_id: str,
        existing: ConfigEntry | None,
        user_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build the complete, normalized config-entry data for this submission.

        Always a full replacement dict, so a stale prefix cannot survive a
        partial merge.
        """
        base: dict[str, Any] = dict(existing.data) if existing is not None else {}

        if step_id == _STEP_REAUTH:
            base[CONF_ACCESS_KEY_ID] = user_input[CONF_ACCESS_KEY_ID]
            base[CONF_SECRET_ACCESS_KEY] = user_input[CONF_SECRET_ACCESS_KEY]
            base[CONF_ENDPOINT_URL] = normalize_endpoint_url(base[CONF_ENDPOINT_URL])
            base[CONF_PREFIX] = normalize_prefix(base.get(CONF_PREFIX, ""))
            base.setdefault(CONF_REGION, DEFAULT_REGION)
            base.setdefault(CONF_ADDRESSING_STYLE, ADDRESSING_STYLE_AUTO)
            return base

        merged: dict[str, Any] = {**base, **user_input}
        merged[CONF_ENDPOINT_URL] = normalize_endpoint_url(
            user_input[CONF_ENDPOINT_URL]
        )
        merged[CONF_REGION] = (
            user_input.get(CONF_REGION) or ""
        ).strip() or DEFAULT_REGION
        merged[CONF_BUCKET] = user_input[CONF_BUCKET]
        merged[CONF_ACCESS_KEY_ID] = user_input[CONF_ACCESS_KEY_ID]
        merged[CONF_ADDRESSING_STYLE] = user_input.get(
            CONF_ADDRESSING_STYLE, ADDRESSING_STYLE_AUTO
        )
        # Persist the normalized prefix key unconditionally, including "".
        merged[CONF_PREFIX] = normalize_prefix(user_input.get(CONF_PREFIX, ""))

        supplied_secret = user_input.get(CONF_SECRET_ACCESS_KEY, "")
        if supplied_secret:
            merged[CONF_SECRET_ACCESS_KEY] = supplied_secret  # no trimming
        elif existing is not None:
            merged[CONF_SECRET_ACCESS_KEY] = existing.data[CONF_SECRET_ACCESS_KEY]
        else:
            merged[CONF_SECRET_ACCESS_KEY] = supplied_secret

        return merged

    def _find_duplicate(
        self,
        endpoint: str,
        bucket: str,
        prefix: str,
        ignore_entry_id: str | None,
    ) -> bool:
        """Return True if another entry shares ``(endpoint, bucket, prefix)``."""
        target = (endpoint, bucket, prefix)
        for entry in self._async_current_entries(include_ignore=False):
            if entry.entry_id == ignore_entry_id:
                continue
            try:
                if _canonical(entry.data) == target:
                    return True
            except GenericS3ValidationError:
                continue
        return False
