"""A setup failure must be loud, and entity writes must be loop-safe.

Both of these came from a single incident that went unnoticed for several
releases. The chain was:

  1. `coordinator.async_start()` raised during setup.
  2. My cleanup popped the entry out of `hass.data`, which is correct.
  3. The select entity's "Upload image..." option started a reconfigure flow.
  4. `async_step_reconfigure` found no bundle and called
     `async_abort(reason="reload_required")`.
  5. Home Assistant dismisses an abort dialog immediately.

So the user saw a button that did nothing, with no error in the UI *and*
nothing in the log. Meanwhile the real cause -- a traceback at the top of the
log from a much earlier boot -- was not connected to the symptom by anything.

The thread-safety fix came out of the same log:

  RuntimeError: Detected that custom integration 'mojipic' calls
  async_write_ha_state from a thread other than the event loop
                at custom_components/mojipic/sensor.py:59
"""

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _fakeha  # noqa: E402
import _mjloader  # noqa: E402


def _source(name: str) -> str:
    return (Path(_mjloader.PKG_PATH) / f"{name}.py").read_text()


def _function_body(src: str, header: str) -> str:
    """Return a function's own lines, without the next top-level definition.

    Splitting on "\ndef " is unreliable here because the file uses two blank
    lines between functions, and because the docstrings themselves mention
    `def` in prose. Walk to the next line that starts at column zero instead.
    """
    start = src.index(header)
    out = []
    for line in src[start:].split("\n")[1:]:
        if line and not line[0].isspace():
            break
        out.append(line)
    return "\n".join(out)


class TestNoSilentAbort(unittest.TestCase):
    def test_missing_bundle_is_logged_at_error_level(self):
        """An abort with no log line is indistinguishable from a dead button."""
        src = _source("config_flow")
        abort = src.split('reason="reload_required"')[0]
        window = abort[-800:]
        self.assertIn(
            "_LOGGER.error(",
            window,
            "the reload_required abort must be logged, or it is invisible",
        )

    def test_log_message_names_the_actual_cause(self):
        """The log has to point at async_setup_entry, not just say 'failed'."""
        src = _source("config_flow")
        self.assertIn(
            "async_setup_entry failed",
            src,
            "the error should tell the reader where to look",
        )

    def test_log_message_names_the_entry(self):
        src = _source("config_flow")
        self.assertIn("entry.entry_id", src.split('reason="reload_required"')[0])

    def test_unknown_entry_is_still_reported(self):
        src = _source("config_flow")
        self.assertIn('reason="unknown_entry"', src)


class TestThreadSafeStateWrites(unittest.TestCase):
    def test_sensor_uses_the_scheduled_write(self):
        """`schedule_update_ha_state` is the documented thread-safe form."""
        src = _source("sensor")
        handler = src.split("def _handle_update")[1].split("def ")[0]
        self.assertIn(
            "schedule_update_ha_state()",
            handler,
            "sensor.py must not call async_write_ha_state from a dispatcher "
            "callback; a signal can arrive on an executor thread and Home "
            "Assistant raises 'calls async_write_ha_state from a thread other "
            "than the event loop'",
        )
        self.assertNotIn("async_write_ha_state()", handler)

    def test_sensor_still_guards_missing_hass(self):
        src = _source("sensor")
        handler = src.split("def _handle_update")[1].split("def ")[0]
        self.assertIn("if self.hass is None:", handler)

    def test_select_dispatcher_callback_guards_missing_hass(self):
        src = _source("select")
        handler = src.split("def _handle_images_changed")[1].split("def ")[0]
        self.assertIn(
            "if self.hass is None:",
            handler,
            "the dispatcher subscription is registered in __init__, before "
            "the platform assigns hass, so a signal can arrive early",
        )


class TestVersionIsLogged(unittest.TestCase):
    def test_setup_logs_the_loaded_version(self):
        """A stale tree must be obvious from the log, not from guesswork.

        Home Assistant reads integration files once at boot. Editing them
        afterwards has no effect until restart, and the only clue was a
        traceback whose line numbers no longer matched the file on disk.
        """
        src = _source("__init__")
        setup = src.split("async def async_setup_entry")[1]
        self.assertIn("_LOGGER.info(", setup.split("transport =")[0])
        self.assertIn("_loaded_version(hass)", setup)

    def test_version_helper_does_no_blocking_io(self):
        """Home Assistant rejected the file read, correctly.

        Observed on the live install:

            WARNING (MainThread) [homeassistant.util.loop] Detected blocking
            call to open with args (PosixPath('.../manifest.json'),) inside
            the event loop by custom integration 'mojipic' at
            __init__.py, line 66

        The version must come from the loader's own record instead.
        """
        src = _source("__init__")
        tree = ast.parse(src)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_loaded_version"
        )

        # Inspect executable code only. A textual scan would trip over the
        # docstring, which deliberately quotes the file path and the warning
        # it replaced -- and a test that fails because of its own
        # documentation trains people to ignore red.
        used = {
            node.attr for node in ast.walk(fn)
            if isinstance(node, ast.Attribute)
        } | {
            node.id for node in ast.walk(fn)
            if isinstance(node, ast.Name)
        } | {
            node.func.id for node in ast.walk(fn)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        for forbidden in ("read_text", "open", "load", "loads"):
            self.assertNotIn(
                forbidden,
                used,
                f"_loaded_version calls {forbidden}(); file I/O here blocks "
                "the event loop. loader.Integration.version already holds it.",
            )

        self.assertIn(
            "async_get_loaded_integration",
            used,
            "the version should come from the loader",
        )

    def test_helper_receives_hass(self):
        """The loader needs hass, so the signature must take it."""
        self.assertIn(
            "def _loaded_version(hass: HomeAssistant)",
            _source("__init__"),
        )

    def test_helper_cannot_break_setup(self):
        """A diagnostics helper must never be the thing that fails."""
        body = _source("__init__").split("def _loaded_version")[1]
        self.assertIn(
            "except Exception",
            body,
            "_loaded_version must swallow its own errors",
        )

    def test_setup_passes_hass_to_the_helper(self):
        setup = _source("__init__").split("async def async_setup_entry")[1]
        self.assertIn("_loaded_version(hass)", setup)

class TestNoStaleTimedelta(unittest.TestCase):
    """The bug that started all of this, asserted once more at the top."""

    def test_no_module_level_dt_util_timedelta(self):
        for module in ("coordinator", "select", "sensor", "image", "__init__"):
            src = _source(module)
            self.assertNotIn(
                "dt_util.timedelta(",
                src,
                f"{module}.py calls homeassistant.util.dt.timedelta, which "
                "does not exist. This is the exact line that failed setup.",
            )

    def test_stdlib_timedelta_is_imported_where_used(self):
        for module in ("coordinator", "select"):
            src = _source(module)
            self.assertIn("from datetime import timedelta", src)


class TestFakeMirrorsRealHA(unittest.TestCase):
    """The fakes must never be more generous than Home Assistant.

    This is the meta-test. Every invented API so far -- `dt_util.timedelta`,
    `issue_registry.async_get_issue`, `IssueEntry.async_create_flow` -- was
    blessed by a stub that provided it, so the tests passed while the feature
    could not work. The fake deliberately omits the real ones.
    """

    def test_fake_issue_registry_has_no_module_level_async_get_issue(self):
        fake = _fakeha.install()
        import homeassistant.helpers.issue_registry as ir

        self.assertFalse(
            hasattr(ir, "async_get_issue"),
            "real HA has no module-level async_get_issue; a stub that adds "
            "it hides the bug it was written to catch",
        )
        self.assertTrue(hasattr(ir, "async_get"))
        self.assertTrue(fake is not None)


if __name__ == "__main__":
    unittest.main(verbosity=2)