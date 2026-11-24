# EnergyID Import for Home Assistant

Imports hourly meter data from an [EnergyID](https://www.energyid.eu) record into
Home Assistant long-term statistics, at the original timestamps, so the Energy
dashboard shows the official Fluvius consumption per hour.

> Status: early development (phase 1 of `docs/SPEC.md`). The config flow works;
> the statistics import arrives in phase 2.

## What it does and does not do

- Reads only: it never writes readings to EnergyID.
- Imports history, not live data. EnergyID fetches Fluvius data once a day, so
  values arrive at least a day late.
- Hourly resolution: Home Assistant statistics are hourly.

## Installation

1. In HACS, add this repository as a custom repository of type *Integration*.
2. Install **EnergyID Import** and restart Home Assistant.
3. In EnergyID, create a personal API key with read access to records.
4. Add the integration under *Settings → Devices & services* and enter the key.

## Development

```sh
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
```

See `docs/SPEC.md` for the design and `CLAUDE.md` for the working rules.
