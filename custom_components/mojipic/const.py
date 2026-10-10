"""Constants for the MojiPic integration."""

DOMAIN = "mojipic"

# Vendor service and characteristic UUIDs. MojiPic uses 16-bit short-form UUIDs,
# which the Bluetooth spec expands to the 0000xxxx-0000-1000-8000-00805f9b34fb
# base. Confirmed against a live GATT dump on real hardware, 2026.10.10.
BASE_UUID_SUFFIX = "-0000-1000-8000-00805f9b34fb"

SERVICE_CONFIG = "0000fff0" + BASE_UUID_SUFFIX
SERVICE_BATTERY = "0000180f" + BASE_UUID_SUFFIX

CHAR_BATTERY_LEVEL = "00002a19" + BASE_UUID_SUFFIX
CHAR_FIRMWARE_VERSION = "00002a26" + BASE_UUID_SUFFIX

CHAR_WRITE_IMAGE = "0000fff1" + BASE_UUID_SUFFIX
CHAR_STATUS_REQUEST = "0000fff2" + BASE_UUID_SUFFIX
CHAR_IMAGE_ID_TO_DISPLAY = "0000fff3" + BASE_UUID_SUFFIX
CHAR_READ_IMAGES_COUNT = "0000fff4" + BASE_UUID_SUFFIX
CHAR_READ_IMAGES_IDS = "0000fff5" + BASE_UUID_SUFFIX
CHAR_DEVICE_COMMANDS = "0000fff6" + BASE_UUID_SUFFIX
CHAR_ERASE_IMAGE = "0000fff7" + BASE_UUID_SUFFIX
CHAR_TIMEOUT = "0000fff8" + BASE_UUID_SUFFIX
CHAR_BRIGHTNESS = "0000fff9" + BASE_UUID_SUFFIX
CHAR_PROCESSOR_ID = "0000fffd" + BASE_UUID_SUFFIX

# Panel geometry and protocol framing, from the vendor APK
# (com.mojipic.tools_bluetooth_kit), validated on real hardware 2026.10.10.
SIZE = 32
BYTES_PER_FRAME = SIZE * SIZE * 3          # 3072
CHUNK_BYTES = 16                           # payload per BLE packet
PACKET_BYTES = 20                          # full BLE write: 4-byte header + 16
CHUNKS_PER_FRAME = BYTES_PER_FRAME // CHUNK_BYTES   # 192

# Color mode. 2 = 5 bits per channel (RGB565-style).
HEADER_PREFIX_5BIT = 2
MIN_SLOT, MAX_SLOT = 0, 4095

# Advertised BLE name used by the vendor app as its scan filter.
DEVICE_NAME_PREFIX = "mojipic"

# A full 192-packet upload takes a few seconds. Give services room to finish.
UPLOAD_TIMEOUT = 120

DEFAULT_SCAN_TIMEOUT = 20
