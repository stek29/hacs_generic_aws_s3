"""Resolve the installed Home Assistant pytest test-helper package.

Home Assistant core publishes its own ``pytest-homeassistant`` package
(release numbers matching ``homeassistant`` exactly) from 2026.11 on. This
project's documented minimum, 2026.8.0, predates that package, so its CI
lane -- and local dev envs pinned to the minimum -- still install the
older, now-unmaintained ``pytest-homeassistant-custom-component`` (see
``scripts/ci_provision.py``). Import through here instead of naming either
package directly so the test suite runs unmodified against both.
"""

from __future__ import annotations

try:
    import pytest_homeassistant.common as _common

    PYTEST_PLUGIN = "pytest_homeassistant"
except ImportError:
    import pytest_homeassistant_custom_component.common as _common

    PYTEST_PLUGIN = "pytest_homeassistant_custom_component"

MockConfigEntry = _common.MockConfigEntry
