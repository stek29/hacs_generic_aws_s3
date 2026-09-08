# VALIDATION

Status of this deliverable, separating **code completion**, **checks actually
executed here**, and **publication / external checks still pending**.

Environment used for local validation: macOS (arm64), Docker 29.7.2, `uv` 0.12.5,
Python 3.14.7. Both Home Assistant lanes run on Python 3.14 because
`homeassistant==2026.9.0` and `2026.9.1` both declare `requires_python >=3.14.2`.

Date of validation runs: 2026-09-08.

---

## 1. What was implemented

A `generic_s3` custom integration that exposes an arbitrary S3-compatible store
as a Home Assistant backup destination, built as a **thin adapter** over Home
Assistant's official `aws_s3` backup agent.

| Area | Implementation |
|---|---|
| Backup agent | `custom_components/generic_s3/backup.py` subclasses `homeassistant.components.aws_s3.backup.S3BackupAgent`, overriding only `domain`. No backup operation is overridden or copied. |
| Platform hooks | Domain-specific `async_get_backup_agents` / `async_register_backup_agents_listener` (`@callback`, synchronous) with a domain-scoped listener list `HassKey("generic_s3.backup_agent_listeners")`. |
| Client factory | `client.py` — one `@asynccontextmanager` used by both config validation and runtime setup. `AioConfig(warm_up_loader_caches=True, request_checksum_calculation="when_required", response_checksum_validation="when_required")`, plus `s3={"addressing_style": ...}` only when not `auto`. `region_name` forwarded. |
| Runtime data | `models.py` — small frozen-ish dataclass `GenericS3RuntimeData(client, _stack)` with an idempotent `async_close()`. No coordinator. |
| Setup lifecycle | `__init__.py` — enters the client through an `AsyncExitStack`, validates with `HeadBucket`, stores runtime data, registers an `EVENT_HOMEASSISTANT_STOP` cleanup and an `async_on_state_change` backup-listener notifier (both via `entry.async_on_unload`). On any failure or cancellation the stack is closed and the exception re-raised; cancellation is never converted to a config error. Unload closes the context exactly once. |
| Config flow | `config_flow.py` — standalone `ConfigFlow` (not an `aws_s3` subclass) with `user`, `reconfigure`, and credential-only `reauth`/`reauth_confirm` sharing one validate path. Conservative URL normalization, upstream-style prefix normalization (`strip("/")` only), data-based duplicate detection on the `(endpoint, bucket, prefix)` tuple excluding the edited entry and re-checked after validation, `async_update_reload_and_abort` for edits, no options flow, no `unique_id`. |
| Error handling | `errors.py` — one `classify_s3_error()` returning a stable key; `flow_error_field()` places it on a field or `base`; `setup_error_from_key()` maps it to `ConfigEntryNotReady` / `ConfigEntryAuthFailed` / `ConfigEntryError`. Every key has an English string in `translations/en.json` under both `config.error`/`config.abort` and `exceptions`. |
| Manifest | `dependencies: ["aws_s3", "backup"]`, `integration_type: "service"`, `iot_class: "cloud_polling"` (matches upstream `aws_s3`; not a cloud-only restriction), `config_flow: true`, `version: "0.1.0"`, real documentation/issue URLs, `codeowners: ["@stek29"]`. No separate `aiobotocore`/`botocore`/`boto3` requirement. |
| Brand images | Original flat artwork (storage bucket + sync arrows, teal/slate — not AWS orange, not HA blue) at `brand/icon.png` (256×256) and `brand/icon@2x.png` (512×512), transparent PNG. |
| Repo | `hacs.json` (`name` + `homeassistant: "2026.9.0"` only), `pyproject.toml` (ruff + pytest config, `license = "0BSD"`), `LICENSE` (0BSD), `.gitignore`, three CI workflows, `.github/dependabot.yml`, `.github/CODEOWNERS`, `scripts/ci_provision.py`, `README.md`, this file. |

### Justified deviations from the handoff

1. **License is 0BSD, not MIT.** Requested by the project owner. 0BSD is
   OSI-approved. `pyproject.toml`, `LICENSE` and the README agree.
2. **Client config adds the two `*_checksum_*` knobs** that upstream `aws_s3`
   (2026.9.0 / 2026.9.1) does not set. The handoff prescribes this in §5 for
   generic-backend compatibility; it is a documented, minimal divergence, not a
   reimplementation. Verified that `AioConfig` in the HA-pinned
   `botocore==1.42.97` accepts these keyword arguments.
3. **`scripts/ci_provision.py`** exists as the "small readable
   installation/resolution helper" §8.4 permits. It is one lane's recipe, not a
   framework.
4. **`errors.py` classifier carries `# noqa: PLR0911, PLR0912`.** The branch and
   return count *is* the classification table.

### Why the minimum is `2026.9.0`

This is a **policy choice carried from the implementation handoff** ("Initial
minimum HA target: `2026.9.0` … Do not promise support for earlier HA releases,
or spend time finding the oldest possible supported release"), confirmed by
testing, **not** a hard technical wall:

- The `aws_s3` component has existed since ~2025.6, and its current backup-agent
  shape (recursive pagination + buffered multipart) settled around 2026.2–2026.3
  (`homeassistant/components/aws_s3/backup.py` history: "Add pagination support"
  #162578, "Improved buffer handling" #162955). The integration would most
  likely run on releases back to roughly 2026.3.
- The two `*_checksum_*` `AioConfig` keys need botocore ≥ 1.36, which HA's
  `aws_s3` aiobotocore pin has satisfied for well over a year — not a limiting
  factor in that range.
- `2026.9.0` is the first release requiring Python ≥ 3.14; earlier releases use
  Python 3.13. The integration code is 3.13-compatible (PEP 695 `type`
  statements only), so an older lane would run on a 3.13 venv.

Lowering the floor is deliberate follow-up work, not a bug fix: pick an
evidenced candidate release, add it as a CI lane (its own Python + SDK env via
`scripts/ci_provision.py`), run the full suite **and** a live lifecycle test
against it, then update `hacs.json`, the README and the CI matrix in lockstep.
Until then `2026.9.0` / `2026.9.1` are the only releases actually verified, and
HA's own support window is short, so a current-release floor is reasonable for
`0.1.0`.

### Upstream limitations preserved (not owned)

- Multipart-abort cleanup catches `BotoCoreError` only — not every `ClientError`
  or cancellation. This integration makes no stronger automatic-cleanup claim.
- Backup object filenames derive from `name` + `date` (upstream
  `suggested_filename`), not `backup_id`.
- Listing is recursive; a bucket-root or parent-prefix destination sees metadata
  under nested prefixes. A prefix is not an access boundary. No overlap
  detection was added.
- `*.metadata.json` objects are stored unencrypted, as upstream writes them.

---

## 2. Upstream sources inspected

Reviewed at tag **`2026.9.0`** (documented minimum) and diffed against
**`2026.9.1`** (current latest stable).

```
homeassistant/components/aws_s3/__init__.py          2026.9.0 == 2026.9.1 (identical)
homeassistant/components/aws_s3/backup.py            2026.9.0 == 2026.9.1 (identical)
homeassistant/components/aws_s3/helpers.py           2026.9.0
homeassistant/components/aws_s3/config_flow.py       2026.9.0
homeassistant/components/aws_s3/coordinator.py       2026.9.0 == 2026.9.1 (identical)
homeassistant/components/aws_s3/const.py             2026.9.0 == 2026.9.1 (identical)
homeassistant/components/aws_s3/manifest.json        2026.9.0 == 2026.9.1  (requirements: aiobotocore==3.7.0)
homeassistant/components/backup/__init__.py          2026.9.0
homeassistant/components/backup/agent.py             2026.9.0  (BackupAgentPlatformProtocol)
homeassistant/components/backup/manager.py           2026.9.0  (_async_add_backup_agent_platform / _async_reload_backup_agents)
homeassistant/components/backup/manifest.json        2026.9.0  (requirements: cronsim==2.7, securetar==2026.4.1)
homeassistant/config_entries.py                      2026.9.0  (async_on_state_change, ConfigEntryState, async_update_reload_and_abort, _get_reauth_entry, _get_reconfigure_entry)
```

Internal contracts relied on:

- `S3BackupAgent.__init__(self, hass, entry)` reads `entry.runtime_data.client`,
  `entry.data["bucket"]`, `entry.data.get("prefix", "")`, `entry.title`,
  `entry.entry_id`.
- `MULTIPART_MIN_PART_SIZE_BYTES = 20 * 2**20`, module-level in `aws_s3.backup`;
  imported by the multipart and live tests, never redefined.
- `BackupAgent.agent_id` is `f"{domain}.{unique_id}"` → stable id
  `generic_s3.<entry_id>`.
- Backup manager registers one listener per domain, calls it once, and re-reads
  `async_get_backup_agents` on each notification; it removes agents whose
  `.domain` matches. Removal notification arrives at `UNLOAD_IN_PROGRESS`.
- `home-assistant/package_constraints.txt` for `2026.9.x` pins
  `botocore==1.42.97` **and** `boto3==1.42.97` (HA core pulls `boto3` via
  `hass-nabucasa` → `pycognito`).

Refresh these when re-implementing against a later release.

---

## 3. Checks executed here

| Check | Command | Result |
|---|---|---|
| Ruff lint | `ruff check .` (ruff 0.16.3, pinned in `pyproject.toml` and both CI workflows) | **PASS** |
| Ruff format | `ruff format --check .` | **PASS** (15 files) |
| Offline tests — min lane | `pytest` with HA **2026.9.0**, Python 3.14.7, aiobotocore 3.7.0, botocore 1.42.97, phacc 0.13.363 | **PASS** — 44 passed, 1 deselected (`live`); coverage 95% |
| Offline tests — latest lane | `pytest` with HA **2026.9.1**, same SDK stack | **PASS** — 44 passed, 1 deselected |
| Import smoke — both lanes | `python -c "from custom_components.generic_s3 import config_flow"` / `... .backup import S3BackupAgent"` (fresh process) | **PASS** |
| Dependency consistency — both lanes | `uv pip check` after provisioning via `scripts/ci_provision.py` | **PASS** — "All installed packages are compatible" (requires the explicit `boto3==1.42.97` / `botocore==1.42.97` alignment under `package_constraints.txt`; without it `boto3 1.43.x` vs the pinned `botocore 1.42.97` conflict) |
| HA-version assertion — both lanes | `scripts/ci_provision.py` asserts installed `homeassistant.const.__version__` == requested | **PASS** |
| hassfest | `ghcr.io/home-assistant/hassfest:latest` on the repo | **PASS** — "Integrations: 1, Invalid integrations: 0" |
| Live backend lifecycle | see §4 | **PASS** |
| Inherited-agent contract | `tests/test_backup.py` — subclass + method-identity for all 5 public ops + both multipart helpers; happy-path small upload / list / get / download / delete on the real `GenericS3RuntimeData` shape with mocked SDK responses; offline multipart path (≥2 parts) via the imported threshold; real Backup-manager discovery, multi-entry, exclusion of unloaded entries, stable id across reload | **PASS** in both lanes |
| Lifecycle | `tests/test_init.py` — setup success; retry (`ConfigEntryNotReady`) / auth (`ConfigEntryAuthFailed` + reauth flow) / permanent (`ConfigEntryError`) failures; cancellation propagation with context close; failed-setup context close; normal unload = exactly-once close; HA-shutdown close then unload = still one close; stable agent id across reload | **PASS** in both lanes |
| Config flow | `tests/test_config_flow.py` — user success + endpoint/prefix normalization + secret stored untrimmed; endpoint syntax errors on the field; invalid bucket name on the field; connection/SSL/5xx/no-creds/auth/access/404/ambiguous-400/region classification on `base`; duplicate abort on create; reconfigure changing destination with blank secret preserved and prefix cleared to `""`; reconfigure replacing secret untrimmed; reconfigure duplicate excludes self, catches another; reauth swaps only credentials and reloads | **PASS** in both lanes |
| Client factory | `tests/test_client.py` — region forwarded; both checksum settings `when_required`; `auto` omits the addressing override (`config.s3 is None`); `path`/`virtual` set `{"addressing_style": ...}` | **PASS** in both lanes |

### CI reproduction

`.github/workflows/tests.yaml` runs `ruff check`, `ruff format --check`, then a
matrix (`fail-fast: false`) of the fixed minimum `2026.9.0` and a
**dynamically resolved** latest stable (from the GitHub releases API, logged as
a `::notice::`), each provisioned by `scripts/ci_provision.py`, which:

1. derives the Python minor from the target release's PyPI `requires_python`;
2. installs that exact HA + a HA-matched `pytest-homeassistant-custom-component`;
3. reads the installed HA's `aws_s3` and `backup` manifests and installs their
   requirements (`aiobotocore==3.7.0`, `cronsim==2.7`, `securetar==2026.4.1`)
   plus `boto3`/`botocore` under that release's `package_constraints.txt`;
4. runs `uv pip check` and asserts the resolved HA version;
5. logs HA / Python / aiobotocore / botocore / phacc versions.

`hassfest.yaml` and `hacs.yaml` are separate workflows. All three run on
`push` (to `main`), `pull_request`, `workflow_dispatch` and a daily `schedule`.
Checkout uses `persist-credentials: false`; `permissions: contents: read`.

---

## 4. Live backend actually tested

| Item | Value |
|---|---|
| Backend | **VersityGW v1.8.0** (build `fd04bc1`), POSIX backend on a Docker named volume |
| Addressing mode | **path** |
| Region | `us-east-1` |
| HA lanes | Passed under **both** 2026.9.0 and 2026.9.1 |
| Operations exercised | `HeadBucket`; small `PutObject` upload + metadata; **genuine multipart** upload of `MULTIPART_MIN_PART_SIZE_BYTES + 512 KiB` verified to issue exactly one `CreateMultipartUpload` and ≥2 `UploadPart` calls (threshold imported from upstream, never lowered); `ListObjectsV2` pagination; `GetObject` metadata + **byte-for-byte** download comparison for both objects; `DeleteObject` of both the backup and its metadata object; post-delete list shows them gone |
| Isolation & cleanup | Unique run prefix `generic-s3-live/<uuid4hex>` in a dedicated bucket; `finally` sweep deletes only keys under that prefix and reports any cleanup failure (none) |
| Command | `GENERIC_S3_LIVE_ENDPOINT=… GENERIC_S3_LIVE_BUCKET=ha-live-test GENERIC_S3_LIVE_ACCESS_KEY=… GENERIC_S3_LIVE_SECRET_KEY=… GENERIC_S3_LIVE_ADDRESSING_STYLE=path pytest tests/test_live_backup.py -m live -v` |

Result: **PASS**. This is the "one representative non-AWS backend lifecycle
test" the handoff requires for initial live validation. Garage, MinIO, RustFS
and Storj Gateway remain **unverified** here.

No live workflow is shipped. If one is added later it must be manual-dispatch
only with protected secrets and must never run untrusted refs or use
`pull_request_target`.

---

## 5. Files created

```
custom_components/generic_s3/__init__.py
custom_components/generic_s3/backup.py
custom_components/generic_s3/client.py
custom_components/generic_s3/config_flow.py
custom_components/generic_s3/const.py
custom_components/generic_s3/errors.py
custom_components/generic_s3/models.py
custom_components/generic_s3/manifest.json
custom_components/generic_s3/translations/en.json
custom_components/generic_s3/brand/icon.png
custom_components/generic_s3/brand/icon@2x.png
tests/conftest.py
tests/test_backup.py
tests/test_client.py
tests/test_config_flow.py
tests/test_init.py
tests/test_live_backup.py
scripts/ci_provision.py
.github/CODEOWNERS
.github/dependabot.yml
.github/workflows/hacs.yaml
.github/workflows/hassfest.yaml
.github/workflows/tests.yaml
hacs.json
pyproject.toml
README.md
LICENSE
VALIDATION.md
.gitignore
```

`errors.py` and `models.py` are the two optional helper modules from the
handoff's structure; both are used. No unused scaffolding.

### Unresolved publication-metadata substitutions

None outstanding for identity: GitHub owner/repo `stek29/hacs_generic_aws_s3`
and maintainer `@stek29` / Viktor Oreshkin were supplied. If the repository is
published under a different slug, update in lockstep:

- `custom_components/generic_s3/manifest.json` → `documentation`, `issue_tracker`, `codeowners`
- `.github/CODEOWNERS`
- `pyproject.toml` → `[project.urls]`
- `README.md` links

---

## 6. Publication / external checks still pending

Not performed — no publish authorization, and the repository does not yet exist
on GitHub.

| Item | State |
|---|---|
| HACS validation (`hacs/action`) | **NOT RUN.** The action authenticates and reaches preflight, then fails with `GitHubNotFoundException` because `stek29/hacs_generic_aws_s3` does not exist yet. Its file-structure checks are gated behind repository existence. `hacs.json`, the manifest, and the local `brand/` images follow current spec (manifest/translations/icons confirmed by hassfest; `brand/` folder per the HA "custom integrations can ship their own brand images" guidance, 2026.3.0+). Re-run once the repo is public. |
| Public GitHub repository | Not created. Needs: public, non-archived, **Issues enabled**, a description, and meaningful topics (e.g. `home-assistant`, `hacs`, `backup`, `s3`, `custom-integration`). |
| Branch protection on `main` | Not configured (owner's choice). |
| First release | Not created. Tag `v0.1.0` + matching GitHub Release, `version` in `manifest.json` already `0.1.0`. |
| HACS default inclusion | Separate later submission; not guaranteed by the repo. |
| Install-through-HACS into `/config/custom_components/generic_s3/`, flow discoverability in a running HA, backup-destination visibility in Backup settings | **NOT RUN** here (no live HA instance). Unit tests exercise flow creation and real Backup-manager discovery with mocked network, which is not the same as a HACS install. |
| Additional live backends (Garage / MinIO / RustFS / Storj) | **NOT RUN.** |

Nothing in this repository was published, and no account or repository settings
were changed.
