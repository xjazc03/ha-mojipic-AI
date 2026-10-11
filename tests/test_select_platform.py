"""Executable tests for select.py. These run the real platform setup.

The two regressions below both shipped because the earlier tests only read
select.py as text. Text assertions cannot catch a NameError or a lifecycle
violation -- only running the code can. These tests execute the real
`async_setup_entry` and `async_select_option` against fakes that enforce Home
Assistant's rules (state may only be written after the entity is added).

Regression 1 -- the dropdown disappeared
    async_setup_entry called async_refresh_options() before async_add_entities().
    async_refresh_options() ended in async_write_ha_state(), and an entity that
    has not been added has no entity_id, so that raises. The select platform
    failed to load, so the entity vanished from the dashboard while the battery
    and image entities -- different platforms -- kept working.

Regression 2 -- the upload option raised NameError
    _async_open_upload_flow referenced config_entries.SOURCE_REPAIR without
    importing config_entries. Python compiles that fine; it only fails when
    the user clicks "Upload image...".
"""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _fakeha  # noqa: E402
import _mjloader  # noqa: E402


class _Manager:
    def __init__(self, entries=None):
        self._entries = entries if entries is not None else []
        self.uploaded = []

    async def async_list(self):
        return list(self._entries)

    async def async_framebuffer(self, image_id):
        return b"\x00" * 128

    async def async_get(self, image_id):
        return {"path": "/tmp/x.png"}

    async def async_upload(self, raw, filename, image_id=None):
        self.uploaded.append((raw, filename))
        entry = {"id": len(self._entries)}
        self._entries.append(entry)
        return entry


class _Result:
    """Stands in for the coordinator's PushResult."""

    def __init__(self, ok: bool):
        import types

        self.outcome = types.SimpleNamespace(
            ok=ok, value="sent_verified" if ok else "failed"
        )
        self.error = None if ok else "boom"
        self.image_id = 0
        self.packets_sent = 192
        self.duration = 2.0
        self.verification = "readback matched"


class _Coordinator:
    def __init__(self, ok=True):
        self.link_state = "configured"
        self.name_prefix = "Desk"
        self._ok = ok
        self.displayed = []

    async def async_display(self, framebuffer, image_id):
        self.displayed.append(image_id)
        return _Result(self._ok)


class SelectPlatformTest(unittest.TestCase):
    def setUp(self):
        self.state = _fakeha.install()
        self.select = _mjloader.load_module("select")
        self.const = _mjloader.const
        self.hass = _fakeha.FakeHass()
        self.manager = _Manager()
        self.entry = _fakeha.FakeEntry()
        self.hass.data[self.const.DOMAIN] = {
            self.entry.entry_id: {
                "coordinator": _Coordinator(),
                "manager": self.manager,
            }
        }

    def _run_setup(self):
        added = []
        add = _fakeha.add_entity(self.hass, added)
        asyncio.run(
            self.select.async_setup_entry(self.hass, self.entry, add)
        )
        return added

    def test_platform_setup_adds_the_entity(self):
        """The dropdown must appear at all. It previously did not."""
        added = self._run_setup()
        self.assertEqual(len(added), 1, "select platform added no entity")

    def test_platform_setup_does_not_write_state_before_adding(self):
        """The direct cause of the vanished dropdown.

        With the strict fake, writing before add raises NoEntitySpecifiedError,
        which is what made the whole platform fail to load.
        """
        try:
            self._run_setup()
        except _fakeha.NoEntitySpecifiedError as err:
            self.fail(
                "select.py wrote state before adding the entity -- this is the "
                f"crash that removed the dropdown: {err}"
            )
        except Exception as err:  # noqa: BLE001
            self.fail(f"select platform setup raised {err!r}")

    def test_options_include_the_upload_entry(self):
        """The whole point: a way to add an image without YAML."""
        added = self._run_setup()
        self.assertIn(
            self.const.UPLOAD_OPTION,
            added[0].options,
            "the dropdown must offer the upload entry",
        )

    def test_options_are_ids_after_the_upload_entry(self):
        self.manager._entries = [{"id": 0}, {"id": 1}]
        added = self._run_setup()
        self.assertEqual(
            added[0].options,
            [
                self.const.UPLOAD_OPTION,
                self.const.TEST_PATTERN_OPTION,
                self.const.URL_DOWNLOAD_OPTION,
                "0",
                "1",
            ],
        )

    def test_upload_option_is_first(self):
        self.manager._entries = [{"id": 0}]
        added = self._run_setup()
        self.assertEqual(added[0].options[0], self.const.UPLOAD_OPTION)

    def test_health_timer_is_registered(self):
        self._run_setup()
        self.assertEqual(len(self.state["timers"]), 1)
        _action, interval = self.state["timers"][0]
        # Regression guard: raw seconds used to raise here.
        self.assertTrue(hasattr(interval, "total_seconds"))

    def test_unload_callbacks_are_registered(self):
        self._run_setup()
        self.assertTrue(
            self.entry._unload,
            "the dispatcher subscription and timer must be cleaned up on unload",
        )


class UploadOptionTest(unittest.TestCase):
    """Selecting "Upload image..." must open a flow, not raise.

    Regression: it previously called `ir.async_get_issue`, which does not
    exist at module level in `homeassistant.helpers.issue_registry`, so the
    click produced

        module 'homeassistant.helpers.issue_registry' has no attribute
        'async_get_issue'

    in the UI. The whole repairs-based path has been replaced with a
    reconfigure config flow.
    """

    def setUp(self):
        self.state = _fakeha.install()
        self.select = _mjloader.load_module("select")
        self.const = _mjloader.const
        self.hass = _fakeha.FakeHass()
        self.manager = _Manager()
        self.entry = _fakeha.FakeEntry()
        self.coordinator = _Coordinator()
        self.hass.data[self.const.DOMAIN] = {
            self.entry.entry_id: {
                "coordinator": self.coordinator,
                "manager": self.manager,
            }
        }
        self.hass.config_entries._entry_by_id[self.entry.entry_id] = self.entry

    def tearDown(self):
        # Close any coroutine a test scheduled but did not drain, so an
        # un-awaited coroutine never becomes a warning that masks a real
        # scheduling bug later.
        while self.hass.created:
            coro = self.hass.created.pop()
            close = getattr(coro, "close", None)
            if close is not None:
                close()

    def _make(self):
        added = []
        add = _fakeha.add_entity(self.hass, added)
        asyncio.run(self.select.async_setup_entry(self.hass, self.entry, add))
        return added[0]

    def _click_upload(self):
        """Click the option and let any scheduled task actually run."""
        entity = self._make()

        async def go():
            await entity.async_select_option(self.const.UPLOAD_OPTION)
            for coro in self.hass.created:
                await coro

        try:
            asyncio.run(go())
        except (NameError, AttributeError) as err:
            self.fail(
                f"clicking the upload option raised {err!r} -- this is the "
                "error the user saw in the UI"
            )
        return entity

    def test_selecting_upload_opens_the_flow(self):
        """The file picker must actually open."""
        self._click_upload()
        self.assertTrue(
            self.hass.config_entries.flow.flow_inits,
            "no flow was started -- clicking upload does nothing",
        )

    def test_flow_is_started_for_the_right_entry(self):
        self._click_upload()
        init = self.hass.config_entries.flow.flow_inits[0]
        self.assertEqual(init["handler"], self.const.DOMAIN)

    def test_flow_context_carries_source_reconfigure(self):
        """Home Assistant routes on this exact source value."""
        self._click_upload()
        init = self.hass.config_entries.flow.flow_inits[0]
        self.assertEqual(init["context"]["source"], "reconfigure")

    def test_flow_context_carries_the_entry_id(self):
        """Without it the flow cannot find the display to push to."""
        self._click_upload()
        init = self.hass.config_entries.flow.flow_inits[0]
        self.assertEqual(init["context"]["entry_id"], self.entry.entry_id)

    def test_flow_source_matches_the_step_that_exists(self):
        """The source must route to a real step, or the click does nothing.

        Home Assistant dispatches on the source; `async_step_reconfigure` is
        the step that has to be present to receive it.
        """
        config_flow = _mjloader.load_module("config_flow")
        self._click_upload()
        init = self.hass.config_entries.flow.flow_inits[0]
        step = f"async_step_{init['context']['source']}"
        self.assertTrue(
            hasattr(config_flow.MojipicConfigFlow, step),
            f"MojipicConfigFlow has no {step}, so a flow started with source "
            f"{init['context']['source']!r} has nothing to handle it",
        )

    def test_config_flow_owns_the_reconfigure_step(self):
        config_flow = _mjloader.load_module("config_flow")
        self.assertTrue(hasattr(config_flow.MojipicConfigFlow, "async_step_reconfigure"))

    def test_no_repairs_flow_remains(self):
        """The unreachable repairs flow is gone and must not come back."""
        config_flow = _mjloader.load_module("config_flow")
        self.assertFalse(
            hasattr(config_flow, "MojipicRepairFlow"),
            "the repairs flow was unreachable and used non-existent APIs",
        )

    def test_selecting_upload_does_not_push_an_image(self):
        """It is a side action, not a selection."""
        self._click_upload()
        self.assertEqual(self.coordinator.displayed, [])

    def test_selecting_upload_does_not_change_the_selection(self):
        entity = self._make()

        async def go():
            await entity.async_select_option(self.const.UPLOAD_OPTION)
            for coro in self.hass.created:
                await coro

        asyncio.run(go())
        self.assertNotEqual(entity.current_option, self.const.UPLOAD_OPTION)


class StoredImageSelectionTest(unittest.TestCase):
    def setUp(self):
        _fakeha.install()
        self.select = _mjloader.load_module("select")
        self.const = _mjloader.const
        self.hass = _fakeha.FakeHass()
        self.manager = _Manager([{"id": 0}, {"id": 1}])
        self.entry = _fakeha.FakeEntry()
        self.coordinator = _Coordinator()
        self.hass.data[self.const.DOMAIN] = {
            self.entry.entry_id: {
                "coordinator": self.coordinator,
                "manager": self.manager,
            }
        }

    def _make(self):
        added = []
        add = _fakeha.add_entity(self.hass, added)
        asyncio.run(self.select.async_setup_entry(self.hass, self.entry, add))
        return added[0]

    def test_selecting_a_stored_image_pushes_it(self):
        entity = self._make()
        asyncio.run(entity.async_select_option("1"))
        self.assertEqual(self.coordinator.displayed, [1])

    def test_current_option_updates_after_push(self):
        entity = self._make()
        asyncio.run(entity.async_select_option("1"))
        self.assertEqual(entity.current_option, "1")

    def tearDown(self):
        while self.hass.created:
            close = getattr(self.hass.created.pop(), "close", None)
            if close is not None:
                close()


if __name__ == "__main__":
    unittest.main(verbosity=2)