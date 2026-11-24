# EnergyID Import for Home Assistant

Imports meter data from an [EnergyID](https://www.energyid.eu) record into Home
Assistant's long-term statistics, at the original timestamps. The Energy
dashboard then shows the official, validated Fluvius consumption per hour, and
two sensors track the monthly quarter-hour peak of the capacity tariff.

## What it does and does not do

- **Reads only.** It never writes readings to EnergyID.
- **History, not live data.** EnergyID fetches Fluvius data once a day, so values
  arrive a day late. For real-time power, use a P1 reader or your own meter.
- **Hourly statistics.** Home Assistant statistics are hourly; quarter-hour data
  is summed per hour. Meters that only have daily values are booked at local
  midnight.
- **Electricity and gas.** Water is left out: a digital water meter reports
  through the P1 port of the electricity meter, which Home Assistant reads
  directly.

## Installation

1. In Home Assistant open **Marketplace** (HACS on older versions) → ⋮ →
   **Custom repositories**, add `https://github.com/wvandeweyer/ha-energyid-import`
   as type *Integration*, and download **EnergyID Import**.
   Without Marketplace, copy `custom_components/energyid_import/` into the
   `custom_components` folder of your configuration.
2. Restart Home Assistant.
3. In EnergyID, create a personal API key with read access to records
   (account settings → API keys).
4. **Settings → Devices & services → Add integration → EnergyID Import**, paste
   the key, choose the record and the meters.

Home Assistant 2026.2 or newer is required.

## Configuration

| Setting | Where | Meaning |
| --- | --- | --- |
| API key | setup, reconfigure | Personal EnergyID key with read access |
| Record | setup | One config entry per EnergyID record |
| Meters | setup, options | Meters to import; electricity import and export are preselected |
| Months of history | setup, options | Backfill depth on the first sync (0–60, default 12) |

A meter added in the options imports its history on the next sync. A meter
removed from the selection keeps its statistics but loses its device.

## What you get

- **One statistic per meter and tariff register**, for example
  `EnergyID <record> <meter> (high)`. Fluvius delivers day (`high`) and night
  (`low`) registers as separate meters.
- **A device per record** with:
  - *Last sync*: when the last successful sync finished;
  - *Peak this month* and *Peak last month* (kW): the highest quarter-hour
    offtake with all import registers summed, as the capacity tariff counts it.
    The attribute `peak_start` gives the start of that quarter. These only exist
    when the import meters have quarter-hour data.
- **A device per meter** with *Latest data*: the end of the last imported hour.
  The device's serial number shows the last four digits of the EAN.
- **Repairs** for stale data (older than 4 days, usually an expired Fluvius
  mandate in EnergyID), meters without data, and meters that disappeared.

### Energy dashboard

**Settings → Dashboards → Energy → Electricity grid**: add the import
statistics (day and night) under *Grid consumption* and the export statistics
under *Return to grid*. If a P1 or other meter sensor is already configured
there, replace it instead of adding both, or consumption counts twice.

## Data updates

The integration syncs every 6 hours. Each sync rewrites the last 35 days, so
values Fluvius corrects within that period are picked up automatically.
Corrections older than that need the `rebuild_statistics` action.

## Actions

| Action | Fields | What it does |
| --- | --- | --- |
| `energyid_import.sync` | `config_entry_id` | Sync a record now |
| `energyid_import.rebuild_statistics` | `device_id` (a meter), optional `start_date` | Rewrite a meter's statistics from that date, or from the start of the backfill |
| `energyid_import.generate_dashboard` | `config_entry_id` | Return a ready-made dashboard for the record |

The record device also has a **Sync now** button.

### Ready-made dashboard

1. **Settings → Tools → Actions**, choose *EnergyID Import: Generate dashboard*,
   pick the record and press **Perform action**.
2. Copy the response.
3. **Settings → Dashboards → Add dashboard → New dashboard from scratch**, open
   it, then ⋮ → **Edit dashboard** → ⋮ → **Raw configuration editor**, paste
   and save.

The dashboard shows daily graphs, yesterday's and this month's totals per meter,
the monthly peaks, the sync status and the sync button. Generate it again after
changing the meter selection.

```yaml
action: energyid_import.rebuild_statistics
data:
  device_id: 0123456789abcdef0123456789abcdef
  start_date: "2026-06-01"
```

## Examples

- Compare your daily consumption with Mijn Fluvius in the Energy dashboard,
  with separate prices for the day and night tariff.
- Show *Peak this month* on a dashboard to keep an eye on the capacity tariff.
- Notify yourself when the *Stale data* repair appears, so an expired Fluvius
  mandate does not go unnoticed.

## Known limitations

- Data is at least a day old.
- Only hourly resolution in Home Assistant.
- The history goes back as far as EnergyID has it, which is usually the date
  the Fluvius connection was added to the record.
- Corrections older than 35 days need a manual rebuild.
- Tested with Fluvius records (Flanders). Gas meters follow the same path but
  have not been tested against a real record.

## Troubleshooting

- **No data:** check *Latest data* on the meter devices and the repairs. Then
  download the diagnostics from the integration page; private data is redacted.
- **Authentication failed:** Home Assistant asks for a new key; the key needs
  read access to the configured record.
- **Logs:** enable debug logging for the integration from its page, or with the
  `logger.set_level` action for `custom_components.energyid_import`.

## Removal

**Settings → Devices & services → EnergyID Import → ⋮ → Delete**. The imported
statistics stay; delete them under **Settings → Tools → Statistics** if you want
them gone. Then remove the repository from Marketplace.

## Development

```sh
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
```

`docs/SPEC.md` holds the design, `docs/api-notes.md` the verified API behaviour,
and `CLAUDE.md` the working rules.
