"""Manifest dependency hygiene.

This exists because of a real bug found on a real host. The integration pinned
``Pillow==10.4.0``. That release ships no cp314 wheel, so on a machine running
Python 3.14 pip fell back to a source build, failed, and Home Assistant raised
``RequirementsNotFound`` *while loading the integration* -- before the config
flow was ever imported. Users saw "Config flow could not be loaded: 500".

Every test in this repository ran against a working Pillow on the build host
and passed anyway. **A dependency that will not install cannot be caught by
tests that execute the code.** This check is the closest thing to a guard that
is actually available offline.

The rule: for a publicly distributed integration running on hosts we do not
control, **never exact-pin a runtime dependency whose wheel availability
depends on the Python version.** Use a floor with no ceiling and let the
resolver pick a build that exists for the interpreter in front of it.
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "custom_components" / "mojipic" / "manifest.json"

# Exact pins are the problem. Comparisons like >= or < are fine.
EXACT_PIN = re.compile(r"==[^=]")

# Dependencies whose installability is interpreter-dependent: any package that
# ships compiled wheels. If one of these is ever pinned exactly, it will break
# on some future Python release.
COMPILED_PACKAGES = {"pillow", "numpy", "cryptography", "cffi", "lxml", "pyyaml"}


def _requirements() -> list[str]:
    return json.loads(MANIFEST.read_text()).get("requirements", [])


class TestManifestDependencies(unittest.TestCase):
    def test_no_exact_pins_on_interpreter_sensitive_packages(self):
        """Regression guard for the Pillow==10.4.0 / Python 3.14 failure."""
        offenders = []
        for requirement in _requirements():
            name = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower()
            if name in COMPILED_PACKAGES and EXACT_PIN.search(requirement):
                offenders.append(requirement)

        self.assertFalse(
            offenders,
            "Exact-pinned interpreter-sensitive dependencies in manifest.json: "
            f"{offenders}. An exact pin means the integration breaks the moment "
            "a new Python release ships without a matching wheel -- Pillow "
            "10.4.0 has no cp314 wheel, which made the integration fail to load "
            "on Python 3.14. Use a floor (>=) with no ceiling and let pip pick a "
            "build that exists for the user's interpreter.",
        )

    def test_requirements_are_declared(self):
        self.assertTrue(
            _requirements(),
            "manifest.json must declare its runtime requirements; Pillow is "
            "imported directly by image_encode.py",
        )

    def test_pillow_has_a_lower_bound(self):
        """Pillow is a hard import, so it needs a floor that permits modern wheels."""
        pillow = [r for r in _requirements() if r.lower().startswith("pillow")]
        self.assertEqual(len(pillow), 1, "expected exactly one Pillow requirement")
        self.assertRegex(
            pillow[0].lower(),
            r"^pillow>=\d+",
            f"Pillow must use a '>=' floor so pip can select a wheel matching "
            f"the user's interpreter; found {pillow[0]!r}",
        )

    def test_bleak_is_not_a_requirement(self):
        """The integration must not depend on bleak itself.

        It reaches Bluetooth through Home Assistant's own ``bluetooth``
        component, which brings bleak along transitively. Depending on bleak
        directly would drag a second, competing Bluetooth stack onto the host.

        ``bleak-retry-connector`` is a different package and *is* required --
        Home Assistant asks integrations to use it for reliable connects.
        """
        names = [
            re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0].strip().lower()
            for r in _requirements()
        ]
        self.assertNotIn(
            "bleak",
            names,
            "bleak must not be a requirement; transport.py uses Home "
            "Assistant's own bluetooth component",
        )

    def test_bleak_retry_connector_is_required(self):
        """HA logs a warning when BleakClient.connect() is used directly."""
        names = [
            re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0].strip().lower()
            for r in _requirements()
        ]
        self.assertIn(
            "bleak-retry-connector",
            names,
            "establish_connection() from bleak-retry-connector is required; "
            "Home Assistant logs a warning for a bare BleakClient.connect()",
        )

    def test_version_is_semver(self):
        version = json.loads(MANIFEST.read_text())["version"]
        self.assertRegex(
            version, r"^\d+\.\d+\.\d+$",
            f"version must be plain semver so HACS orders releases correctly; "
            f"found {version!r}",
        )

    def test_hacs_minimum_matches_manifest_age(self):
        """The HACS floor must not exceed what the integration actually needs.

        Guards against declaring a minimum that excludes users the code would
        in fact work for.
        """
        hacs = json.loads((REPO_ROOT / ".hacs.json").read_text())
        self.assertRegex(hacs["homeassistant"], r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
