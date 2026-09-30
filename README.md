# GeORG Heizungssteuerung für Home Assistant

<img src="assets/icon.svg" alt="" width="96">

Custom Integration, die Heizungen in Home Assistant anhand der Raumbelegungen in **GeORG** schaltet.
GeORG entscheidet, Home Assistant setzt um: Die Integration meldet in einem festen Intervall Ist-Temperatur und Luftfeuchte der Räume an GeORG und erhält in derselben Antwort die Schaltanforderungen. Die Verbindung geht immer von Home Assistant nach außen – keine Portfreigabe, kein VPN.

Das vollständige Konzept steht in [KONZEPT.md](KONZEPT.md), die API in [api/openapi.yaml](api/openapi.yaml).

## Installation

**HACS (Custom Repository):** HACS → Integrationen → ⋮ → Benutzerdefinierte Repositories → `https://github.com/sekru1/georg-homeassistant`, Kategorie *Integration*. Danach „GeORG Heizungssteuerung“ installieren und Home Assistant neu starten.

**Manuell:** Den Ordner `custom_components/georg` nach `config/custom_components/georg` kopieren und Home Assistant neu starten.

## Einrichtung

1. In GeORG unter **Organisationseinstellungen → API-Zugänge** einen Zugang anlegen (z. B. „Home Assistant“) mit dem Recht **Heizungssteuerung**. Den Token kopieren – er wird nur einmal angezeigt.
   **Pro Home-Assistant-Installation ein eigener API-Zugang**, sonst nehmen sich die Installationen die Schaltanforderungen gegenseitig weg.
2. In Home Assistant: Einstellungen → Geräte & Dienste → Integration hinzufügen → **GeORG Heizungssteuerung**. GeORG-Adresse (z. B. `https://georg.example.org`) und Token eingeben.
3. In den **Optionen** der Integration unter „Raum zuordnen“ jedem GeORG-Raum Heizgeräte (`climate`, `switch`) und optional einen Temperatur- und Feuchtesensor zuordnen. Mit „Speichern und schließen“ übernehmen.
   Ohne Temperatursensor wird `current_temperature` des ersten Thermostats gemeldet.
4. Unter „Einstellungen“ lassen sich Meldeintervall (Standard 120 s) und Karenz der Ausfall-Absicherung (Standard 30 min) anpassen.

Nur zugeordnete Räume werden an GeORG gemeldet.

## Schaltverhalten

Geschaltet wird ausschließlich, wenn GeORG eine neue Anforderung schickt (`command` ≠ `null`). Manuelle Änderungen am Thermostat oder in Home Assistant bleiben deshalb bis zur nächsten echten Änderung in GeORG bestehen.

| Gerät | Heizen (`on`) | Nicht heizen (`off`) |
| --- | --- | --- |
| `climate` | Heizmodus einschalten, falls aus; Solltemperatur = Komforttemperatur | Solltemperatur = Absenktemperatur |
| `switch` | einschalten | ausschalten |

Ist in GeORG keine Temperatur gepflegt, bleiben Thermostate unverändert; Schalter werden trotzdem geschaltet.
Ist ein Gerät gerade nicht verfügbar, wird die Anforderung nachgeholt, sobald es wieder verfügbar ist.

## Entitäten

Je zugeordnetem Raum ein Gerät mit:

| Entität | Bedeutung |
| --- | --- |
| Heizen (`binary_sensor`) | Letzte Anforderung von GeORG. Attribute: Empfangszeit, Ausfall-Absicherung aktiv, noch nicht geschaltete Geräte. |
| Solltemperatur (`sensor`) | Zieltemperatur der letzten Anforderung. |
| Heizfenster Beginn / Ende / Termin (`sensor`) | Laufendes oder nächstes Heizfenster laut GeORG. |
| Automatik (`switch`) | Aus = Anforderungen von GeORG für diesen Raum ignorieren. Beim Wiedereinschalten wird die aktuelle Anforderung neu abgeholt. |

Dazu das Gerät „GeORG-Verbindung“ mit Verbindungsstatus und Zeitpunkt des letzten erfolgreichen Abrufs.

## Störungen

| Situation | Verhalten |
| --- | --- |
| GeORG nicht erreichbar | Geräte bleiben im letzten Zustand. Ist ein Raum am Heizen und das Heizfenster plus Karenz abgelaufen, wird er lokal abgesenkt bzw. ausgeschaltet (Thermostate ohne bekannte Absenktemperatur werden ausgeschaltet). Nach Wiederverbindung wird mit `force: true` gemeldet. |
| Länger als 12 h nicht erreichbar | Reparaturhinweis. |
| Token ungültig / API-Zugang deaktiviert (401/403) | Home Assistant fragt nach einem neuen Token. |
| Modul „Heizungssteuerung“ nicht gebucht (404) | Reparaturhinweis. |
| Raum in GeORG unbekannt (422) | Raum wird nicht mehr gemeldet, Reparaturhinweis „Raum neu zuordnen“; die übrigen Räume laufen weiter. |
| Neustart / geänderte Zuordnung | Erste Meldung mit `force: true`. |

## Entwicklung

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest
uvx ruff check . && uvx ruff format --check .
```

Aufbau:

| Datei | Aufgabe |
| --- | --- |
| `api.py` | API-Client für `/heating/rooms` und `/heating/sync` inkl. Fehlerklassen – ohne HA-Abhängigkeiten, später als `pygeorg` auslagerbar |
| `coordinator.py` | Meldung, Anforderungen verteilen, persistenter Zustand (`Store`), Ausfall-Absicherung, Reparaturhinweise |
| `controller.py` | Ansteuerung von `climate` und `switch` |
| `config_flow.py` | Einrichtung, Reauth, Rekonfiguration, Raumzuordnung (Optionen) |
| `sensor.py`, `binary_sensor.py`, `switch.py` | Entitäten |
| `brand/icon.png`, `brand/icon@2x.png` | Icon in Home Assistant (256 bzw. 512 px), erzeugt aus [assets/icon.svg](assets/icon.svg) |

Icons nach Änderung am SVG neu erzeugen:

```bash
uvx --with cairosvg --with pillow python scripts/render_icons.py
```
