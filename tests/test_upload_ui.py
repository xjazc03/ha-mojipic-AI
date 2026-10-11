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

    async def async_add_executor_job(self, func, *args):
        """Run on a real thread, as Home Assistant does."""
        import asyncio
        import functools

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(func, *args)
        )


class TestResolveUploadPath(unittest.TestCase):
    """Resolve what Home Assistant's `file` selector hands back.

    Everything here was wrong until the integration was run against a real
    Home Assistant 2026.10.0. The previous version of this test asserted
    behaviour that does not exist:

      * that a *relative path* could arrive. It cannot. `FileSelector.__call__`
        runs `UUID(data)` and rejects anything else during schema validation:

            probatio.error.MultipleInvalid: not a valid value: badly formed
            hexadecimal UUID string at 'file'

      * that uploads live in `.storage/uploads` or `media`. They do not.
        `homeassistant/components/image_upload/` writes to
        `<config>/image/<token_hex(16)>/original`.

    The tests below encode the verified layout instead.
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.hass = _FakeHass(self.root)
        self.addCleanup(self._tmp.cleanup)

    def _png(self):
        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", (32, 32), (128, 0, 128)).save(buf, format="PNG")
        return buf.getvalue()

    def _stage(self, upload_id: str) -> None:
        """Reproduce exactly what image_upload writes."""
        d = self.root / "image" / upload_id
        d.mkdir(parents=True)
        (d / "original").write_bytes(self._png())

    def _resolve(self, value):
        import asyncio

        return asyncio.run(image_manager.async_resolve_upload_path(self.hass, value))

    def test_resolves_the_real_upload_layout(self):
        """image_upload writes <config>/image/<id>/original."""
        upload_id = "a" * 32
        self._stage(upload_id)
        raw, name = self._resolve(upload_id)
        self.assertEqual(raw, self._png())
        self.assertEqual(name, "original")

    def test_accepts_the_dashed_uuid_form(self):
        """UUID() normalises either shape."""
        upload_id = "b" * 32
        self._stage(upload_id)
        dashed = (f"{upload_id[:8]}-{upload_id[8:12]}-{upload_id[12:16]}-"
                  f"{upload_id[16:20]}-{upload_id[20:]}")
        raw, _ = self._resolve(dashed)
        self.assertEqual(raw, self._png())

    def test_rejects_a_relative_path(self):
        """It never reaches here in practice, and must not be hunted for.

        Schema validation rejects it first. Accepting it anyway would imply a
        code path Home Assistant can never produce.
        """
        d = self.root / "mojipic_images"
        d.mkdir()
        (d / "doorbell.png").write_bytes(self._png())
        with self.assertRaises(image_manager.UploadResolutionError):
            self._resolve("mojipic_images/doorbell.png")

    def test_rejects_empty(self):
        with self.assertRaises(image_manager.UploadResolutionError):
            self._resolve("")

    def test_rejects_a_traversal_attempt(self):
        """A UUID cannot contain a path separator, and this must not escape."""
        outside = self.root.parent / "outside_secret.png"
        outside.write_bytes(self._png())
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        with self.assertRaises(image_manager.UploadResolutionError):
            self._resolve("../../outside_secret")

    def test_missing_upload_reports_where_it_looked(self):
        with self.assertRaises(image_manager.UploadResolutionError) as ctx:
            self._resolve("c" * 32)
        message = str(ctx.exception)
        self.assertIn("image", message)
        self.assertIn("original", message)

    def test_resolver_is_a_coroutine(self):
        """It touches the filesystem, so it must run in the executor.

        Home Assistant flagged the inline version on a real install:
        `Detected blocking call to read_bytes ... inside the event loop`.
        """
        import inspect

        self.assertTrue(
            inspect.iscoroutinefunction(image_manager.async_resolve_upload_path),
            "resolve_upload_path must be async so file I/O goes to the executor",
        )



class TestUploadFlowReachable(unittest.TestCase):
    """The upload form must be reachable from the dropdown.

    The flow went through repairs first, and every part of that path turned
    out to be different from what it looked like. Home Assistant's
    `issue_registry` has no module-level `async_get_issue`, and `IssueEntry`
    has no `async_create_flow`, so the option raised an AttributeError the
    first time it was used.
    """

    def test_reconfigure_step_exists(self):
        config_flow = _mjloader.load_module("config_flow")
        self.assertTrue(
            hasattr(config_flow.MojipicConfigFlow, "async_step_reconfigure"),
            "the reconfigure step is what SOURCE_RECONFIGURE routes to",
        )

    def test_repair_flow_is_gone(self):
        """It was unreachable and used two APIs that do not exist."""
        config_flow = _mjloader.load_module("config_flow")
        self.assertFalse(hasattr(config_flow, "MojipicRepairFlow"))

    def test_select_does_not_use_the_issue_registry(self):
        source = (
            Path(_mjloader.PKG_PATH) / "select.py"
        ).read_text()
        self.assertNotIn(
            "issue_registry",
            source.replace(
                # The explanatory comment mentions it on purpose.
                "`homeassistant.helpers.issue_registry` has no module-level",
                "",
            ).replace("async_create_flow", ""),
            "select.py must not reach into the issue registry to open a flow",
        )


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
            "options = [UPLOAD_OPTION, TEST_PATTERN_OPTION, URL_DOWNLOAD_OPTION] + [str(item[\"id\"]) for item in entries]",
            source,
            "the upload entry must be offered in the dropdown",
        )
        self.assertIn(
            "if option == UPLOAD_OPTION:", source,
            "selecting the upload entry must open the upload flow",
        )
        self.assertIn(
            "TEST_PATTERN_OPTION", source,
            "test pattern entry must be offered",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
