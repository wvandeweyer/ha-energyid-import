"""Record anonymised EnergyID API responses for docs/api-notes.md and test fixtures.

Usage:
    ENERGYID_API_KEY=... uv run python scripts/discover.py [--output DIR]

The script calls the four endpoints the integration uses, keeps status codes and
response headers, and replaces ids, names, addresses, EANs, meter numbers and
the household profile by fixed dummy values. Measured values stay real, so the
output is for local analysis only: committed fixtures get synthetic values.
Unknown keys that held text are listed at the end of the run.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
from typing import Final
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientSession, ClientTimeout

type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)

API_BASE_URL: Final = "https://api.energyid.eu/api/v1"
TIME_ZONE: Final = ZoneInfo("Europe/Brussels")
DEFAULT_OUTPUT: Final = Path("tests/fixtures/discover")
TIMEOUT: Final = ClientTimeout(total=60)

# (interval, days back from today). The pairs around 7 and 31 days find the real
# window limit; the 5-year P1M probe shows how far the history goes back.
DATA_PROBES: Final = (
    ("PT15M", 2),
    ("PT15M", 7),
    ("PT15M", 8),
    ("PT1H", 3),
    ("PT1H", 31),
    ("PT1H", 32),
    ("P1D", 14),
    ("P1M", 1826),
)

# Keys ending in "id" are identifiers; keys ending in "number" are private.
ID_SUFFIX: Final = "id"
NUMBER_SUFFIX: Final = "number"
NAME_KEYS: Final = frozenset(
    {"displayname", "name", "fullname", "firstname", "lastname", "initials"}
)
PRIVATE_KEYS: Final = frozenset(
    {
        "address",
        "street",
        "streetaddress",
        "housenumber",
        "city",
        "postalcode",
        "zip",
        "zipcode",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "lng",
        "location",
        "ean",
        "eancode",
        "email",
        "phone",
        "iban",
        "vat",
        # Household profile: harmless alone, revealing next to a name.
        "occupants",
        "dwellingtype",
        "heatingon",
        "auxiliaryheatingon",
        "cookingon",
        "hotwateron",
        "plan",
        "addons",
        "enabledfeatures",
        "created",
        "lastsubmissionat",
        "lastreadingvalue",
    }
)
DROPPED_HEADERS: Final = frozenset({"set-cookie", "cookie", "authorization"})
# Keys whose text values are known to be safe; anything else is reported.
SAFE_TEXT_KEYS: Final = frozenset(
    {
        "timestamp",
        "metric",
        "unit",
        "readingtype",
        "interval",
        "activated",
        "deactivated",
        "timezone",
        "type",
        "category",
        "status",
        "source",
        "lang",
        "country",
        "icon",
        "register",
        "metertype",
        "theme",
        "recordtype",
        "mypermlevel",
        "primaryserviceslug",
        "dso",
        "maxresolution",
        "lastreadingtimestamp",
        "timespan",
    }
)


def _is_id_key(key: str) -> bool:
    """Return whether a lower-cased key holds an identifier."""
    return key.endswith(ID_SUFFIX)


def _is_private_key(key: str) -> bool:
    """Return whether a lower-cased key holds private data of any type."""
    return key in PRIVATE_KEYS or key.endswith(NUMBER_SUFFIX)


@dataclass(frozen=True, slots=True, kw_only=True)
class Probe:
    """One recorded request and its raw response."""

    name: str
    path: str
    params: dict[str, str]
    status: int
    headers: dict[str, str]
    body: JsonValue


class Anonymiser:
    """Replace private values by stable dummy values."""

    def __init__(self) -> None:
        """Start without known ids."""
        self._aliases: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self.unknown_text_keys: set[str] = set()

    def _next(self, kind: str) -> int:
        """Return the next sequence number for a kind of value."""
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return self._counters[kind]

    def register_id(self, real: str, kind: str) -> str:
        """Return the dummy id for a real id, creating it on first use."""
        if real not in self._aliases:
            self._aliases[real] = f"dummy-{kind}-{self._next(kind)}"
        return self._aliases[real]

    def register_ids(self, value: JsonValue, kind: str) -> None:
        """Register every id in a payload; the top-level kind names plain ids."""
        if isinstance(value, list):
            for item in value:
                self.register_ids(item, kind)
        elif isinstance(value, dict):
            for key, item in value.items():
                lowered = key.lower()
                is_id = isinstance(item, str | int) and not isinstance(item, bool)
                if _is_id_key(lowered) and is_id:
                    self.register_id(str(item), _id_kind(lowered, kind))
                else:
                    self.register_ids(item, kind)

    def scrub_text(self, text: str) -> str:
        """Replace every known real id inside free text."""
        for real in sorted(self._aliases, key=len, reverse=True):
            text = text.replace(real, self._aliases[real])
        return text

    def scrub(self, value: JsonValue, key: str = "") -> JsonValue:
        """Return a copy of value with private fields replaced."""
        if isinstance(value, list):
            return [self.scrub(item, key) for item in value]
        if isinstance(value, dict):
            return {k: self.scrub(v, k) for k, v in value.items()}
        lowered = key.lower()
        if value is None or isinstance(value, bool):
            return value
        return self._scrub_scalar(value, lowered)

    def _scrub_scalar(self, value: str | float, lowered: str) -> JsonValue:
        """Scrub one string or number by the key it is stored under."""
        if _is_private_key(lowered):
            replacement = f"dummy-{lowered}"
        elif _is_id_key(lowered) and not isinstance(value, float):
            replacement = self._aliases.get(str(value), "dummy-unknown")
        elif lowered in NAME_KEYS:
            replacement = f"Dummy {lowered} {self._next(lowered)}"
        elif isinstance(value, str):
            replacement = self._scrub_free_text(value, lowered)
        else:
            return value
        return replacement

    def _scrub_free_text(self, value: str, lowered: str) -> str:
        """Scrub a text field that is not known to be private."""
        if lowered not in SAFE_TEXT_KEYS:
            self.unknown_text_keys.add(lowered)
        return self.scrub_text(value)


def _id_kind(key: str, top_level_kind: str) -> str:
    """Return the dummy kind for an id key."""
    return top_level_kind if key == "id" else key.removesuffix("id")


def _today() -> date:
    """Return today's date in Belgium."""
    return datetime.fromtimestamp(time.time(), tz=TIME_ZONE).date()


async def _get(
    session: ClientSession, api_key: str, path: str, params: dict[str, str]
) -> tuple[int, dict[str, str], JsonValue]:
    """Perform one GET and return status, headers and body."""
    headers = {"Authorization": f"apiKey {api_key}", "Accept": "application/json"}
    async with session.get(
        f"{API_BASE_URL}/{path}", headers=headers, params=params
    ) as response:
        text = await response.text()
        kept = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in DROPPED_HEADERS
        }
        try:
            body: JsonValue = json.loads(text)
        except ValueError:
            body = {"non_json_body": text[:500]}
        return response.status, kept, body


async def _probe(
    session: ClientSession,
    api_key: str,
    name: str,
    path: str,
    params: dict[str, str] | None = None,
) -> Probe:
    """Record one request, keeping connection errors as status 0."""
    query = params or {}
    try:
        status, headers, body = await _get(session, api_key, path, query)
    except (ClientError, TimeoutError) as err:
        status, headers, body = 0, {}, {"error": type(err).__name__}
    print(f"{status:>3} {name}", file=sys.stderr)
    return Probe(
        name=name, path=path, params=query, status=status, headers=headers, body=body
    )


def _ids(body: JsonValue) -> list[str]:
    """Return the id of every object in a list payload."""
    if not isinstance(body, list):
        return []
    return [
        str(item["id"])
        for item in body
        if isinstance(item, dict) and isinstance(item.get("id"), str | int)
    ]


async def _probe_meter(
    session: ClientSession, api_key: str, meter_id: str, today: date
) -> list[Probe]:
    """Record the data probes of one meter."""
    probes: list[Probe] = []
    for interval, days in DATA_PROBES:
        params = {
            "start": (today - timedelta(days=days)).isoformat(),
            "end": today.isoformat(),
            "interval": interval,
        }
        name = f"meter_{meter_id}_data_{interval}_{days}d"
        probes.append(
            await _probe(session, api_key, name, f"meters/{meter_id}/data", params)
        )
    return probes


async def collect(api_key: str) -> list[Probe]:
    """Call every endpoint sequentially and return the raw probes."""
    today = _today()
    async with ClientSession(timeout=TIMEOUT) as session:
        probes = [await _probe(session, api_key, "records", "members/me/records")]
        for record_id in _ids(probes[0].body):
            probes.append(
                await _probe(
                    session, api_key, f"record_{record_id}", f"records/{record_id}"
                )
            )
            meters = await _probe(
                session,
                api_key,
                f"record_{record_id}_meters",
                f"records/{record_id}/meters",
            )
            probes.append(meters)
            for meter_id in _ids(meters.body):
                probes.extend(await _probe_meter(session, api_key, meter_id, today))
    return probes


def anonymise(probes: list[Probe], anonymiser: Anonymiser) -> list[JsonValue]:
    """Return the probes as anonymised JSON documents."""
    for probe in probes:
        kind = "meter" if probe.name.endswith("_meters") else "record"
        if probe.name.startswith(("records", "record_")):
            anonymiser.register_ids(probe.body, kind)
    return [
        {
            "name": anonymiser.scrub_text(probe.name),
            "path": anonymiser.scrub_text(probe.path),
            "params": dict(probe.params),
            "status": probe.status,
            "headers": dict(probe.headers),
            "body": anonymiser.scrub(probe.body),
        }
        for probe in probes
    ]


def _summary(document: JsonValue) -> str:
    """Return a one-line summary of a data probe for comparing with Mijn Fluvius."""
    body = document.get("body") if isinstance(document, dict) else None
    points = body.get("data") if isinstance(body, dict) else None
    if not isinstance(points, list) or not points:
        return "no data points"
    values = [
        p["total"]
        for p in points
        if isinstance(p, dict) and isinstance(p.get("total"), int | float)
    ]
    first, last = points[0], points[-1]
    first_ts = first.get("timestamp") if isinstance(first, dict) else None
    last_ts = last.get("timestamp") if isinstance(last, dict) else None
    total = sum(v for v in values if isinstance(v, int | float))
    return f"{len(points)} points, {first_ts} .. {last_ts}, total {total:.3f}"


def write(documents: list[JsonValue], output: Path) -> None:
    """Replace the output directory's JSON files and print data summaries."""
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("*.json"):
        stale.unlink()
    for index, document in enumerate(documents):
        name = document.get("name") if isinstance(document, dict) else None
        path = output / f"{index:02d}_{name}.json"
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if isinstance(name, str) and "_data_" in name:
            print(f"{name}: {_summary(document)}")


def main() -> int:
    """Run the discovery and return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    api_key = os.environ.get("ENERGYID_API_KEY", "")
    if not api_key:
        print("Set ENERGYID_API_KEY in the environment.", file=sys.stderr)
        return 2
    anonymiser = Anonymiser()
    documents = anonymise(asyncio.run(collect(api_key)), anonymiser)
    output: Path = args.output
    write(documents, output)
    print(f"Wrote {len(documents)} files to {output}")
    if anonymiser.unknown_text_keys:
        keys = ", ".join(sorted(anonymiser.unknown_text_keys))
        print(f"Review these text fields before committing: {keys}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
