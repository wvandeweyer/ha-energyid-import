# EnergyID API notes

Status: **answered except question 9** — two runs of `scripts/discover.py` on
2026-11-24 against one Fluvius record (1 record, 4 meters,
`limits.maxResolution` = `PT5M`). Question 9 needs two runs weeks apart.

## How to run phase 0

```sh
export ENERGYID_API_KEY=...   # personal key, records:read only
uv run python scripts/discover.py
```

The raw output lands in `tests/fixtures/discover/`, which git ignores. Review
every file, check the "Review these text fields" line at the end of the run, and
only then copy the files that become test fixtures into `tests/fixtures/`.
Compare one day of the `PT15M` and `P1D` summaries with Mijn Fluvius.

## Questions from section 13

| # | Question | Assumption in the spec | Answer | Evidence |
| --- | --- | --- | --- | --- |
| 1 | Does `meters/{id}/data` return consumption per interval or cumulative readings? | Consumption per interval | **Per interval.** | Four `PT15M` values sum to the `PT1H` value of their hour (48 hours, max diff 4e-16); hours sum to `P1D` exactly. |
| 2 | Is `multiplier` already applied, and in which unit are the values? | Applied, meter unit | **Unit of the meter (kWh).** Multiplier is 1.0 on every meter, so "applied" is untested. | Response repeats `unit` at top level. |
| 3 | Does `timestamp` mark the start or the end of the interval? | Start | **Start.** | `start=2026-11-22, end=2026-11-24` returns `00:00` … `23:45`; the quarters `00:00`–`00:45` sum to hour `00:00`. |
| 4 | Does `PT1H` work for Fluvius meters; what comes back for a record with daily values only? | Works; daily meters via `P1D` | **`PT1H` works.** Daily-only record not tested (this record has quarter-hour data). | 72 points for 3 days. |
| 5 | How do Fluvius meters appear: one per direction, or also per day/night register? | One import, one export | **Per direction and per register: 4 meters.** `electricityImport` and `electricityExport`, each with `register` `high` and `low`. | Meters response. |
| 6 | What is the real maximum window per request; are there rate limits or 429s? | 31 days; no hard limit known | **`PT15M`: 7 days; `PT1H`: 31 days.** One day more gives 400 with an empty body. `P1D` up to 731 days untested. No 429 in 52 sequential calls; no rate-limit headers. | Second run: 7d/8d and 31d/32d probes on all 4 meters. |
| 7 | Does `members/me/records` exist for a personal key, and is read access enough? | Yes | **Yes.** | 200 with a list of one record. |
| 8 | How far back does the Fluvius history in EnergyID go? | At least 12 months | **Only back to the month the record was created.** A 5-year `P1M` request returns nothing earlier. | `P1M` probes. |
| 9 | Does EnergyID refetch corrected Fluvius values, and within what period? | Yes, within 35 days | Not answerable from one run; needs two runs weeks apart. | |

## Response shapes

- `meters/{id}/data`: `{"metric", "unit", "total", "timespan", "data": [{"timestamp", "total"}]}`.
  The value field is **`total`**, not `value`.
- `end` is **exclusive**: `start=2026-11-22&end=2026-11-24` returns two days.
  Consecutive windows therefore share no points.
- Timestamps carry the local offset (`+01:00`); daily points sit at local midnight.
- Daylight saving: the 31-day `PT1H` window over 2026-10-25 returns 745 points,
  with `02:00+02:00` and `02:00+01:00` both present: 745 unique UTC hours, no gaps.
- A window beyond the limit returns **400 with an empty body**, which the client
  maps to `EnergyIDApiError`.
- Meter fields seen: `id`, `recordId`, `gridConnectionId`, `meterType`, `theme`,
  `metric`, `displayName`, `hidden`, `excludeFromReports`, `multiplier`,
  `readingType` (`premarkedInterval`), `unit`, `automatic`, `integrationId`,
  `bidirectional`, `icon`, `confirmed`, `closed`, `connectionNumber` (the EAN),
  `renewable`, `meterNumber`, `errorCount`, `interval`, `register`,
  (the client keeps `gridConnectionId` and only the last 4 characters of
  `connectionNumber`),
  `lastReadingTimestamp`, `lastReadingValue`. There is **no `activated` or
  `deactivated`**; a closed meter has `closed: true`.
- Record fields include `timeZone` (top level and under `address`), `limits.maxResolution`,
  `plan`, `closed`, `created`, a household profile (`occupants`, `dwellingType`,
  `heatingOn`, …) and private data: `recordNumber`, `address.lat/lon`, `stationIds`.

## Impact on the spec

- Section 6: "not closed" means `closed` is not `true`.
- Section 7: the backfill start cannot use an activation date; it is
  `today - backfill_months` (optionally bounded by the record's `created`).
- Section 6/13: each direction has separate `high`/`low` registers. Decision
  (Wim, 2026-11-24): keep them separate, one statistic per register.

## Assumptions the phase 1 code made, now checked

| Where | Assumption | Result |
| --- | --- | --- |
| `members/me/records` | A list of records with `id` and `displayName` | Confirmed |
| Record object | Time zone in `timeZone` | Confirmed |
| `meters/{id}/data` | `{"data": [{"timestamp", "value"}]}` | Wrong: the field is `total`; code fixed |
| `end` parameter | Windows share their boundary day | Wrong: `end` is exclusive; code needs no change |
| Meter `deactivated` | Set for closed meters | Wrong: `closed` flag; code fixed |

`tests/fixtures/` holds the bodies of real responses with every private value
replaced: ids, names, EAN and household profile are dummies, and every measured
value is synthetic. Timestamps, point counts and the daylight-saving switch are
real: `records.json`, `record.json`, `meters.json`, `data_pt15m_2d.json`,
`data_pt1h_3d.json`, `data_pt1h_31d_dst.json` and `data_p1d_14d.json`.
`records_multiple.json` stays hand-written. The raw output of
`scripts/discover.py` keeps real measurements and never leaves the machine.
