"""Pin the real Home Assistant base-class constructor signatures.

The HAOS OpenCode diagnosis found this:

    TypeError: ImageEntity.__init__() missing 1 required positional
               argument: 'hass'
    at /config/custom_components/mojipic/image.py, line 30, in __init__

and attributed it to "a Core 2026.10 entity-constructor change". That is
wrong, and the correction matters:

  * `ImageEntity.__init__(self, hass, verify_ssl=False)` has required `hass`
    since at least Home Assistant 2024.10.0. Verified against four release
    tags; the signature never changed.
  * v1.1.2 added a bare `super().__init__()` to image.py. v1.0.6 and v1.1.0
    had no `super().__init__()` call at all and set `self.hass` manually,
    which worked.

So this was my regression, not a Core change. It is the fourth time this
project shipped something a permissive test stub failed to catch, and the
third distinct pattern:

  1. an attribute that does not exist on a module
  2. an entity lifecycle violation
  3. a method on the wrong object
  4. a constructor whose arity the stub did not model

The general lesson is that stubs must be as strict as the thing they stand in
for, or they actively conceal defects. These tests are the concrete guard for
constructor arity; the fake now models the real signatures too.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _fakeha  # noqa: E402
import _mjloader  # noqa: E402


def _source(name: str) -> str:
    return (Path(_mjloader.PKG_PATH) / f"{name}.py").read_text()


def _base_call(module: str) -> str:
    """Return the argument list passed to super().__init__ in `module`.

    Only considers real code: the constructors carry long comment blocks that
    mention `super().__init__()` while explaining why it is wrong, so a naive
    search finds the prose first.
    """
    src = _source(module)
    start = src.index("def __init__(self,")
    for line in src[start:].split("\n")[1:]:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("super().__init__"):
            return stripped
    raise AssertionError(f"{module}.py has no super().__init__() call")


class TestImageEntitySignature(unittest.TestCase):
    """The one that broke: hass is a REQUIRED positional argument."""

    def test_image_passes_hass_to_super(self):
        self.assertEqual(
            _base_call("image"),
            "super().__init__(hass)",
            "ImageEntity.__init__(self, hass, verify_ssl=False) requires "
            "hass. A bare super().__init__() raises TypeError at every setup.",
        )

    def test_image_does_not_reassign_hass(self):
        """The base class owns it; reassigning is wrong once super() runs."""
        body = _source("image").split("def __init__(self,")[1].split("def ")[0]
        self.assertNotIn(
            "self.hass = hass",
            body,
            "ImageEntity sets self.hass itself; re-assigning it hides the "
            "constructor contract",
        )

    def test_image_constructor_documents_the_contract(self):
        """A future reader must not 'fix' this back to a bare call."""
        body = _source("image").split("def __init__(self,")[1].split("def ")[0]
        self.assertIn("REQUIRED positional argument", body)


class TestSelectAndSensorSignatures(unittest.TestCase):
    """SelectEntity and SensorEntity declare no __init__ at all.

    Verified against core/dev. They inherit `Entity`, which has no __init__,
    so the MRO reaches object.__init__ -- which takes no arguments. Passing
    hass here would be the error here, not the omission.
    """

    def test_select_calls_super_with_no_arguments(self):
        self.assertEqual(_base_call("select"), "super().__init__()")

    def test_sensor_calls_super_with_no_arguments(self):
        self.assertEqual(_base_call("sensor"), "super().__init__()")

    def test_the_three_platforms_are_not_uniform(self):
        """Pin the asymmetry itself.

        It is tempting to 'normalise' all three to the same call. Doing so
        breaks two of them. This test exists to make that visible.
        """
        self.assertNotEqual(_base_call("image"), _base_call("select"))


class TestWorkerShutsDownCleanly(unittest.TestCase):
    """A 20 ms poll loop kept the worker alive past shutdown.

    Home Assistant logged:

        Task<MojipicCoordinator._async_worker()> was still running after final
        writes shutdown stage; Integrations should cancel non-critical tasks
        when receiving the stop event

    and it slowed restarts.
    """

    def test_worker_parks_on_an_event_not_a_sleep(self):
        src = _source("coordinator")
        worker = src.split("async def _async_worker")[1].split("\n    async def")[0]
        self.assertNotIn(
            "asyncio.sleep(0.02)",
            worker,
            "the 20 ms poll loop is what kept the task alive at shutdown",
        )
        self.assertIn("await self._push_wakeup.wait()", worker)

    def test_queueing_a_push_wakes_the_worker(self):
        src = _source("coordinator")
        queue = src.split("async def async_display")[1].split("\n    async def")[0]
        self.assertIn(
            "self._push_wakeup.set()",
            queue,
            "without this the worker stays parked and pushes never happen",
        )

    def test_stopping_wakes_the_worker(self):
        """Otherwise cancellation waits on an event nobody sets."""
        src = _source("coordinator")
        stop = src.split("async def async_stop")[1].split("\n    def ")[0]
        self.assertIn("self._push_wakeup.set()", stop)

    def test_event_is_created(self):
        src = _source("coordinator")
        self.assertIn("self._push_wakeup = asyncio.Event()", src)

    def test_clear_happens_before_wait(self):
        """Clear-then-wait, or the worker spins after the first push."""
        src = _source("coordinator")
        worker = src.split("async def _async_worker")[1].split("\n    async def")[0]
        self.assertLess(
            worker.index("self._push_wakeup.clear()"),
            worker.index("await self._push_wakeup.wait()"),
        )


class TestGuardIsNotSilent(unittest.TestCase):
    """`if self.hass is None: return` must not swallow writes invisibly.

    The HAOS report noted this guard "is masking a genuine thread-safety
    problem rather than solving it ... A guard that silently swallows a state
    write is worse than the error it hides." That is fair: while the
    constructor was broken, `hass` really was None on every signal and this
    branch swallowed them all without a word.
    """

    def test_sensor_guard_is_reachable_only_before_platform_setup(self):
        src = _source("sensor")
        handler = src.split("def _handle_update")[1].split("def ")[0]
        self.assertIn("if self.hass is None:", handler)

    def test_sensor_uses_the_thread_safe_write(self):
        """The real fix for the SyncWorker spam, not the guard."""
        src = _source("sensor")
        handler = src.split("def _handle_update")[1].split("def ")[0]
        self.assertIn("schedule_update_ha_state()", handler)
        self.assertNotIn("async_write_ha_state()", handler)


if __name__ == "__main__":
    unittest.main(verbosity=2)