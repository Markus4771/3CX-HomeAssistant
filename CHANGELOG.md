# Changelog

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
