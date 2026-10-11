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
            [self.const.UPLOAD_OPTION, "0", "1"],
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
    """Selecting "Upload image..." must open a flow, not raise."""

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

    def _make(self):
        added = []
        add = _fakeha.add_entity(self.hass, added)
        asyncio.run(self.select.async_setup_entry(self.hass, self.entry, add))
        return added[0]

    def test_selecting_upload_creates_a_fixable_issue(self):
        entity = self._make()
        try:
            asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        except NameError as err:
            self.fail(
                "NameError when the upload option is selected -- this was "
                f"shipping: {err}"
            )
        self.assertTrue(self.state["issues_created"], "no repair issue created")

    def test_upload_issue_is_marked_fixable(self):
        """Without is_fixable the repair card has no button to open the flow."""
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        _domain, _issue_id, kwargs = self.state["issues_created"][0]
        self.assertTrue(kwargs.get("is_fixable"))

    def test_upload_issue_carries_a_translation_key(self):
        """Without one the UI shows a raw key instead of a title."""
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        _domain, _issue_id, kwargs = self.state["issues_created"][0]
        self.assertTrue(kwargs.get("translation_key"))

    def test_selecting_upload_opens_the_flow(self):
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        self.assertTrue(
            self.state["flows_started"],
            "the file picker never opened -- this is the click doing nothing",
        )

    def test_flow_handler_key_matches_the_step_name(self):
        """The handler key must match an `async_step_<key>` on the flow class.

        The earlier version of the flow only defined `async_step_init`, which
        matched no handler key, so the entire class was unreachable and
        clicking a repair silently did nothing.
        """
        config_flow = _mjloader.load_module("config_flow")
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        flow = self.state["flows_started"][0]
        step = f"async_step_{flow['handler_key']}"
        self.assertTrue(
            hasattr(config_flow.MojipicRepairFlow, step),
            f"{config_flow.MojipicRepairFlow.__name__} has no {step}, so the "
            f"flow for handler key {flow['handler_key']!r} is unreachable",
        )

    def test_selecting_upload_does_not_push_an_image(self):
        """It is a side action, not a selection."""
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        self.assertEqual(self.coordinator.displayed, [])


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

    def test_selecting_upload_does_not_become_the_current_option(self):
        entity = self._make()
        asyncio.run(entity.async_select_option(self.const.UPLOAD_OPTION))
        self.assertNotEqual(entity.current_option, self.const.UPLOAD_OPTION)


if __name__ == "__main__":
    unittest.main(verbosity=2)