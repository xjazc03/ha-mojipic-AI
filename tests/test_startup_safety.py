"""A failed setup must never wedge Home Assistant.

Found the hard way: an integration that failed to set up left a live
coordinator running. That orphan kept dispatching to entities whose platform
setup had already been abandoned, producing an endless stream of

    RuntimeError: Attribute hass is None for <entity unknown.unknown=unknown>
    RuntimeError: Detected that custom integration 'mojipic' calls
                  async_write_ha_state from a thread other than the event loop

repeating every 60 seconds, which held Core in a restart loop. One bad line of
code -- `dt_util.timedelta`, which does not exist -- became an outage.

These tests pin the containment rules that prevent that class of failure
happening again:

  * `async_start` tears the coordinator down before re-raising
  * `async_setup_entry` removes the entry and stops the coordinator on failure
  * entities never write state before the platform has assigned `hass`
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _mjloader  # noqa: E402

# These are static checks against the integration source. Nothing is executed,
# deliberately: the point of the rules is what the code says, and importing the
# modules would require stubbing Home Assistant for no extra signal.


def _source(name: str) -> str:
    """Read an integration module by its module name, e.g. "coordinator"."""
    return (Path(_mjloader.PKG_PATH) / f"{name}.py").read_text()


class TestSetupContainment(unittest.TestCase):
    """Structural rules. These are the ones that caused the outage."""

    def test_coordinator_start_tears_down_on_failure(self):
        src = _source("coordinator")
        self.assertIn(
            "except BaseException:", src,
            "async_start must catch and clean up before re-raising",
        )
        self.assertIn(
            "await self.async_stop()", src,
            "a failed start must stop the coordinator, not orphan it",
        )
        self.assertIn(
            "raise", src,
            "cleanup must not swallow the original error",
        )

    def test_start_re_raises_rather_than_returning_quietly(self):
        """Silently swallowing would look like a successful setup."""
        start = _source("coordinator").split("async def async_start")[1]
        start = start.split("async def async_stop")[0]
        self.assertIn("raise", start)

    def test_setup_entry_cleans_up_on_failure(self):
        src = _source("__init__")
        self.assertIn(
            "except BaseException:", src,
            "async_setup_entry must clean up if start or platform setup fails",
        )
        self.assertIn(
            "hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)", src,
            "the half-initialised entry must be removed from hass.data",
        )
        self.assertIn("await coordinator.async_stop()", src)

    def test_entities_guard_against_missing_hass(self):
        for module in ("image", "sensor"):
            src = _source(module)
            self.assertIn(
                "if self.hass is None:", src,
                f"{module} must guard async_write_ha_state against an "
                "unassigned hass; otherwise every dispatcher signal raises "
                "'Attribute hass is None for <entity unknown>'",
            )

    def test_no_dt_util_timedelta_anywhere(self):
        """The original cause. homeassistant.util.dt has no `timedelta`."""
        for module in ("coordinator", "select"):
            src = _source(module)
            self.assertNotIn(
                "dt_util.timedelta(", src,
                f"{module} uses dt_util.timedelta, which does not exist",
            )
            self.assertIn("from datetime import timedelta", src)

    def test_unload_still_stops_the_coordinator(self):
        src = _source("__init__")
        self.assertIn("async_unload_entry", src)
        self.assertIn("async_stop", src)


class TestStartupOrdering(unittest.TestCase):
    def test_interval_is_registered_inside_the_guarded_block(self):
        """If the interval were registered outside the try, a later failure
        would leave it scheduled forever."""
        src = _source("coordinator")
        start = src.split("async def async_start")[1].split("async def async_stop")[0]
        try_idx = start.index("try:")
        track_idx = start.index("async_track_time_interval")
        stop_idx = start.index("await self.async_stop()")
        self.assertLess(try_idx, track_idx, "timer must be registered inside try")
        self.assertLess(track_idx, stop_idx, "cleanup must come after registration")

    def test_worker_is_started_before_the_fallible_connect(self):
        start = _source("coordinator").split("async def async_start")[1]
        start = start.split("async def async_stop")[0]
        self.assertLess(start.index("_ensure_worker"), start.index("try:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
