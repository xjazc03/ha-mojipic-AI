"""Discovery behaviour against a fake Home Assistant Bluetooth manager.

The bug this exists for: discovery called ``discover()`` on Home Assistant's
*shared* scanner. HA already runs a scanner continuously, so that call returns
immediately with whatever happens to be cached -- normally nothing. The setup
form therefore failed instantly with "No MojiPic found" without ever listening
for advertisements.

These tests assert the behaviour that actually matters:

  * discovery checks devices HA already knows about, so a display seen before
    the form was submitted is found without waiting
  * discovery subscribes to the advertisement stream
  * ``match_dict`` and the scanning mode are passed in the right positions
  * discovery genuinely waits, rather than returning immediately
  * the subscription is always cancelled
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _mjloader import load_module  # noqa: E402

transport = load_module("transport")


class FakeInfo:
    """Stand-in for BluetoothServiceInfoBleak."""

    def __init__(self, address, name, local_name=None):
        self.address = address
        self.name = name
        self.advertisement = types.SimpleNamespace(local_name=local_name)


class FakeBluetoothManager:
    """Minimal stand-in for homeassistant.components.bluetooth."""

    def __init__(self, known=(), emit_after=None):
        self.known = list(known)
        self.emit_after = emit_after
        self.subscriptions = []
        self.scan_calls = 0
        self._emitted = []

    def async_discovered_service_info(self, hass, connectable=True):
        return list(self.known)

    def async_register_callback(self, hass, callback, match_dict, mode, **kwargs):
        self.subscriptions.append(
            {"callback": callback, "match_dict": match_dict, "mode": mode,
             "cancelled": False}
        )

        async def _emitter():
            if self.emit_after is None:
                return
            await asyncio.sleep(self.emit_after)
            for info in self.emit_after:
                callback(info)

        if self.emit_after is not None:
            asyncio.get_running_loop().create_task(_emitter())

        def _cancel():
            self.subscriptions[-1]["cancelled"] = True

        return _cancel

    async def async_get_scanner(self, hass):
        # Deliberately present so that any code path reaching for the shared
        # scanner and calling discover() on it is visible.
        self.scan_calls += 1
        raise AssertionError(
            "discovery must not use the shared scanner: HA already runs one, "
            "so discover() returns immediately with nothing cached"
        )


class _FakeHass:
    pass


def _install(fake):
    """Install the fake as homeassistant.components.bluetooth."""
    homeassistant = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    bluetooth = types.ModuleType("homeassistant.components.bluetooth")
    bluetooth.BluetoothScanningMode = types.SimpleNamespace(
        PASSIVE="passive", ACTIVE="active", AUTO="auto"
    )
    bluetooth.async_scanner_count = lambda hass: 1
    bluetooth.async_discovered_service_info = fake.async_discovered_service_info
    bluetooth.async_register_callback = fake.async_register_callback
    bluetooth.async_get_scanner = fake.async_get_scanner
    bluetooth.async_ble_device_from_address = lambda hass, address, connectable=True: None
    components.bluetooth = bluetooth
    homeassistant.components = components
    sys.modules["homeassistant"] = homeassistant
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.bluetooth"] = bluetooth
    return bluetooth


class TestDiscovery(unittest.IsolatedAsyncioTestCase):
    async def test_finds_device_ha_already_knows(self):
        fake = FakeBluetoothManager(
            known=[FakeInfo("AA:BB:CC:DD:EE:FF", "Mojipic")]
        )
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        found = await dev.async_discover("Mojipic", timeout=20)
        self.assertEqual(found, "AA:BB:CC:DD:EE:FF")

    async def test_matches_on_advertisement_local_name(self):
        fake = FakeBluetoothManager(
            known=[FakeInfo("11:22:33:44:55:66", None, local_name="Mojipic")]
        )
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        found = await dev.async_discover("Mojipic", timeout=20)
        self.assertEqual(found, "11:22:33:44:55:66")

    async def test_ignores_other_devices(self):
        fake = FakeBluetoothManager(
            known=[
                FakeInfo("AA:AA:AA:AA:AA:AA", "Kitchen Hub"),
                FakeInfo("BB:BB:BB:BB:BB:BB", "Some Speaker"),
            ]
        )
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        found = await dev.async_discover("Mojipic", timeout=1)
        self.assertIsNone(found)

    async def test_subscribes_to_advertisements(self):
        fake = FakeBluetoothManager(known=[])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        await dev.async_discover("Mojipic", timeout=1)
        self.assertEqual(len(fake.subscriptions), 1,
                         "must subscribe to the advertisement stream")

    async def test_callback_arguments_are_positionally_correct(self):
        """match_dict precedes mode; passing mode third silently disables it."""
        fake = FakeBluetoothManager(known=[])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        await dev.async_discover("Mojipic", timeout=1)
        sub = fake.subscriptions[0]
        self.assertIsNone(sub["match_dict"],
                          "match_dict must be None, not the scanning mode")
        self.assertEqual(sub["mode"], "active",
                         "scanning mode must land in the mode argument")

    async def test_actually_waits_for_advertisements(self):
        """The bug: returning instantly without listening."""
        fake = FakeBluetoothManager(known=[])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        loop = asyncio.get_running_loop()
        started = loop.time()
        found = await dev.async_discover("Mojipic", timeout=3)
        elapsed = loop.time() - started
        self.assertIsNone(found)
        self.assertGreater(
            elapsed, 2.0,
            "discovery must wait for the full window, not return instantly",
        )

    async def test_cancels_subscription(self):
        fake = FakeBluetoothManager(known=[])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        await dev.async_discover("Mojipic", timeout=1)
        self.assertTrue(fake.subscriptions[0]["cancelled"],
                        "the advertisement subscription must always be cancelled")

    async def test_never_uses_the_shared_scanner(self):
        """FakeBluetoothManager raises if anything calls the shared scanner."""
        fake = FakeBluetoothManager(known=[FakeInfo("AA:BB:CC:DD:EE:FF", "Mojipic")])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        await dev.async_discover("Mojipic", timeout=1)
        self.assertEqual(fake.scan_calls, 0)

    async def test_empty_prefix_returns_none_without_scanning(self):
        fake = FakeBluetoothManager(known=[])
        _install(fake)
        dev = transport.HomeAssistantBluetoothTransport(_FakeHass())
        self.assertIsNone(await dev.async_discover("", timeout=5))
        self.assertEqual(len(fake.subscriptions), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
