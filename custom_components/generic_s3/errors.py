"""Shared classification of S3 SDK failures.

One classifier is used by both the config flow (which turns a key into a form
error) and runtime setup (which turns the same key into the matching
``ConfigEntry*`` exception). This is a lookup, not an exception framework.
"""

from __future__ import annotations

from botocore.exceptions import (
    ClientError,
    ConnectionError as BotoConnectionError,
    EndpointConnectionError,
    NoCredentialsError,
    ParamValidationError,
    SSLError,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from .const import DOMAIN

# Result keys. Every key has an English translation in translations/en.json,
# both as a config-flow ``error``/``abort`` and as a component-owned setup error.
RESULT_CANNOT_CONNECT = "cannot_connect"
RESULT_INVALID_AUTH = "invalid_auth"
RESULT_ACCESS_DENIED = "access_denied"
RESULT_BUCKET_UNAVAILABLE = "bucket_unavailable"
RESULT_WRONG_REGION = "wrong_region"
RESULT_INVALID_ENDPOINT_URL = "invalid_endpoint_url"
RESULT_INVALID_BUCKET_NAME = "invalid_bucket_name"
RESULT_UNKNOWN = "unknown"

# Keys that belong on a specific form field rather than on ``base``.
_FIELD_ERRORS = {
    RESULT_INVALID_ENDPOINT_URL: "endpoint_url",
    RESULT_INVALID_BUCKET_NAME: "bucket",
}

# botocore ``ClientError`` codes that indicate the request reached a bucket in a
# different signing region than the one supplied.
_WRONG_REGION_CODES = {
    "PermanentRedirect",
    "AuthorizationHeaderMalformed",
    "IllegalLocationConstraintException",
}
# Explicit credential rejections. ``SignatureDoesNotMatch`` on its own does not
# prove which signing input is wrong, so the translated text stays neutral, but
# credentials are the most common cause and routing it through reauth lets the
# user correct them (or the region).
_INVALID_AUTH_CODES = {
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "InvalidSecurity",
    "InvalidToken",
    "401",
    "Unauthorized",
}
_ACCESS_DENIED_CODES = {"AccessDenied", "AllAccessDisabled", "403", "Forbidden"}
_BUCKET_UNAVAILABLE_CODES = {"NoSuchBucket", "404", "NotFound"}


class GenericS3ValidationError(HomeAssistantError):
    """Raised by config-flow validation carrying a classification key."""

    def __init__(self, key: str) -> None:
        """Store the classification key."""
        super().__init__(key)
        self.key = key


def classify_s3_error(err: BaseException) -> str:  # noqa: PLR0911, PLR0912
    """Map an SDK/validation exception to a stable result key.

    Deliberately a flat ladder of ``isinstance`` / code checks: the branch and
    return count is the classification table, not accidental complexity.
    ``HeadBucket`` can return ambiguous 400/403/404 responses; no precise
    diagnosis is invented from a bare status. Programming errors are not hidden
    behind a retryable-outage key.
    """
    if isinstance(err, GenericS3ValidationError):
        return err.key

    if isinstance(err, ParamValidationError):
        if "Invalid bucket name" in str(err):
            return RESULT_INVALID_BUCKET_NAME
        return RESULT_UNKNOWN

    if isinstance(err, NoCredentialsError):
        return RESULT_INVALID_AUTH

    # SSL/certificate problems must read as a connection/security issue, never as
    # an invalid-password issue.
    if isinstance(err, SSLError):
        return RESULT_CANNOT_CONNECT

    if isinstance(err, (EndpointConnectionError, BotoConnectionError)):
        return RESULT_CANNOT_CONNECT

    if isinstance(err, ClientError):
        error = err.response.get("Error", {}) if err.response else {}
        code = str(error.get("Code", ""))
        metadata = err.response.get("ResponseMetadata", {}) if err.response else {}
        status = metadata.get("HTTPStatusCode")
        headers = {k.lower() for k in metadata.get("HTTPHeaders", {})}

        if code in _WRONG_REGION_CODES or (
            "x-amz-bucket-region" in headers and code not in _ACCESS_DENIED_CODES
        ):
            return RESULT_WRONG_REGION
        if code in _INVALID_AUTH_CODES:
            return RESULT_INVALID_AUTH
        if code in _ACCESS_DENIED_CODES or status == 403:
            return RESULT_ACCESS_DENIED
        if code in _BUCKET_UNAVAILABLE_CODES or status == 404:
            return RESULT_BUCKET_UNAVAILABLE
        if status == 400:
            # Ambiguous: a 400 from HeadBucket is not, on its own, a bucket-name
            # syntax error nor a transient outage.
            return RESULT_UNKNOWN
        if isinstance(status, int) and status >= 500:
            return RESULT_CANNOT_CONNECT
        return RESULT_UNKNOWN

    if isinstance(err, ValueError):
        # aiobotocore raises ValueError for an unusable endpoint URL.
        return RESULT_INVALID_ENDPOINT_URL

    return RESULT_UNKNOWN


def flow_error_field(key: str) -> str:
    """Return the form field a result key should attach to (``base`` otherwise)."""
    return _FIELD_ERRORS.get(key, "base")


def setup_error_from_key(key: str) -> Exception:
    """Return the ``ConfigEntry*`` exception runtime setup should raise for a key."""
    if key == RESULT_CANNOT_CONNECT:
        return ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key=RESULT_CANNOT_CONNECT
        )
    if key == RESULT_INVALID_AUTH:
        return ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key=RESULT_INVALID_AUTH
        )
    return ConfigEntryError(translation_domain=DOMAIN, translation_key=key)
