# GeORG ↔ Home Assistant – Konzept

**Ziel:** Heizungen, die in **Home Assistant (HA)** eingebunden sind, automatisch anhand der Raumbelegungen in **GeORG** steuern.

**Entscheidung:** Wir bauen eine eigene **HA-Integration** (Custom Integration). Sie fragt die GeORG-API regelmäßig ab (**Pull**), stellt die Heizungen danach ein und meldet den Zustand an GeORG zurück.

---

## 1. Grundprinzip

- In GeORG werden **Termine, Raumbelegungen und die Einstellungen zur Heizungssteuerung** verwaltet.
- **GeORG berechnet die Heizfenster.** Die API liefert pro Raum fertige Zeiträume mit Solltemperatur. Home Assistant setzt sie nur um und hat keine eigene Heizlogik.
- Die API gibt **nur Belegungen aus, für die auch geheizt werden soll.** Welche Belegungen das sind, entscheidet GeORG.
- Die Verbindung wird **immer von HA nach außen** aufgebaut, auch für die Rückmeldung. Es braucht keine Portfreigabe, kein VPN und keinen HA-Token in GeORG.
- **Eine HA-Installation ist mit genau einer GeORG-Instanz** verbunden.
- Fällt GeORG aus oder ist das Internet weg, arbeitet HA mit den zuletzt abgerufenen Heizfenstern weiter. Wichtig: Der Cache enthält zwingend einen Absenk-Schaltpunkt. Heizungen müssen also immer wieder ausgehen können.

---

## 2. Heizeinstellungen in GeORG

GeORG pflegt je Raum:

| Einstellung | Bedeutung |
| --- | --- |
| Komforttemperatur | Solltemperatur während eines Heizfensters |
| Absenktemperatur | Solltemperatur außerhalb der Heizfenster |
| Vorlaufzeit | so lange vor Beginn der Belegung wird geheizt, bei Fußbodenheizung entsprechend länger |
| Nachlaufzeit | so lange nach dem Ende der Belegung bleibt die Komforttemperatur bestehen, bzw. so früh wird schon abgesenkt |
| Heizperiode | Zeitraum im Jahr, in dem überhaupt gesteuert wird |

Aus diesen Einstellungen und den Belegungen berechnet GeORG die **Heizfenster**. Überschneiden sich Belegungen oder folgen sie dicht aufeinander, werden sie in GeORG zu einem Fenster zusammengefasst.

---

## 3. Datenfluss

```text
GeORG-Instanz                            Home Assistant (beim Verein)
┌──────────────────────┐                 ┌───────────────────────────────┐
│ Heizeinstellungen    │   GET (Pull)    │ Coordinator (Polling)         │
│ Raumbelegungen       │ ◄────────────── │   ↓ Cache (persistent)        │
│   ↓                  │                 │ Scheduler: Schaltpunkte       │
│ Heizfenster-API      │                 │   ↓                           │
│                      │   POST Status   │ climate / switch ansteuern    │
│ Status-Empfang       │ ◄────────────── │ Ist-Temp. + gesetzter Soll    │
└──────────────────────┘   (OAuth2)      └───────────────────────────────┘
```

1. Der **Coordinator** (`DataUpdateCoordinator`) ruft die Räume und die Heizfenster der nächsten Tage ab, zum Beispiel alle 5 Minuten.
2. Die Heizfenster werden **persistent gecacht** (`homeassistant.helpers.storage.Store`) und überstehen so auch einen Neustart von HA ohne Internet.
3. Der **Scheduler** legt für jeden Beginn und jedes Ende eines Heizfensters einen Schaltpunkt an (`async_track_point_in_time`). Ändern sich die Fenster, werden die Schaltpunkte neu berechnet.
4. An jedem Schaltpunkt werden die zugeordneten Heizgeräte gesetzt (siehe Abschnitt 4).
5. Die **Rückmeldung** schickt die Ist-Temperaturen und den zuletzt gesetzten Sollwert je Raum per `POST` an GeORG. Das passiert nach jedem Schaltpunkt und zusätzlich in einem festen Intervall.

---

## 4. Ansteuerung der Heizgeräte

Ein GeORG-Raum kann **mehrere Geräte** haben. Unterstützt werden:

| Gerätetyp | HA-Entität | im Heizfenster | außerhalb |
| --- | --- | --- | --- |
| Heizkörperthermostat | `climate` | `set_temperature` Komfort | `set_temperature` Absenk |
| Fußbodenheizung | `climate` | wie Thermostat, längere Vorlaufzeit in GeORG | wie Thermostat |
| Elektroheizung, Steckdose | `switch` | `turn_on` | `turn_off` |

Ist ein `climate`-Gerät aus (`hvac_mode: off`), wird es am Beginn eines Heizfensters in den Heizmodus geschaltet.

**Manuelle Änderungen vor Ort** bleiben bis zum nächsten Schaltpunkt bestehen. Die Integration schreibt nur an Schaltpunkten und korrigiert zwischendurch nichts. Wird eine Abweichung vom Sollwert erkannt, meldet die Integration sie über die Rückmeldung an GeORG („manuell übersteuert“).

**Außerhalb der Heizperiode** liefert GeORG keine Heizfenster, und die Integration schaltet nichts.

---

## 5. Verhalten bei Störungen

| Situation | Verhalten |
| --- | --- |
| GeORG nicht erreichbar | Die Integration arbeitet mit den gecachten Heizfenstern weiter. Nach dem letzten bekannten Fenster bleibt der Raum auf Absenktemperatur. |
| Längerer Ausfall (Schwelle konfigurierbar, z. B. 12 h) | Die Integration legt einen **HA-Reparaturhinweis** an (`issue_registry`). |
| OAuth-Token ungültig bzw. Anmeldung abgelaufen | Es startet ein **Reauth-Flow**, der in HA als Reparaturhinweis bzw. Benachrichtigung erscheint. |
| Heizgerät nicht verfügbar | Das Gerät wird übersprungen, die Integration protokolliert es und meldet es an GeORG. |

**In GeORG** ist zu sehen, wann sich HA zuletzt gemeldet hat. Das ergibt sich aus den Abrufen und Rückmeldungen. GeORG kann daraus selbst einen Hinweis erzeugen, etwa „Heizungssteuerung seit 24 h ohne Kontakt“.

---

## 6. Authentifizierung: OAuth2

- Die Integration nutzt den OAuth2-Flow von HA (`config_entry_oauth2_flow`) mit **Authorization Code + PKCE**.
- Weil jede GeORG-Instanz eine eigene URL hat, baut die Integration die OAuth2-Implementierung dynamisch aus der eingegebenen Instanz-URL (`/oauth/authorize`, `/oauth/token`).
- Die **Client-ID** ist fest und in jeder GeORG-Instanz als öffentlicher Client registriert. Deshalb braucht es kein Client-Secret und keine Application Credentials beim Verein.
- Als Redirect wird `https://my.home-assistant.io/redirect/oauth` verwendet. Das funktioniert auch bei HA-Instanzen, die von außen nicht erreichbar sind.
- **Scopes**, zum Beispiel `heating:read` (Räume, Heizfenster) und `heating:report` (Rückmeldung). Der Token darf nichts anderes in GeORG.
- Welcher GeORG-Benutzer bzw. welche Rolle die Integration autorisieren darf, legt GeORG fest.

---

## 7. Einrichtung in Home Assistant

1. **Instanz-URL** der GeORG-Instanz eingeben (z. B. `georg.lkg-spremberg.de`).
2. **Anmeldung** per OAuth2 im Browser gegen diese Instanz.
3. Die Integration lädt die **Räume** aus GeORG.
4. **Raumzuordnung** im Options Flow: Jedem GeORG-Raum werden eine oder mehrere `climate`- bzw. `switch`-Entitäten zugeordnet. Die Zuordnung lässt sich jederzeit ändern.

Die Zuordnung liegt **nur in HA**. GeORG kennt die Entitäten in HA nicht. Es bekommt über die Rückmeldung nur die Werte je Raum.

`manifest.json` enthält `"single_config_entry": true`, weil es genau eine GeORG-Instanz pro HA gibt.

---

## 8. Entitäten in Home Assistant

Pro GeORG-Raum ein **Gerät** mit:

| Entität | Zweck |
| --- | --- |
| `calendar.<raum>_heizfenster` | Heizfenster des Raums (sichtbar im HA-Kalender) |
| `sensor.<raum>_naechstes_heizfenster` | Beginn des nächsten Heizfensters |
| `sensor.<raum>_solltemperatur` | aktueller Sollwert laut GeORG |
| `binary_sensor.<raum>_heizphase` | Heizfenster aktiv ja/nein |
| `binary_sensor.<raum>_uebersteuert` | manuell vom Sollwert abweichend |
| `switch.<raum>_automatik` | GeORG-Steuerung für diesen Raum ein/aus |

Zusätzlich gibt es ein Gerät „GeORG-Verbindung“ mit `binary_sensor` Verbindungsstatus und `sensor` letzter erfolgreicher Abruf.

Die Heizgeräte selbst bleiben die vorhandenen Entitäten des Vereins. Die Integration steuert sie nur an.

---

## 9. GeORG-API (Skizze)

Die Details kommen in eine eigene OpenAPI-Spezifikation (`api/openapi.yaml`). Grobe Struktur:

| Methode | Pfad | Scope | Inhalt |
| --- | --- | --- | --- |
| `GET` | `/api/heating/v1/rooms` | `heating:read` | Räume: ID, Name, Komfort- und Absenktemperatur |
| `GET` | `/api/heating/v1/windows?from=…&to=…` | `heating:read` | Heizfenster: Raum-ID, Beginn, Ende, Solltemperatur, Änderungszeitpunkt |
| `POST` | `/api/heating/v1/status` | `heating:report` | je Raum: Ist-Temperatur, gesetzter Sollwert, Zeitpunkt, übersteuert ja/nein |

- Zeiten werden in ISO 8601 mit Zeitzone angegeben (Europe/Berlin).
- Ein `ETag` bzw. `If-None-Match` auf `/windows` spart unnötige Übertragungen beim Polling.
- Heizfenster enthalten keine Termintitel oder Personendaten. So gelangen nur die für die Heizung nötigen Daten in HA.

---

## 10. Verteilung

Die Integration liegt in HA unter `config/custom_components/georg/` und wird in einem **eigenen, öffentlichen GitHub-Repo** entwickelt.

- **HACS Custom Repository** (Start): Der Verein trägt die Repo-URL in HACS ein. In GeORG erledigt ein „My Home Assistant“-Button das mit einem Klick:
  `https://my.home-assistant.io/redirect/hacs_repository/?owner=<org>&repository=<repo>&category=integration`
- **HACS-Standardliste** (sobald stabil): Dafür braucht es einen PR beim HACS-Projekt, die Validierung durch hassfest und die HACS-Action, eine `hacs.json`, Releases und ein Logo.
- **Manuelle Installation** als Fallback: ZIP entpacken nach `custom_components`, ohne automatische Updates.
- **Core-Integration** ist ein optionales Ziel für später.

Der Quelltext ist ohnehin bei jedem Verein einsehbar. Schützenswert sind die GeORG-API und die Zugangsdaten, nicht der Code der Integration.

---

## 11. Aufbau des Repos

```text
georg-homeassistant/
├── api/
│   └── openapi.yaml         # Spezifikation der GeORG-Heizungs-API
├── custom_components/georg/
│   ├── __init__.py          # Setup, Coordinator, Scheduler starten
│   ├── manifest.json        # domain, version, requirements, iot_class: cloud_polling
│   ├── oauth.py             # dynamische OAuth2-Implementierung (PKCE, feste Client-ID)
│   ├── config_flow.py       # Instanz-URL, OAuth2, Reauth, Raumzuordnung (Options)
│   ├── coordinator.py       # pollt die GeORG-API, Cache
│   ├── scheduler.py         # Schaltpunkte, Ansteuerung climate/switch
│   ├── reporter.py          # Rückmeldung an GeORG
│   ├── calendar.py / sensor.py / binary_sensor.py / switch.py
│   ├── strings.json + translations/de.json
│   └── brand/               # Icon/Logo
├── tests/                   # pytest-homeassistant-custom-component
├── hacs.json
└── .github/workflows/       # hassfest, HACS-Validierung, Tests
```

Der **API-Client** wird von Anfang an als eigenes kleines Python-Paket (z. B. `pygeorg`) auf PyPI angelegt und in `manifest.json` unter `requirements` referenziert. Das erspart den Umbau, falls die Integration später in den Core soll.

---

## 12. Vorgehen

1. **OpenAPI-Spezifikation** der Heizungs-API entwerfen und mit GeORG abstimmen (inkl. OAuth2-Client und Scopes).
2. **Mock-Server** aus der Spezifikation erzeugen, damit die Integration parallel zur API entwickelt werden kann.
3. Öffentliches Repo unter einer GeORG-Organisation auf GitHub anlegen.
4. Grundgerüst: `manifest.json`, Config Flow (Instanz-URL + OAuth2), Coordinator mit Cache.
5. Scheduler und Ansteuerung von `climate` und `switch`, dazu die Entitäten.
6. Rückmeldung, Reparaturhinweise, Reauth.
7. Releases mit semantischer Versionierung, Verteilung als HACS Custom Repository.
8. Sobald stabil: Aufnahme in die HACS-Standardliste beantragen.

---

## 13. Offene Punkte

- Polling-Intervall und Horizont der abgerufenen Heizfenster (z. B. 15 min, 7 Tage)
- Intervall der Rückmeldung an GeORG und ab welcher Abweichung „übersteuert“ gilt
- Schwelle für den Reparaturhinweis bei Ausfall
- GitHub-Organisation, Lizenz, Mindestversion von Home Assistant
