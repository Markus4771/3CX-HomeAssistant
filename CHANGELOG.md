# Changelog

## 0.8.1 – 2026-07-14

- Added tolerant normalization of unknown Call Control event structures
- Maps common event values to `ringing`, `dialing`, `connected`, `held`, `transferred`, `ended`, `queue_login` and `queue_logout`
- Extracts call ID, source, destination and direction from nested event objects
- Stores the 20 most recent normalized events for diagnostics
- Extended the `Call Control verbunden` entity with normalized event details
- Home Assistant now fires normalized events such as `threecx_ringing` and `threecx_connected`

## 0.8.0 – 2026-07-14

- Added an isolated Call Control websocket client
- Added bearer-token authentication, heartbeat and automatic reconnection
- Added non-fatal endpoint discovery for multiple local Call Control paths
- Added `Call Control verbunden` binary sensor and raw event diagnostics
- Added generic `threecx_call_control_event` events on the Home Assistant event bus
- Configuration API polling remains operational if Call Control is unavailable

## 0.7.2 – 2026-07-14

- Added user-level queue-login field detection as fallback
- Supports multiple queue and agent login field-name variants
- Queue login detection no longer depends solely on Queue Agent records

## 0.7.1 – 2026-07-14

- Expanded queue agents together with their nested User object
- Improved matching between queue members and 3CX extensions

## 0.7.0 – 2026-07-14

- Added multi-source user import from Users and Groups/Departments
- Added role-independent merging by 3CX ID and extension number
- Added role, department, email, mobile, active state and import source metadata
- Added fallback support for users omitted from the primary Users collection

## 0.6.1 – 2026-07-14

- Fixed queues showing `0/0 angemeldet` although agents were configured
- Expanded the 3CX V20 `Agents` navigation property in the queue request
- Queue member and logged-in-agent sensors can now receive the actual agent collection returned by 3CX
- Kept the OData page limit at 100 with automatic next-link pagination

## 0.6.0 – 2026-07-14

- Added one Home Assistant device for every 3CX queue
- Added per-queue sensor for configured member count
- Added per-queue sensor for currently logged-in agent count
- Added per-queue agent-status sensor listing logged-in and logged-out members
- Added central count of PBX-registered extensions
- Added central count of users logged into at least one queue
- Extended the central queue overview with member counts
- New queue devices are discovered automatically during later coordinator updates
- Values not exposed by the Configuration API, such as live waiting callers, remain unavailable instead of being guessed
- Call Control API live call states and write actions remain intentionally separate until their documented endpoints are implemented

## 0.5.1 – 2026-07-14

- Fixed HTTP 400 startup failure on 3CX V20 systems that enforce a maximum OData `$top` value of 100
- Changed Users and Queues requests from `$top=1000` to `$top=100`
- Keeps automatic `@odata.nextLink` pagination so more than 100 records are still loaded

## 0.5.0 – 2026-07-14

- Added PBX registration detection for every user/extension
- Added binary sensor `An Anlage angemeldet` per extension
- Added `/xapi/v1/Queues` retrieval with graceful fallback when unavailable
- Added queue membership and queue login detection for every extension
- Added binary sensor `In Warteschleife angemeldet` per extension
- Added per-user `Warteschleifenstatus` sensor with membership and logged-in queue attributes
- Added central `Warteschleifen` sensor listing every queue, all members and currently logged-in members
- Added dynamic field-name detection for differences between 3CX V20 update builds
- Queue or registration values that are not exposed by the installed build are shown as unknown instead of breaking the integration

## 0.4.4 – 2026-07-14

- Changed the V20 Users request to include `$count=true`
- Requests up to 100 user records per response with `$top=100`
- Keeps OData next-link pagination as a fallback
- Works around V20 responses that return a shortened first collection without a usable continuation link
- Keeps complete user records so dynamic presence and status detection continues to work

## 0.4.3 – 2026-07-14

- Added a user-import diagnostic sensor on the central 3CX device
- Shows the number of API-returned, imported and skipped user records
- Shows how many OData pages were loaded
- Lists the reason for every skipped record, including missing IDs and duplicate IDs
- Added Home Assistant log messages for import totals and skipped records
- Added a permission hint when the 3CX API itself returns fewer users than expected

## 0.4.2 – 2026-07-14

- Fixed release publishing so the version tag and `threecx.zip` asset are created in the same GitHub Actions run
- Prepared reliable HACS installation from the release asset

## 0.4.1 – 2026-07-14

- Fixed HACS domain detection by declaring the `threecx` integration domain explicitly
- Enabled installation from the generated `threecx.zip` release asset
- Fixed the HACS error `custom_components/None/manifest.json`

## 0.4.0 – 2026-07-14

- Added one separate status sensor for every 3CX V20 user
- Changed the Users request to retrieve complete user records instead of a fixed field selection
- Added dynamic detection of all status, presence, profile, DND, routing, availability and registration fields returned by the installed V20 build
- Added a prioritized main status while retaining every other supplied status field as entity attributes
- Kept extension-number sensors unchanged for compatibility
- Live ringing, connected-call and call-ended states remain reserved for the separate Call Control API

## 0.3.1 – 2026-07-14

- Added OData pagination so all API-visible users are loaded
- Added loop and page-limit protection for pagination

## 0.3.0 – 2026-07-13

- Added one Home Assistant device for every 3CX V20 user/extension
- Added one extension sensor per user with the extension number as state
- Added attributes for 3CX ID, first name, last name and display name
- Added stable unique IDs based on the Home Assistant config entry and 3CX user ID
- Added automatic discovery of extensions created after integration setup
- Removed extensions remain registered but become unavailable
- Linked extension devices to the central 3CX PBX device
- Normalized and sorted V20 `/xapi/v1/Users` results
- Added HACS metadata for custom-repository installation
- Added a complete installation and troubleshooting guide
- Added GitHub Actions validation for Python syntax, JSON and version consistency
- Added automated `threecx.zip` build and GitHub release workflow

## 0.2.0 – 2026-07-13

- Added official 3CX V20 client-credentials authentication through `/connect/token`
- Added bearer-token caching and renewal
- Added Configuration API access through `/xapi/v1/...`
- Added productive user/extension count retrieval
- Added authentication and connection error handling

## 0.1.0 – 2026-07-13

- Initial private project structure
- Home Assistant manifest and UI config flow
- Safe 3CX reachability test
- Central DataUpdateCoordinator
- Connection, extension-count, active-call and API-mode entities
- German UI translation
- Productive 3CX endpoint implementation intentionally deferred until target version and authentication are confirmed
