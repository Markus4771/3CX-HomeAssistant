# Changelog

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
