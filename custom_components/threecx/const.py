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
# 3CX V20 enforces a maximum OData $top value of 100. Further records are
# retrieved through @odata.nextLink pagination.
XAPI_USERS_PATH = f"{XAPI_BASE_PATH}/Users?$count=true&$top=100&$orderby=Number"
# Different V20 builds expose department/group membership under different
# navigation names. Each path is optional and tried in order.
XAPI_GROUP_PATHS = (
    f"{XAPI_BASE_PATH}/Groups?$count=true&$top=100&$expand=Members",
    f"{XAPI_BASE_PATH}/Groups?$count=true&$top=100&$expand=Users",
    f"{XAPI_BASE_PATH}/Groups?$count=true&$top=100&$expand=GroupMembers",
    f"{XAPI_BASE_PATH}/Groups?$count=true&$top=100",
)
# Queue agents are navigation records. In V20 the actual extension identity is
# commonly nested in the agent's User navigation object, so both levels must be
# expanded for membership and queue-login evaluation.
XAPI_QUEUES_PATH = (
    f"{XAPI_BASE_PATH}/Queues?$count=true&$top=100&$orderby=Number"
    "&$expand=Agents($expand=User)"
)

# Call Control is intentionally isolated from Configuration API polling. 3CX
# builds can expose the realtime websocket at different local paths. Discovery
# is non-fatal and the first successful path is retained by the client.
CALL_CONTROL_WS_PATHS = (
    "/callcontrol/ws",
    "/callcontrol",
    "/api/callcontrol/ws",
)
EVENT_CALL_CONTROL = f"{DOMAIN}_call_control_event"
EVENT_CALL_CONTROL_CONNECTED = f"{DOMAIN}_call_control_connected"
EVENT_CALL_CONTROL_DISCONNECTED = f"{DOMAIN}_call_control_disconnected"
