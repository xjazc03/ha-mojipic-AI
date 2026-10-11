"""Fake Home Assistant objects that actually enforce entity lifecycle rules.

The earlier test suite for `select.py` only read the source as text and asserted
that certain strings were present. That is why a NameError and a
write-before-add both shipped: the code looked right and never ran.

These fakes are deliberately strict. `SelectEntity.async_write_ha_state` raises
if the entity has not been added yet, exactly as Home Assistant does, so the
platform-setup crash that made the dropdown vanish reproduces here in a second
rather than on the owner's dashboard.
"""

from __future__ import annotations

import sys
import types


class NoEntitySpecifiedError(Exception):
    """Raised when state is written before the entity is registered."""


class FakeEntity:
    """Base entity with Home Assistant's add-then-write ordering rule."""

    _attr_should_poll = True

    def __init__(self) -> None:
        self.hass = None
        self.entity_id = None
        self.state = None
        self.write_count = 0

    def async_write_ha_state(self) -> None:
        # Home Assistant requires the entity to be registered first. An
        # unadded entity has no entity_id and calling this raises.
        if self.hass is None or self.entity_id is None:
            raise NoEntitySpecifiedError(
                f"{type(self).__name__} wrote state before being added to the "
                "entity platform"
            )
        self.write_count += 1

    @property
    def available(self) -> bool:
        return True


class FakeSelectEntity(FakeEntity):
    """Stand-in for homeassistant.components.select.SelectEntity."""

    _attr_translation_key = None
    _attr_name = None
    _attr_unique_id = None
    _attr_icon = None
    _attr_device_info = None

    def __init__(self) -> None:
        super().__init__()
        # Home Assistant's Entity base sets hass via the platform, not in
        # __init__; the integration sets it manually. Mirror both facts.
        self.hass = None


class FakeEntry:
    """Config entry stand-in with async_on_unload tracking."""

    def __init__(self, entry_id="entry1", data=None):
        self.entry_id = entry_id
        self.data = data or {"address": "AA:BB:CC:DD:EE:FF", "name": "Desk"}
        self.title = "MojiPic"
        self._unload = []

    def async_on_unload(self, func):
        self._unload.append(func)
        return func

    def add_update_listener(self, listener):
        self._unload.append(listener)
        return lambda: None


class FakeHass:
    """Minimal hass for the bits select.py touches."""

    def __init__(self, domain_data=None):
        self.data = domain_data or {}
        self.created = []
        self.flow_inits = []
        self.config_entries = _ConfigEntries(self)

    def async_create_task(self, coro):
        self.created.append(coro)
        return coro


class _FlowManager:
    """Records the flows an integration starts."""

    def __init__(self, entries):
        self._entries = entries
        self.flow_inits = []

    async def async_init(self, handler, *, context=None, data=None):
        self.flow_inits.append(
            {"handler": handler, "context": context, "data": data}
        )
        return {"type": "form", "step_id": "reconfigure"}

    def async_get_entry(self, entry_id):
        return self._entries._entry_by_id.get(entry_id)


class _ConfigEntries:
    def __init__(self, hass):
        self._hass = hass
        self.flow = _FlowManager(self)
        self._entry_by_id = {}


def install(monkey_state=None):
    """Install stub `homeassistant` modules. Returns a record of what was made.

    `monkey_state` collects observable side effects (created issues, dispatched
    signals) so tests can assert on behaviour rather than on source text.
    """
    state = monkey_state if monkey_state is not None else {}
    state.setdefault("issues_created", [])
    state.setdefault("issues_deleted", [])
    state.setdefault("flows_started", [])
    state.setdefault("signals", [])
    state.setdefault("timers", [])

    def mod(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module
        return module

    def _create_issue(hass, domain, issue_id, **kwargs):
        state["issues_created"].append((domain, issue_id, kwargs))

    def _delete_issue(hass, domain, issue_id):
        state["issues_deleted"].append((domain, issue_id))

    def _get_issue(hass):
        """Real HA exposes `async_get`, not a module-level `async_get_issue`.

        The stub deliberately does NOT provide `async_get_issue` or
        `async_create_flow`. Both were invented and both were absent from
        `homeassistant.helpers.issue_registry`, which is how the upload
        button reached an AttributeError in the UI. A fake that is more
        generous than the real module hides exactly that class of bug, so this
        one mirrors reality: the `async_get_issue` method lives on the
        registry object, and `IssueEntry` has no `async_create_flow`.
        """

        class _Registry:
            def __init__(self):
                self.issues = {}

            def async_get_issue(self, domain, issue_id):
                return self.issues.get((domain, issue_id))

        return _Registry()

    class _Severity:
        ERROR = "error"
        WARNING = "warning"

    def _track_time_interval(hass, action, interval, **kwargs):
        state["timers"].append((action, interval))
        return lambda: None

    def _dispatcher_send(hass, signal, *args):
        state["signals"].append(signal)

    class _FlowBase:
        """Minimal FlowHandler so config_flow.py's classes can be defined."""

        def __init_subclass__(cls, **kwargs):
            # config_entries.ConfigFlow is declared as `class X(ConfigFlow,
            # domain=DOMAIN)`, so the base must tolerate a class keyword.
            super().__init_subclass__()
            cls.domain = kwargs.get("domain")

        def __init__(self, hass=None, handler=None, context=None, data=None):
            self.hass = hass
            self.context = context or {}
            self.data = data or {}

        def async_create_entry(self, *, title=None, data=None):
            return {"type": "create_entry", "title": title, "data": data}

        def async_show_form(self, *, step_id=None, data_schema=None, errors=None,
                            description_placeholders=None):
            return {"type": "form", "step_id": step_id, "schema": data_schema,
                    "errors": errors or {}}

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

    # Build the package skeleton so relative imports resolve.
    mod("homeassistant")
    mod("homeassistant.config_entries",
        ConfigEntry=FakeEntry,
        ConfigFlow=_FlowBase,
        OptionsFlow=_FlowBase,
        RepairsFlow=_FlowBase,
        SOURCE_RECONFIGURE="reconfigure",
        SOURCE_REPAIR="repair")
    mod("homeassistant.components")
    mod("homeassistant.components.select", SelectEntity=FakeSelectEntity)
    mod("homeassistant.core",
        HomeAssistant=FakeHass,
        ServiceCall=object,
        callback=lambda f: f)
    mod("homeassistant.exceptions", HomeAssistantError=Exception)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.device_registry",
        CONNECTION_BLUETOOTH="bluetooth",
        DeviceInfo=lambda **kw: kw)
    mod("homeassistant.helpers.dispatcher",
        async_dispatcher_send=_dispatcher_send,
        async_dispatcher_connect=lambda hass, signal, target: (lambda: None))
    mod("homeassistant.helpers.entity_platform",
        AddEntitiesCallback=object)
    mod("homeassistant.helpers.event",
        async_track_time_interval=_track_time_interval)
    mod("homeassistant.helpers.issue_registry",
        async_create_issue=_create_issue,
        async_delete_issue=_delete_issue,
        async_get=_get_issue,
        IssueSeverity=_Severity)
    mod("homeassistant.helpers.typing", ConfigType=dict)

    # Enough of selector + voluptuous to import config_flow.py, so tests can
    # assert that a handler key actually resolves to a step on the flow class.
    def _selector(*args, **kwargs):
        return ("selector", args, kwargs)

    mod("homeassistant.helpers.selector",
        FileSelector=_selector,
        TextSelector=_selector,
        BooleanSelector=_selector,
        SelectSelector=_selector,
        NumberSelector=_selector,
        SelectSelectorConfig=object)
    mod("homeassistant.components.bluetooth",
        callback=lambda f: f,
        BluetoothScanningMode=types.SimpleNamespace(ACTIVE=1, PASSIVE=0),
        async_register_callback=lambda *a, **k: (lambda: None))
    # Must be registered under helpers.* -- config_flow does
    # `from homeassistant.helpers import config_validation as cv`.
    mod("homeassistant.helpers.config_validation", string=str)
    mod("homeassistant.util")
    mod("homeassistant.util.threading",
        HassJob=types.SimpleNamespace(
            target=lambda f: f,
            executor=lambda f: f,
        ))

    try:
        import voluptuous  # noqa: F401
    except ImportError:
        mod("voluptuous",
            Schema=lambda *a, **k: dict,
            Required=lambda *a, **k: a[0] if a else None,
            Optional=lambda *a, **k: a[0] if a else None,
            Invalid=type("Invalid", (Exception,), {}),
            All=lambda *a, **k: a[0] if a else None,
            Coerce=lambda f, **k: f)
    return state


def add_entity(hass, entities):
    """Mimic the entity platform assigning ids to entities being added."""

    def _add(new):
        for entity in new:
            entity.hass = hass
            entity.entity_id = f"select.{id(entity)}"
        entities.extend(new)

    return _add