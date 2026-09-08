"""Opt-in end-to-end lifecycle test against a real S3-compatible backend.

Excluded from ordinary ``pytest`` runs by the ``live`` marker. It uses the
integration's own client factory and the actual inherited backup agent - never a
parallel raw-S3 implementation.

Run it with a reachable backend. VersityGW's POSIX backend needs a filesystem
with xattr support, so back it with a Docker named volume rather than a macOS
bind mount:

    docker volume create vgwdata
    docker run --rm -v vgwdata:/data alpine mkdir -p /data/ha-live-test
    docker run -d --name versitygw -p 7070:7070 -v vgwdata:/data \\
      -e ROOT_ACCESS_KEY_ID=testkey -e ROOT_SECRET_ACCESS_KEY=testsecret \\
      versity/versitygw:latest posix /data

    GENERIC_S3_LIVE_ENDPOINT=http://127.0.0.1:7070 \\
    GENERIC_S3_LIVE_BUCKET=ha-live-test \\
    GENERIC_S3_LIVE_ACCESS_KEY=testkey \\
    GENERIC_S3_LIVE_SECRET_KEY=testsecret \\
    GENERIC_S3_LIVE_ADDRESSING_STYLE=path \\
    pytest tests/test_live_backup.py -m live -v
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
import os
import uuid

from homeassistant.components.aws_s3.backup import MULTIPART_MIN_PART_SIZE_BYTES
from homeassistant.components.backup import AgentBackup
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.generic_s3.backup import S3BackupAgent
from custom_components.generic_s3.client import async_get_client
from custom_components.generic_s3.const import DOMAIN
from custom_components.generic_s3.models import GenericS3RuntimeData

pytestmark = [pytest.mark.live, pytest.mark.usefixtures("socket_enabled")]

_ENV = "GENERIC_S3_LIVE_ENDPOINT"


def _cfg() -> dict[str, str]:
    try:
        return {
            "endpoint": os.environ[_ENV],
            "bucket": os.environ["GENERIC_S3_LIVE_BUCKET"],
            "access_key": os.environ["GENERIC_S3_LIVE_ACCESS_KEY"],
            "secret_key": os.environ["GENERIC_S3_LIVE_SECRET_KEY"],
            "region": os.environ.get("GENERIC_S3_LIVE_REGION", "us-east-1"),
            "addressing": os.environ.get("GENERIC_S3_LIVE_ADDRESSING_STYLE", "path"),
        }
    except KeyError as err:  # pragma: no cover - skip path
        pytest.skip(f"{_ENV} and friends not set: missing {err}")


def _make_backup(backup_id: str, size: int, minute: int) -> AgentBackup:
    # Upstream derives object filenames from name + date only, so each backup
    # needs a distinct name/timestamp the way real Home Assistant backups do.
    return AgentBackup(
        addons=[],
        backup_id=backup_id,
        date=f"2026-09-08T00:{minute:02d}:00+00:00",
        database_included=True,
        extra_metadata={"live-test": True, "case": backup_id},
        folders=[],
        homeassistant_included=True,
        homeassistant_version="2026.9.0",
        name=f"generic-s3 live {backup_id}",
        protected=False,
        size=size,
    )


async def test_live_backend_lifecycle(hass: HomeAssistant) -> None:
    """HeadBucket, small + multipart upload, list/get, download, delete."""
    cfg = _cfg()
    run_prefix = f"generic-s3-live/{uuid.uuid4().hex}"
    cleanup_errors: list[str] = []

    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            async_get_client(
                endpoint_url=cfg["endpoint"],
                region=cfg["region"],
                access_key_id=cfg["access_key"],
                secret_access_key=cfg["secret_key"],
                addressing_style=cfg["addressing"],
            )
        )
        await client.head_bucket(Bucket=cfg["bucket"])

        entry = MockConfigEntry(
            domain=DOMAIN,
            title=f"{cfg['bucket']}/{run_prefix} ({cfg['endpoint']})",
            data={
                "endpoint_url": cfg["endpoint"],
                "region": cfg["region"],
                "bucket": cfg["bucket"],
                "access_key_id": cfg["access_key"],
                "secret_access_key": cfg["secret_key"],
                "prefix": run_prefix,
                "addressing_style": cfg["addressing"],
            },
        )
        entry.add_to_hass(hass)
        entry.runtime_data = GenericS3RuntimeData(
            client=client, _stack=AsyncExitStack()
        )
        agent = S3BackupAgent(hass, entry)  # type: ignore[arg-type]

        small_payload = b"small-backup-body\n" * 64
        big_payload = _deterministic_bytes(MULTIPART_MIN_PART_SIZE_BYTES + 512 * 1024)
        cases = {
            "live-small": (
                _make_backup("live-small", len(small_payload), minute=1),
                small_payload,
            ),
            "live-multi": (
                _make_backup("live-multi", len(big_payload), minute=2),
                big_payload,
            ),
        }

        mpu_starts = 0
        part_puts = 0
        real_create = client.create_multipart_upload
        real_upload_part = client.upload_part

        async def _spy_create(**kw: object):
            nonlocal mpu_starts
            mpu_starts += 1
            return await real_create(**kw)

        async def _spy_upload_part(**kw: object):
            nonlocal part_puts
            part_puts += 1
            return await real_upload_part(**kw)

        client.create_multipart_upload = _spy_create
        client.upload_part = _spy_upload_part

        try:
            for backup, payload in cases.values():
                await agent.async_upload_backup(
                    open_stream=_streamer(payload),
                    backup=backup,
                    on_progress=lambda **_: None,
                )

            # The large payload must have gone through a real >= 2-part MPU.
            assert mpu_starts == 1, mpu_starts
            assert part_puts >= 2, part_puts

            listed = {b.backup_id for b in await agent.async_list_backups()}
            assert {"live-small", "live-multi"} <= listed

            for backup_id, (_, payload) in cases.items():
                fetched = await agent.async_get_backup(backup_id)
                assert fetched.backup_id == backup_id
                downloaded = b"".join(
                    [c async for c in await agent.async_download_backup(backup_id)]
                )
                assert downloaded == payload, f"{backup_id} byte mismatch"

            for backup_id in cases:
                await agent.async_delete_backup(backup_id)

            remaining = {b.backup_id for b in await agent.async_list_backups()}
            assert not ({"live-small", "live-multi"} & remaining)
        finally:
            await _sweep(client, cfg["bucket"], run_prefix, cleanup_errors)

    assert not cleanup_errors, f"cleanup problems: {cleanup_errors}"


def _streamer(payload: bytes):
    async def _open_stream() -> AsyncIterator[bytes]:
        async def _chunks() -> AsyncIterator[bytes]:
            for i in range(0, len(payload), 1024 * 1024):
                yield payload[i : i + 1024 * 1024]

        return _chunks()

    return _open_stream


def _deterministic_bytes(length: int) -> bytes:
    block = (b"generic-s3-live-multipart-block-" * 64)[:1024]
    reps = length // len(block) + 1
    return (block * reps)[:length]


async def _sweep(client, bucket: str, prefix: str, errors: list[str]) -> None:
    """Delete only objects created under this run's unique prefix."""
    try:
        paginator = client.get_paginator("list_objects_v2")
        keys: list[str] = []
        async for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/"):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        for key in keys:
            try:
                await client.delete_object(Bucket=bucket, Key=key)
            except Exception as err:  # noqa: BLE001
                errors.append(f"delete {key}: {err}")
    except Exception as err:  # noqa: BLE001
        errors.append(f"list for cleanup: {err}")
