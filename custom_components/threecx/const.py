"""Constants for the 3CX integration."""

from datetime import timedelta

DOMAIN = "threecx"
PLATFORMS = ["sensor", "binary_sensor", "button"]

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
# Load queues without an expansion first. Several V20 builds reject nested
# $expand expressions even though their agent navigation can be queried
# separately. The coordinator probes the navigation endpoints afterwards.
XAPI_QUEUES_PATH = (
    f"{XAPI_BASE_PATH}/Queues?$count=true&$top=100&$orderby=Number"
)
# Queue-agent navigation differs between V20 update builds. The queue id is
# inserted into every candidate. A failed candidate is diagnostic only and does
# not make the complete integration unavailable.
XAPI_QUEUE_AGENT_PATH_TEMPLATES = (
    f"{XAPI_BASE_PATH}/Queues({{queue_id}})/Agents?$expand=User",
    f"{XAPI_BASE_PATH}/Queues({{queue_id}})/Agents",
    f"{XAPI_BASE_PATH}/Queues('{{queue_id}}')/Agents?$expand=User",
    f"{XAPI_BASE_PATH}/Queues('{{queue_id}}')/Agents",
    f"{XAPI_BASE_PATH}/Queues/{{queue_id}}/Agents",
)

# Call Control remains isolated from Configuration API polling. The discovery
# engine keeps every successful websocket open and selects the first channel
# that actually emits frames. Both bearer-header and access_token-query
# authentication are tested for each path.
CALL_CONTROL_WS_PATHS = (
    "/callcontrol/ws",
    "/callcontrol",
    "/api/callcontrol/ws",
    "/api/callcontrol",
    "/call-control/ws",
    "/call-control",
    "/api/call-control/ws",
    "/api/call-control",
    "/events/ws",
    "/eventstream/ws",
    "/event-stream/ws",
    "/api/events/ws",
    "/api/eventstream/ws",
    "/api/event-stream/ws",
    "/signalr",
    "/signalr/connect",
    "/hubs/callcontrol",
    "/hub/callcontrol",
    "/ws/callcontrol",
    "/websocket/callcontrol",
)
EVENT_CALL_CONTROL = f"{DOMAIN}_call_control_event"
EVENT_CALL_CONTROL_CONNECTED = f"{DOMAIN}_call_control_connected"
EVENT_CALL_CONTROL_DISCONNECTED = f"{DOMAIN}_call_control_disconnected"
