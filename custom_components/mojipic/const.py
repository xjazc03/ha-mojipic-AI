"""Constants for the MojiPic integration.

Nothing here is owner-specific. The BLE address is always discovered at runtime
or entered by the user; the device name is configurable with the vendor's
advertised name as the default.
"""

DOMAIN = "mojipic"

# --- GATT identifiers ------------------------------------------------------
# MojiPic uses 16-bit short-form UUIDs, which the Bluetooth spec expands to the
# 0000xxxx-0000-1000-8000-00805f9b34fb base. Verified against a live GATT dump
# on real hardware.
BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"

SERVICE_CONFIG = "0000fff0" + BASE_UUID_SUFFIX
SERVICE_BATTERY = "0000180f" + BASE_UUID_SUFFIX
SERVICE_DEVICE_INFO = "0000180a" + BASE_UUID_SUFFIX

# Standard Device Information characteristics.
CHAR_MANUFACTURER = "00002a29" + BASE_UUID_SUFFIX
CHAR_MODEL = "00002a28" + BASE_UUID_SUFFIX
CHAR_SERIAL_NUMBER = "00002a25" + BASE_UUID_SUFFIX
CHAR_HW_REVISION = "00002a23" + BASE_UUID_SUFFIX
CHAR_SW_REVISION = "00002a2a" + BASE_UUID_SUFFIX
CHAR_FIRMWARE_VERSION = "00002a26" + BASE_UUID_SUFFIX

# Standard Battery Service.
CHAR_BATTERY_LEVEL = "00002a19" + BASE_UUID_SUFFIX

# Vendor characteristics.
CHAR_WRITE_IMAGE = "0000fff1" + BASE_UUID_SUFFIX        # read,write
CHAR_STATUS_REQUEST = "0000fff2" + BASE_UUID_SUFFIX     # read,notify
CHAR_IMAGE_ID_TO_DISPLAY = "0000fff3" + BASE_UUID_SUFFIX  # read,write,notify
CHAR_READ_IMAGES_COUNT = "0000fff4" + BASE_UUID_SUFFIX  # read
CHAR_READ_IMAGES_IDS = "0000fff5" + BASE_UUID_SUFFIX    # read,write-no-rsp,notify
CHAR_DEVICE_COMMANDS = "0000fff6" + BASE_UUID_SUFFIX    # read,write
CHAR_ERASE_IMAGE = "0000fff7" + BASE_UUID_SUFFIX        # read,write
CHAR_TIMEOUT = "0000fff8" + BASE_UUID_SUFFIX            # read,write
CHAR_BRIGHTNESS = "0000fff9" + BASE_UUID_SUFFIX         # read,write-without-response
CHAR_PROCESSOR_ID = "0000fffd" + BASE_UUID_SUFFIX       # read only

# Default advertised name prefix used for discovery. Configurable per entry.
DEFAULT_NAME_PREFIX = "Mojipic"

# --- Panel geometry and protocol framing ------------------------------------
SIZE = 32
BYTES_PER_FRAME = SIZE * SIZE * 3          # 3072
CHUNK_BYTES = 16                           # payload per BLE packet
PACKET_BYTES = 20                          # 4-byte header + 16-byte payload
CHUNKS_PER_FRAME = BYTES_PER_FRAME // CHUNK_BYTES   # 192

HEADER_PREFIX_5BIT = 2                     # 5 bits per channel
MIN_SLOT, MAX_SLOT = 0, 4095

# --- Storage (all relative to the Home Assistant config directory) ---------
STORAGE_DIR = "mojipic"
IMAGE_STORAGE_DIR = "mojipic/images"
IMAGE_STORAGE_BIN_DIR = "mojipic/framebuffers"
MANIFEST_FILENAME = "manifest.json"

FRAMEBUFFER_SUFFIX = ".bin"
SUPPORTED_UPLOAD_EXTENSIONS = (".png", ".jpg", ".jpeg")

# --- Timeouts --------------------------------------------------------------
CONNECT_TIMEOUT = 25
PUSH_TIMEOUT = 120
RECONNECT_BACKOFF_INITIAL = 1.0
RECONNECT_BACKOFF_MAX = 30.0
HEALTH_INTERVAL = 60.0
HEARTBEAT_INTERVAL = 5
DISCOVERY_TIMEOUT = 20

# Below this RSSI the link is considered unreliable and we log a warning. BLE at
# roughly 10 ft through a wall commonly lands in this region.
RSSI_WARN_THRESHOLD = -80

# --- Config entry keys -----------------------------------------------------
CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_NAME_PREFIX = "name_prefix"
CONF_SLOT = "slot"
CONF_SCAN = "scan"

# --- Service / entity attribute names --------------------------------------
ATTR_IMAGE_ID = "image_id"
ATTR_IMAGE_PATH = "image_path"
ATTR_FILENAME = "filename"
ATTR_IMAGE_DATA = "image_data"
ATTR_SLOT = "slot"
ATTR_BRIGHTNESS = "brightness"
ATTR_ADDRESS = "address"
ATTR_VERIFICATION = "verification"
ATTR_PACKETS_SENT = "packets_sent"

# --- Upload UI --------------------------------------------------------------
# Leading entry in the select entity's option list. Selecting it opens a repair
# flow with a file picker, so images can be added without writing YAML.
UPLOAD_OPTION = "\u2b06 Upload image\u2026"

# Where Home Assistant may have put an uploaded file, relative to config_dir.
# A file *picked* from the config directory comes back as a relative path; a
# file *uploaded* through the picker comes back as a UUID.
UPLOAD_CANDIDATE_DIRS = (".storage/uploads", "media", "")

# --- States ----------------------------------------------------------------
STATE_CONFIGURED = "configured"   # address remembered (NOT "paired": MojiPic
                                   # does not support Bluetooth pairing)
STATE_UNREACHABLE = "unreachable" # address known, link down
STATE_REBOUND = "rebound"         # address changed and was re-persisted
