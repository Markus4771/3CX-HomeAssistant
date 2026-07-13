"""Constants for the 3CX integration."""

from datetime import timedelta

DOMAIN = "threecx"
PLATFORMS = ["sensor", "binary_sensor"]

CONF_VERIFY_SSL = "verify_ssl"
CONF_API_MODE = "api_mode"

DEFAULT_NAME = "3CX"
DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

API_MODE_AUTO = "auto"
API_MODE_V20 = "v20"
API_MODE_LEGACY = "legacy"
