"""The single S3 client factory for the Generic S3 Backup integration.

Both config-flow validation and runtime setup enter a client through
``async_get_client`` so there is exactly one place that configures the SDK.
The aiobotocore/botocore stack is the one shipped by Home Assistant's official
``aws_s3`` integration (declared via ``dependencies`` in the manifest); this
integration deliberately declares no SDK requirement of its own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiobotocore.client import AioBaseClient as S3Client
from aiobotocore.config import AioConfig
from aiobotocore.session import AioSession

from .const import ADDRESSING_STYLE_AUTO


@asynccontextmanager
async def async_get_client(
    *,
    endpoint_url: str,
    region: str,
    access_key_id: str,
    secret_access_key: str,
    addressing_style: str = ADDRESSING_STYLE_AUTO,
) -> AsyncIterator[S3Client]:
    """Yield an aiobotocore S3 client for the given connection settings.

    ``addressing_style`` of ``"auto"`` omits the override entirely and lets
    botocore decide; ``"path"`` and ``"virtual"`` set their literal botocore
    values. ``"auto"`` is never silently rewritten to ``"path"``.

    ``request_checksum_calculation`` / ``response_checksum_validation`` are set to
    ``"when_required"`` so botocore does not add its newer default per-request
    CRC checksums, which several S3-compatible backends reject. This only reduces
    *optional* checksum behaviour; it is not a claim that every backend is
    compatible or that all checksum/trailer behaviour is disabled. TLS
    verification, payload signing and the signing implementation are left at
    botocore defaults.

    ``warm_up_loader_caches`` is an ``aiobotocore`` 3.x ``AioConfig`` option;
    it is why the integration's minimum Home Assistant is 2026.8.0, the first
    release whose ``aws_s3`` integration pins ``aiobotocore==3.7.0`` (2026.7.x
    and earlier ship ``aiobotocore==2.21.1``, which rejects the keyword).
    """
    config_kwargs: dict[str, object] = {
        "warm_up_loader_caches": True,
        "request_checksum_calculation": "when_required",
        "response_checksum_validation": "when_required",
    }
    if addressing_style != ADDRESSING_STYLE_AUTO:
        config_kwargs["s3"] = {"addressing_style": addressing_style}

    config = AioConfig(**config_kwargs)
    session = AioSession()
    async with session.create_client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        config=config,
    ) as client:
        yield client
