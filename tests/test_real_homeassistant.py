"""Contract tests against REAL Home Assistant, not stubs.

Every bug that reached a user's dashboard in this project came from calling a
Home Assistant API that does not exist, or behaves differently than assumed:

  1. `homeassistant.util.dt.timedelta` -- does not exist
  2. `async_write_ha_state` before the entity is added -- raises
  3. `issue_registry.async_get_issue` -- no such module-level function
  4. `ImageEntity.__init__()` without `hass` -- hass is REQUIRED
  5. `hass.async_create_task` for a long-running worker -- blocks bootstrap
  6. `FileSelector` returning a relative path -- it returns a UUID

In every case the test suite passed, because the fakes were more generous than
the real thing. These tests remove that possibility for the facts that can be
checked by import.

They skip cleanly when Home Assistant is not installed, so the offline suite
still runs anywhere. When it *is* installed they are exact.

    pip install homeassistant    # or use a prepared venv
    python -m unittest tests.test_real_homeassistant -v
"""

import inspect
import os
import pathlib
import subprocess
import sys
import unittest

def _real_homeassistant_available() -> bool:
    """True only if the genuine package is importable and not shadowed.

    `tests/_mjloader.py` installs stub `homeassistant` modules into
    `sys.modules` so the offline suite can run without Home Assistant. Under
    `unittest discover` those stubs are in place by the time this module is
    imported, so a plain `import homeassistant` returns the stub and every
    assertion here would be testing the fake -- the exact failure mode this
    file exists to eliminate.

    A real install has a `__file__` under site-packages; the stubs are bare
    `types.ModuleType` objects with none.
    """
    if "homeassistant" not in sys.modules:
        try:
            import homeassistant.const  # noqa: F401
        except ImportError:
            return False
    mod = sys.modules.get("homeassistant")
    path = getattr(mod, "__file__", None)
    return bool(path) and "site-packages" in str(path)


def requires_ha(target):
    """Guard these tests against running against a stub.

    Used on classes. The check happens in `setUp`, i.e. at RUN time, because
    `unittest discover` imports every test module before running any of them.
    At import time Home Assistant is still the real package, but
    `tests/test_coordinator_offline.py` installs stub modules into
    `sys.modules` when it runs, and by the time these tests execute,
    `homeassistant.config_entries.ConfigFlow` is a stub. Asserting against
    that would be asserting against a fake, which is the entire problem this
    file exists to prevent.
    """

    def _skip_reason():
        if not _real_homeassistant_available():
            return (
                "real homeassistant is not importable here (absent, or "
                "shadowed by the offline test stubs)"
            )
        return None

    if isinstance(target, type) and issubclass(target, unittest.TestCase):
        original_setUp = target.setUp

        def setUp(self):  # noqa: N802 - unittest naming
            reason = _skip_reason()
            if reason:
                self.skipTest(reason)
            if original_setUp is not unittest.TestCase.setUp:
                original_setUp(self)

        target.setUp = setUp
        return target

    def wrapper(self):
        reason = _skip_reason()
        if reason:
            self.skipTest(reason)
        return target(self)

    wrapper.__name__ = target.__name__
    wrapper.__doc__ = target.__doc__
    return wrapper


@requires_ha
class TestEntityConstructorContracts(unittest.TestCase):
    """Bug 4. The one that stopped the image platform from loading at all."""

    def test_image_entity_requires_hass(self):
        from homeassistant.components.image import ImageEntity

        params = inspect.signature(ImageEntity.__init__).parameters
        self.assertIn(
            "hass",
            params,
            "ImageEntity.__init__ takes hass as its first argument. Calling "
            "super().__init__() without it raises TypeError at every setup.",
        )
        self.assertIs(
            params["hass"].default, inspect.Parameter.empty,
            "hass is REQUIRED, not optional",
        )

    def test_select_and_sensor_take_no_constructor_args(self):
        """They declare no __init__ at all, so a bare super()() is correct.

        The asymmetry is the point. Normalising all three entities to the same
        call is what caused bug 4.
        """
        from homeassistant.components.select import SelectEntity
        from homeassistant.components.sensor import SensorEntity

        for cls in (SelectEntity, SensorEntity):
            self.assertNotIn(
                "__init__",
                cls.__dict__,
                f"{cls.__name__} defines no __init__; passing hass here would "
                "be the error",
            )

    def test_integration_matches_those_contracts(self):
        """Check this repo against the installed Home Assistant."""
        import ast
        from pathlib import Path

        pkg = Path(__file__).resolve().parents[1] / "custom_components" / "mojipic"
        expectations = {"image": True, "select": False, "sensor": False}
        for module, wants_hass in expectations.items():
            tree = ast.parse((pkg / f"{module}.py").read_text())
            call = None
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "__init__"
                    and isinstance(node.func.value, ast.Call)
                ):
                    call = node
            self.assertIsNotNone(call, f"{module}.py has no super().__init__()")
            has_arg = bool(call.args) or any(
                kw.arg == "hass" for kw in call.keywords
            )
            self.assertEqual(
                has_arg, wants_hass,
                f"{module}.py: super().__init__"
                f"{'(hass)' if has_arg else '()'} but the installed "
                "Home Assistant says otherwise",
            )


@requires_ha
class TestFileSelectorContract(unittest.TestCase):
    """Bug 6. Found by running the real integration against real Home Assistant."""

    def test_selector_validates_a_uuid(self):
        """A relative path never reaches integration code.

        FileSelector's validator is just `UUID(data)`, so the failure is a
        plain ValueError. Real Home Assistant surfaces it as

            MultipleInvalid: not a valid value: badly formed hexadecimal
            UUID string at 'file'
        """
        from homeassistant.helpers.selector import FileSelector

        sel = FileSelector({"accept": "image/*"})
        self.assertEqual(sel("a" * 32), "a" * 32)
        with self.assertRaises(ValueError):
            sel("mojipic_images/doorbell.png")

    def test_image_upload_layout(self):
        """Where Home Assistant actually stores what the picker returns."""
        from homeassistant.components.image_upload import const as iu

        global iu_folder
        iu_folder = iu.FOLDER_IMAGE
        self.assertEqual(iu.FOLDER_IMAGE, "image")

        import inspect as _inspect
        from homeassistant.components.image_upload import (
            ImageStorageCollection,
        )

        src = _inspect.getsource(ImageStorageCollection._move_data)
        self.assertIn(
            'media_file = media_folder / "original"',
            src,
            "uploads land at <config>/image/<id>/original",
        )

    def test_resolver_targets_that_layout(self):
        import importlib
        import sys
        from pathlib import Path

        pkg = Path(__file__).resolve().parents[1] / "custom_components" / "mojipic"
        sys.path.insert(0, str(pkg.parent))
        try:
            const = importlib.import_module("mojipic.const")
        finally:
            sys.path.pop(0)
            sys.modules.pop("mojipic.const", None)
        self.assertEqual(const.UPLOAD_ROOT_DIR, iu_folder)
        self.assertEqual(const.UPLOAD_ORIGINAL_NAME, "original")


@requires_ha
class TestBackgroundTaskContract(unittest.TestCase):
    """Bug 5. A normal task held bootstrap for the full five-minute timeout."""

    def test_config_entry_background_task_takes_hass_explicitly(self):
        from homeassistant.config_entries import ConfigEntry

        params = list(
            inspect.signature(ConfigEntry.async_create_background_task).parameters
        )
        self.assertEqual(
            params[:3], ["self", "hass", "target"],
            "ConfigEntry.async_create_background_task(hass, target, name); "
            "omitting hass raises TypeError at setup",
        )

    def test_coordinator_uses_background_tasks(self):
        """Inspect the AST, not the text.

        The module docstring and several comments mention
        `hass.async_create_task` while explaining why it is wrong. A substring
        search therefore fails on its own documentation.
        """
        import ast
        from pathlib import Path

        pkg = Path(__file__).resolve().parents[1] / "custom_components" / "mojipic"
        tree = ast.parse((pkg / "coordinator.py").read_text())
        used = {
            node.attr for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn(
            "async_create_background_task",
            used,
            "the push worker must be a background task",
        )
        self.assertNotIn(
            "async_create_task",
            used,
            "a long-running worker created with async_create_task blocks "
            "async_block_till_done and therefore bootstrap",
        )


@requires_ha
class TestReconfigureFlowAPI(unittest.TestCase):
    """The path the 'Upload image...' option depends on."""

    def test_source_reconfigure_exists(self):
        from homeassistant.config_entries import SOURCE_RECONFIGURE

        self.assertEqual(SOURCE_RECONFIGURE, "reconfigure")

    def test_flow_manager_requires_source_in_context(self):
        """`async_init` raises unless the context carries a source."""
        from homeassistant.config_entries import ConfigEntriesFlowManager

        params = inspect.signature(
            ConfigEntriesFlowManager.async_init
        ).parameters
        self.assertIn("context", params)
        self.assertIsNone(
            params["context"].default,
            "context defaults to None; async_init raises unless the caller "
            "supplies one containing 'source'",
        )
        source = pathlib.Path(
            inspect.getfile(ConfigEntriesFlowManager)
        ).read_text()
        self.assertIn(
            '"source" not in context',
            source,
            "async_init rejects a context without a source; the reconfigure "
            "context must carry SOURCE_RECONFIGURE",
        )

    def test_reconfigure_entry_id_comes_from_context(self):
        """`_reconfigure_entry_id` reads context['entry_id'] -- verbatim."""
        from homeassistant.config_entries import ConfigFlow, SOURCE_RECONFIGURE

        flow = ConfigFlow()
        flow.hass = None
        flow.context = {"source": SOURCE_RECONFIGURE, "entry_id": "abc"}
        self.assertEqual(flow._reconfigure_entry_id, "abc")  # noqa: SLF001

        flow.context = {"source": "user"}
        with self.assertRaises(ValueError):
            flow._reconfigure_entry_id  # noqa: SLF001

    def test_update_reload_and_abort_exists(self):
        from homeassistant.config_entries import ConfigFlow

        self.assertTrue(hasattr(ConfigFlow, "async_update_reload_and_abort"))

    def test_loader_exposes_the_loaded_version(self):
        """Used instead of reading manifest.json, which blocked the loop."""
        from homeassistant.loader import Integration, async_get_loaded_integration

        self.assertTrue(hasattr(Integration, "version"))
        self.assertTrue(callable(async_get_loaded_integration))


class TestContractFileRunsStandalone(unittest.TestCase):
    """Run this file in a clean interpreter.

    Under `unittest discover` the offline stubs shadow Home Assistant, so these
    tests would all skip -- which would make a green run meaningless. This
    shells out with the same interpreter so the real package is imported, and
    asserts the contract file actually passes.
    """

    def test_contracts_hold_against_real_home_assistant(self):
        if os.environ.get("MOJIPIC_CONTRACT_CHILD"):
            self.skipTest("already the child process; do not recurse")

        env = dict(os.environ, MOJIPIC_CONTRACT_CHILD="1")
        here = pathlib.Path(__file__).resolve()
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_real_homeassistant",
             "-v"],
            cwd=str(here.parent.parent),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if proc.returncode != 0:
            self.fail(
                "real-Home-Assistant contract tests failed:\n"
                + proc.stdout[-3000:]
                + proc.stderr[-3000:]
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)