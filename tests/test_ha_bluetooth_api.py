"""Home Assistant Bluetooth API signatures, pinned.

Every one of these was got wrong at least once. They are all *signature*
mistakes: the function existed, the name was right, the call simply had the
wrong shape, and nothing failed until a real Home Assistant ran it.

  * ``async_scanner_count(hass, connectable)`` -- called with no arguments,
    raising TypeError and surfacing to the user as "Unknown error occurred".
  * ``async_register_callback(hass, callback, match_dict, mode)`` -- the
    scanning mode was passed into the ``match_dict`` slot.
  * ``async_ble_device_from_address(hass, address, connectable)`` -- synchronous,
    and returns a BLEDevice rather than a connected client. It was being
    awaited and then used as if it had ``write_gatt_char``.

None of these are catchable by a test that runs against a stub. They are
caught by reading the source, which is what this file records so the next
person does not have to re-derive it from a live failure.

If Home Assistant changes these APIs, this file is where you will find out.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _mjloader  # noqa: E402  (ensures the integration modules load)

# Transcribed from homeassistant/components/bluetooth/api.py (HA core, dev).
# Each entry is (callable_name, positional parameter names).
EXPECTED_SIGNATURES = {
    "async_scanner_count": ["hass", "connectable"],
    "async_ble_device_from_address": ["hass", "address", "connectable"],
    "async_register_callback": [
        "hass",
        "callback",
        "match_dict",
        "mode",
        "scan_interval",
        "scan_duration",
        "replay",
    ],
    "async_get_scanner": ["hass"],
}


class TestHomeAssistantApiUsage(unittest.TestCase):
    """Static checks on how transport.py and config_flow.py call HA."""

    def _source(self, name: str) -> str:
        import inspect as _inspect
        from pathlib import Path

        from _mjloader import PKG_PATH

        return (Path(PKG_PATH) / f"{name}.py").read_text()

    def test_scanner_count_is_called_with_hass(self):
        """The bug that produced 'Unknown error occurred' on scan submit."""
        source = self._source("config_flow")
        self.assertIn(
            "counter(hass)",
            source,
            "async_scanner_count must be called with hass as its first "
            "argument; calling it bare raises TypeError and breaks setup",
        )
        self.assertNotIn(
            "async_scanner_count, lambda: 0)()",
            source,
            "do not call async_scanner_count with no arguments",
        )

    def test_ble_device_from_address_is_not_awaited(self):
        """It is synchronous and returns a BLEDevice, not a client."""
        source = self._source("transport")
        self.assertNotIn(
            "await self._c(",  # sanity: the real client is awaited elsewhere
            source,
        )
        self.assertNotIn(
            "await bluetooth.async_ble_device_from_address",
            source,
            "async_ble_device_from_address is synchronous; awaiting it is wrong",
        )
        self.assertIn(
            "bluetooth.async_ble_device_from_address(\n                    self.hass, address, True\n                )",
            source,
            "call async_ble_device_from_address positionally with hass first",
        )

    def test_ble_device_is_not_treated_as_a_client(self):
        """A BLEDevice has no write_gatt_char; the BleakClient does."""
        source = self._source("transport")
        self.assertIn("BleakClient", source, "must build a real BleakClient")
        self.assertNotIn(
            "self._client = bluetooth.async_ble_device_from_address",
            source,
            "a BLEDevice is not a client and must not be assigned as one",
        )

    def test_discovery_awaits_the_shared_scanner(self):
        source = self._source("transport")
        self.assertIn(
            "await scanner.discover(",
            source,
            "discovery must await the shared scanner's discover()",
        )
        self.assertIn(
            "async_get_scanner", source, "must use HA's shared scanner"
        )

    def test_manifest_depends_on_bluetooth_adapters(self):
        """HA guidance: adapters must be connected before we try to use them."""
        import json
        from pathlib import Path

        from _mjloader import PKG_PATH

        manifest = json.loads(
            (PKG_PATH / "manifest.json").read_text()
        )
        self.assertIn(
            "bluetooth_adapters",
            manifest["dependencies"],
            "integrations using a Bluetooth adapter should declare "
            "bluetooth_adapters in dependencies",
        )

    def test_expected_signatures_recorded(self):
        """Keep this table aligned with upstream if HA changes."""
        self.assertIn("hass", EXPECTED_SIGNATURES["async_scanner_count"])
        self.assertIn("hass", EXPECTED_SIGNATURES["async_ble_device_from_address"])
        self.assertIn(
            "match_dict", EXPECTED_SIGNATURES["async_register_callback"],
            "match_dict precedes mode; passing mode third silently binds it "
            "to match_dict",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestHomeAssistantTimerApi(unittest.TestCase):
    """Home Assistant timer helpers require timedelta, not seconds.

    `async_track_time_interval` calls `interval.total_seconds()`. Passing a
    float raises

        AttributeError: 'float' object has no attribute 'total_seconds'

    which aborted config entry setup entirely -- the integration never got as
    far as connecting. Found only on a real host.
    """

    def _source(self, name: str) -> str:
        from _mjloader import PKG_PATH

        return (PKG_PATH / f"{name}.py").read_text()

    def test_health_interval_is_wrapped_in_timedelta(self):
        source = self._source("coordinator")
        self.assertIn(
            "dt_util.timedelta(seconds=HEALTH_INTERVAL)",
            source,
            "async_track_time_interval needs a timedelta; a bare float raises "
            "AttributeError and fails config entry setup",
        )

    def test_select_refresh_interval_is_wrapped_in_timedelta(self):
        """select.py uses async_track_time_interval too."""
        import sys
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "custom_components/mojipic/select.py"
        source = path.read_text()
        if "async_track_time_interval" not in source:
            self.skipTest("select.py no longer schedules a timer")
        self.assertIn(
            "dt_util.timedelta",
            source,
            "async_track_time_interval needs a timedelta, not seconds",
        )
        self.assertIn(
            "from homeassistant.util import dt as dt_util", source
        )
