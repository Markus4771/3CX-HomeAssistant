# 3CX Protocol Explorer

Version **0.1.0**

Der Protocol Explorer ist ein eigenständiges Diagnosewerkzeug für die Entwicklung der 3CX-Home-Assistant-Integration. Er arbeitet ausschließlich lesend.

## Funktionen in 0.1.0

- lädt die Startseite des 3CX-Webclients;
- ermittelt eingebundene JavaScript-Dateien;
- durchsucht JavaScript nach WebSocket-, SignalR-, Call-Control-, Queue- und Subscription-Hinweisen;
- prüft gefundene und bekannte WebSocket-Pfade mit Bearer-Authentifizierung;
- wartet kurz auf eingehende Frames;
- erstellt einen bereinigten JSON-Bericht;
- schreibt keine Konfiguration und führt keine Queue-An-/Abmeldung aus.

## Installation unter Debian oder Raspberry Pi OS

```bash
sudo apt update
sudo apt install -y python3 python3-venv
cd tools/protocol_explorer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Aufruf

```bash
python protocol_explorer.py \
  --base-url https://telefon01.example.de \
  --client-id 'DEINE_CLIENT_ID' \
  --client-secret 'DEIN_CLIENT_SECRET' \
  --output 3cx_protocol_report.json
```

Bei einem internen oder selbst signierten Zertifikat kann vorübergehend ergänzt werden:

```bash
--no-verify-ssl
```

Das Abschalten der Zertifikatsprüfung sollte nur im vertrauenswürdigen internen Netz erfolgen.

## Ergebnis

Der Bericht enthält insbesondere:

```json
{
  "script_count_discovered": 0,
  "websocket_paths_tested": 0,
  "summary": {
    "successful_http_root": true,
    "successful_websocket_upgrades": 1,
    "websockets_with_frames": 0
  }
}
```

Interessant sind außerdem:

- `scripts[].findings.websocket_paths`
- `scripts[].findings.protocol_terms`
- `websocket_probes[].connected`
- `websocket_probes[].frames_received`
- `websocket_probes[].first_frame_preview`

## Sicherheit

- Tokens und Client-Secrets werden nicht absichtlich in den Bericht geschrieben.
- Bekannte sensible Felder sowie Bearer- und Query-Tokens werden vor dem Speichern bereinigt.
- Den Bericht trotzdem vor einer öffentlichen Weitergabe prüfen.
- Das Programm sollte nur gegen eine eigene oder ausdrücklich freigegebene 3CX-Anlage eingesetzt werden.

## Nächste Ausbaustufe

Für Version 0.2.0 sind vorgesehen:

- Analyse dynamisch nachgeladener JavaScript-Chunks;
- Extraktion konkreter Handshake- und Subscription-Nachrichten;
- optionaler Browser-Netzwerkmitschnitt;
- Vergleich zweier Zustände, etwa Queue angemeldet und abgemeldet.
