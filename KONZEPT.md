# GeORG ↔ Home Assistant – Konzept

**Ziel:** Heizungen, die in **Home Assistant (HA)** eingebunden sind, automatisch anhand der Raumbelegungen in **GeORG** steuern.

**Entscheidung:** Wir bauen eine eigene **HA-Integration** (Custom Integration) in einem eigenen Repo. Sie meldet in einem festen Intervall die Messwerte der Räume an GeORG (**Pull**) und bekommt dabei **Schaltanforderungen** zurück, die sie an den zugeordneten Heizgeräten umsetzt.

> Stand: 2026-09-30. Die GeORG-Seite (API, Einstellungen, Übersicht) ist umgesetzt. Dieses Dokument beschreibt den verbindlichen Vertrag für die Integration.

---

## 1. Grundprinzip

- **GeORG entscheidet, HA setzt um.** GeORG pflegt Termine, Raumbelegungen und Heizeinstellungen und entscheidet zu jedem Zeitpunkt, ob ein Raum heizen soll. HA hat keine eigene Heizlogik.
- **Eine Anfrage für alles:** HA meldet je Raum Ist-Temperatur und Luftfeuchte und erhält in derselben Antwort die Schaltanforderung.
- **Jede Schaltanforderung kommt genau einmal.** GeORG merkt sich je API-Zugang, welche Anforderung zuletzt gesendet wurde. Solange sich nichts ändert, kommt `command: null`. Manuelle Änderungen vor Ort (am Thermostat, in HA) bleiben dadurch bis zur nächsten echten Änderung bestehen.
- **Die Verbindung geht immer von HA nach außen.** Es braucht keine Portfreigabe, kein VPN und keinen HA-Token in GeORG.
- **Eine HA-Installation ist mit genau einer GeORG-Instanz** verbunden und nutzt dafür **einen eigenen API-Zugang**.
- Die Schaltgenauigkeit ergibt sich aus dem **Meldeintervall**: GeORG entscheidet im Moment der Meldung.

---

## 2. Heizeinstellungen in GeORG

### Je Raum („Raum bearbeiten → Heizungssteuerung")

| Einstellung | Bedeutung |
| --- | --- |
| Heizung für diesen Raum aktiv | Nur aktive Räume erscheinen an der API. |
| Vorlaufzeit (min) | So lange vor Beginn einer Belegung wird geheizt. Bei Fußbodenheizung entsprechend länger. |
| Vorlaufzeit dynamisch bei … °C | Optional: Vorlaufzeit skaliert mit der gemeldeten Ist-Temperatur (siehe 3.2). |
| Nachlaufzeit (± min) | So lange nach dem Ende bleibt geheizt; negativ = schon vor dem Ende absenken. |
| Komforttemperatur | Solltemperatur während des Heizens. |
| Auto-Regelung + Hysterese (K) | Optional: Zweipunktregelung durch GeORG anhand der Ist-Temperatur (siehe 3.3). |
| Absenktemperatur | Solltemperatur außerhalb des Heizens. |

### Global („Raumplan" bzw. „Ressourcen" → „Heizungseinstellungen")

| Einstellung | Bedeutung |
| --- | --- |
| Heizungssteuerung | `Aktiv` (immer), `Automatik` (nur in der Heizperiode), `Inaktiv` (nie). |
| Beginn / Ende der Heizperiode | Kalenderwochen, nur bei `Automatik` relevant; darf über den Jahreswechsel gehen (z. B. KW 40 bis KW 18). |

Änderungen werden sofort gespeichert. Der Dialog zeigt außerdem je Raum Einstellungen, zuletzt gemeldete Temperatur/Feuchte, Zeitpunkt der letzten Meldung, Schaltzustand und das nächste Heizereignis.

### Je Termin

| Feld | Bedeutung |
| --- | --- |
| Heizung an/aus | Nur Termine mit „an" (Standard) lösen Heizen aus. |
| Heizung reduzieren | Vorlaufzeit auf ein Viertel verkürzt. |

---

## 3. Entscheidungslogik in GeORG

Bei jeder Meldung berechnet GeORG je Raum:

### 3.1 Soll geheizt werden?

Ein Raum soll heizen, wenn **alle** Bedingungen erfüllt sind:

1. Heizungssteuerung des Raums ist aktiv.
2. Global: Modus `Aktiv`, oder Modus `Automatik` und das heutige Datum liegt in der Heizperiode.
3. Es gibt einen Termin mit „Heizung an" in diesem Raum, für den gilt:
   `Beginn − Vorlaufzeit ≤ jetzt < Ende + Nachlaufzeit`
   (Vorlaufzeit bei „Heizung reduzieren": ein Viertel).
4. Bei aktiver Auto-Regelung zusätzlich die Hysterese-Bedingung (3.3).

Überlappende Termine werden **nicht** zu einem Fenster zusammengefasst; es genügt, dass irgendein Termin die Bedingung erfüllt.

### 3.2 Dynamische Vorlaufzeit

Nur wenn aktiviert und der Raum aktuell **nicht** heizt (letzte Anforderung `off`):

```text
faktor      = (Komfort − Ist) / (Komfort − Basistemperatur)
vorlaufzeit = round(faktor × eingestellte Vorlaufzeit)     // 0, wenn Ist ≥ Komfort
```

Ein kalter Raum beginnt also früher, ein warmer später. Voraussetzung ist, dass HA die Ist-Temperatur meldet.

### 3.3 Auto-Regelung (Zweipunktregler)

Nur innerhalb eines Heizzeitraums:

| Ist-Temperatur | Ergebnis |
| --- | --- |
| unbekannt (nie gemeldet oder `null`) | an |
| ≥ Komfort + Hysterese/2 | aus |
| ≤ Komfort − Hysterese/2 | an |
| dazwischen | letzter gesendeter Zustand bleibt |

Gedacht für schaltbare Geräte (Steckdose, Elektroheizung) ohne eigenen Thermostat. **Achtung:** Schaltet die Regelung auf „aus", ist die Anforderung `off` mit Absenktemperatur – ein Thermostat in einem solchen Raum würde also abgesenkt. Räume mit Thermostaten daher ohne Auto-Regelung betreiben.

### 3.4 Die Schaltanforderung

| Ergebnis | `state` | `target_temperature` |
| --- | --- | --- |
| heizen | `on` | Komforttemperatur |
| nicht heizen | `off` | Absenktemperatur |

Die Anforderung wird gesendet, wenn sich `state` **oder** `target_temperature` gegenüber der zuletzt an diesen API-Zugang gesendeten Anforderung ändert – also auch, wenn in GeORG die Komfort- oder Absenktemperatur geändert wird. Beim allerersten Kontakt je Raum wird immer gesendet.

Außerhalb der Heizperiode bzw. bei Modus `Inaktiv` ist das Ergebnis dauerhaft `off` + Absenktemperatur; das wird einmal gesendet, danach kommt nichts mehr.

---

## 4. Ansteuerung der Heizgeräte (HA)

Ein GeORG-Raum kann **mehrere Geräte** haben. Beim Eintreffen einer Anforderung (`command` ≠ `null`):

| Gerätetyp | HA-Entität | `state: on` | `state: off` |
| --- | --- | --- | --- |
| Heizkörperthermostat, Fußbodenheizung | `climate` | Heizmodus sicherstellen (falls `hvac_mode: off`), `set_temperature` = `target_temperature` | `set_temperature` = `target_temperature` |
| Elektroheizung, Steckdose | `switch` | `turn_on` | `turn_off` |

- **Nur bei `command` ≠ `null` schalten.** Zwischendurch nichts korrigieren – so bleiben manuelle Änderungen erhalten.
- `target_temperature` kann `null` sein, wenn in GeORG keine Temperatur gepflegt ist → `climate` dann nicht verändern, `switch` trotzdem schalten.
- Ist ein Gerät nicht verfügbar, wird es übersprungen und protokolliert. Da GeORG die Anforderung als gesendet betrachtet, sollte die Integration die zuletzt empfangene Anforderung je Raum lokal speichern und beim Wiederverfügbarwerden des Geräts nachholen.

---

## 5. Verhalten bei Störungen

| Situation | Verhalten |
| --- | --- |
| GeORG nicht erreichbar | Keine neuen Anforderungen; Geräte bleiben im letzten Zustand. **Empfohlene Absicherung:** Ist ein Raum `on` und `window.ends_at` (siehe API) plus Karenz (z. B. 30 min) überschritten, ohne dass GeORG erreichbar war, lokal auf Absenken bzw. `turn_off` schalten. So kann keine Heizung dauerhaft anbleiben. |
| Längerer Ausfall (12 h) | HA-Reparaturhinweis (`issue_registry`). |
| 401 / 403 (Token ungültig, gelöscht oder API-Zugang deaktiviert) | Reauth-Flow: neuen Token abfragen. |
| 404 | Modul „Heizungssteuerung" in GeORG nicht (mehr) gebucht → Reparaturhinweis. |
| 422 mit `rooms.N.id` | Raum unbekannt oder Heizungssteuerung dort deaktiviert (z. B. Slug geändert). **Die gesamte Meldung wird abgelehnt.** Räume neu laden, betroffenen Raum aus der Meldung nehmen und Reparaturhinweis „Raum neu zuordnen" anlegen. |
| HA-Neustart / Cache verloren / Zuordnung geändert | Einmal mit `force: true` melden, um die aktuelle Anforderung aller Räume erneut zu bekommen. |

**In GeORG sichtbar:** „Zuletzt genutzt" am API-Zugang und „gemeldet vor …" je Raum in der Heizungsübersicht.

---

## 6. Authentifizierung: API-Zugang (Bearer-Token)

**Kein OAuth2.** Die Integration nutzt einen API-Zugang von GeORG:

1. In GeORG unter **Organisationseinstellungen → API-Zugänge** einen neuen Zugang anlegen, z. B. „Home Assistant", Recht **„Heizungssteuerung"** (`heating:control`).
2. Der Token wird **nur einmal** angezeigt und im HA-Config-Flow eingegeben.
3. Jede Anfrage: `Authorization: Bearer <token>` und `Accept: application/json`.

- Der Mandant (GeORG-Organisation) ergibt sich aus dem Token.
- Das Recht `heating:control` erlaubt nur die beiden Heizungs-Endpunkte.
- **Pro HA-Installation ein eigener API-Zugang**, denn der Stand der gesendeten Anforderungen hängt am Zugang. Zwei Installationen mit demselben Token würden sich die Anforderungen gegenseitig „wegnehmen".
- Wird der Token in GeORG neu erzeugt oder der Zugang deaktiviert, antwortet die API mit 401 bzw. 403.
- Benutzer-Tokens funktionieren für `/heating/sync` nicht (403).

---

## 7. Einrichtung in Home Assistant

1. **Instanz-URL** der GeORG-Instanz eingeben (z. B. `https://georg.lkg-spremberg.de`).
2. **Token** des API-Zugangs eingeben. Der Config-Flow prüft ihn mit `GET /api/v1/heating/rooms`.
3. Die Integration lädt die **Räume** aus GeORG.
4. **Raumzuordnung** im Options Flow: Jedem GeORG-Raum werden zugeordnet
   - eine oder mehrere `climate`- bzw. `switch`-Entitäten (Heizgeräte),
   - optional ein Temperatur- und ein Feuchtesensor (`sensor` mit `device_class` `temperature` / `humidity`). Ohne Sensor wird bei `climate` das Attribut `current_temperature` verwendet.
5. Nur zugeordnete Räume werden gemeldet. Nach jeder Änderung der Zuordnung einmal mit `force: true` melden.

Die Zuordnung liegt **nur in HA**; GeORG kennt die HA-Entitäten nicht. `manifest.json` enthält `"single_config_entry": true`.

---

## 8. Entitäten in Home Assistant

Pro zugeordnetem GeORG-Raum ein **Gerät** mit:

| Entität | Quelle |
| --- | --- |
| `binary_sensor.<raum>_heizen` | letzter empfangener `command.state` |
| `sensor.<raum>_solltemperatur` | letzter empfangener `command.target_temperature` |
| `sensor.<raum>_heizfenster_beginn` / `_ende` | `window.starts_at` / `window.ends_at` (Zeitstempel) |
| `sensor.<raum>_heizfenster_termin` | `window.name` |
| `switch.<raum>_automatik` | GeORG-Steuerung für diesen Raum ein/aus (nur in HA). Aus = Anforderungen ignorieren; beim Wiedereinschalten mit `force: true` melden. |

Zusätzlich ein Gerät „GeORG-Verbindung" mit `binary_sensor` Verbindungsstatus und `sensor` letzter erfolgreicher Abruf.

---

## 9. GeORG-API

**Basis-URL:** `https://<instanz>/api/v1` · JSON · Zeiten in ISO 8601 mit Offset (Europe/Berlin) · Temperaturen in °C, Feuchte in %.

Die OpenAPI-Spezifikation erzeugt GeORG automatisch (Scramble, Tag „Heating", `php artisan scramble:export` → `api.json`).

### 9.1 `GET /heating/rooms` – Räume auflisten

Alle Räume mit aktiver Heizungssteuerung, sortiert wie in GeORG (Kategorie, Reihenfolge, Name). Für den Config-/Options-Flow.

```http
GET /api/v1/heating/rooms
Authorization: Bearer 12|abc…
Accept: application/json
```

```json
{
  "data": [
    {
      "id": "gemeindesaal",
      "name": "Gemeindesaal",
      "comfort_temperature": 21.5,
      "eco_temperature": 16
    }
  ]
}
```

| Feld | Typ | Beschreibung |
| --- | --- | --- |
| `id` | string | Stabile Kennung des Raums (in GeORG der Slug). Damit wird der Raum bei `/heating/sync` gemeldet. Ändert sich nur, wenn der Slug in GeORG bewusst geändert wird. |
| `name` | string | Anzeigename. |
| `comfort_temperature` | number \| null | Komforttemperatur. |
| `eco_temperature` | number \| null | Absenktemperatur. |

### 9.2 `POST /heating/sync` – Zustand melden, Schaltanforderungen abholen

#### Anfrage

```http
POST /api/v1/heating/sync
Authorization: Bearer 12|abc…
Accept: application/json
Content-Type: application/json
```

```json
{
  "force": false,
  "rooms": [
    { "id": "gemeindesaal", "current_temperature": 18.7, "humidity": 52 },
    { "id": "kapelle" }
  ]
}
```

| Feld | Typ | Pflicht | Beschreibung |
| --- | --- | --- | --- |
| `force` | boolean | nein | `true` = aktuelle Anforderung aller gemeldeten Räume erneut senden (Neustart, geänderte Zuordnung). Standard `false`. |
| `rooms` | array | ja | 1–500 Einträge, jede `id` nur einmal. |
| `rooms[].id` | string | ja | `id` aus `/heating/rooms`. |
| `rooms[].current_temperature` | number \| null | nein | Ist-Temperatur (−50 … 100). Weglassen = alter Wert bleibt; `null` = Wert löschen (Sensor nicht verfügbar). |
| `rooms[].humidity` | number \| null | nein | Relative Luftfeuchte (0 … 100), Semantik wie oben. |

Nur die gemeldeten Räume werden ausgewertet. Die Messwerte werden **vor** der Entscheidung übernommen (wichtig für dynamische Vorlaufzeit und Auto-Regelung).

#### Antwort `200`

```json
{
  "data": [
    {
      "id": "gemeindesaal",
      "command": { "state": "on", "target_temperature": 21.5 },
      "window": {
        "name": "Gottesdienst",
        "starts_at": "2026-11-10T09:00:00+01:00",
        "ends_at": "2026-11-10T12:15:00+01:00"
      }
    },
    {
      "id": "kapelle",
      "command": null,
      "window": null
    }
  ]
}
```

| Feld | Typ | Beschreibung |
| --- | --- | --- |
| `id` | string | Raum, Reihenfolge wie in der Anfrage. |
| `command` | object \| null | Neue Schaltanforderung; `null` = seit der letzten Anforderung an diesen API-Zugang unverändert → **nichts tun**. |
| `command.state` | `"on"` \| `"off"` | Für schaltbare Geräte; bei `climate` Heizmodus sicherstellen. |
| `command.target_temperature` | number \| null | Komfort- (`on`) bzw. Absenktemperatur (`off`) für Thermostate. |
| `window` | object \| null | **Informativ:** laufender oder nächster Heizzeitraum des Raums, bezogen auf einen einzelnen Termin (mit Vor-/Nachlaufzeit). `null`, wenn kein Termin ansteht oder die Steuerung global aus ist bzw. keine Heizperiode ist. |
| `window.name` | string | Titel des auslösenden Termins. |
| `window.starts_at` / `window.ends_at` | string | Beginn/Ende des Heizzeitraums. |

`window` dient der Anzeige und der Absicherung bei Ausfällen (Abschnitt 5). **Geschaltet wird ausschließlich nach `command`.**

### 9.3 Fehler

Fehler kommen im Laravel-Format `{"message": "…", "errors": {…}}` (`errors` nur bei 422).

| Status | Bedeutung |
| --- | --- |
| 401 | Kein oder ungültiger Token. |
| 403 | Token ohne Recht `heating:control`, API-Zugang deaktiviert oder Benutzer-Token statt API-Zugang. |
| 404 | Modul „Heizungssteuerung" für die Organisation nicht gebucht. |
| 422 | Validierungsfehler, z. B. `rooms.0.id`: „Raum „kapelle" ist unbekannt oder nicht heizungsgesteuert." – dann wird **nichts** verarbeitet. |

### 9.4 Beispiel mit curl

```bash
curl -s https://georg.example.org/api/v1/heating/sync \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"rooms":[{"id":"gemeindesaal","current_temperature":18.7,"humidity":52}]}'
```

---

## 10. Ablauf in der Integration

```text
GeORG-Instanz                                   Home Assistant
┌───────────────────────────┐                   ┌──────────────────────────────────┐
│ Räume + Heizeinstellungen │  GET /rooms       │ Config-/Options-Flow             │
│                           │ ◄──────────────── │  (Räume zuordnen)                │
│ Entscheidung je Raum      │                   │                                  │
│ Stand je API-Zugang       │  POST /sync       │ Coordinator (alle 1–5 min)       │
│                           │ ◄──────────────── │  Messwerte sammeln → melden      │
│                           │ ────────────────► │  command ≠ null → Geräte schalten│
└───────────────────────────┘  command/window   │  letzte Anforderung speichern    │
                                                └──────────────────────────────────┘
```

1. **Coordinator** (`DataUpdateCoordinator`, Intervall konfigurierbar, Vorschlag **60 s bis 5 min**) sammelt je zugeordnetem Raum Temperatur und Feuchte und ruft `POST /heating/sync` auf.
2. Für jeden Raum mit `command` ≠ `null`: Geräte gemäß Abschnitt 4 schalten.
3. Letzte Anforderung und letztes `window` je Raum **persistent speichern** (`homeassistant.helpers.storage.Store`) – für Entitäten, das Nachholen bei nicht verfügbaren Geräten und die Ausfall-Absicherung.
4. Beim Start der Integration (und nach Änderung der Zuordnung) die erste Meldung mit `force: true` senden.
5. Die Räume (`GET /heating/rooms`) werden beim Einrichten, im Options Flow und nach einem 422 neu geladen – nicht bei jedem Intervall.

---

## 11. Verteilung

Die Integration liegt in HA unter `config/custom_components/georg/` und wird in einem **eigenen, öffentlichen GitHub-Repo** entwickelt.

- **HACS Custom Repository** (Start): Der Verein trägt die Repo-URL in HACS ein. In GeORG kann ein „My Home Assistant"-Button das mit einem Klick erledigen:
  `https://my.home-assistant.io/redirect/hacs_repository/?owner=<org>&repository=<repo>&category=integration`
- **HACS-Standardliste** (sobald stabil): PR beim HACS-Projekt, Validierung durch hassfest und die HACS-Action, `hacs.json`, Releases und ein Logo.
- **Manuelle Installation** als Fallback: ZIP entpacken nach `custom_components`, ohne automatische Updates.
- **Core-Integration** ist ein optionales Ziel für später.

---

## 12. Aufbau des Repos

```text
georg-homeassistant/
├── api/
│   └── openapi.yaml         # aus GeORG exportiert (Scramble), Heating-Teil
├── custom_components/georg/
│   ├── __init__.py          # Setup, Coordinator starten
│   ├── manifest.json        # domain, version, requirements, iot_class: cloud_polling, single_config_entry
│   ├── config_flow.py       # Instanz-URL + Token, Reauth, Raumzuordnung (Options)
│   ├── coordinator.py       # meldet an /heating/sync, speichert letzte Anforderung (Store)
│   ├── controller.py        # setzt Anforderungen an climate/switch um, Nachholen, Ausfall-Absicherung
│   ├── sensor.py / binary_sensor.py / switch.py
│   ├── strings.json + translations/de.json
│   └── brand/               # Icon/Logo
├── tests/                   # pytest-homeassistant-custom-component
├── hacs.json
└── .github/workflows/       # hassfest, HACS-Validierung, Tests
```

Der **API-Client** wird als eigenes kleines Python-Paket (z. B. `pygeorg`) auf PyPI angelegt und in `manifest.json` unter `requirements` referenziert. Er kapselt die beiden Endpunkte und die Fehlerfälle aus 9.3 (eigene Exceptions für Auth, Modul fehlt, unbekannter Raum).

---

## 13. Vorgehen

1. `api.json` aus GeORG exportieren und den Heating-Teil als `api/openapi.yaml` ins Repo übernehmen; Mock-Server daraus erzeugen.
2. `pygeorg`: Client für `rooms` und `sync` inkl. Fehlerbehandlung.
3. Grundgerüst: `manifest.json`, Config Flow (URL + Token), Coordinator.
4. Options Flow mit Raumzuordnung (Geräte + Sensoren).
5. Ansteuerung von `climate` und `switch`, persistente letzte Anforderung, Entitäten.
6. Reparaturhinweise, Reauth, Ausfall-Absicherung.
7. Releases mit semantischer Versionierung, Verteilung als HACS Custom Repository.
8. Sobald stabil: Aufnahme in die HACS-Standardliste beantragen.

---

## 14. Offene Punkte

- Standard-Meldeintervall und Karenz der Ausfall-Absicherung
- Schwelle für den Reparaturhinweis bei Ausfall
- Lokale Erkennung „manuell übersteuert" (Soll am Gerät ≠ letzte Anforderung) – nur als HA-Anzeige; GeORG erhält diese Information derzeit nicht
- GitHub-Organisation, Lizenz, Mindestversion von Home Assistant
