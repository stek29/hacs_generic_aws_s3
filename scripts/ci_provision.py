#!/usr/bin/env python3
"""Provision one isolated Home Assistant test lane.

Usage: ``ci_provision.py <ha_version> <venv_dir>``

Steps, in order:

1. Derive a compatible Python version from the target HA release's own PyPI
   metadata (``requires_python``) and create the venv with it.
2. Find the ``pytest-homeassistant-custom-component`` release that pins this
   exact HA version (every release pins one ``homeassistant==<version>``
   exactly; see ``_phacc_version_for``) and install it alongside that HA
   version and the test runner. Pinning explicitly, rather than letting the
   resolver pick, avoids it silently backtracking to an unrelated ancient
   release -- with its own ancient, incompatible pytest -- when the matching
   release hasn't been published yet (e.g. right after a new HA release).
3. Read the *installed* HA's ``aws_s3`` and ``backup`` manifests and install
   their requirements under that release's ``package_constraints.txt`` so
   botocore/boto3 stay on HA's pinned versions rather than whatever pip would
   otherwise pick. ``boto3``/``botocore`` are named explicitly because HA core
   pulls ``boto3`` transitively (hass-nabucasa -> pycognito) and the constraints
   file pins it alongside ``botocore``.
4. Run ``pip check``; assert the resolved HA version equals the request; print a
   version summary for the CI log.

This is a readable one-lane helper, not a generic CI framework.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import urllib.request


def _run(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True)


def _capture(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _python_minor_for(ha_version: str) -> str:
    url = f"https://pypi.org/pypi/homeassistant/{ha_version}/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        meta = json.load(resp)
    requires = (meta["info"].get("requires_python") or "").strip()
    print(f"homeassistant=={ha_version} requires_python={requires!r}", flush=True)
    for token in requires.replace(",", " ").split():
        digits = token.lstrip(">=~!<")
        parts = digits.split(".")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{parts[0]}.{parts[1]}"
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _phacc_version_for(ha_version: str, max_lookback: int = 40) -> str | None:
    """Find the phacc release whose pin is ``homeassistant=={ha_version}``.

    Checks the ``max_lookback`` newest releases (newest first, since a match
    -- if published at all -- is always near the tip) and returns ``None``
    if none of them pin this exact HA version yet.
    """
    url = "https://pypi.org/pypi/pytest-homeassistant-custom-component/json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        meta = json.load(resp)

    def _numeric_key(v: str) -> list[int] | None:
        parts = v.split(".")
        if all(p.isdigit() for p in parts):
            return [int(p) for p in parts]
        return None

    versions = sorted(
        (v for v, files in meta["releases"].items() if files and _numeric_key(v)),
        key=_numeric_key,
        reverse=True,
    )

    needle = f"homeassistant=={ha_version}"
    for version in versions[:max_lookback]:
        v_url = (
            "https://pypi.org/pypi/"
            f"pytest-homeassistant-custom-component/{version}/json"
        )
        with urllib.request.urlopen(v_url, timeout=30) as resp:
            v_meta = json.load(resp)
        requires = v_meta["info"].get("requires_dist") or []
        if any(r.strip() == needle for r in requires):
            return version
    return None


def main() -> int:
    """Provision the lane named by ``sys.argv`` and return a process exit code."""
    ha_version, venv_dir = sys.argv[1], sys.argv[2]
    venv = Path(venv_dir)
    py_minor = _python_minor_for(ha_version)
    print(f"::notice::HA {ha_version} -> Python {py_minor}", flush=True)

    phacc_version = _phacc_version_for(ha_version)
    if phacc_version is None:
        print(
            "::error::no pytest-homeassistant-custom-component release pins "
            f"homeassistant=={ha_version} yet",
            flush=True,
        )
        return 1
    print(
        f"::notice::pytest-homeassistant-custom-component=={phacc_version} "
        f"pins homeassistant=={ha_version}",
        flush=True,
    )

    _run("uv", "venv", "--python", py_minor, str(venv))
    py = str(venv / "bin" / "python")

    _run(
        "uv",
        "pip",
        "install",
        "--python",
        py,
        f"homeassistant=={ha_version}",
        f"pytest-homeassistant-custom-component=={phacc_version}",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "pipdeptree",
    )

    site = Path(
        _capture(
            py,
            "-c",
            "import homeassistant,pathlib;"
            "print(pathlib.Path(homeassistant.__file__).parent)",
        ).strip()
    )
    constraints = site / "package_constraints.txt"
    reqs: list[str] = []
    for component in ("aws_s3", "backup"):
        path = site / "components" / component / "manifest.json"
        reqs.extend(json.loads(path.read_text()).get("requirements", []))
    print("component requirements:", reqs, flush=True)

    _run(
        "uv",
        "pip",
        "install",
        "--python",
        py,
        "-c",
        str(constraints),
        *reqs,
        "boto3",
        "botocore",
    )

    _run("uv", "pip", "check", "--python", py)

    resolved = _capture(
        py, "-c", "from homeassistant.const import __version__; print(__version__)"
    ).strip()
    if resolved != ha_version:
        print(f"::error::resolved HA {resolved} != requested {ha_version}")
        return 1

    summary = _capture(
        py,
        "-c",
        "import sys, aiobotocore, botocore, homeassistant;"
        "from homeassistant.const import __version__ as v;"
        "import importlib.metadata as m;"
        "print('HA', v, '| Python', sys.version.split()[0],"
        "'| aiobotocore', aiobotocore.__version__, '| botocore', botocore.__version__,"
        "'| phacc', m.version('pytest-homeassistant-custom-component'))",
    ).strip()
    print(f"::notice::{summary}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
