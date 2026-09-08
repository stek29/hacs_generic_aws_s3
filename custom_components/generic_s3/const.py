"""Constants for the Generic S3 Backup integration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from homeassistant.components.aws_s3.const import (
    CONF_ACCESS_KEY_ID,
    CONF_BUCKET,
    CONF_ENDPOINT_URL,
    CONF_SECRET_ACCESS_KEY,
)
from homeassistant.const import CONF_PREFIX
from homeassistant.util.hass_dict import HassKey

DOMAIN: Final = "generic_s3"

# Connection settings kept in config-entry data. The four credential/endpoint
# keys are imported from Home Assistant's official ``aws_s3`` integration so the
# inherited backup agent, which reads ``entry.data[CONF_BUCKET]`` etc. from that
# same namespace, always agrees with what this integration persists. If upstream
# renames one of these keys the import breaks loudly instead of silently
# desynchronising the config entry from the agent.
CONF_REGION: Final = "region"
CONF_ADDRESSING_STYLE: Final = "addressing_style"

DEFAULT_REGION: Final = "us-east-1"

ADDRESSING_STYLE_AUTO: Final = "auto"
ADDRESSING_STYLE_PATH: Final = "path"
ADDRESSING_STYLE_VIRTUAL: Final = "virtual"
ADDRESSING_STYLES: Final = (
    ADDRESSING_STYLE_AUTO,
    ADDRESSING_STYLE_PATH,
    ADDRESSING_STYLE_VIRTUAL,
)

# Domain-specific listener collection. The core Backup manager registers exactly
# one listener per integration domain; this key never collides with the
# ``aws_s3`` integration's own listener list.
DATA_BACKUP_AGENT_LISTENERS: HassKey[list[Callable[[], None]]] = HassKey(
    f"{DOMAIN}.backup_agent_listeners"
)

__all__ = [
    "ADDRESSING_STYLES",
    "ADDRESSING_STYLE_AUTO",
    "ADDRESSING_STYLE_PATH",
    "ADDRESSING_STYLE_VIRTUAL",
    "CONF_ACCESS_KEY_ID",
    "CONF_ADDRESSING_STYLE",
    "CONF_BUCKET",
    "CONF_ENDPOINT_URL",
    "CONF_PREFIX",
    "CONF_REGION",
    "CONF_SECRET_ACCESS_KEY",
    "DATA_BACKUP_AGENT_LISTENERS",
    "DEFAULT_REGION",
    "DOMAIN",
]
