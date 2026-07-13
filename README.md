# 3CX Home Assistant

Private Home Assistant custom integration for connecting a 3CX V20 phone system directly with Home Assistant.

## Project status

Development version: **0.3.0**

The integration uses the official 3CX V20 Configuration API with client-credentials authentication. It creates a central PBX device and individual Home Assistant devices and sensor entities for all users/extensions returned by the V20 Users endpoint.

## Current functions

- Connection status for the 3CX V20 system
- Official authentication through `/connect/token`
- Automatic bearer-token renewal
- Number of configured extensions
- One Home Assistant device for every 3CX user/extension
- Extension number as sensor state
- Attributes for 3CX ID, first name, last name and display name
- Automatic discovery of newly created extensions
- Removed extensions are marked unavailable
- API mode display

## Not yet implemented

- Live active-call data
- Incoming-call events
- Registration and presence status
- DND switching
- Queue login and logout

Live call information belongs to the separate 3CX Call Control API and is not simulated by this integration.

## 3CX V20 preparation

Create an API application in the 3CX administration interface under **Integrations → API** and enable Configuration API access. Save the generated client ID and API key securely.

Use only the permissions required for reading users and extensions. Broader administrative permissions are not required for version 0.3.0.

## Installation for development

Copy `custom_components/threecx` to the Home Assistant configuration directory:

```text
/config/custom_components/threecx
```

Restart Home Assistant and add **3CX V20** under **Settings → Devices & services → Add integration**.

Enter:

- 3CX host name or IP address
- HTTPS port
- Client ID
- API key
- SSL verification setting

## Extension entities

Each 3CX user returned by `/xapi/v1/Users` becomes a separate device below the central PBX device. The extension sensor uses the extension number as its state and exposes these attributes:

```text
3cx_id
number
first_name
last_name
display_name
```

The stable 3CX user ID is used internally so renaming a user or changing the extension number does not create a duplicate entity.

## Important

This is an unofficial private integration and is not affiliated with 3CX or Home Assistant.
