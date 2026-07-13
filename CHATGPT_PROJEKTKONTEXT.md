# ChatGPT-Projektkontext – 3CX-HomeAssistant

## Verbindliche Projektdaten

- Repository: `Markus4771/3CX-HomeAssistant`
- Sichtbarkeit: privat
- Hauptbranch: `main`
- Aktuelle Version: `0.1.0`
- Umfang: ausschließlich Home Assistant und 3CX
- Keine Integration von Odoo, ContactsSync, Stempeluhr, Nextcloud oder Zammad

## Ziel

Entwicklung einer nativen Home-Assistant-Custom-Integration für eine direkt angebundene 3CX-Telefonanlage. Installation erfolgt unter `custom_components/threecx` und später optional über HACS.

## Ist-Stand 0.1.0

- Home-Assistant-Manifest vorhanden
- Einrichtung über die Home-Assistant-Oberfläche
- Konfigurierbar: Host/URL, Port, Benutzername, Passwort, SSL-Prüfung und API-Modus
- Sicherer Erreichbarkeitstest gegen den 3CX-Webdienst
- DataUpdateCoordinator mit 30 Sekunden Aktualisierungsintervall
- Entitäten für Verbindung, Nebenstellenanzahl, aktive Gespräche und API-Modus
- Deutsche Übersetzung

## Technische Grenze der Version 0.1.0

Die Integration ruft bewusst noch keine undokumentierten 3CX-Endpunkte auf. Nebenstellen- und Gesprächszähler bleiben daher bei null, bis die konkrete 3CX-Version, Lizenz/API-Verfügbarkeit und Authentifizierung geprüft sind.

## Nächste Aufgaben

1. Eingesetzte 3CX-Hauptversion und Build feststellen.
2. Verfügbare offizielle API und Authentifizierung prüfen.
3. API-Login und Tokenverwaltung implementieren.
4. Nebenstellen und Präsenzstatus einlesen.
5. Aktive/eingehende Gespräche als Zustände und Ereignisse bereitstellen.
6. Tests und GitHub Actions ergänzen.
7. Installationsprüfung auf einer Home-Assistant-Testinstanz durchführen.

## Entwicklungsregel

Vor jeder Weiterentwicklung zuerst `NEUER_CHAT.md`, danach diese Datei, `version.txt`, `README.md`, `CHANGELOG.md` und anschließend den tatsächlichen Quellcode lesen. Ausschließlich auf Basis des aktuellen GitHub-Stands arbeiten.
