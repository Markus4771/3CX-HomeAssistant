"""Constants for the 3CX integration."""

from datetime import timedelta

DOMAIN = "threecx"
PLATFORMS = ["sensor", "binary_sensor"]

CONF_VERIFY_SSL = "verify_ssl"
CONF_API_MODE = "api_mode"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

DEFAULT_NAME = "3CX"
DEFAULT_PORT = 443
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)

API_MODE_AUTO = "auto"
API_MODE_V20 = "v20"
API_MODE_LEGACY = "legacy"

TOKEN_PATH = "/connect/token"
XAPI_BASE_PATH = "/xapi/v1"
XAPI_DEFS_PATH = f"{XAPI_BASE_PATH}/Defs?$select=Id"
# Request complete records because status field names differ between V20 updates.
XAPI_USERS_PATH = f"{XAPI_BASE_PATH}/Users?$count=true&$top=1000&$orderby=Number"
XAPI_QUEUES_PATH = f"{XAPI_BASE_PATH}/Queues?$count=true&$top=1000&$orderby=Number"
