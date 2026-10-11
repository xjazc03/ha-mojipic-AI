"""Import the MojiPic integration modules without triggering HA dependencies.

The integration's `__init__.py` imports Home Assistant, which is not installed
on the development host. These modules are importable standalone, so we load
them through a stub package that keeps relative imports working.
"""

import importlib.util
import sys
import types
from pathlib import Path

PKG_PATH = Path(__file__).resolve().parents[1] / "integrations" / "mojipic"
STUB_NAME = "mjpkg"


def load_module(name: str):
    """Load integrations/mojipic/<name>.py as `mjpkg.<name>`."""
    if STUB_NAME not in sys.modules:
        stub = types.ModuleType(STUB_NAME)
        stub.__path__ = [str(PKG_PATH)]
        sys.modules[STUB_NAME] = stub

    qualified = f"{STUB_NAME}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]

    spec = importlib.util.spec_from_file_location(qualified, PKG_PATH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


const = load_module("const")
image_encode = load_module("image_encode")
transport = load_module("transport")
