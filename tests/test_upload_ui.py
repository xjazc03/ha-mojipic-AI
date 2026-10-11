"""Upload-UI behaviour: file resolution, repair-flow reachability, dropdown option.

Two real gaps are covered here.

1. ``MojipicRepairFlow`` was unreachable. Home Assistant dispatches to
   ``async_step_<handler_key>``, but the class only defined ``async_step_init``,
   which no handler key ever matched. So clicking through to a repair silently
   did nothing. The tests below assert the step names match the handler keys
   that actually get used.

2. The file selector is not consistent about what it returns: a *picked* file
   comes back as a relative path, an *uploaded* file as a UUID. Both must
   resolve, and neither may escape the config directory.
"""

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _mjloader  # noqa: E402

def _stub_homeassistant():
    """Minimal Home Assistant stand-ins so config_flow.py can be imported."""
    import voluptuous as vol

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class _CF:
        VERSION = 1

        def __init_subclass__(cls, **kw):
            super().__init_subclass__()

    class _Schema(dict):
        def __init__(self, *a, **k):
            super().__init__()

    mod("homeassistant")
    mod("homeassistant.config_entries",
        ConfigEntry=object, ConfigFlow=_CF, OptionsFlow=object,
        RepairsFlow=object, SOURCE_REPAIR="repair")
    mod("homeassistant.core", callback=lambda f: f, HomeAssistant=object,
        ServiceCall=object)
    mod("homeassistant.exceptions", HomeAssistantError=Exception)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.selector",
        TextSelector=lambda *a, **k: "text",
        BooleanSelector=lambda *a, **k: "bool",
        FileSelector=lambda *a, **k: "file")
    mod("homeassistant.helpers.typing", ConfigType=dict)
    mod("homeassistant.helpers.dispatcher",
        async_dispatcher_send=lambda *a, **k: None)
    mod("homeassistant.helpers.event",
        async_track_time_interval=lambda *a, **k: (lambda: None))
    mod("homeassistant.helpers.issue_registry",
        async_create_issue=lambda *a, **k: None,
        async_delete_issue=lambda *a, **k: None,
        async_get_issue=lambda *a, **k: None,
        IssueSeverity=types.SimpleNamespace(WARNING="warning"))
    mod("homeassistant.config_validation", string=str)
    mod("homeassistant.components")
    mod("homeassistant.components.bluetooth",
        callback=lambda f: f,
        BluetoothScanningMode=types.SimpleNamespace(ACTIVE=1),
        async_register_callback=lambda *a, **k: (lambda: None))
    return vol


_stub_homeassistant()

const = _mjloader.const
image_manager = _mjloader.load_module("image_manager")
config_flow = _mjloader.load_module("config_flow")


class _FakeConfig:
    def __init__(self, root):
        self.config_dir = str(root)


class _FakeHass:
    def __init__(self, root):
        self.config = _FakeConfig(root)


class TestResolveUploadPath(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hass = _FakeHass(self.root)
        (self.root / "mojipic_images").mkdir()
        (self.root / ".storage" / "uploads").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def _png(self):
        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (128, 0, 128)).save(buf, format="PNG")
        return buf.getvalue()

    def test_resolves_relative_path(self):
        target = self.root / "mojipic_images" / "doorbell.png"
        target.write_bytes(self._png())
        raw, name = image_manager.resolve_upload_path(
            self.hass, "mojipic_images/doorbell.png"
        )
        self.assertEqual(name, "doorbell.png")
        self.assertEqual(raw, self._png())

    def test_resolves_uploaded_uuid(self):
        uuid = "a" * 32
        (self.root / ".storage" / "uploads" / uuid).write_bytes(self._png())
        raw, name = image_manager.resolve_upload_path(self.hass, uuid)
        self.assertEqual(len(raw), len(self._png()))
        self.assertEqual(len(name), 32)

    def test_resolves_uuid_from_media_dir(self):
        uuid = "b" * 32
        (self.root / "media").mkdir()
        (self.root / "media" / uuid).write_bytes(self._png())
        raw, _ = image_manager.resolve_upload_path(self.hass, uuid)
        self.assertEqual(len(raw), len(self._png()))

    def test_rejects_empty(self):
        with self.assertRaises(image_manager.UploadResolutionError):
            image_manager.resolve_upload_path(self.hass, "")

    def test_reports_missing_file_helpfully(self):
        with self.assertRaises(image_manager.UploadResolutionError) as ctx:
            image_manager.resolve_upload_path(self.hass, "mojipic_images/nope.png")
        self.assertIn("config directory", str(ctx.exception))

    def test_refuses_to_escape_config_dir(self):
        """A traversal path must not be read, even if the file exists."""
        outside = self.root.parent / "outside_secret.png"
        outside.write_bytes(self._png())
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaises(image_manager.UploadResolutionError):
            image_manager.resolve_upload_path(self.hass, "../outside_secret.png")


class TestRepairFlowReachable(unittest.TestCase):
    """The bug: async_step_init matched no handler key, so nothing ran."""

    def test_steps_match_the_handler_keys_we_use(self):
        cls = config_flow.MojipicRepairFlow
        self.assertTrue(
            hasattr(cls, "async_step_upload_image"),
            "async_step_upload_image must exist: this is the handler key used "
            "by the select entity's upload option",
        )
        self.assertTrue(
            hasattr(cls, "async_step_reconnect"),
            "async_step_reconnect must exist: matches the reconnect handler key",
        )

    def test_no_bare_init_step(self):
        """An init step is unreachable unless 'init' is a real handler key."""
        cls = config_flow.MojipicRepairFlow
        self.assertFalse(
            hasattr(cls, "async_step_init"),
            "async_step_init matched no handler key and made the whole class "
            "dead code; remove it or rename it to a real handler key",
        )

    def test_handler_keys_come_from_constants(self):
        self.assertEqual(const.ISSUE_UPLOAD_IMAGE, "upload_image")
        self.assertEqual(const.ISSUE_RECONNECT, "reconnect")


class TestUploadOption(unittest.TestCase):
    def test_upload_option_is_distinct(self):
        """If it looked like an id, selecting it would try to push a slot."""
        self.assertNotEqual(const.UPLOAD_OPTION, "0")
        self.assertFalse(const.UPLOAD_OPTION.isdigit())

    def test_select_offers_upload_option(self):
        source = (
            Path(config_flow.__file__).parent / "select.py"
        ).read_text()
        self.assertIn(
            "options = [UPLOAD_OPTION] + [str(item[\"id\"]) for item in entries]",
            source,
            "the upload entry must be offered in the dropdown",
        )
        self.assertIn(
            "if option == UPLOAD_OPTION:", source,
            "selecting the upload entry must open the upload flow",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
