# Generic S3 Backup for Home Assistant

`generic_s3` exposes an arbitrary, sufficiently S3-compatible object store as a
native Home Assistant **backup destination**. You can then pick it in
**Settings → System → Backups** like any other agent.

- **Domain:** `generic_s3`
- **Version:** `0.1.0`
- **Minimum Home Assistant:** `2026.9.0` (also tested against the latest stable release on every CI run)

## Architecture and upgrade risk

This integration is a **thin adapter around Home Assistant's official
`aws_s3` backup agent**. It does not implement S3 REST calls, SigV4, multipart
transfer, a backup format, scheduling or encryption. It:

- subclasses `homeassistant.components.aws_s3.backup.S3BackupAgent`, changing
  only the integration `domain`, so upload, download, listing, lookup,
  deletion, multipart transfer, progress reporting, pagination, metadata
  handling, caching and object-key construction are all inherited unchanged;
- adds a standalone config flow, a single aiobotocore client factory, and the
  runtime wiring needed to point that agent at a non-AWS endpoint (custom
  endpoint URL, signing region, addressing style).

**Consequence:** it depends on Home Assistant internals that carry no
stability guarantee — the `aws_s3` backup agent's constructor/runtime-data
shape and the core backup platform hooks. A Home Assistant release that
changes those can break this integration until it is updated. Breakage is
designed to be loud (import/attribute errors), never a silent fallback to a
private copy of the transfer code. The bundled CI runs `hassfest`, HACS
validation and the full test suite daily against a fixed minimum **and** the
current stable Home Assistant so regressions surface quickly.

The `aws_s3` integration itself does **not** need to be configured; declaring
it as a dependency is enough for Home Assistant to install its
aiobotocore/botocore requirements.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories**.
2. Add `https://github.com/stek29/hacs_generic_aws_s3`, category **Integration**.
3. Install **Generic S3 Backup**.
4. **Restart Home Assistant.**

### Manual

Copy `custom_components/generic_s3/` into your Home Assistant
`config/custom_components/` directory and restart Home Assistant.

## Adding a destination

**Settings → Devices & Services → Add Integration → Generic S3 Backup.**

| Field | Meaning | Default |
|---|---|---|
| **Endpoint URL** | Full HTTP or HTTPS URL of the S3 API — scheme and host only. No username/password, query string or fragment. The bucket is **not** taken from the path. | — |
| **Region** | The signing region the backend expects (SigV4). Must match the region the bucket was created in or the backend's configured region. Many self-hosted servers accept `us-east-1`. | `us-east-1` |
| **Bucket** | Name of an **existing** bucket. This integration never creates or deletes buckets. | — |
| **Access key ID** | Static S3 access key ID. | — |
| **Secret access key** | Static S3 secret access key. Masked input. | — |
| **Prefix** | Optional key prefix inside the bucket. Empty = bucket root. Only leading/trailing `/` are stripped; internal slashes, case and spaces are kept. | *(empty)* |
| **Addressing style** | `auto` lets botocore decide; `path` forces `endpoint/bucket/key`; `virtual` forces `bucket.endpoint/key`. | `auto` |

On submit the integration normalizes the input, checks it is not a duplicate
of an existing destination, opens a temporary client and calls **`HeadBucket`**.
No objects are written during validation.

### Addressing style and DNS

- **`path`** works with a bare host or IP and needs no extra DNS. Most
  self-hosted gateways expect this.
- **`virtual`** requires that `<bucket>.<host>` resolves and that the server's
  TLS certificate covers it. Only pick it if the backend documents
  virtual-hosted style.
- **`auto`** leaves the choice to botocore. It is never silently rewritten to
  `path`.

### Region

The region is a **signing input**, not a network address. If it does not match
the backend, requests can fail with `SignatureDoesNotMatch`, `PermanentRedirect`
or an `AuthorizationHeaderMalformed` error. Set it to whatever region the
backend signs for (`us-east-1` for many gateways).

### Checksum policy

The client sets botocore's `request_checksum_calculation` and
`response_checksum_validation` to `when_required`. This suppresses botocore's
newer *default* per-request CRC checksums, which several S3-compatible backends
reject. It is a reduction of optional behaviour only — it does **not** disable
all checksum/trailer handling and is **not** a guarantee that every backend is
compatible. Checksum modes are not exposed as options.

### TLS and security

HTTPS is strongly recommended. Plain **HTTP is allowed** for local deployments,
but the access key ID and secret are then sent in clear text on your network.
TLS verification and payload signing are left at botocore defaults; there is no
option to disable certificate verification. A certificate failure is reported
as a connection/security problem, not as a wrong password.

## Permissions

The credentials need, scoped to the destination bucket (and prefix, if your
backend supports prefix-conditioned policies):

| S3 API operation | Purpose |
|---|---|
| `HeadBucket` | Config-flow and startup validation |
| `ListObjectsV2` | Enumerating backups (recursive under the prefix) |
| `GetObject` | Reading metadata and downloading a backup |
| `PutObject` | Small uploads and metadata objects |
| `CreateMultipartUpload`, `UploadPart`, `CompleteMultipartUpload`, `AbortMultipartUpload` | Uploads at/above the multipart threshold (~20 MiB) |
| `DeleteObject` | Deleting a backup and its metadata object |

Notes:

- **API operation names are not IAM action names.** For example the
  `HeadBucket` *operation* is authorized by the `s3:ListBucket` *action*.
- **Passing `HeadBucket` does not prove object or multipart access.** It only
  shows the credentials can address the bucket.
- A **strict prefix-conditioned `s3:ListBucket` policy** (one that only allows
  listing with a matching `s3:prefix`) can make the bucket-wide `HeadBucket`
  validation fail even though normal backup operations would work. If you must
  use such a policy, expect the setup check to report `access_denied`.
- Use a **dedicated bucket and/or prefix and narrowly scoped credentials.**
  This integration deliberately ships **no IAM policy generator**.

## What this destination does and does not do

- Home Assistant owns **backup creation, encryption and restore**. This
  integration only stores the resulting objects. It does **not** change or
  replace Home Assistant's encryption or its recovery-key requirements — keep
  your recovery key.
- Config-entry secrets are stored by Home Assistant in its normal config
  storage. There is **no** promise of encrypted-at-rest config-entry secrets,
  and backup metadata (`*.metadata.json`) is stored **unencrypted** next to
  each backup, exactly as the upstream agent writes it.
- Sharing an identical **bucket + prefix** between two Home Assistant
  installations can cause **object-name collisions and cross-deletes**. Give
  each installation its own bucket or its own non-overlapping prefix.

### Recursive listing / prefix overlap

Listing is recursive, inherited from upstream:

- A **bucket-root** destination (empty prefix) sees backup metadata under
  *every* prefix in the bucket.
- A destination with prefix `ha` sees metadata below a nested destination such
  as `ha/other`.

A **prefix is not an access-control boundary.** For independent installations
or destinations, use **separate buckets** or **disjoint, non-nested** prefixes.
This is preserved upstream behaviour; the integration adds no overlap-detection
logic.

## Troubleshooting ambiguous responses

`HeadBucket` can answer with a bare `400`, `403` or `404` and little detail. The
setup/flow error is chosen conservatively:

| You see | Usual meaning |
|---|---|
| `cannot_connect` | DNS, connection, timeout, throttling or a 5xx. Transient — Home Assistant retries. |
| `invalid_auth` | The backend explicitly rejected the credentials (`InvalidAccessKeyId`, `SignatureDoesNotMatch`, 401). Could also be a wrong region. Triggers re-authentication. |
| `access_denied` | Credentials are valid but not permitted (403 / `AccessDenied`), possibly a prefix-restricted list policy. |
| `bucket_unavailable` | Bucket missing or not visible with these credentials (404 / `NoSuchBucket`). Not a name-syntax error. |
| `wrong_region` | The backend indicated the bucket is in another region (`PermanentRedirect`, `x-amz-bucket-region`). |
| `invalid_endpoint_url` / `invalid_bucket_name` | Local input problem. |
| `unknown` | Unclassified — check the Home Assistant log. |

`SignatureDoesNotMatch` alone does not reveal whether the secret, the region or
the endpoint is wrong; the message stays neutral and re-auth lets you fix the
credentials or the region.

## Backend compatibility

`generic_s3` implements **no provider-specific code**. The servers below are
intended targets, not a guarantee of universal S3 compatibility. A pass for one
version and addressing mode is not a pass for all combinations.

| Backend | Status | Notes |
|---|---|---|
| **VersityGW** | `tested` | v1.8.0, POSIX backend, **path**-style, `us-east-1`. Full lifecycle test (HeadBucket, small + multipart upload, list/get, byte-for-byte download, delete) passes on HA 2026.9.0 and 2026.9.1. |
| **Garage** | `expected compatible — unverified` | S3-compatible; use path-style. Not yet run here. |
| **MinIO** | `expected compatible — unverified` | Widely used with the upstream `aws_s3` agent; not run here. |
| **RustFS** | `expected compatible — unverified` | Not run here. |
| **Storj Gateway** | `unverified` | No successful run yet. No speculative workaround. |

To verify another backend, run the opt-in live test (below) against it and
record the server version and addressing mode.

## Running the live test

Excluded from normal `pytest` runs by the `live` marker. Example with VersityGW
(its POSIX backend needs xattr support, so back it with a Docker **named
volume**, not a macOS bind mount):

```bash
docker volume create vgwdata
docker run --rm -v vgwdata:/data alpine mkdir -p /data/ha-live-test
docker run -d --name versitygw -p 7070:7070 -v vgwdata:/data \
  -e ROOT_ACCESS_KEY_ID=testkey -e ROOT_SECRET_ACCESS_KEY=testsecret \
  versity/versitygw:latest posix /data

GENERIC_S3_LIVE_ENDPOINT=http://127.0.0.1:7070 \
GENERIC_S3_LIVE_BUCKET=ha-live-test \
GENERIC_S3_LIVE_ACCESS_KEY=testkey \
GENERIC_S3_LIVE_SECRET_KEY=testsecret \
GENERIC_S3_LIVE_ADDRESSING_STYLE=path \
pytest tests/test_live_backup.py -m live -v
```

The test uses a unique run prefix in the designated test bucket, never touches
other prefixes, and cleans up only what it created.

## Development

Lint/format only need the pinned ruff:

```bash
uvx ruff@0.16.3 check .
uvx ruff@0.16.3 format --check .
```

Running the test suite needs a full lane: Home Assistant **plus** the `aws_s3`
and `backup` component requirements installed under that release's
`package_constraints.txt` (Home Assistant does not bundle `aiobotocore`). Use
the same helper CI uses:

```bash
python3 scripts/ci_provision.py 2026.9.0 .venv     # or any released HA version
.venv/bin/python -m pytest                         # offline suite (the 'live' test is excluded)
```

CI runs this for the fixed minimum and for the latest stable release resolved at
run time.

## Releasing

Semantic versions, with the manifest and the Git tag in agreement
(`0.1.0` ↔ `v0.1.0`). Manual process:

1. Update `version` in `custom_components/generic_s3/manifest.json` (and
   `pyproject.toml`).
2. Run `ruff check .`, `ruff format --check .`, `pytest`, `hassfest`, HACS
   validation.
3. Merge to `main`.
4. Tag `vX.Y.Z` and create the matching **GitHub Release**.

Any release automation added later must verify that the manifest version and
the tag agree.

## License

[0BSD](LICENSE). This integration imports, and does not redistribute, Home
Assistant's Apache-2.0 `aws_s3` component.
