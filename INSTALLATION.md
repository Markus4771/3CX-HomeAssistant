# Installation – 3CX Home Assistant

## Voraussetzungen

- Home Assistant mit Zugriff auf den Ordner `/config`
- 3CX V20
- In 3CX eingerichtete API-Anwendung mit Zugriff auf die Configuration API
- Client-ID und API-Key der 3CX-API-Anwendung

## Variante A: Installation über HACS

Da dieses Repository privat ist, muss das GitHub-Konto, mit dem HACS verbunden ist, Zugriff auf das Repository haben.

1. HACS in Home Assistant öffnen.
2. **Integrationen** öffnen.
3. Rechts oben das Menü öffnen und **Benutzerdefinierte Repositories** wählen.
4. Repository eintragen:
   `Markus4771/3CX-HomeAssistant`
5. Kategorie **Integration** auswählen.
6. **3CX Home Assistant** öffnen und herunterladen.
7. Home Assistant vollständig neu starten.
8. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **3CX V20** suchen.

## Variante B: Manuelle Installation

1. Das Release-Artefakt `threecx.zip` herunterladen und entpacken.
2. Den entpackten Ordner als `threecx` nach folgendem Ziel kopieren:

   `/config/custom_components/threecx/`

3. Prüfen, dass diese Datei vorhanden ist:

   `/config/custom_components/threecx/manifest.json`

4. Home Assistant vollständig neu starten.
5. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach **3CX V20** suchen.

## 3CX V20 vorbereiten

1. In der 3CX-Administrationsoberfläche **Integrationen → API** öffnen.
2. Eine neue API-Anwendung anlegen.
3. Zugriff auf die **Configuration API** aktivieren.
4. Eine passende Rolle mit mindestens Leserechten für Benutzer/Nebenstellen wählen.
5. Client-ID und API-Key sicher speichern.

## Integration einrichten

Folgende Angaben werden benötigt:

- 3CX-Adresse, beispielsweise `pbx.example.de`
- Port, normalerweise `443`
- Client-ID
- API-Key
- SSL-Prüfung aktiviert lassen, wenn ein gültiges Zertifikat verwendet wird

## Aktualisierung

Über HACS kann eine neuere Version direkt aktualisiert werden. Bei manueller Installation den vorhandenen Ordner `/config/custom_components/threecx` durch die Dateien der neuen Version ersetzen und Home Assistant neu starten.

## Fehlerbehebung

### Integration wird nicht gefunden

- Home Assistant wirklich neu starten, nicht nur YAML neu laden.
- Ordnerstruktur prüfen: Es darf nicht versehentlich `threecx/threecx/manifest.json` entstanden sein.
- Home-Assistant-Protokoll auf Fehler unter `threecx` prüfen.

### Anmeldung schlägt fehl

- Client-ID und API-Key prüfen.
- Sicherstellen, dass die API-Anwendung Zugriff auf die Configuration API besitzt.
- 3CX-Adresse ohne zusätzliche Pfade wie `/webclient` eintragen.

### SSL-Fehler

Bei einem selbst signierten Zertifikat kann die SSL-Prüfung testweise deaktiviert werden. Für den dauerhaften Betrieb wird ein gültiges Zertifikat empfohlen.
