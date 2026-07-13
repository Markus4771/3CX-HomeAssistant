# 3CX Home Assistant

Private Home-Assistant custom integration for connecting a 3CX phone system directly with Home Assistant.

## Project status

Development version: **0.1.0**

The first milestone provides the Home Assistant integration structure, UI-based configuration, a central update coordinator and placeholder entities. The productive 3CX API adapter will be connected after the exact 3CX version and available API authentication method are confirmed.

## Planned first functions

- Connection status
- Number of configured extensions
- Number of active calls
- Incoming-call event support
- Extension presence/status
- DND and queue controls where supported by the 3CX API

## Installation for development

Copy `custom_components/threecx` to the Home Assistant configuration directory:

```text
/config/custom_components/threecx
```

Restart Home Assistant and add **3CX** under **Settings → Devices & services → Add integration**.

## Important

This is an unofficial private integration and is not affiliated with 3CX or Home Assistant.
