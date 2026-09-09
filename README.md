# Generic S3 Backup for Home Assistant

Use any S3-compatible object store (MinIO, Garage, VersityGW, RustFS, Storj
Gateway, …) as a Home Assistant **backup destination**, selectable under
**Settings → System → Backups**.

It is a thin adapter over Home Assistant's built-in `aws_s3` integration:
`S3BackupAgent` is subclassed with only the integration domain changed, so all
transfer, listing, multipart and metadata handling is inherited. The trade-off
is that it depends on Home Assistant internals with no stability guarantee — a
core release can break it until it's updated. CI runs hassfest, HACS validation,
the test suite, and an end-to-end lifecycle test against a real S3 backend
(VersityGW) daily against the minimum and the latest stable Home Assistant.

**Minimum Home Assistant:** 2026.8.0 — the first release whose `aws_s3`
integration ships `aiobotocore` 3.x, which the client relies on. Earlier
releases are not supported.

## Install

**HACS** → ⋮ → Custom repositories → add
`https://github.com/stek29/hacs_generic_aws_s3` (category *Integration*) →
install → restart Home Assistant → add the **Generic S3 Backup** integration.

Or copy `custom_components/generic_s3/` into `<config>/custom_components/` and
restart.

## Configuration

| Field | Notes |
|---|---|
| Endpoint URL | Scheme + host of the S3 API only (no bucket in the path). HTTPS recommended; plain HTTP is allowed for local use but sends credentials in clear text. |
| Region | The **signing** region the backend expects, not a network address. `us-east-1` works for many self-hosted servers. A wrong value shows up as `invalid_auth` / `wrong_region`. |
| Bucket | Must already exist. This integration never creates or deletes buckets. |
| Access key ID / Secret access key | Static S3 credentials. |
| Prefix | Optional key prefix; empty = bucket root. Only leading/trailing `/` are stripped. |
| Addressing style | `auto` lets the SDK decide. `path` (`host/bucket/key`) suits most gateways. `virtual` (`bucket.host/key`) needs matching DNS and TLS. |

Validation opens a temporary client and calls `HeadBucket`; no objects are
written. `reconfigure` and a credential-only `reauth` flow are supported; on
`reconfigure`, leaving the secret blank keeps the stored one.

## Permissions

Grant the credentials, scoped to the bucket/prefix: `ListBucket` (for
`HeadBucket` + listing), `GetObject`, `PutObject`, `DeleteObject`, and the
multipart actions (`s3:PutObject` covers `CreateMultipartUpload` /
`UploadPart` / `CompleteMultipartUpload` / `AbortMultipartUpload`). Passing the
`HeadBucket` check does not prove object or multipart access. A prefix-scoped
`ListBucket` policy can make that check fail even when backups would work.

## Notes

- Home Assistant owns backup creation, encryption and restore — this only stores
  the resulting objects, and does not change the recovery-key requirement.
- Listing is recursive (inherited from `aws_s3`): a bucket-root or parent-prefix
  destination sees metadata under nested prefixes. **A prefix is not an
  access-control boundary.** Give each Home Assistant install its own bucket or
  a disjoint, non-nested prefix.
- Metadata objects (`*.metadata.json`) are stored unencrypted, as upstream
  writes them.
- On first connect you may see a one-time log warning about a *"blocking call to
  `load_verify_locations`"* naming this integration. It is harmless: `aiobotocore`
  < 3.8.0 (the version Home Assistant currently pins for `aws_s3`) loads CA
  certificates on the event loop, and the built-in `aws_s3` integration logs the
  same thing. It goes away once Home Assistant ships `aiobotocore` ≥ 3.8.0;
  backups are unaffected.

## Backend compatibility

No provider-specific code. Verified: **VersityGW** v1.8.0 (path-style, full
upload/download/delete lifecycle incl. multipart). MinIO, Garage, RustFS and
Storj Gateway are expected to work but are not yet verified here.

## License

[Apache-2.0](LICENSE), matching Home Assistant's `aws_s3` component.
