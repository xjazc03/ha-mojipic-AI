"""Manifest dependencies must not add failure modes without adding capability.

v1.1.6 added `image_upload` to `dependencies` because the upload form needs
Home Assistant's file picker. That was wrong, and it took the whole
integration down on the owner's machine: after updating, MojiPic could no
longer be discovered or talked to at all.

The reason is visible in Home Assistant's own bootstrap:

    # bootstrap.py
    image_upload as image_upload_import,
        # noqa: F401 - not named pre_import since it has requirements

`image_upload` is imported unconditionally at startup precisely because it
carries requirements. It is a `integration_type: system` integration, so
Home Assistant has *already* loaded it, installed its `Pillow==12.3.0`, and
registered `/api/image/upload` long before any custom integration runs.

Declaring it as a dependency therefore bought nothing, and cost real
reliability: it turned a guaranteed-available system integration into a hard
setup-ordering edge. If that requirement cannot be resolved in the running
environment -- Home Assistant OS installs into a read-only container -- the
dependency fails and `mojipic` is never set up at all. No entities, no
discovery, no transport.

These tests pin that lesson. The general rule: depend on an integration only
when this code imports from it or calls a service it registers.
"""

import json
import unittest
from pathlib import Path

MANIFEST = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "mojipic" / "manifest.json"
)

# Integrations Home Assistant imports at bootstrap whether or not a custom
# integration names them. Source: homeassistant/bootstrap.py, the
# unconditional import block whose comment reads
# "not named pre_import since it has requirements".
#
# `http` is deliberately NOT in this set. It is bootstrap-imported too, but it
# has been a dependency since v1.0.0, carries no exact-pin requirement, and
# removing it now would be an unrelated change while the owner is trying to
# restore function. The set below is the dangerous one: these carry their own
# requirements and nothing in this integration imports them.
ALWAYS_LOADED_WITH_REQUIREMENTS = {
    "image_upload",
    "recorder",
    "http",
}

# Declared here for context, not enforced: lovelace, frontend, onboarding,
# history, logbook, diagnostics, person, group, file_upload,
# device_automation, default_config.


class TestManifestDependencies(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(MANIFEST.read_text())

    def test_no_always_loaded_integration_is_a_dependency(self):
        declared = set(self.manifest.get("dependencies", []))
        # `http` is exempt: long-standing, and it carries no exact pin.
        redundant = declared & (ALWAYS_LOADED_WITH_REQUIREMENTS - {"http"})
        self.assertEqual(
            redundant,
            set(),
            f"{sorted(redundant)} are imported unconditionally by "
            "homeassistant/bootstrap.py. Naming them as dependencies adds a "
            "setup-ordering edge for no benefit, and if their requirements "
            "cannot be resolved in the running environment the whole "
            "integration fails to set up. That is what v1.1.6 did with "
            "image_upload, and it stopped the integration loading entirely.",
        )

    def test_dependencies_are_exactly_the_imported_helpers(self):
        """Guard against both omission and creeping additions."""
        self.assertEqual(
            sorted(self.manifest.get("dependencies", [])),
            ["bluetooth", "bluetooth_adapters", "http", "image"],
        )

    def test_requirements_are_never_exactly_pinned(self):
        """An exact pin on a compiled package is unresolvable on new Python.

        `Pillow==10.4.0` shipped as v1.0.1 had no cp314 wheel, which is what
        Home Assistant OS 2026.10 runs.
        """
        for requirement in self.manifest.get("requirements", []):
            self.assertNotIn(
                "==",
                requirement,
                f"{requirement} is exactly pinned; compiled packages must use "
                "a floor (>=) so a matching wheel can be found",
            )

    def test_version_is_present_and_parseable(self):
        version = self.manifest.get("version", "")
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main(verbosity=2)