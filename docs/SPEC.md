# Specificatie: EnergyID-integratie voor Home Assistant

2026-11-24 · Wim

## 1. Doel en scope

De integratie importeert meetdata uit een EnergyID-dossier in de long-term statistics van Home Assistant, met de originele tijdstippen, zodat de Energy-dashboard het officiële Fluvius-verbruik per uur toont. Ze is read-only en bedoeld voor historiek, niet voor sturing: de data komt minstens een dag later binnen.

Werknaam: **EnergyID Import**, domein `energyid_import`. Het domein mag niet botsen met de officiële core-integratie (`energyid`, pusht HA-data naar EnergyID) en de bestaande custom integratie (`energy_id`).

**In scope (v1)**

- Config flow met een persoonlijke API-key, keuze van dossier en meters.
- Uurlijkse statistics per gekozen meter: elektriciteit afname en injectie. Gas loopt via hetzelfde pad als de eenheid ondersteund is.
- Backfill van historiek bij eerste setup, daarna incrementele sync met herberekening van een recent venster.
- Een kleine set diagnostische sensoren, diagnostics-download, repair-issues bij auth-fouten en verouderde data.
- Services om een sync te forceren en statistics vanaf een datum opnieuw op te bouwen.

**Optioneel (v1.1)**

- Maandpiek (capaciteitstarief) per maand, zelf berekend uit kwartierwaarden.

**Buiten scope**

- Schrijven naar EnergyID (meterstanden toevoegen, data pushen).
- Water. De digitale watermeter (Pidpa) stuurt zijn stand via de digitale elektriciteitsmeter; EnergyID's Fluvius-koppeling bevat geen water. Water hoort via een P1-lezer rechtstreeks in HA (beslissing 2026-11-24). Ook real-time vermogen en kostenberekening vallen buiten scope.
- Kwartierresolutie in HA: statistics-import is per uur.
- OAuth-clientflow en Workspace-keys.

## 2. Uitgangspunten

Vier eigenschappen van de bron en van HA bepalen het ontwerp.

1. **De data is vertraagd.** EnergyID haalt de Fluvius-data één keer per dag op, als dag- of kwartierwaarden naargelang de licentie ([EnergyID help: Fluvius](https://help.energyid.eu/nl/integraties/fluvius/)). Een gewone sensor zou het verbruik op het poll-tijdstip boeken in plaats van op het moment van verbruik.
2. **Statistics-import is per uur.** `async_add_external_statistics` aanvaardt alleen uurbuckets met `start` op het hele uur. Dit is het patroon van de core-integratie Opower, die hetzelfde probleem oplost.
3. **De data kan achteraf wijzigen.** De aanname is dat Fluvius geschatte waarden nog door gevalideerde vervangt en dat EnergyID die opnieuw ophaalt (vraag 9 in sectie 13). De sync herberekent daarom telkens een recent venster in plaats van alleen nieuwe uren toe te voegen.
4. **De koppeling kan stilvallen.** Het Fluvius-mandaat vervalt na drie jaar; daarna komt er geen data meer zonder foutmelding. De integratie moet verouderde data zelf signaleren.

Referenties voor de bouwer:

- Opower in HA core (`homeassistant/components/opower/coordinator.py`): referentie-implementatie voor external statistics met herberekening.
- De officiële Python-client [`energyid`](https://pypi.org/project/energyid/)[ 1.0.0](https://pypi.org/project/energyid/): bron voor endpoints en parameters.
- De review van `tijsverkoyen/HomeAssistant-EnergyID`: lijst van fouten die hier niet herhaald mogen worden (sectie 8).

## 3. EnergyID API

De integratie gebruikt vier GET-endpoints op `https://api.energyid.eu/api/v1` met een persoonlijke API-key. Alles hieronder komt uit de broncode van de officiële client en uit de bestaande custom integratie; niets is tegen een echt account getest. Fase 0 (sectie 12) verifieert dit.

**Authenticatie**

- Header: `Authorization: apiKey <key>`.
- Alleen leesrechten nodig (`records:read`). De key wordt aangemaakt in de accountinstellingen van EnergyID.
- 401 en 403 betekenen een ongeldige key of ontbrekende rechten.

**Endpoints**

| Doel | Pad | Parameters |
| --- | --- | --- |
| Key valideren, dossiers oplijsten | `members/me/records` | geen |
| Dossier ophalen | `records/{recordId}` | geen |
| Meters van een dossier | `records/{recordId}/meters` | geen |
| Intervaldata van een meter | `meters/{meterId}/data` | `start`, `end` (`YYYY-MM-DD`), `interval` |

**Intervaldata**

- `interval` is een ISO 8601-duur: `PT5M`, `PT15M`, `PT1H`, `P1D`, `P7D`, `P1M`, `P1Y`.
- De officiële client knipt aanvragen in vensters: 7 dagen voor `PT15M`, 31 dagen voor `PT1H`, 731 dagen voor `P1D`. De integratie houdt dezelfde maxima aan.
- Het antwoord bevat een lijst `data` met per punt een `timestamp` (ISO 8601 met offset) en een waarde.
- De integratie vraagt `PT1H` op. Zo aggregeert de server en zijn er 12 aanvragen nodig per meter per jaar historiek.

**Relevante meter-eigenschappen** (uit `records/{id}/meters`): `id`, `displayName`, `metric` (o.a. `electricityImport`, `electricityExport`, `naturalGasImport`, `drinkingWaterImport`), `unit`, `multiplier`, `readingType`, `interval`, `automatic`, `hidden`, `activated`, `deactivated`.

**Eigen client, niet de PyPI-library**

De integratie krijgt een eigen dunne client in `api.py` op de gedeelde aiohttp-sessie van HA. De officiële library vereist `pandas>=2.2.3`, wat te zwaar is voor vier GET-calls, en beheert zelf geen timeouts of 429-afhandeling.

**Rate limits** zijn voor de Web API niet gedocumenteerd. De client stuurt aanvragen één voor één en respecteert `Retry-After` bij 429.

## 4. Architectuur

Eén config entry staat voor één EnergyID-dossier en heeft één coordinator. De rekenlogica voor statistics staat los van HA in pure functies, zodat ze zonder recorder te testen is.

| Bestand | Verantwoordelijkheid |
| --- | --- |
| `api.py` | Async client, dataclasses (`Record`, `Meter`, `DataPoint`), excepties (`EnergyIDAuthError`, `EnergyIDConnectionError`, `EnergyIDApiError`), timeouts, retries, 429 |
| `config_flow.py` | User-, reauth-, reconfigure- en options-flow |
| `coordinator.py` | Periodieke sync: meters ophalen, per meter data ophalen, statistics schrijven, issues aanmaken |
| `statistics.py` | Pure functies: normaliseren, uurbuckets, cumulatieve som, statistic-id en metadata |
| `sensor.py`, `entity.py` | Diagnostische sensoren op basis van coordinator-data |
| `services.py` | Services `sync` en `rebuild_statistics`, geregistreerd in `async_setup` |
| `diagnostics.py` | Diagnostics-download met redactie |
| `__init__.py` | Setup, unload, `runtime_data` |

**Dataflow van één sync**

1. De coordinator haalt het dossier en de meterlijst op en filtert op de gekozen meters.
2. Per meter bepaalt hij het venster: backfill bij een lege statistic, anders het herberekenvenster (sectie 7).
3. `api.py` haalt de `PT1H`-data op in vensters van maximaal 31 dagen.
4. `statistics.py` zet de punten om naar uurbuckets in UTC met een doorlopende som.
5. De coordinator schrijft ze weg met `async_add_external_statistics`.
6. De coordinator geeft per meter de status terug (laatste data-uur, aantal geschreven uren, fouten) voor de sensoren en diagnostics.

Een fout bij één meter stopt de andere meters niet. De sync mislukt alleen als geheel bij een auth-fout of als geen enkele meter gelukt is.

**Poll-interval:** elke 6 uur, niet instelbaar. De bron ververst één keer per dag; vaker pollen levert niets op.

## 5. Config flow

De flow valideert elke invoer tegen de API voordat er een entry ontstaat. Alle state leeft op de flow-instantie, nooit op de klasse.

**Setup (****`user`****)**

1. **API-key.** Wachtwoordveld. Validatie via `members/me/records`. Fouten: `invalid_auth` (401/403), `cannot_connect` (timeout, netwerk, 5xx), `unknown` (onverwacht antwoord van de API). Geen dossiers: abort `no_records`.
2. **Dossier.** Keuzelijst; overgeslagen als er maar één is. `unique_id` is het record-id; een dossier dat al geconfigureerd is geeft abort `already_configured`.
3. **Meters en historiek.** Multi-select van de importeerbare meters (sectie 6), met afname en injectie van elektriciteit vooraf aangevinkt. Veld `backfill_months`, standaard 12, bereik 0 tot 60.

**Opslag**

| Waar | Sleutels |
| --- | --- |
| `entry.data` | `api_key`, `record_id` |
| `entry.options` | `meter_ids`, `backfill_months` |

De titel van de entry is de naam van het dossier. `entry.data` en `entry.options` worden nooit in place gewijzigd.

**Reauth.** Een 401/403 tijdens een sync geeft `ConfigEntryAuthFailed`. De reauth-flow vraagt een nieuwe key en controleert dat die toegang geeft tot hetzelfde dossier.

**Reconfigure.** Zelfde stap als reauth, door de gebruiker gestart.

**Options.** Meterselectie aanpassen. Een toegevoegde meter krijgt bij de volgende sync zijn backfill. Voor een verwijderde meter stopt de import; de bestaande statistics blijven staan.

**Versie.** `VERSION = 1`, `MINOR_VERSION = 1`. Een latere wijziging van de opslag krijgt een `async_migrate_entry`.

## 6. Devices, entities en statistics

Het eigenlijke product is één external statistic per meter. Entities blijven beperkt tot wat nodig is om de sync te bewaken: geen dossier- of adresgegevens als entity.

**Importeerbare meters**

Een meter is importeerbaar als hij niet afgesloten is (`closed` is niet `true`), geen watermeter is (`metric` is niet `drinkingWaterImport`) en zijn eenheid in deze tabel staat. Andere meters verschijnen niet in de config flow.

| EnergyID `unit` | HA-eenheid | `unit_class` |
| --- | --- | --- |
| `kWh` | `kWh` | `energy` |
| `Wh` | `Wh` | `energy` |
| `m³` | `m³` | `volume` |

**Statistic per meter**

| Veld | Waarde |
| --- | --- |
| `statistic_id` | `energyid_import:meter_<slug van meter-id>` |
| `source` | `energyid_import` |
| `name` | `EnergyID <dossiernaam> <meternaam>`, aangevuld met het register (dag/nacht) als de meter er een heeft, en met `EAN …1234` als het dossier meer dan één aansluiting heeft |
| `has_sum` | `True` |
| `mean_type` | `StatisticMeanType.NONE` |
| `unit_class`, `unit_of_measurement` | volgens de tabel hierboven |

De slug bevat alleen `a-z`, `0-9` en enkele underscores, want HA weigert andere tekens en dubbele underscores. Het meter-id is de sleutel, niet de naam: een meter hernoemen in EnergyID verandert alleen `name`.

De gebruiker kiest deze statistics zelf in de Energy-dashboard (grid consumption, return to grid, gas). Fluvius levert per richting een apart dag- (`high`) en nachtregister (`low`); elk register is een eigen meter en dus een eigen statistic (beslissing 2026-11-24). Zo kan de gebruiker per tarief een prijs zetten.

**Meerdere aansluitingen (EAN's).** Een gebruiker kan meerdere EAN's hebben. Staan ze in aparte dossiers, dan wordt elk dossier een eigen config entry. Staan ze in één dossier, dan groepeert de integratie de meters per `gridConnectionId` en toont ze in het meterlabel en de statistic-naam de laatste 4 cijfers van de EAN (`connectionNumber`), alleen als er meer dan één aansluiting is. De client bewaart nooit de volledige EAN (beslissing 2026-11-24). Niet getest tegen een echt dossier met meerdere EAN's.

**Devices**

- Eén device per dossier: `entry_type = SERVICE`, identifier `(DOMAIN, record_id)`, fabrikant EnergyID, `configuration_url` naar het dossier.
- Eén device per gekozen meter met `via_device` naar het dossier. Een meter die uit de selectie verdwijnt, verliest zijn device.

**Entities**

| Entity | Device | Type | Betekenis |
| --- | --- | --- | --- |
| `latest_data` | meter | timestamp, diagnostic | Einde van het laatste geïmporteerde uur |
| `last_sync` | dossier | timestamp, diagnostic | Laatste geslaagde sync |

Alle entities: `_attr_has_entity_name = True`, `translation_key`, stabiele `unique_id` op basis van record-id en meter-id, `PARALLEL_UPDATES = 0`.

**Repair-issues**

| Issue | Voorwaarde | Verdwijnt |
| --- | --- | --- |
| `stale_data` | `latest_data` van een meter is ouder dan 4 dagen | zodra er nieuwe data is |
| `no_data` | een gekozen meter levert geen enkel datapunt | zodra er data is |
| `meter_removed` | een gekozen meter bestaat niet meer in het dossier | na aanpassen van de selectie |

De tekst van `stale_data` verwijst naar het Fluvius-mandaat in EnergyID als waarschijnlijke oorzaak.

**Maandpiek**

Per dossier twee sensoren in kW op het dossierapparaat, `peak_current_month` en `peak_previous_month`, met het begin van het piekkwartier als attribuut `peak_start`. Berekening: per kwartier de som van alle afnamemeters van het dossier (dag- en nachtregister samen, gekozen of niet), daarvan het hoogste kwartier in kWh maal 4, per kalendermaand in de tijdzone van het dossier (beslissing 2026-11-24: het capaciteitstarief kijkt naar de totale afname). Dit vraagt `PT15M`-data vanaf de eerste dag van vorige maand; de sensoren ontstaan alleen als elke afnamemeter kwartierwaarden heeft. Een mislukte kwartier-opvraging logt een waarschuwing en laat de piek weg; de statistics-sync blijft staan.

## 7. Statistics-import

Elke sync herschrijft per meter een aaneengesloten venster van uurbuckets met een opnieuw berekende som. Twee syncs zonder wijziging aan de bron geven exact dezelfde statistics.

**Constanten** (in `const.py`)

| Naam | Waarde | Reden |
| --- | --- | --- |
| `UPDATE_INTERVAL` | 6 uur | bron ververst dagelijks |
| `REFETCH_DAYS` | 35 | dekt een volledige maand aan correcties |
| `MAX_WINDOW_DAYS` | 31 | maximum per aanvraag voor `PT1H` |
| `STALE_AFTER_DAYS` | 4 | marge boven de normale vertraging |
| `REQUEST_TIMEOUT` | 30 s | per aanvraag |

**Normalisatie** (`statistics.py`, pure functies)

1. Parse elke `timestamp` naar een timezone-aware datetime en zet om naar UTC.
2. Bepaal de uurbucket: het hele uur waarin het interval valt. Of de timestamp het begin of het einde van het interval aanduidt, legt fase 0 vast in één constante.
3. Sla punten zonder waarde over. Sla negatieve waarden over en tel ze; log één waarschuwing per meter per sync.
4. Tel meerdere punten in dezelfde bucket op.
5. Laat buckets weg waarvan het uur nog niet voorbij is.

De waarde per punt wordt behandeld als verbruik binnen het interval, in de eenheid van de meter. Blijkt in fase 0 dat de API cumulatieve standen teruggeeft of de `multiplier` niet toepast, dan corrigeert deze stap dat.

**Algoritme per meter**

1. Lees de laatste statistic met `get_last_statistics`.
2. Bepaal het venster:
   - Geen statistic: `start` is vandaag min `backfill_months`; de API geeft geen activatiedatum. `base_sum = 0`.
   - Wel een statistic: `start` is het laatste uur min `REFETCH_DAYS`, afgerond naar lokale middernacht. `base_sum` is de som van het laatste uur vóór `start`, of 0 als dat niet bestaat.
3. Haal de data op van `start` tot en met morgen, in stukken van maximaal `MAX_WINDOW_DAYS`. Datums zijn lokale datums in de tijdzone van het dossier.
4. Lees de bestaande statistics in het venster (`state` per uur) met `statistics_during_period`.
5. Voeg samen: verse data overschrijft bestaande uren; bestaande uren die in het antwoord ontbreken, behouden hun waarde. Zo gaat niets verloren als de API tijdelijk minder teruggeeft.
6. Bereken over het samengevoegde venster, in tijdsvolgorde: `state` is het verbruik van het uur, `sum` is `base_sum` plus de lopende som.
7. Schrijf alle uren van het venster met `async_add_external_statistics`.

Alle recorder-queries lopen via `get_instance(hass).async_add_executor_job`. Het manifest declareert `recorder` als dependency.

**Randgevallen**

- **Leeg antwoord:** niets schrijven, geen fout. De `stale_data`-controle vangt dit op.
- **Gat in de data:** ontbrekende uren krijgen geen rij; de som loopt door. Wordt het gat binnen 35 dagen gevuld, dan herstelt de volgende sync het.
- **Correctie ouder dan 35 dagen:** wordt gemist. De service `rebuild_statistics` lost dat op.
- **Zomer- en wintertijd:** alles is UTC, dus dagen van 23 of 25 uur vragen geen aparte logica. Tests dekken 29 maart 2026 en 25 oktober 2026.
- **Meter met alleen dagwaarden:** `P1D` opvragen en de dagwaarde in het uur van lokale middernacht boeken. De README vermeldt deze beperkte resolutie.
- **Eerste setup:** de backfill loopt in de eerste refresh. Mislukt die door een verbindingsfout, dan volgt `ConfigEntryNotReady` en probeert HA opnieuw.

**Services**

| Service | Velden | Werking |
| --- | --- | --- |
| `energyid_import.sync` | config entry | Start meteen een gewone sync |
| `energyid_import.rebuild_statistics` | meter-device, optioneel `start_date` | Zelfde algoritme met `start` op de opgegeven datum; zonder datum vanaf het begin van de historiek |

## 8. Foutafhandeling en robuustheid

Elke fout uit de API wordt in `api.py` vertaald naar een van drie excepties; de rest van de code vangt alleen die drie.

**Gedrag per fout**

| Situatie | Exceptie | Bij setup | Tijdens sync |
| --- | --- | --- | --- |
| 401, 403 | `EnergyIDAuthError` | `ConfigEntryAuthFailed` | `ConfigEntryAuthFailed`, reauth start |
| Timeout, DNS, verbinding, 5xx | `EnergyIDConnectionError` | `ConfigEntryNotReady` | `UpdateFailed`, vorige data blijft |
| 429 | intern afgehandeld | wachten volgens `Retry-After`, max. 3 pogingen | idem, daarna `EnergyIDConnectionError` |
| 404 op een meter | `EnergyIDApiError` | meter overslaan | issue `meter_removed` |
| Ongeldige JSON, onverwachte structuur | `EnergyIDApiError` | `ConfigEntryNotReady` | meter overslaan, fout in diagnostics |

Retries: 5xx en verbindingsfouten krijgen 2 nieuwe pogingen met exponentiële backoff (2 s, 8 s). 4xx behalve 429 nooit.

**Defensief parsen.** Dataclasses lezen alleen de velden die de integratie gebruikt. Verplicht zijn `id`, `displayName`, `unit` en `metric`; een meter zonder die velden wordt overgeslagen met een waarschuwing. Datums worden geparsed met `datetime.fromisoformat`, niet met vaste formaten.

**Lessen uit de review**

| Fout in de bestaande integratie | Eis hier |
| --- | --- |
| Flow-state als class-attribuut | State alleen op de instantie |
| Geen validatie van credentials | Elke stap valideert tegen de API |
| Platform-setup niet ge-await, geen retry | `await async_forward_entry_setups`; eerste refresh vóór de platforms |
| Geen `async_unload_entry` | Unload en reload werken zonder herstart |
| `requests` zonder timeout in een executor | aiohttp op de HA-sessie, timeout op elke aanvraag |
| Alleen `HTTPError` gevangen | Alle netwerk- en parsefouten vertaald naar eigen excepties |
| `state` en `unit_of_measurement` overschreven | Alleen `native_value` en `_attr_*` |
| `datetime.now()` | Altijd `dt_util.utcnow()` of `dt_util.now()` |
| `entry.data` in place gemuteerd | Runtime-state in `entry.runtime_data` |
| Tientallen statische diagnostic-entities | Twee sensortypes; de rest in de diagnostics-download |
| `pytz` en `dateutil` niet gedeclareerd | Alleen standaardbibliotheek en HA-helpers; `requirements` leeg |
| Services per entry opnieuw geregistreerd | Registratie één keer in `async_setup` |

**Geheimen en privacy.** De API-key komt nooit in logs, excepties of diagnostics. `diagnostics.py` redigeert `api_key`, record-id, adres, EAN en meternummers. Logging gebruikt lazy formatting (`%s`), geen f-strings.

## 9. Code standards en kwaliteitspoorten

Elke regel in deze sectie wordt door een tool afgedwongen en blokkeert de merge; wat niet automatisch te controleren is, staat er niet in. De lat ligt boven de Silver-trap van de Integration Quality Scale van HA.

### 9.1 Poorten

Negen poorten draaien lokaal via pre-commit en opnieuw in CI. Eén melding, waarschuwing of gezakte drempel blokkeert de merge.

| Poort | Tool | Blokkeert bij |
| --- | --- | --- |
| Lint | `ruff check`, alle regels aan | elke melding |
| Formaat | `ruff format --check` | elk verschil |
| Types | `mypy`, strict plus extra foutcodes | elke fout |
| Tests | `pytest`, warnings zijn fouten | falende test, warning, test trager dan 10 s |
| Dekking | `coverage`, branch coverage | onder de drempels van 9.3 |
| HA-conventies | `hassfest`, HACS-validatie | elke fout |
| Teksten en config | `codespell`, `yamllint`, `check-json`, `actionlint` | elke melding |
| Geheimen | `gitleaks` | elk gevonden geheim |
| Lockfile | `uv lock --check` | lockfile wijkt af van `pyproject.toml` |

Python 3.13, de versie die HA 2026.2 vereist. Alle tools zijn vastgepind in `uv.lock`; een nieuwe ruff- of mypy-versie komt alleen binnen via een aparte pull request.

**Configuratie** (`pyproject.toml`, bindend)

```toml
[tool.ruff]
target-version = "py313"
line-length = 88

[tool.ruff.lint]
select = ["ALL"]
ignore = [
  "D203", "D213",      # botsen met D211 en D212
  "COM812", "ISC001",  # botsen met de formatter
  "CPY001",            # geen copyright-header per bestand; licentie staat in LICENSE
  "TC001", "TC002", "TC003",  # imports blijven runtime-imports, zoals in HA core
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106", "PLR2004"]
"scripts/**" = ["T201"]

[tool.ruff.lint.pydocstyle]
convention = "google"

[tool.ruff.lint.mccabe]
max-complexity = 8

[tool.ruff.lint.pylint]
max-args = 5
max-branches = 8
max-returns = 4
max-statements = 30

[tool.ruff.lint.flake8-tidy-imports]
ban-relative-imports = "parents"

[tool.ruff.lint.flake8-tidy-imports.banned-api]
"asyncio.create_task".msg = "Use entry.async_create_background_task."
"asyncio.ensure_future".msg = "Use entry.async_create_background_task."
"asyncio.gather".msg = "Requests run sequentially."
"time.sleep".msg = "Never block the event loop."
"datetime.datetime.now".msg = "Take `now` as an argument or use dt_util."
"datetime.datetime.utcnow".msg = "Take `now` as an argument or use dt_util."
"datetime.datetime.today".msg = "Take `now` as an argument or use dt_util."
"requests".msg = "Use the injected aiohttp session."
"urllib.request".msg = "Use the injected aiohttp session."
"pytz".msg = "Use zoneinfo."
"dateutil".msg = "Use the standard library."

[tool.ruff.lint.isort]
force-sort-within-sections = true
combine-as-imports = true

[tool.mypy]
python_version = "3.13"
strict = true
warn_unreachable = true
local_partial_types = true
disallow_any_explicit = true
disallow_any_unimported = true
enable_error_code = [
  "deprecated", "explicit-override", "ignore-without-code",
  "mutable-override", "possibly-undefined", "redundant-expr",
  "redundant-self", "truthy-bool", "truthy-iterable", "unused-awaitable",
]

[[tool.mypy.overrides]]
# HA legt hier dict[str, Any] op in de methodesignaturen.
module = [
  "custom_components.energyid_import.config_flow",
  "custom_components.energyid_import.diagnostics",
]
disallow_any_explicit = false

[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = "--strict-markers --strict-config --cov --cov-branch"
filterwarnings = [
  "error",
  # Hypothesis on Python 3.14 warns about its own internal PRNG.
  "ignore:It looks like `register_random`:hypothesis.errors.HypothesisWarning",
]
xfail_strict = true
timeout = 10

[tool.coverage.report]
fail_under = 100
exclude_also = ["if TYPE_CHECKING:"]
```

De ignore-lijsten hierboven zijn volledig; een regel toevoegen vraagt een aanpassing van deze spec. De enige uitzondering in `filterwarnings` (2026-11-24, akkoord Wim) negeert een `HypothesisWarning` die Hypothesis onder Python 3.14 over zijn eigen interne random-generator geeft. De configuratie is gecontroleerd met ruff 0.17.6 en mypy 2.4.1: ze laadt zonder fouten en de strengere regels slaan aan op testcode.

### 9.2 Codeerregels

Elke regel heeft een bewaker: een ruff-regel, een mypy-vlag of een test in `tests/test_architecture.py` die de broncode met `ast` controleert.

**Onderdrukkingen: nul**

In `custom_components/` en `tests/` staat geen enkele `# noqa`, `# type: ignore` of `# pragma: no cover`. De architectuurtest telt ze en faalt boven nul. Wie een regel niet kan halen, past de code aan of stelt een wijziging van 9.1 voor.

**Types en data**

| Regel | Bewaker |
| --- | --- |
| Elke functie is volledig geannoteerd, ook in tests | mypy strict |
| Geen `Any`, behalve waar een HA-signatuur het oplegt (`config_flow`, `diagnostics`) | `disallow_any_explicit`, `ANN401` |
| Elke overschreven methode draagt `@override` | `explicit-override` |
| API-JSON heeft het type `JsonValue` (eigen recursieve alias) en wordt in `api.py` meteen omgezet; publieke functies van `api.py` geven alleen dataclasses terug | architectuurtest |
| Elke dataclass is `frozen=True, slots=True, kw_only=True`; entity descriptions zijn vrijgesteld van `slots` | architectuurtest |
| Constanten zijn `Final`; metric, eenheid en interval zijn `StrEnum`s in `const.py`, en hun waarden komen nergens anders als literal voor | architectuurtest |
| Geen muteerbare class-attributen of muteerbare state op moduleniveau | `RUF012`, architectuurtest |
| Booleans zijn keyword-only | `FBT001`, `FBT002`, `FBT003` |

**Structuur**

| Regel | Bewaker |
| --- | --- |
| `api.py` en `statistics.py` importeren niets uit `homeassistant` | architectuurtest |
| Alleen `api.py` importeert `aiohttp`; de sessie wordt geïnjecteerd | architectuurtest |
| Importrichting: `const` ← `api`, `statistics` ← `coordinator` ← `entity`, `sensor`, `services`, `diagnostics`; niets importeert `config_flow` | architectuurtest |
| Geen `hass.data`; runtime-state alleen in `entry.runtime_data` | architectuurtest |
| Functie: hoogstens 30 statements, complexiteit 8, 5 parameters, 8 vertakkingen, 4 returns | `PLR0915`, `C901`, `PLR0913`, `PLR0912`, `PLR0911` |
| Docstring in Google-stijl op elke module, klasse en functie, ook tests | ruff `D` |
| Geen uitgecommentarieerde code; geen TODO zonder issue-link | `ERA001`, `TD003`, `FIX002` |

**Async en tijd**

| Regel | Bewaker |
| --- | --- |
| Geen blocking calls in async code | ruff `ASYNC`, `TID251` |
| Geen losse tasks: `asyncio.create_task`, `ensure_future`, `gather` en `hass.async_create_task` zijn verboden | `TID251`, architectuurtest |
| API-aanvragen lopen sequentieel, elk binnen `asyncio.timeout` | test op de client |
| Datums zijn altijd timezone-aware; `datetime.now()` is verboden | ruff `DTZ`, `TID251` |
| `statistics.py` leest de klok niet: `now` komt binnen als argument | architectuurtest |

**Fouten en logging**

| Regel | Bewaker |
| --- | --- |
| Geen `except Exception`, geen kale `except`, geen stille `pass` | `BLE001`, `E722`, `S110`, `SIM105` |
| Opnieuw gooien altijd met `raise ... from err` | `B904` |
| Geen `assert` en geen `print` buiten tests en scripts | `S101`, `T201` |
| Logging met lazy formatting; `_LOGGER.exception` alleen in een `except`-blok | `G004`, `LOG`, `TRY400` |
| De testsuite bevat de API-key nooit in een logregel of exceptietekst | autouse-fixture |
| Geen logregel op `WARNING` of hoger, tenzij de test die uitdrukkelijk verwacht | autouse-fixture |

**Home Assistant**

| Regel | Bewaker |
| --- | --- |
| Entities zetten alleen `_attr_*` en `native_value`; `state`, `name` en `unit_of_measurement` worden nooit overschreven | architectuurtest |
| Ongebruikte parameters uit een HA-signatuur krijgen een underscore-prefix | `ARG001`, `ARG002` |
| Elke sleutel in `strings.json` bestaat in `en.json` en `nl.json`, en omgekeerd | test |
| De versie in `manifest.json` is gelijk aan de git-tag | CI-stap bij release |

### 9.3 Tests

De drempel is 100% regel- en branchdekking op het hele pakket, zonder uitsluitingen behalve `if TYPE_CHECKING:`. Tests draaien met `pytest-homeassistant-custom-component`.

**Regels**

| Regel | Bewaker |
| --- | --- |
| Mocken gebeurt alleen op de HTTP-grens (`aioclient_mock`); eigen functies worden nooit gepatcht | architectuurtest op `patch`-doelen |
| Statistics-tests draaien tegen een echte in-memory recorder | architectuurtest: geen `patch` op de recorder |
| Geen netwerk, geen `sleep`; tijd loopt via `freezer` | `pytest-socket`, `TID251`, timeout van 10 s |
| Elke warning is een fout, ook deprecations van HA | `filterwarnings = [
  "error",
  # Hypothesis on Python 3.14 warns about its own internal PRNG.
  "ignore:It looks like `register_random`:hypothesis.errors.HypothesisWarning",
]` |
| Fixtures bevatten alleen dummy-id's en dummy-namen | test op een vast patroon, `gitleaks` |
| Diagnostics, entity-states en de geschreven statistics liggen vast in snapshots | `syrupy` |

**Property-based tests** (`hypothesis`, op `statistics.py`)

Voor willekeurige reeksen datapunten gelden deze invarianten:

1. De som daalt nooit.
2. De laatste som is `base_sum` plus de som van alle `state`-waarden, binnen 1e-9.
3. Elke bucket start op een heel uur in UTC; buckets zijn uniek en gesorteerd.
4. De volgorde van de invoer verandert het resultaat niet.
5. Dezelfde data twee keer verwerken geeft hetzelfde resultaat als één keer.
6. Eén sync over een periode geeft hetzelfde resultaat als twee opeenvolgende syncs over de twee helften.

**Te dekken gevallen**

| Domein | Gevallen |
| --- | --- |
| Config flow | Geslaagde setup; ongeldige key; geen verbinding; onverwacht antwoord; geen dossiers; dubbel dossier; één dossier slaat de keuzestap over; afgebroken flow laat geen state achter; reauth; reconfigure; options |
| Setup en unload | Laden; `ConfigEntryNotReady` bij verbindingsfout; `ConfigEntryAuthFailed` bij 401; unload en reload |
| API-client | Header; timeout; retries op 5xx; 429 met `Retry-After`; ongeldige JSON; ontbrekende verplichte velden; venstersplitsing op 31 dagen |
| Normalisatie | Uurbuckets; begin- of eindsemantiek; null en negatieve waarden; meerdere punten per uur; onvolledig lopend uur; `Wh` en `kWh` |
| Statistics | Eerste backfill; incrementele sync; correctie binnen het venster; gat dat later gevuld wordt; leeg antwoord; zomer- en wintertijd; dagwaarden; rebuild vanaf datum |
| Fouten per meter | Eén meter faalt, de andere worden geschreven; 404 geeft `meter_removed` |
| Entities en issues | `latest_data`, `last_sync`; `stale_data` en `no_data` aanmaken en opruimen |
| Diagnostics | Geen key, record-id, adres of EAN in de output |

### 9.4 CI en repo-beleid

De hoofdbranch aanvaardt alleen pull requests waarvan alle poorten groen zijn.

| Onderdeel | Regel |
| --- | --- |
| `ci.yml` | Op elke push en pull request: alle pre-commit-hooks, daarna pytest tegen de minimale HA-versie en tegen de laatste stabiele, elk met de Python-versie die die release vereist |
| `validate.yml` | `hassfest` en HACS-validatie |
| `nightly.yml` | Elke nacht pytest tegen de HA-bèta; een fout opent een issue en blokkeert geen pull requests |
| `release.yml` | Op tag `vX.Y.Z`: controleert dat `manifest.json` dezelfde versie draagt en maakt de release |
| Actions | Vastgepind op commit-SHA; `permissions: contents: read` als standaard |
| Updates | Dependabot wekelijks voor Actions en `uv.lock` |
| Branchbeveiliging | Geen directe pushes naar `main`; alle checks verplicht; squash merge; lineaire historiek |
| Commits | Conventional Commits, gecontroleerd op de titel van de pull request |
| Omvang | Hoogstens 400 gewijzigde regels per pull request, zonder fixtures, snapshots en lockfile; een CI-stap telt ze |

## 10. Repo-structuur en tooling

Eén GitHub-repo, HACS-compatibel als custom repository, MIT-licentie. Deze specificatie staat er als `docs/SPEC.md` in en is de bron van waarheid voor de bouwer.

```text
ha-energyid-import/
├── custom_components/energyid_import/
│   ├── __init__.py
│   ├── api.py
│   ├── config_flow.py
│   ├── const.py
│   ├── coordinator.py
│   ├── diagnostics.py
│   ├── entity.py
│   ├── icons.json
│   ├── manifest.json
│   ├── quality_scale.yaml
│   ├── sensor.py
│   ├── services.py
│   ├── services.yaml
│   ├── statistics.py
│   ├── strings.json
│   └── translations/{en,nl}.json
├── tests/
│   ├── conftest.py
│   ├── fixtures/*.json
│   ├── snapshots/
│   ├── test_architecture.py
│   └── test_*.py
├── scripts/discover.py
├── docs/{SPEC.md,api-notes.md}
├── .github/workflows/{ci,validate,nightly,release}.yml
├── .github/dependabot.yml
├── .pre-commit-config.yaml
├── CLAUDE.md
├── README.md
├── LICENSE
├── hacs.json
├── pyproject.toml
└── uv.lock
```

**`manifest.json`**

| Sleutel | Waarde |
| --- | --- |
| `domain` | `energyid_import` |
| `name` | `EnergyID Import` |
| `config_flow` | `true` |
| `integration_type` | `service` |
| `iot_class` | `cloud_polling` |
| `dependencies` | `["recorder"]` |
| `requirements` | `[]` |
| `version` | SemVer, start op `0.1.0` |

`hacs.json` bevat de naam en de minimale HA-versie.

**`CLAUDE.md`** legt de werkregels voor Claude Code vast:

- Lees `docs/SPEC.md` en `docs/api-notes.md` voor elke taak. Wijkt de API af van de spec, pas dan eerst `api-notes.md` en de spec aan.
- Werk per fase op een eigen branch met een pull request; CI moet groen zijn.
- Schrijf tests samen met de code; een fase is niet af zonder haar tests.
- Commit nooit een API-key of een ongeanonimiseerde API-response.
- Code, commentaar, commits en README in het Engels.

- Omzeil nooit een poort uit sectie 9: geen `--no-verify`, geen onderdrukkingen, geen versoepeling van `pyproject.toml` zonder akkoord van Wim.
- Lukt een regel niet, stop dan en leg het conflict voor in plaats van eromheen te werken.

**README** (Engels): wat de integratie doet en niet doet, installatie via HACS, API-key aanmaken, statistics toevoegen aan de Energy-dashboard, de vertraging van de data, de services, probleemoplossing.

## 11. Acceptatiecriteria

v1 is klaar als alle punten hieronder aantoonbaar zijn, de eerste zes in CI en de laatste vier op de eigen HA-installatie.

**Geautomatiseerd**

- [ ] CI is groen: alle negen poorten uit sectie 9.1, met 100% dekking en nul onderdrukkingen.
- [ ] Een ongeldige key of een onbereikbare API maakt geen config entry aan.
- [ ] Een onbereikbare API bij opstart geeft `ConfigEntryNotReady`; HA probeert vanzelf opnieuw.
- [ ] Twee opeenvolgende syncs op dezelfde fixtures geven identieke statistics.
- [ ] Een gewijzigde waarde binnen 35 dagen past dat uur en alle latere sommen aan.
- [ ] De diagnostics-output bevat geen key, record-id, adres of EAN.

**Op de eigen installatie**

- [ ] Setup via de UI lukt met een key met alleen leesrechten.
- [ ] De dagtotalen in de Energy-dashboard komen voor drie willekeurige dagen overeen met Mijn Fluvius, tot op 0,01 kWh.
- [ ] De uurverdeling van één dag komt overeen met de kwartierwaarden in Mijn Fluvius, opgeteld per uur.
- [ ] Reload en verwijderen van de integratie werken zonder herstart van HA.

## 12. Bouwfasering

Zes fasen, elk een eigen pull request. Fase 0 is een harde poort: de statistics-logica wordt pas gebouwd als de echte API-responses bekend zijn.

1. **Fase 0: API-verkenning.**
   - Bouw `scripts/discover.py`: leest `ENERGYID_API_KEY` uit de omgeving, roept de vier endpoints aan en schrijft geanonimiseerde responses naar `tests/fixtures/`.
   - Per meter: `PT15M` over 2 dagen, `PT1H` over 3 dagen, `P1D` over 14 dagen, en één `PT1H`-aanvraag over 40 dagen om het maximumvenster te testen. Leg statuscodes en response-headers vast.
   - Anonimisering: id's, namen, adres, EAN, meternummers en het huishoudprofiel vervangen door vaste dummywaarden. De ruwe output met echte meetwaarden blijft lokaal; de gecommitte fixtures krijgen synthetische meetwaarden met de echte tijdstippen en structuur (beslissing 2026-11-24, repo is publiek).
   - Wim draait het script lokaal en vergelijkt één dag met Mijn Fluvius.
   - Resultaat: `docs/api-notes.md` met een antwoord op elke vraag uit sectie 13, en een aangepaste spec waar nodig.
2. **Fase 1: skelet, client en config flow.** Repo-opzet met alle poorten uit sectie 9 en de architectuurtest vóór de eerste regel productiecode; daarna `api.py`, `config_flow.py`, lege coordinator, setup en unload. Klaar als de flow tegen de fixtures werkt en CI groen is.
3. **Fase 2: statistics.** `statistics.py`, de sync in de coordinator, alle statistics-tests uit sectie 9. Klaar als de geautomatiseerde acceptatiecriteria over idempotentie en correcties slagen.
4. **Fase 3: entities, issues, services, diagnostics.** Klaar als reauth, `stale_data` en `rebuild_statistics` getest zijn.
5. **Fase 4: release.** README, vertalingen, `quality_scale.yaml`, tag `v0.1.0`. Wim installeert via HACS en loopt de vier handmatige acceptatiecriteria af.
6. **Fase 5 (optioneel): maandpiek.** Alleen als fase 0 bevestigt dat het dossier kwartierwaarden levert.

## 13. Open vragen

Negen technische aannames wachten op fase 0; vier keuzes zijn aan Wim.

**Te verifiëren in fase 0**

| # | Vraag | Aanname in deze spec | Raakt |
| --- | --- | --- | --- |
| 1 | Geeft `meters/{id}/data` verbruik per interval of cumulatieve standen? | Verbruik per interval | Normalisatie |
| 2 | Is de `multiplier` al toegepast, en in welke eenheid staan de waarden? | Toegepast, eenheid van de meter | Normalisatie |
| 3 | Duidt `timestamp` het begin of het einde van het interval aan? | Begin | Uurbuckets |
| 4 | Werkt `PT1H` voor Fluvius-meters, en wat komt terug bij een dossier met alleen dagwaarden? | Werkt; dagmeters via `P1D` | Ophalen |
| 5 | Hoe verschijnen Fluvius-meters: één per richting, of ook apart per dag- en nachtregister? | Beantwoord: per richting een `high`- en een `low`-meter; elk een eigen statistic | Meterselectie |
| 6 | Wat is het echte maximumvenster per aanvraag, en zijn er rate limits of 429's? | Beantwoord: 7 dagen voor `PT15M`, 31 voor `PT1H`; daarboven 400. Geen 429 gezien | Client |
| 7 | Bestaat `members/me/records` voor een persoonlijke key, en volstaan leesrechten? | Ja | Config flow |
| 8 | Hoe ver terug gaat de Fluvius-historiek in EnergyID? | Beantwoord: tot de aanmaak van het dossier; oudere data bestaat niet | Backfill |
| 9 | Haalt EnergyID gecorrigeerde Fluvius-waarden opnieuw op, en binnen welke termijn? | Ja, binnen 35 dagen | `REFETCH_DAYS` |

**Te beslissen door Wim**

- [ ] Naam en domein: `EnergyID Import` / `energyid_import`, repo `ha-energyid-import`?
- [x] Publieke repo (bruikbaar voor anderen via HACS) of privé? Publiek (2026-11-24).
- [x] Maandpiek in v1.1 meenemen, of weglaten omdat de VM-3P75CT de piek al real-time meet? Meenemen (2026-11-24): het is de officiële Fluvius-waarde voor de factuur.
- [ ] Volstaat de gratis EnergyID-licentie voor kwartierwaarden, of is Premium nodig? Zonder kwartierwaarden valt de integratie terug op dagresolutie.
